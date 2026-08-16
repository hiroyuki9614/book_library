import { afterEach, describe, expect, test, vi } from 'vitest';
import { fetchReadingInfo, saveReadingInfo } from './readingInfo';

describe('reading-info API client', () => {
	afterEach(() => {
		vi.unstubAllEnvs();
		vi.restoreAllMocks();
	});

	test('保存済みの現在ページを取得する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
			new Response(JSON.stringify({ bookId: 7, currentPage: 3, readStatus: 'reading' }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' },
			}),
		);

		await expect(fetchReadingInfo(7)).resolves.toEqual({ bookId: 7, currentPage: 3, readStatus: 'reading' });
		expect(fetch).toHaveBeenCalledWith('http://localhost:3000/api/v1/books/7/reading-info', expect.objectContaining({ credentials: 'include' }));
	});

	test('現在ページをPATCHで保存する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
			new Response(JSON.stringify({ bookId: 7, currentPage: 4, readStatus: 'reading' }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' },
			}),
		);

		await expect(saveReadingInfo(7, 4)).resolves.toEqual({ bookId: 7, currentPage: 4, readStatus: 'reading' });
		expect(fetch).toHaveBeenCalledWith(
			'http://localhost:3000/api/v1/books/7/reading-info',
			expect.objectContaining({
				method: 'PATCH',
				credentials: 'include',
				body: JSON.stringify({ currentPage: 4 }),
			}),
		);
	});
});
