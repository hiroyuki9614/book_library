import { afterEach, describe, expect, test, vi } from 'vitest';
import { fetchReadingInfo, saveReadingInfo } from './readingInfo';

describe('reading-info API client', () => {
	afterEach(() => {
		vi.unstubAllEnvs();
		vi.restoreAllMocks();
	});

	test('保存済みの読書位置を取得する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
			new Response(JSON.stringify({ bookId: 7, currentPosition: '3', currentPage: 3, readStatus: 'reading' }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' },
			}),
		);

		await expect(fetchReadingInfo(7)).resolves.toEqual({ bookId: 7, currentPosition: '3', currentPage: 3, readStatus: 'reading' });
		expect(fetch).toHaveBeenCalledWith(
			'http://localhost:3000/api/v1/books/7/reading-info',
			expect.objectContaining({ credentials: 'include' }),
		);
	});

	test('PDF現在ページとcompleted状態をPATCHで保存する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
			new Response(JSON.stringify({ bookId: 7, currentPosition: '4', currentPage: 4, readStatus: 'completed' }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' },
			}),
		);

		await expect(saveReadingInfo(7, 4, 'completed')).resolves.toEqual({
			bookId: 7,
			currentPosition: '4',
			currentPage: 4,
			readStatus: 'completed',
		});
		expect(fetch).toHaveBeenCalledWith(
			'http://localhost:3000/api/v1/books/7/reading-info',
			expect.objectContaining({
				method: 'PATCH',
				credentials: 'include',
				body: JSON.stringify({ currentPage: 4, readStatus: 'completed' }),
			}),
		);
	});

	test('EPUB CFIをcurrentPositionとして保存する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const cfi = 'epubcfi(/6/2!/4/1:0)';
		vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
			new Response(JSON.stringify({ bookId: 7, currentPosition: cfi, currentPage: 1, readStatus: 'reading' }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' },
			}),
		);

		await saveReadingInfo(7, cfi);
		expect(fetch).toHaveBeenCalledWith(
			'http://localhost:3000/api/v1/books/7/reading-info',
			expect.objectContaining({
				body: JSON.stringify({ currentPosition: cfi, readStatus: 'reading' }),
			}),
		);
	});
	test('遅い古いPATCHを追い越さず、再入場のGETも最新保存を待つ', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		let release!: () => void;
		const firstFinished = new Promise<void>((resolve) => {
			release = resolve;
		});
		const calls: string[] = [];
		let position = '1';
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method === 'PATCH') {
				const page = String(JSON.parse(String(options.body)).currentPage);
				calls.push(`PATCH ${page}`);
				if (page === '2') await firstFinished;
				position = page;
			} else calls.push(`GET ${position}`);
			return new Response(JSON.stringify({ bookId: 99, currentPosition: position, currentPage: Number(position), readStatus: 'reading' }), {
				headers: { 'Content-Type': 'application/json' },
			});
		});
		const older = saveReadingInfo(99, 2);
		const newer = saveReadingInfo(99, 9);
		const reopened = fetchReadingInfo(99);
		await vi.waitFor(() => expect(calls).toEqual(['PATCH 2']));
		release();
		await Promise.all([older, newer]);
		await expect(reopened).resolves.toMatchObject({ currentPosition: '9' });
		expect(calls).toEqual(['PATCH 2', 'PATCH 9', 'GET 9']);
	});
});
