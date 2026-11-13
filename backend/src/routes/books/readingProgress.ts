import type { PrismaClient } from '../../../prisma/generated/prisma/client.js';

export type ReadStatus = 'unread' | 'reading' | 'completed';

export type ReadingProgressRecord = {
	currentPosition?: string | null;
	progressPercentage?: number | null;
	readStatus?: string | null;
	saveSequence?: bigint | number | null;
};

export const READING_SESSION_USER_HEADER = 'X-BeLib-User-Id';
export const READING_SAVE_PROTOCOL = 'cas-v1';
export const MAX_CURRENT_POSITION_LENGTH = 16_384;
const MAX_READING_SAVE_SEQUENCE = 9_223_372_036_854_775_807n;
const MAX_FORWARD_SAVE_SEQUENCE_GAP = 1_000_000n;

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

export function parseReadingSaveSequence(value: unknown): bigint | null | undefined {
	if (value === undefined) return undefined;
	if (typeof value === 'number') {
		if (!Number.isSafeInteger(value) || value < 0) return null;
		return BigInt(value);
	}
	if (typeof value !== 'string' || !/^\d+$/.test(value)) return null;
	try {
		const sequence = BigInt(value);
		return sequence <= MAX_READING_SAVE_SEQUENCE ? sequence : null;
	} catch {
		return null;
	}
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
	saveSequence?: bigint;
	expectedSaveSequence?: bigint;
};

export class StaleReadingInfoError extends Error {
	readonly code = 'STALE_READING_SAVE';

	constructor() {
		super('Reading save is older than the persisted position');
		this.name = 'StaleReadingInfoError';
	}
}

function isUniqueConflict(error: unknown) {
	return typeof error === 'object'
		&& error !== null
		&& 'code' in error
		&& error.code === 'P2002';
}

function sequenceCondition(expectedSaveSequence: bigint | undefined) {
	if (expectedSaveSequence !== undefined) return { equals: expectedSaveSequence };
	return { equals: 0n };
}

function canApplySequence(storedSequence: bigint, incomingSequence: bigint, expectedSaveSequence: bigint | undefined) {
	if (expectedSaveSequence !== undefined) return storedSequence === expectedSaveSequence;
	return incomingSequence === 0n && storedSequence === 0n;
}

function getRecordSequence(readingInfo: ReadingProgressRecord | null | undefined) {
	if (typeof readingInfo?.saveSequence === 'bigint') return readingInfo.saveSequence;
	if (typeof readingInfo?.saveSequence === 'number' && Number.isSafeInteger(readingInfo.saveSequence) && readingInfo.saveSequence >= 0) {
		return BigInt(readingInfo.saveSequence);
	}
	return 0n;
}

/**
 * Persist reader state with a database-enforced per-user/book sequence.
 * updateMany's sequence predicate is the compare-and-set: a slow request can
 * finish after a newer request, but it cannot overwrite that newer row.
 * Sequence-zero requests are the compatibility path for pre-P1 clients and
 * are accepted only while the row has never received a versioned save. A
 * versioned save may advance over a bounded gap so browser-global reservations
 * can arrive out of order without allowing an unbounded sequence jump.
 */
export async function persistReadingInfo(
	prisma: ReadingInfoPersistence,
	input: PersistReadingInfoInput,
): Promise<ReadingProgressRecord> {
	if (input.currentPosition.length > MAX_CURRENT_POSITION_LENGTH) {
		throw new RangeError(`currentPosition must be ${MAX_CURRENT_POSITION_LENGTH} characters or fewer`);
	}

	const where = {
		userId_bookId: { userId: input.userId, bookId: input.bookId },
	};
	const saveSequence = input.saveSequence;
	const expectedSaveSequence = input.expectedSaveSequence;
	const sequence = saveSequence ?? 0n;
	if (saveSequence !== undefined && saveSequence > 0n && expectedSaveSequence === undefined) throw new StaleReadingInfoError();
	if (expectedSaveSequence !== undefined && (saveSequence === undefined || saveSequence <= expectedSaveSequence || saveSequence - expectedSaveSequence > MAX_FORWARD_SAVE_SEQUENCE_GAP)) {
		throw new StaleReadingInfoError();
	}
	const sequencePredicate = sequenceCondition(expectedSaveSequence);

	const progressFields = input.progressPercentage === undefined
		? {}
		: { progressPercentage: input.progressPercentage };
	const readingData = {
		currentPosition: input.currentPosition,
		readStatus: 'reading' as const,
		saveSequence: sequence,
		...progressFields,
	};
	const completedData = {
		currentPosition: input.currentPosition,
		readStatus: 'completed' as const,
		progressPercentage: 100,
		saveSequence: sequence,
	};

	for (let attempt = 0; attempt < 2; attempt += 1) {
		const updated = input.readStatus === 'completed'
			? await prisma.readingInfo.updateMany({
				where: { userId: input.userId, bookId: input.bookId, saveSequence: sequencePredicate },
				data: completedData,
			})
			: await prisma.readingInfo.updateMany({
				where: { userId: input.userId, bookId: input.bookId, readStatus: { not: 'completed' }, saveSequence: sequencePredicate },
				data: readingData,
			});
		if (updated.count > 0) {
			const saved = await prisma.readingInfo.findUnique({ where });
			if (saved) return saved;
			continue;
		}

		const existing = await prisma.readingInfo.findUnique({
			where,
			select: { currentPosition: true, progressPercentage: true, readStatus: true, saveSequence: true },
		});
		if (existing) {
			if (!canApplySequence(getRecordSequence(existing), sequence, expectedSaveSequence)) throw new StaleReadingInfoError();

			if (input.readStatus === 'reading' && existing.readStatus === 'completed') {
				const completedUpdate = await prisma.readingInfo.updateMany({
					where: { userId: input.userId, bookId: input.bookId, readStatus: 'completed', saveSequence: sequencePredicate },
					data: completedData,
				});
				if (completedUpdate.count > 0) {
					const saved = await prisma.readingInfo.findUnique({ where });
					if (saved) return saved;
				}
			}
			continue;
		}

		if (expectedSaveSequence !== undefined && expectedSaveSequence !== 0n) throw new StaleReadingInfoError();
		if (sequence > MAX_FORWARD_SAVE_SEQUENCE_GAP) throw new StaleReadingInfoError();

		try {
			return await prisma.readingInfo.create({
				data: {
					userId: input.userId,
					bookId: input.bookId,
					...(input.readStatus === 'completed' ? completedData : readingData),
				},
			});
		} catch (error) {
			if (!isUniqueConflict(error)) throw error;
		}
	}

	throw new Error('ReadingInfo changed during save');
}

export function toReadingInfoResponse(
	bookId: number,
	readingInfo: ReadingProgressRecord | null | undefined,
	includeSaveSequence = false,
	includeReadingSaveProtocol = false,
) {
	const currentPosition = readingInfo?.currentPosition ?? null;
	const parsedPage = currentPosition !== null && /^\d+$/.test(currentPosition) ? Number(currentPosition) : Number.NaN;
	const currentPage = Number.isSafeInteger(parsedPage) && parsedPage > 0 ? parsedPage : 1;

	const response = {
		bookId,
		currentPosition,
		currentPage,
		progressPercentage: toReadingProgress(readingInfo),
		readStatus: normalizeReadStatus(readingInfo?.readStatus),
	};
	if (!includeSaveSequence) return response;
	return {
		...response,
		saveSequence: getRecordSequence(readingInfo).toString(),
		...(includeReadingSaveProtocol ? { readingSaveProtocol: READING_SAVE_PROTOCOL } : {}),
	};
}
