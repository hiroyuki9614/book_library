import type { Book } from '@/data/booksData';
import { apiFetch, getApiBaseUrl } from '@/lib/api-client';

export type BookDetails = {
	id: number;
	title: string;
	authorName: string | null;
	category: { id: number; name: string };
	readStatus: 'unread' | 'reading' | 'completed';
	hasFile: boolean;
	fileType: 'pdf' | 'epub' | null;
	createdAt: string;
	updatedAt: string;
};

type BooksResponse = {
	books: BookDetails[];
};

function toFrontendBook(book: BookDetails): Book {
	const status = book.readStatus[0].toUpperCase() + book.readStatus.slice(1) as Book['status'];
	const category = book.category.name as Book['category'];

	return {
		id: book.id,
		title: book.title,
		author: book.authorName ?? '',
		status,
		fileType: book.fileType ?? 'pdf',
		category,
		currentPage: 0,
		createdAt: book.createdAt,
		updatedAt: book.updatedAt,
	};
}

export async function fetchBooks() {
	const response = await apiFetch<BooksResponse>('/api/v1/books');
	return response.books.map(toFrontendBook);
}

export async function fetchBook(bookId: number) {
	return apiFetch<BookDetails>(`/api/v1/books/${bookId}`);
}

function isSupportedBookFileContentType(response: Response) {
	const contentType = response.headers.get('content-type')?.split(';', 1)[0].trim().toLowerCase();
	return contentType === 'application/pdf' || contentType === 'application/epub+zip';
}

async function readBookFileResponse(response: Response) {
	if (!isSupportedBookFileContentType(response)) {
		throw new Error('API returned an unsupported book file response');
	}
	return response.blob();
}

export async function fetchBookFile(bookId: number) {
	const baseUrl = getApiBaseUrl();
	if (!baseUrl) {
		throw new Error('VITE_API_BASE_URL is not configured');
	}

	const response = await fetch(`${baseUrl.replace(/\/$/, '')}/api/v1/books/${bookId}/file`, {
		credentials: 'include',
	});
	if (!response.ok) {
		throw new Error(`API request failed: ${response.status}`);
	}

	const contentType = response.headers.get('content-type')?.split(';', 1)[0].trim().toLowerCase();
	if (contentType === 'application/pdf' || contentType === 'application/epub+zip') {
		return response.blob();
	}

	if (contentType !== 'application/json') {
		throw new Error('API returned an unsupported book file response');
	}

	const access = await response.json() as { kind?: unknown; url?: unknown };
	if (access.kind !== 'signed-url' || typeof access.url !== 'string' || !access.url) {
		throw new Error('API returned an invalid signed book file response');
	}

	const signedResponse = await fetch(access.url, { credentials: 'omit' });
	if (!signedResponse.ok) {
		throw new Error(`Signed book file request failed: ${signedResponse.status}`);
	}
	return readBookFileResponse(signedResponse);
}
