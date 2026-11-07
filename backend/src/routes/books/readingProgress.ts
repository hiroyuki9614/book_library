import type { PrismaClient } from '../../../prisma/generated/prisma/client.js';

export type ReadStatus = 'unread' | 'reading' | 'completed';

export type ReadingProgressRecord = {
	currentPosition?: string | null;
	progressPercentage?: number | null;
	readStatus?: string | null;
};

export const READING_SESSION_USER_HEADER = 'X-BeLib-User-Id';

export function parseReadingSessionUserId(value: string | undefined) {
	if (!value) return null;
	const normalized = value.trim();
	if (!/^[1-9]\d*$/.test(normalized)) return null;
	const userId = Number(normalized);
	return Number.isSafeInteger(userId) ? userId : null;
}

export function matchesReadingSessionUser(value: string | undefined, userId: number) {
	return parseReadingSessionUserId(value) === userId;
}

export function hasReadingSessionUserMismatch(value: string | undefined, userId: number) {
	return value !== undefined && !matchesReadingSessionUser(value, userId);
}

export function normalizeReadStatus(value: string | null | undefined): ReadStatus {
	return value === 'reading' || value === 'completed' ? value : 'unread';
}

export function normalizeProgressPercentage(value: unknown): number | null {
	return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 && value <= 100 ? value : null;
}

export function toReadingProgress(readingInfo: ReadingProgressRecord | null | undefined): number | null {
	const status = normalizeReadStatus(readingInfo?.readStatus);
	if (status === 'unread') return 0;
	if (status === 'completed') return 100;
	return normalizeProgressPercentage(readingInfo?.progressPercentage) ?? null;
}

type ReadingInfoPersistence = Pick<PrismaClient, 'readingInfo'>;

type PersistReadingInfoInput = {
	userId: number;
	bookId: number;
	currentPosition: string;
	readStatus: 'reading' | 'completed';
	progressPercentage: number | null | undefined;
};

function isUniqueConflict(error: unknown) {
	return typeof error === 'object'
		&& error !== null
		&& 'code' in error
		&& error.code === 'P2002';
}

/**
 * Persist reader state without allowing a stale reading save to downgrade a
 * completed record. The conditional update is atomic at the database row
 * boundary; the create retry closes the unique-key race for a first save.
 */
export async function persistReadingInfo(
	prisma: ReadingInfoPersistence,
	input: PersistReadingInfoInput,
): Promise<ReadingProgressRecord> {
	const where = {
		userId_bookId: { userId: input.userId, bookId: input.bookId },
	};

	if (input.readStatus === 'completed') {
		return prisma.readingInfo.upsert({
			where,
			create: {
				userId: input.userId,
				bookId: input.bookId,
				currentPosition: input.currentPosition,
				readStatus: 'completed',
				progressPercentage: 100,
			},
			update: {
				currentPosition: input.currentPosition,
				readStatus: 'completed',
				progressPercentage: 100,
			},
		});
	}

	const progressFields = input.progressPercentage === undefined
		? {}
		: { progressPercentage: input.progressPercentage };
	const readingData = {
		currentPosition: input.currentPosition,
		readStatus: 'reading' as const,
		...progressFields,
	};

	for (let attempt = 0; attempt < 2; attempt += 1) {
		const updated = await prisma.readingInfo.updateMany({
			where: { ...where, readStatus: { not: 'completed' } },
			data: readingData,
		});
		if (updated.count > 0) {
			const saved = await prisma.readingInfo.findUnique({ where });
			if (saved) return saved;
			continue;
		}

		const existing = await prisma.readingInfo.findUnique({
			where,
			select: { currentPosition: true, progressPercentage: true, readStatus: true },
		});
		if (existing?.readStatus === 'completed') {
			const completedUpdate = await prisma.readingInfo.updateMany({
				where: { ...where, readStatus: 'completed' },
				data: {
					currentPosition: input.currentPosition,
					readStatus: 'completed',
					progressPercentage: 100,
				},
			});
			if (completedUpdate.count > 0) {
				const saved = await prisma.readingInfo.findUnique({ where });
				if (saved) return saved;
			}
			continue;
		}
		if (existing) continue;

		try {
			return await prisma.readingInfo.create({
				data: {
					userId: input.userId,
					bookId: input.bookId,
					...readingData,
				},
			});
		} catch (error) {
			if (!isUniqueConflict(error)) throw error;
		}
	}

	throw new Error('ReadingInfo changed during save');
}

export function toReadingInfoResponse(bookId: number, readingInfo: ReadingProgressRecord | null | undefined) {
	const currentPosition = readingInfo?.currentPosition ?? null;
	const parsedPage = currentPosition !== null && /^\d+$/.test(currentPosition) ? Number(currentPosition) : Number.NaN;
	const currentPage = Number.isSafeInteger(parsedPage) && parsedPage > 0 ? parsedPage : 1;

	return {
		bookId,
		currentPosition,
		currentPage,
		progressPercentage: toReadingProgress(readingInfo),
		readStatus: normalizeReadStatus(readingInfo?.readStatus),
	};
}
