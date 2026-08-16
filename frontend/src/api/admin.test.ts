import { afterEach, describe, expect, test, vi } from 'vitest';
import { createAdminBook, fetchAdminCategories } from './admin';

describe('admin API client', () => {
	afterEach(() => {
		vi.unstubAllEnvs();
		vi.restoreAllMocks();
	});

	test('activeカテゴリをbackendから取得する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
			new Response(JSON.stringify({ categories: [{ id: 17, name: '技術書' }] }), { status: 200 }),
		);

		await expect(fetchAdminCategories()).resolves.toEqual([{ id: 17, name: '技術書' }]);
		expect(fetch).toHaveBeenCalledWith('http://localhost:3000/api/v1/admin/categories', expect.objectContaining({ credentials: 'include' }));
	});

	test('書籍登録は選択されたreal categoryIdとmetadataをPOSTする', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
			new Response(
				JSON.stringify({
					id: 21,
					title: 'Book',
					authorName: 'Author',
					publisher: 'Publisher',
					publishedAt: '2026-08-16T00:00:00.000Z',
					categoryId: 17,
					pageTurnDirection: 'rtl',
					description: 'Description',
					category: { id: 17, name: '技術書' },
				}),
				{ status: 201 },
			),
		);

		await expect(createAdminBook({
			title: ' Book ',
			authorName: ' Author ',
			publisher: ' Publisher ',
			publishedAt: '2026-08-16',
			categoryId: 17,
			pageTurnDirection: 'rtl',
			description: ' Description ',
		})).resolves.toMatchObject({ id: 21, categoryId: 17 });

		expect(fetch).toHaveBeenCalledWith('http://localhost:3000/api/v1/admin/books', expect.objectContaining({
			method: 'POST',
			credentials: 'include',
			body: JSON.stringify({
				title: 'Book',
				authorName: 'Author',
				publisher: 'Publisher',
				publishedAt: '2026-08-16',
				categoryId: 17,
				pageTurnDirection: 'rtl',
				description: 'Description',
			}),
		}));
	});
});
