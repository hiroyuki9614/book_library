import { apiFetch } from '@/lib/api-client';

export type AdminCategory = {
	id: number;
	name: string;
};

export type AdminBookCreateInput = {
	title: string;
	authorName: string;
	publisher: string;
	publishedAt: string;
	categoryId: number;
	pageTurnDirection: 'ltr' | 'rtl';
	description: string;
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
};

export async function fetchAdminCategories(): Promise<AdminCategory[]> {
	const response = await apiFetch<{ categories: AdminCategory[] }>('/api/v1/admin/categories');
	return response.categories;
}

export function createAdminBook(input: AdminBookCreateInput): Promise<AdminBook> {
	return apiFetch<AdminBook>('/api/v1/admin/books', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({
			title: input.title.trim(),
			authorName: input.authorName.trim() || null,
			publisher: input.publisher.trim() || null,
			publishedAt: input.publishedAt || null,
			categoryId: input.categoryId,
			pageTurnDirection: input.pageTurnDirection,
			description: input.description.trim() || null,
		}),
	});
}
