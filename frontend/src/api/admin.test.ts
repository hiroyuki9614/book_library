import { afterEach, describe, expect, test, vi } from 'vitest';
import { fetchAdminCategories, registerAdminBook } from './admin';

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

	test('EPUBとmetadataと公開範囲をmultipartでfull registration APIへ送る', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const file = new File(['epub'], 'book.epub', { type: 'application/epub+zip' });
		vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
			new Response(
				JSON.stringify({
					id: 21,
					title: 'Book',
					authorName: 'Author',
					publisher: 'Publisher',
					publishedAt: '2026-08-23T00:00:00.000Z',
					categoryId: 17,
					pageTurnDirection: 'rtl',
					description: 'Description',
					category: { id: 17, name: '技術書' },
					publicationScope: 'admin_only',
					file: { id: 31, extension: 'epub', mimeType: 'application/epub+zip', originalFileName: 'book.epub', fileSize: 4 },
				}),
				{ status: 201 },
			),
		);

		await expect(registerAdminBook({
			title: ' Book ',
			authorName: ' Author ',
			publisher: ' Publisher ',
			publishedAt: '2026-08-23',
			categoryId: 17,
			pageTurnDirection: 'rtl',
			description: ' Description ',
			publicationScope: 'admin_only',
			file,
		})).resolves.toMatchObject({ id: 21, publicationScope: 'admin_only', file: { extension: 'epub' } });

		expect(fetch).toHaveBeenCalledTimes(1);
		const [url, init] = vi.mocked(fetch).mock.calls[0];
		expect(url).toBe('http://localhost:3000/api/v1/admin/book-registrations');
		expect(init).toMatchObject({ method: 'POST', credentials: 'include' });
		const body = init?.body as FormData;
		expect(body).toBeInstanceOf(FormData);
		expect(body.get('title')).toBe('Book');
		expect(body.get('categoryId')).toBe('17');
		expect(body.get('publicationScope')).toBe('admin_only');
		expect(body.get('file')).toBeInstanceOf(File);
		expect((body.get('file') as File).name).toBe('book.epub');
	});
});
