import { apiFetch } from '@/lib/api-client';

export type ReadStatus = 'unread' | 'reading' | 'completed';

export type ReadingInfo = {
	bookId: number;
	currentPosition: string | null;
	currentPage: number;
	readStatus: ReadStatus;
};

export async function fetchReadingInfo(bookId: number): Promise<ReadingInfo> {
	return apiFetch<ReadingInfo>(`/api/v1/books/${bookId}/reading-info`);
}

export async function saveReadingInfo(
	bookId: number,
	position: number | string,
	readStatus: Exclude<ReadStatus, 'unread'> = 'reading',
): Promise<ReadingInfo> {
	const body = typeof position === 'number'
		? { currentPage: position, readStatus }
		: { currentPosition: position, readStatus };

	return apiFetch<ReadingInfo>(`/api/v1/books/${bookId}/reading-info`, {
		method: 'PATCH',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body),
	});
}
