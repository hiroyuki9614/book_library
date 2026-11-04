import { afterEach, describe, expect, test, vi } from 'vitest';
import { fetchBookFile, fetchBooks } from './books';

describe('books API client', () => {
	afterEach(() => {
		vi.unstubAllEnvs();
		vi.restoreAllMocks();
	});

	test('一覧APIのレスポンスを画面用の書籍へ変換する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
			new Response(
				JSON.stringify({
					total: 1,
					page: 1,
					limit: 100,
					books: [
						{
							id: 1,
							title: 'Phase 3 PDF',
							authorName: 'BeLib',
							category: { id: 1, name: '技術書' },
							readStatus: 'reading',
							progressPercentage: 40,
							hasFile: true,
							fileType: 'pdf',
							createdAt: '2026-08-01T00:00:00.000Z',
							updatedAt: '2026-08-01T00:00:00.000Z',
						},
					],
				}),
				{ headers: { 'Content-Type': 'application/json' } },
			),
		);

		await expect(fetchBooks()).resolves.toMatchObject([
			{ id: 1, title: 'Phase 3 PDF', author: 'BeLib', status: 'Reading', fileType: 'pdf', progressPercentage: 40 },
		]);
		expect(fetch).toHaveBeenCalledWith('http://localhost:3000/api/v1/books?page=1&limit=100', expect.objectContaining({ credentials: 'include' }));
	});

	test('一覧APIを100件ずつページングして101冊目以降も画面へ渡す', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const makeBook = (id: number) => ({
			id,
			title: `Book ${id}`,
			authorName: 'BeLib',
			category: { id: 1, name: '技術書' },
			readStatus: 'reading',
			progressPercentage: id === 101 ? 100 : null,
			hasFile: false,
			fileType: null,
			createdAt: '2026-08-01T00:00:00.000Z',
			updatedAt: '2026-08-01T00:00:00.000Z',
		});
		vi.spyOn(globalThis, 'fetch')
			.mockResolvedValueOnce(new Response(JSON.stringify({ total: 101, page: 1, limit: 100, books: Array.from({ length: 100 }, (_, index) => makeBook(index + 1)) }), {
				headers: { 'Content-Type': 'application/json' },
			}))
			.mockResolvedValueOnce(new Response(JSON.stringify({ total: 101, page: 2, limit: 100, books: [makeBook(101)] }), {
				headers: { 'Content-Type': 'application/json' },
			}));

		const books = await fetchBooks();

		expect(books).toHaveLength(101);
		expect(books.at(-1)).toMatchObject({ id: 101, progressPercentage: 100 });
		expect(fetch).toHaveBeenNthCalledWith(2, 'http://localhost:3000/api/v1/books?page=2&limit=100', expect.objectContaining({ credentials: 'include' }));
	});

	test('PDF file APIをcookie付きで呼び出しBlobを返す', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000/');
		const pdf = new Blob(['%PDF-1.7'], { type: 'application/pdf' });
		vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(pdf, { status: 200 }));

		await expect(fetchBookFile(7)).resolves.toBeInstanceOf(Blob);
		expect(fetch).toHaveBeenCalledWith('http://localhost:3000/api/v1/books/7/file', { credentials: 'include' });
	});

	test('R2の署名URLは認証APIから取得し、R2 GETではcredentialsを送らない', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.test');
		const pdf = new Blob(['%PDF-1.7'], { type: 'application/pdf' });
		vi.spyOn(globalThis, 'fetch')
			.mockResolvedValueOnce(new Response(JSON.stringify({
				kind: 'signed-url',
				url: 'https://signed.example.test/books/file.pdf?X-Amz-Signature=redacted',
				expiresAt: '2026-09-12T07:00:00.000Z',
			}), { status: 200, headers: { 'Content-Type': 'application/json' } }))
			.mockResolvedValueOnce(new Response(pdf, { status: 200 }));

		await expect(fetchBookFile(7)).resolves.toBeInstanceOf(Blob);
		expect(fetch).toHaveBeenNthCalledWith(1, 'https://api.example.test/api/v1/books/7/file', { credentials: 'include' });
		expect(fetch).toHaveBeenNthCalledWith(2, 'https://signed.example.test/books/file.pdf?X-Amz-Signature=redacted', { credentials: 'omit' });
	});

	test('PDF file APIがJSON等を返した場合はReaderへ渡さない', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
			new Response(JSON.stringify({ message: 'Forbidden' }), {
				status: 403,
				headers: { 'Content-Type': 'application/json' },
			}),
		);

		await expect(fetchBookFile(7)).rejects.toThrow('API request failed: 403');
	});
});
