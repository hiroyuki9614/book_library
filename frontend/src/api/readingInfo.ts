import { apiFetch } from '@/lib/api-client';

export type ReadStatus = 'unread' | 'reading' | 'completed';

export type ReadingInfo = {
	bookId: number;
	currentPosition: string | null;
	currentPage: number;
	progressPercentage: number | null;
	readStatus: ReadStatus;
};

const READING_SESSION_USER_HEADER = 'X-BeLib-User-Id';

let readingSaveGeneration = 0;
const inFlightReadingSaves = new Set<AbortController>();

function getReadingUserId(userId: number | undefined) {
	return userId !== undefined && Number.isSafeInteger(userId) && userId > 0 ? userId : null;
}

export async function fetchReadingInfo(bookId: number, userId?: number): Promise<ReadingInfo> {
	const sessionUserId = getReadingUserId(userId);
	const scopeKey = getReadingScopeKey(bookId, sessionUserId ?? undefined);
	for (const flush of readingFlushes.get(scopeKey) ?? []) flush();
	await pendingSaves.get(scopeKey)?.catch(() => undefined);
	return apiFetch<ReadingInfo>(`/api/v1/books/${bookId}/reading-info`, sessionUserId === null
		? undefined
		: { headers: { [READING_SESSION_USER_HEADER]: String(sessionUserId) } });
}

// Keep writes ordered across reader remounts too. A slow older PATCH must finish
// before a newer position can be submitted for the same book in this tab. The
// scope key prevents a queued save from one account being reused after logout.
// Auth transitions also advance the generation before reader teardown, so a
// cleanup flush retains its original identity and cannot start a stale PATCH.
const pendingSaves = new Map<string, Promise<ReadingInfo>>();
const readingFlushes = new Map<string, Set<() => void>>();

export function getReadingSaveGeneration() {
	return readingSaveGeneration;
}

export function invalidateReadingSaves() {
	readingSaveGeneration += 1;
	pendingSaves.clear();
	readingFlushes.clear();
	for (const controller of inFlightReadingSaves) controller.abort();
	inFlightReadingSaves.clear();
}

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
	generation?: number,
): Promise<ReadingInfo> {
	const sessionUserId = getReadingUserId(userId);
	const requestGeneration = generation ?? readingSaveGeneration;
	const positionBody = typeof position === 'number'
		? { currentPage: position, readStatus }
		: { currentPosition: position, readStatus };
	const body = progressPercentage === undefined ? positionBody : { ...positionBody, progressPercentage };

	const scopeKey = getReadingScopeKey(bookId, sessionUserId ?? undefined);
	const previous = pendingSaves.get(scopeKey) ?? Promise.resolve();
	const headers: Record<string, string> = { 'Content-Type': 'application/json' };
	if (sessionUserId !== null) headers[READING_SESSION_USER_HEADER] = String(sessionUserId);
	const next = previous.catch(() => undefined).then(() => {
		if (requestGeneration !== readingSaveGeneration) {
			throw new Error('Reading save invalidated by authentication change');
		}

		const controller = new AbortController();
		inFlightReadingSaves.add(controller);
		return apiFetch<ReadingInfo>(`/api/v1/books/${bookId}/reading-info`, {
			method: 'PATCH',
			headers,
			body: JSON.stringify(body),
			signal: controller.signal,
		}).finally(() => {
			inFlightReadingSaves.delete(controller);
		});
	});
	pendingSaves.set(scopeKey, next);
	void next.finally(() => {
		if (pendingSaves.get(scopeKey) === next) pendingSaves.delete(scopeKey);
	}).catch(() => undefined);
	return next;
}
