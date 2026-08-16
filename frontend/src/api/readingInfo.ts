import { apiFetch } from '@/lib/api-client';

export type ReadingInfo = {
	bookId: number;
	currentPage: number;
	readStatus: 'unread' | 'reading' | 'completed';
};

export async function fetchReadingInfo(bookId: number): Promise<ReadingInfo> {
	return apiFetch<ReadingInfo>(`/api/v1/books/${bookId}/reading-info`);
}

export async function saveReadingInfo(bookId: number, currentPage: number, totalPages: number): Promise<ReadingInfo> {
	return apiFetch<ReadingInfo>(`/api/v1/books/${bookId}/reading-info`, {
		method: 'PATCH',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ currentPage, totalPages }),
	});
}
