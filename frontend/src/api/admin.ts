import { apiFetch } from '@/lib/api-client';

export type AdminCategory = {
	id: number;
	name: string;
};

export type PublicationScope = 'all_users' | 'admin_only';

export type AdminBookRegistrationInput = {
	title: string;
	authorName: string;
	publisher: string;
	publishedAt: string;
	categoryId: number;
	pageTurnDirection: 'ltr' | 'rtl';
	description: string;
	publicationScope: PublicationScope;
	file: File;
};

export type AdminBookFile = {
	id: number;
	extension: 'epub' | 'pdf';
	mimeType: string;
	originalFileName: string;
	fileSize: number;
};

export type AdminBook = {
	id: number;
	title: string;
	authorName: string | null;
	publisher: string | null;
	publishedAt: string | null;
	categoryId: number;
	pageTurnDirection: 'ltr' | 'rtl';
	description: string | null;
	category: AdminCategory;
	publicationScope: PublicationScope;
	file: AdminBookFile | null;
};

export async function fetchAdminCategories(): Promise<AdminCategory[]> {
	const response = await apiFetch<{ categories: AdminCategory[] }>('/api/v1/admin/categories');
	return response.categories;
}

export async function fetchAdminBooks(): Promise<AdminBook[]> {
	const response = await apiFetch<{ books: AdminBook[] }>('/api/v1/admin/books');
	return response.books;
}

export function registerAdminBook(input: AdminBookRegistrationInput): Promise<AdminBook & { file: AdminBookFile }> {
	const formData = new FormData();
	formData.append('title', input.title.trim());
	formData.append('authorName', input.authorName.trim());
	formData.append('publisher', input.publisher.trim());
	formData.append('publishedAt', input.publishedAt);
	formData.append('categoryId', String(input.categoryId));
	formData.append('pageTurnDirection', input.pageTurnDirection);
	formData.append('description', input.description.trim());
	formData.append('publicationScope', input.publicationScope);
	formData.append('file', input.file, input.file.name);

	return apiFetch<AdminBook & { file: AdminBookFile }>('/api/v1/admin/book-registrations', {
		method: 'POST',
		body: formData,
	});
}
