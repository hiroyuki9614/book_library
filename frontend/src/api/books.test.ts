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
					limit: 20,
					books: [
						{
							id: 1,
							title: 'Phase 3 PDF',
							authorName: 'BeLib',
							category: { id: 1, name: '技術書' },
							readStatus: 'reading',
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
			{ id: 1, title: 'Phase 3 PDF', author: 'BeLib', status: 'Reading', fileType: 'pdf' },
		]);
		expect(fetch).toHaveBeenCalledWith('http://localhost:3000/api/v1/books', expect.objectContaining({ credentials: 'include' }));
	});

	test('PDF file APIをcookie付きで呼び出しBlobを返す', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000/');
		const pdf = new Blob(['%PDF-1.7'], { type: 'application/pdf' });
		vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(pdf, { status: 200 }));

		await expect(fetchBookFile(7)).resolves.toBeInstanceOf(Blob);
		expect(fetch).toHaveBeenCalledWith('http://localhost:3000/api/v1/books/7/file', { credentials: 'include' });
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
