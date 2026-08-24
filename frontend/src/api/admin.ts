import { apiFetch } from '@/lib/api-client';

export type AdminCategory = {
	id: number;
	name: string;
	displayOrder: number;
	isActive: boolean;
};

export type AdminCategorySummary = Pick<AdminCategory, 'id' | 'name'>;
export type PublicationScope = 'all_users' | 'admin_only';
export type AdminBookState = 'active' | 'deleted';

export type AdminUser = {
	id: number;
	email: string;
	name: string;
	deletedAt: string | null;
	createdAt: string;
};

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
	deletedAt: string | null;
	category: AdminCategorySummary;
	publicationScope: PublicationScope;
	file: AdminBookFile | null;
};

type AdminBookDeletionResponse = {
	id: number;
	deletedAt: string | null;
};

type AdminUserStateResponse = {
	id: number;
	email: string;
	name: string;
	deletedAt: string | null;
};

export type AdminCategoryDeletionResponse = {
	movedBookCount: number;
	category: Pick<AdminCategory, 'id' | 'name' | 'isActive'>;
};

export async function fetchAdminCategories(): Promise<AdminCategory[]> {
	const response = await apiFetch<{ categories: AdminCategory[] }>('/api/v1/admin/categories');
	return response.categories;
}

export function createAdminCategory(name: string): Promise<AdminCategory> {
	return apiFetch<AdminCategory>('/api/v1/admin/categories', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ name }),
	});
}

export function renameAdminCategory(categoryId: number, name: string): Promise<AdminCategory> {
	return apiFetch<AdminCategory>(`/api/v1/admin/categories/${categoryId}`, {
		method: 'PATCH',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ name }),
	});
}

export function deleteAdminCategory(categoryId: number): Promise<AdminCategoryDeletionResponse> {
	return apiFetch<AdminCategoryDeletionResponse>(`/api/v1/admin/categories/${categoryId}/delete`, { method: 'PATCH' });
}

export async function fetchAdminUsers(): Promise<AdminUser[]> {
	const response = await apiFetch<{ users: AdminUser[] }>('/api/v1/admin/users');
	return response.users;
}

export function createAdminUser(input: { email: string; name: string; password: string }): Promise<AdminUser> {
	return apiFetch<AdminUser>('/api/v1/admin/users', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({
			email: input.email.trim(),
			name: input.name.trim(),
			password: input.password,
		}),
	});
}

export function disableAdminUser(userId: number): Promise<AdminUserStateResponse> {
	return apiFetch<AdminUserStateResponse>(`/api/v1/admin/users/${userId}/disable`, { method: 'PATCH' });
}

export function restoreAdminUser(userId: number): Promise<AdminUserStateResponse> {
	return apiFetch<AdminUserStateResponse>(`/api/v1/admin/users/${userId}/restore`, { method: 'PATCH' });
}

export function resetAdminUserPassword(userId: number, password: string): Promise<{ id: number; passwordReset: true }> {
	return apiFetch<{ id: number; passwordReset: true }>(`/api/v1/admin/users/${userId}/password`, {
		method: 'PATCH',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ password }),
	});
}

export async function fetchAdminBooks(state: AdminBookState = 'active'): Promise<AdminBook[]> {
	const response = await apiFetch<{ books: AdminBook[] }>(`/api/v1/admin/books?state=${state}`);
	return response.books;
}

export function softDeleteAdminBook(bookId: number): Promise<AdminBookDeletionResponse> {
	return apiFetch<AdminBookDeletionResponse>(`/api/v1/admin/books/${bookId}/delete`, { method: 'PATCH' });
}

export function restoreAdminBook(bookId: number): Promise<AdminBookDeletionResponse> {
	return apiFetch<AdminBookDeletionResponse>(`/api/v1/admin/books/${bookId}/restore`, { method: 'PATCH' });
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
