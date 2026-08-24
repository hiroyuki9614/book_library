import { afterEach, describe, expect, test, vi } from 'vitest';
import {
	createAdminCategory,
	createAdminUser,
	deleteAdminCategory,
	disableAdminUser,
	fetchAdminBooks,
	fetchAdminCategories,
	fetchAdminUsers,
	registerAdminBook,
	renameAdminCategory,
	resetAdminUserPassword,
	restoreAdminBook,
	restoreAdminUser,
	softDeleteAdminBook,
} from './admin';

describe('admin API client', () => {
	afterEach(() => {
		vi.unstubAllEnvs();
		vi.restoreAllMocks();
	});

	test('activeカテゴリをbackendから取得する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
			new Response(JSON.stringify({ categories: [{ id: 1, name: '未分類', displayOrder: 0, isActive: true }] }), { status: 200 }),
		);

		await expect(fetchAdminCategories()).resolves.toEqual([{ id: 1, name: '未分類', displayOrder: 0, isActive: true }]);
		expect(fetch).toHaveBeenCalledWith('http://localhost:3000/api/v1/admin/categories', expect.objectContaining({ credentials: 'include' }));
	});

	test('カテゴリ追加・名称変更・削除APIを呼び出す', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		vi.spyOn(globalThis, 'fetch')
			.mockResolvedValueOnce(new Response(JSON.stringify({ id: 8, name: '科学', displayOrder: 6, isActive: true }), { status: 201 }))
			.mockResolvedValueOnce(new Response(JSON.stringify({ id: 8, name: '自然科学', displayOrder: 6, isActive: true }), { status: 200 }))
			.mockResolvedValueOnce(new Response(JSON.stringify({ movedBookCount: 2, category: { id: 8, name: '自然科学', isActive: false } }), { status: 200 }));

		await expect(createAdminCategory('科学')).resolves.toMatchObject({ id: 8, name: '科学' });
		await expect(renameAdminCategory(8, '自然科学')).resolves.toMatchObject({ id: 8, name: '自然科学' });
		await expect(deleteAdminCategory(8)).resolves.toEqual({ movedBookCount: 2, category: { id: 8, name: '自然科学', isActive: false } });

		expect(fetch).toHaveBeenNthCalledWith(1, 'http://localhost:3000/api/v1/admin/categories', expect.objectContaining({
			method: 'POST', credentials: 'include', body: JSON.stringify({ name: '科学' }),
		}));
		expect(fetch).toHaveBeenNthCalledWith(2, 'http://localhost:3000/api/v1/admin/categories/8', expect.objectContaining({
			method: 'PATCH', credentials: 'include', body: JSON.stringify({ name: '自然科学' }),
		}));
		expect(fetch).toHaveBeenNthCalledWith(3, 'http://localhost:3000/api/v1/admin/categories/8/delete', expect.objectContaining({
			method: 'PATCH', credentials: 'include',
		}));
	});

	test('一般ユーザー一覧を取得する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({ users: [
			{ id: 7, email: 'reader@example.com', name: 'Reader', deletedAt: null, createdAt: '2026-08-01T00:00:00.000Z' },
		] }), { status: 200 }));

		await expect(fetchAdminUsers()).resolves.toEqual([
			expect.objectContaining({ id: 7, email: 'reader@example.com', deletedAt: null }),
		]);
		expect(fetch).toHaveBeenCalledWith('http://localhost:3000/api/v1/admin/users', expect.objectContaining({ credentials: 'include' }));
	});

	test('一般ユーザー登録・停止・再開・仮パスワード再設定APIを呼ぶ', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		vi.spyOn(globalThis, 'fetch')
			.mockResolvedValueOnce(new Response(JSON.stringify({ id: 9, email: 'new@example.com', name: 'New User', deletedAt: null, createdAt: '2026-08-24T00:00:00.000Z' }), { status: 201 }))
			.mockResolvedValueOnce(new Response(JSON.stringify({ id: 9, email: 'new@example.com', name: 'New User', deletedAt: '2026-08-24T01:00:00.000Z' }), { status: 200 }))
			.mockResolvedValueOnce(new Response(JSON.stringify({ id: 9, email: 'new@example.com', name: 'New User', deletedAt: null }), { status: 200 }))
			.mockResolvedValueOnce(new Response(JSON.stringify({ id: 9, passwordReset: true }), { status: 200 }));

		await expect(createAdminUser({ email: ' new@example.com ', name: ' New User ', password: 'password123' })).resolves.toMatchObject({ id: 9 });
		await expect(disableAdminUser(9)).resolves.toMatchObject({ id: 9, deletedAt: expect.any(String) });
		await expect(restoreAdminUser(9)).resolves.toMatchObject({ id: 9, deletedAt: null });
		await expect(resetAdminUserPassword(9, 'temporary123')).resolves.toEqual({ id: 9, passwordReset: true });

		expect(fetch).toHaveBeenNthCalledWith(1, 'http://localhost:3000/api/v1/admin/users', expect.objectContaining({
			method: 'POST', credentials: 'include', body: JSON.stringify({ email: 'new@example.com', name: 'New User', password: 'password123' }),
		}));
		expect(fetch).toHaveBeenNthCalledWith(2, 'http://localhost:3000/api/v1/admin/users/9/disable', expect.objectContaining({ method: 'PATCH', credentials: 'include' }));
		expect(fetch).toHaveBeenNthCalledWith(3, 'http://localhost:3000/api/v1/admin/users/9/restore', expect.objectContaining({ method: 'PATCH', credentials: 'include' }));
		expect(fetch).toHaveBeenNthCalledWith(4, 'http://localhost:3000/api/v1/admin/users/9/password', expect.objectContaining({
			method: 'PATCH', credentials: 'include', body: JSON.stringify({ password: 'temporary123' }),
		}));
	});

	test('保存済み通常書籍一覧をbackendから取得する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
			new Response(JSON.stringify({
				books: [{
					id: 10,
					title: 'Persisted EPUB',
					authorName: null,
					publisher: null,
					publishedAt: null,
					categoryId: 17,
					pageTurnDirection: 'ltr',
					description: null,
					deletedAt: null,
					category: { id: 17, name: '技術書' },
					publicationScope: 'all_users',
					file: { id: 20, extension: 'epub', mimeType: 'application/epub+zip', originalFileName: 'book.epub', fileSize: 1024 },
				}],
			}), { status: 200 }),
		);

		await expect(fetchAdminBooks()).resolves.toEqual([
			expect.objectContaining({ id: 10, title: 'Persisted EPUB', deletedAt: null, publicationScope: 'all_users' }),
		]);
		expect(fetch).toHaveBeenCalledWith('http://localhost:3000/api/v1/admin/books?state=active', expect.objectContaining({ credentials: 'include' }));
	});

	test('削除済み一覧をstate=deletedで取得する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({ books: [] }), { status: 200 }));

		await expect(fetchAdminBooks('deleted')).resolves.toEqual([]);
		expect(fetch).toHaveBeenCalledWith('http://localhost:3000/api/v1/admin/books?state=deleted', expect.objectContaining({ credentials: 'include' }));
	});

	test('書籍をPATCHで論理削除・復元する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		vi.spyOn(globalThis, 'fetch')
			.mockResolvedValueOnce(new Response(JSON.stringify({ id: 10, deletedAt: '2026-08-24T10:00:00.000Z' }), { status: 200 }))
			.mockResolvedValueOnce(new Response(JSON.stringify({ id: 10, deletedAt: null }), { status: 200 }));

		await expect(softDeleteAdminBook(10)).resolves.toMatchObject({ id: 10, deletedAt: expect.any(String) });
		await expect(restoreAdminBook(10)).resolves.toEqual({ id: 10, deletedAt: null });
		expect(fetch).toHaveBeenNthCalledWith(1, 'http://localhost:3000/api/v1/admin/books/10/delete', expect.objectContaining({ method: 'PATCH', credentials: 'include' }));
		expect(fetch).toHaveBeenNthCalledWith(2, 'http://localhost:3000/api/v1/admin/books/10/restore', expect.objectContaining({ method: 'PATCH', credentials: 'include' }));
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
