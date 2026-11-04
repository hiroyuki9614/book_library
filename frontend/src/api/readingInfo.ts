import { apiFetch } from '@/lib/api-client';

export type ReadStatus = 'unread' | 'reading' | 'completed';

export type ReadingInfo = {
	bookId: number;
	currentPosition: string | null;
	currentPage: number;
	progressPercentage: number | null;
	readStatus: ReadStatus;
};

export async function fetchReadingInfo(bookId: number, userId?: number): Promise<ReadingInfo> {
	const scopeKey = getReadingScopeKey(bookId, userId);
	for (const flush of readingFlushes.get(scopeKey) ?? []) flush();
	await pendingSaves.get(scopeKey)?.catch(() => undefined);
	return apiFetch<ReadingInfo>(`/api/v1/books/${bookId}/reading-info`);
}

// Keep writes ordered across reader remounts too. A slow older PATCH must finish
// before a newer position can be submitted for the same book in this tab. The
// scope key prevents a queued save from one account being reused after logout.
const pendingSaves = new Map<string, Promise<ReadingInfo>>();
const readingFlushes = new Map<string, Set<() => void>>();

function getReadingScopeKey(bookId: number, userId?: number) {
	return `${userId === undefined ? 'anonymous' : `user:${userId}`}:book:${bookId}`;
}

export function registerReadingFlush(bookId: number, flush: () => void, userId?: number) {
	const scopeKey = getReadingScopeKey(bookId, userId);
	const callbacks = readingFlushes.get(scopeKey) ?? new Set<() => void>();
	callbacks.add(flush);
	readingFlushes.set(scopeKey, callbacks);
	return () => {
		callbacks.delete(flush);
		if (!callbacks.size) readingFlushes.delete(scopeKey);
	};
}

export function saveReadingInfo(
	bookId: number,
	position: number | string,
	readStatus: Exclude<ReadStatus, 'unread'> = 'reading',
	progressPercentage?: number | null,
	userId?: number,
): Promise<ReadingInfo> {
	const positionBody = typeof position === 'number'
		? { currentPage: position, readStatus }
		: { currentPosition: position, readStatus };
	const body = progressPercentage === undefined ? positionBody : { ...positionBody, progressPercentage };

	const scopeKey = getReadingScopeKey(bookId, userId);
	const previous = pendingSaves.get(scopeKey) ?? Promise.resolve();
	const next = previous.catch(() => undefined).then(() => apiFetch<ReadingInfo>(`/api/v1/books/${bookId}/reading-info`, {
		method: 'PATCH',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body),
	}));
	pendingSaves.set(scopeKey, next);
	void next.finally(() => {
		if (pendingSaves.get(scopeKey) === next) pendingSaves.delete(scopeKey);
	}).catch(() => undefined);
	return next;
}
