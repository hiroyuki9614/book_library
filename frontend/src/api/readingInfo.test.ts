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
			new Response(JSON.stringify({ bookId: 7, currentPosition: '3', currentPage: 3, progressPercentage: 30, readStatus: 'reading' }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' },
			}),
		);

		await expect(fetchReadingInfo(7, 7)).resolves.toEqual({ bookId: 7, currentPosition: '3', currentPage: 3, progressPercentage: 30, readStatus: 'reading' });
		expect(fetch).toHaveBeenCalledWith(
			'http://localhost:3000/api/v1/books/7/reading-info',
			expect.objectContaining({
				credentials: 'include',
				headers: { 'X-BeLib-User-Id': '7' },
			}),
		);
	});

	test('PDF現在ページとcompleted状態をPATCHで保存する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
			new Response(JSON.stringify({ bookId: 7, currentPosition: '4', currentPage: 4, progressPercentage: 100, readStatus: 'completed' }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' },
			}),
		);

		await expect(saveReadingInfo(7, 4, 'completed', undefined, 7)).resolves.toEqual({
			bookId: 7,
			currentPosition: '4',
			currentPage: 4,
			progressPercentage: 100,
			readStatus: 'completed',
		});
		expect(fetch).toHaveBeenCalledWith(
			'http://localhost:3000/api/v1/books/7/reading-info',
			expect.objectContaining({
				method: 'PATCH',
				credentials: 'include',
				headers: {
					'Content-Type': 'application/json',
					'X-BeLib-User-Id': '7',
				},
				body: JSON.stringify({ currentPage: 4, readStatus: 'completed' }),
			}),
		);
	});

	test('EPUB CFIをcurrentPositionとして保存する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const cfi = 'epubcfi(/6/2!/4/1:0)';
		vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
			new Response(JSON.stringify({ bookId: 7, currentPosition: cfi, currentPage: 1, progressPercentage: 30, readStatus: 'reading' }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' },
			}),
		);

		await saveReadingInfo(7, cfi, 'reading', 30, 7);
		expect(fetch).toHaveBeenCalledWith(
			'http://localhost:3000/api/v1/books/7/reading-info',
			expect.objectContaining({
				headers: {
					'Content-Type': 'application/json',
					'X-BeLib-User-Id': '7',
				},
				body: JSON.stringify({ currentPosition: cfi, readStatus: 'reading', progressPercentage: 30 }),
			}),
		);
	});

	test('standalone readerはユーザーヒントなしでGET/PATCHを実際に送信する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		vi.spyOn(globalThis, 'fetch')
			.mockResolvedValueOnce(new Response(JSON.stringify({ bookId: 7, currentPosition: null, currentPage: 1, progressPercentage: 0, readStatus: 'unread' }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' },
			}))
			.mockResolvedValueOnce(new Response(JSON.stringify({ bookId: 7, currentPosition: '4', currentPage: 4, progressPercentage: 40, readStatus: 'reading' }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' },
			}));

		await expect(fetchReadingInfo(7)).resolves.toMatchObject({ currentPage: 1, readStatus: 'unread' });
		await expect(saveReadingInfo(7, 4)).resolves.toMatchObject({ currentPage: 4, readStatus: 'reading' });
		expect(fetch).toHaveBeenNthCalledWith(1, 'http://localhost:3000/api/v1/books/7/reading-info', expect.objectContaining({ headers: {} }));
		expect(fetch).toHaveBeenNthCalledWith(2, 'http://localhost:3000/api/v1/books/7/reading-info', expect.objectContaining({
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ currentPage: 4, readStatus: 'reading' }),
		}));
	});

	test('ユーザーごとに同じ書籍の保存キューを分離する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		let release!: () => void;
		const firstFinished = new Promise<void>((resolve) => { release = resolve; });
		const calls: string[] = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			const body = JSON.parse(String(options?.body)) as { currentPage: number };
			calls.push(`PATCH ${body.currentPage}`);
			if (body.currentPage === 2) await firstFinished;
			return new Response(JSON.stringify({ bookId: 99, currentPosition: String(body.currentPage), currentPage: body.currentPage, progressPercentage: body.currentPage === 2 ? 20 : 80, readStatus: 'reading' }), {
				headers: { 'Content-Type': 'application/json' },
			});
		});

		const firstUserSave = saveReadingInfo(99, 2, 'reading', 20, 7);
		const secondUserSave = saveReadingInfo(99, 9, 'reading', 80, 8);
		await vi.waitFor(() => expect(calls).toEqual(['PATCH 2', 'PATCH 9']));
		release();
		await expect(Promise.all([firstUserSave, secondUserSave])).resolves.toHaveLength(2);
	});

	test('保留中の旧アカウント保存は新しいセッションへ書き込まれない', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		let activeSessionUserId = 7;
		let release!: () => void;
		const firstFinished = new Promise<void>((resolve) => { release = resolve; });
		const requests: Array<{ page: number; expectedUserId: string | null; activeUserId: number }> = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			const body = JSON.parse(String(options?.body)) as { currentPage: number };
			const headers = new Headers(options?.headers);
			const requestSessionUserId = activeSessionUserId;
			requests.push({ page: body.currentPage, expectedUserId: headers.get('X-BeLib-User-Id'), activeUserId: requestSessionUserId });
			if (body.currentPage === 2) await firstFinished;
			if (headers.get('X-BeLib-User-Id') !== String(requestSessionUserId)) {
				return new Response(JSON.stringify({ code: 'READING_SESSION_MISMATCH' }), { status: 409 });
			}
			return new Response(JSON.stringify({ bookId: 99, currentPosition: String(body.currentPage), currentPage: body.currentPage, progressPercentage: 20, readStatus: 'reading' }), {
				headers: { 'Content-Type': 'application/json' },
			});
		});

		const first = saveReadingInfo(99, 2, 'reading', 20, 7);
		const stale = saveReadingInfo(99, 9, 'reading', 80, 7);
		await vi.waitFor(() => expect(requests).toHaveLength(1));
		activeSessionUserId = 8;
		release();

		await expect(first).resolves.toMatchObject({ currentPage: 2 });
		await expect(stale).rejects.toThrow('API request failed: 409');
		expect(requests).toEqual([
			{ page: 2, expectedUserId: '7', activeUserId: 7 },
			{ page: 9, expectedUserId: '7', activeUserId: 8 },
		]);
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
		const older = saveReadingInfo(99, 2, 'reading', undefined, 7);
		const newer = saveReadingInfo(99, 9, 'reading', undefined, 7);
		const reopened = fetchReadingInfo(99, 7);
		await vi.waitFor(() => expect(calls).toEqual(['PATCH 2']));
		release();
		await Promise.all([older, newer]);
		await expect(reopened).resolves.toMatchObject({ currentPosition: '9' });
		expect(calls).toEqual(['PATCH 2', 'PATCH 9', 'GET 9']);
	});
});
