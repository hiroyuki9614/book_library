import { apiFetch } from '@/lib/api-client';

export type ReadStatus = 'unread' | 'reading' | 'completed';

export type ReadingInfo = {
	bookId: number;
	currentPosition: string | null;
	currentPage: number;
	readStatus: ReadStatus;
};

export async function fetchReadingInfo(bookId: number): Promise<ReadingInfo> {
	await pendingSaves.get(bookId)?.catch(() => undefined);
	return apiFetch<ReadingInfo>(`/api/v1/books/${bookId}/reading-info`);
}

// Keep writes ordered across reader remounts too. A slow older PATCH must finish
// before a newer position can be submitted for the same book in this tab.
const pendingSaves = new Map<number, Promise<ReadingInfo>>();

export function saveReadingInfo(
	bookId: number,
	position: number | string,
	readStatus: Exclude<ReadStatus, 'unread'> = 'reading',
): Promise<ReadingInfo> {
	const body = typeof position === 'number'
		? { currentPage: position, readStatus }
		: { currentPosition: position, readStatus };

	const previous = pendingSaves.get(bookId) ?? Promise.resolve();
	const next = previous.catch(() => undefined).then(() => apiFetch<ReadingInfo>(`/api/v1/books/${bookId}/reading-info`, {
		method: 'PATCH',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body),
	}));
	pendingSaves.set(bookId, next);
	void next.finally(() => {
		if (pendingSaves.get(bookId) === next) pendingSaves.delete(bookId);
	}).catch(() => undefined);
	return next;
}
