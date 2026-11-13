import { afterEach, describe, expect, test, vi } from 'vitest';
import {
	fetchReadingInfo,
	flushReadingSaves,
	invalidateReadingSaves,
	reserveReadingSave,
	registerReadingFlush,
	saveReadingInfo,
} from './readingInfo';

const READING_SAVE_TEST_DB_NAME = 'belib-reading-saves';

function openReadingSaveTestDatabase() {
	return new Promise<IDBDatabase>((resolve, reject) => {
		const request = indexedDB.open(READING_SAVE_TEST_DB_NAME);
		request.onsuccess = () => resolve(request.result);
		request.onerror = () => reject(request.error ?? new Error('Could not open reading save test database'));
	});
}

function waitForTransaction(transaction: IDBTransaction) {
	return new Promise<void>((resolve, reject) => {
		transaction.oncomplete = () => resolve();
		transaction.onerror = () => reject(transaction.error ?? new Error('Reading save test transaction failed'));
		transaction.onabort = () => reject(transaction.error ?? new Error('Reading save test transaction aborted'));
	});
}

async function readReadingSaveRecord(storeName: string, key: IDBValidKey) {
	const database = await openReadingSaveTestDatabase();
	try {
		const transaction = database.transaction(storeName, 'readonly');
		const completed = waitForTransaction(transaction);
		let result: unknown;
		const request = transaction.objectStore(storeName).get(key);
		request.onsuccess = () => { result = request.result; };
		await completed;
		return result;
	} finally {
		database.close();
	}
}

async function writeReadingSaveRecord(storeName: string, value: unknown, key?: IDBValidKey) {
	const database = await openReadingSaveTestDatabase();
	try {
		const transaction = database.transaction(storeName, 'readwrite');
		const completed = waitForTransaction(transaction);
		if (key === undefined) transaction.objectStore(storeName).put(value);
		else transaction.objectStore(storeName).put(value, key);
		await completed;
	} finally {
		database.close();
	}
}

async function clearReadingSaveTestDatabase() {
	const database = await openReadingSaveTestDatabase();
	try {
		const storeNames = Array.from(database.objectStoreNames);
		const transaction = database.transaction(storeNames, 'readwrite');
		const completed = waitForTransaction(transaction);
		for (const storeName of storeNames) transaction.objectStore(storeName).clear();
		await completed;
	} finally {
		database.close();
	}
}

describe('reading-info API client', () => {
	afterEach(async () => {
		await invalidateReadingSaves();
		await clearReadingSaveTestDatabase();
		vi.unstubAllEnvs();
		vi.restoreAllMocks();
	});

	function expectVersionedRequestBody(expected: Record<string, unknown>) {
		const request = vi.mocked(fetch).mock.calls[0]?.[1];
		const body = JSON.parse(String(request?.body)) as Record<string, unknown>;
		expect(body).toEqual({ ...expected, saveSequence: expect.any(String) });
		expect(BigInt(String(body.saveSequence))).toBeGreaterThan(0n);
	}

	test('一時的なIndexedDB open失敗後に再試行し、sequenceをユーザーと書籍ごとに保つ', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const bookId = 9401;
		const userId = 9402;
		const openError = new DOMException('Temporary IndexedDB failure', 'UnknownError');
		const failedRequest = {
			error: openError,
			set onerror(handler: IDBOpenDBRequest['onerror'] | null) {
				queueMicrotask(() => handler?.call(this as unknown as IDBOpenDBRequest, new Event('error')));
			},
		} as IDBOpenDBRequest;
		const openSpy = vi.spyOn(indexedDB, 'open').mockReturnValueOnce(failedRequest);
		const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
			new Response(JSON.stringify({ bookId, currentPosition: '2', currentPage: 2, progressPercentage: 20, readStatus: 'reading' }), {
				headers: { 'Content-Type': 'application/json' },
			}),
		);

		await expect(saveReadingInfo(bookId, 2, 'reading', 20, userId)).rejects.toThrow('Temporary IndexedDB failure');
		expect(fetchMock).not.toHaveBeenCalled();

		openSpy.mockRestore();
		await expect(saveReadingInfo(bookId, 2, 'reading', 20, userId)).resolves.toMatchObject({ currentPage: 2 });
		expect(fetchMock).toHaveBeenCalledTimes(1);
		expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toMatchObject({ saveSequence: '1' });
		await expect(reserveReadingSave(bookId, 3, 'reading', 30, userId)).resolves.toBe('2');
		await expect(reserveReadingSave(bookId, 3, 'reading', 30, userId + 1)).resolves.toBe('1');
		await expect(reserveReadingSave(bookId + 1, 3, 'reading', 30, userId)).resolves.toBe('1');
	});

	test('IndexedDBが利用できない認証済み保存はPATCHせずに失敗する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const fetchMock = vi.spyOn(globalThis, 'fetch');
		vi.stubGlobal('indexedDB', undefined);

		try {
			await expect(saveReadingInfo(9403, 2, 'reading', 20, 9404)).rejects.toThrow('IndexedDB is required for durable reading saves');
			expect(fetchMock).not.toHaveBeenCalled();
		} finally {
			vi.unstubAllGlobals();
		}
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
			}),
		);
		expectVersionedRequestBody({ currentPage: 4, readStatus: 'completed' });
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
			}),
		);
		expectVersionedRequestBody({ currentPosition: cfi, readStatus: 'reading', progressPercentage: 30 });
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

	test('保存sequenceは書籍ごとに独立して増える', async () => {
		const firstBook = 9301;
		const secondBook = 9302;

		await expect(reserveReadingSave(firstBook, 1, 'reading', 10, 901)).resolves.toBe('1');
		await expect(reserveReadingSave(secondBook, 1, 'reading', 10, 901)).resolves.toBe('1');
		await expect(reserveReadingSave(firstBook, 2, 'reading', 20, 901)).resolves.toBe('2');
	});

	test('旧ブラウザ全体のsequence履歴が100万を超えても、新しい書籍は1から始まる', async () => {
		const database = await openReadingSaveTestDatabase();
		try {
			const transaction = database.transaction('sequence', 'readwrite');
			const completed = waitForTransaction(transaction);
			transaction.objectStore('sequence').add({ createdAt: Date.now() }, 1_000_001);
			transaction.objectStore('sequence').delete(1_000_001);
			await completed;
		} finally {
			database.close();
		}

		await expect(reserveReadingSave(9303, 1, 'reading', 10, 901)).resolves.toBe('1');
		await expect(reserveReadingSave(9304, 1, 'reading', 10, 901)).resolves.toBe('1');
	});

	test('同じ書籍への同時予約は順番を保ち、outboxには最新保存を残す', async () => {
		const reservations = await Promise.all([
			reserveReadingSave(9305, 2, 'reading', 20, 901),
			reserveReadingSave(9305, 9, 'reading', 90, 901),
		]);

		expect(reservations).toEqual(['1', '2']);
		await expect(readReadingSaveRecord('outbox', 'user:901:book:9305')).resolves.toMatchObject({
			position: 9,
			saveSequence: '2',
			sequenceVersion: 2,
		});
	});

	test('アカウント切替後もsequenceは各ユーザーと書籍の組み合わせで分離する', async () => {
		await expect(reserveReadingSave(9306, 2, 'reading', 20, 7)).resolves.toBe('1');
		await invalidateReadingSaves();

		await expect(reserveReadingSave(9306, 8, 'reading', 80, 8)).resolves.toBe('1');
		await expect(reserveReadingSave(9306, 3, 'reading', 30, 7)).resolves.toBe('2');
	});

	test('永続化したoutbox保存は次の読書情報取得時に再送される', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const sequence = await reserveReadingSave(9307, 6, 'reading', 60, 902);
		await expect(readReadingSaveRecord('outbox', 'user:902:book:9307')).resolves.toMatchObject({
			position: 6,
			saveSequence: sequence,
		});

		const calls: string[] = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method === 'PATCH') {
				calls.push('PATCH');
				return new Response(JSON.stringify({ bookId: 9307, currentPosition: '6', currentPage: 6, progressPercentage: 60, readStatus: 'reading', saveSequence: sequence }), {
					headers: { 'Content-Type': 'application/json' },
				});
			}
			calls.push('GET');
			return new Response(JSON.stringify({ bookId: 9307, currentPosition: '6', currentPage: 6, progressPercentage: 60, readStatus: 'reading', saveSequence: sequence }), {
				headers: { 'Content-Type': 'application/json' },
			});
		});

		await expect(fetchReadingInfo(9307, 902)).resolves.toMatchObject({ currentPage: 6 });
		expect(calls).toEqual(['PATCH', 'GET']);
		await expect(readReadingSaveRecord('outbox', 'user:902:book:9307')).resolves.toBeUndefined();
	});

	test('明示的な認証無効化はoutboxを保ち、同じユーザーだけが再送する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const bookId = 99;
		const oldUserId = 7;
		const newUserId = 8;
		await reserveReadingSave(bookId, 7, 'reading', 70, oldUserId);

		await expect(invalidateReadingSaves()).resolves.toBeUndefined();
		await expect(readReadingSaveRecord('outbox', `user:${oldUserId}:book:${bookId}`)).resolves.toMatchObject({
			position: 7,
			userId: oldUserId,
		});

		const patchUserIds: string[] = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method === 'PATCH') {
				patchUserIds.push(new Headers(options.headers).get('X-BeLib-User-Id') ?? '');
			}
			return new Response(JSON.stringify({ bookId, currentPosition: '1', currentPage: 1, progressPercentage: 0, readStatus: 'unread' }), {
				headers: { 'Content-Type': 'application/json' },
			});
		});

		await expect(fetchReadingInfo(bookId, newUserId)).resolves.toMatchObject({ currentPage: 1 });
		expect(patchUserIds).toEqual([]);
		await expect(readReadingSaveRecord('outbox', `user:${oldUserId}:book:${bookId}`)).resolves.toMatchObject({ position: 7 });

		await expect(fetchReadingInfo(bookId, oldUserId)).resolves.toMatchObject({ currentPage: 1 });
		expect(patchUserIds).toEqual([String(oldUserId)]);
		await expect(readReadingSaveRecord('outbox', `user:${oldUserId}:book:${bookId}`)).resolves.toBeUndefined();
	});

	test('認証無効化で中断されたPATCHは最新のdurable outboxを破棄しない', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		let markPatchStarted!: () => void;
		const patchStarted = new Promise<void>((resolve) => { markPatchStarted = resolve; });
		let patchSignal: AbortSignal | undefined;
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			patchSignal = options?.signal ?? undefined;
			markPatchStarted();
			return new Promise<Response>((_resolve, reject) => {
				const abort = () => reject(new DOMException('Request aborted', 'AbortError'));
				if (patchSignal?.aborted) abort();
				else patchSignal?.addEventListener('abort', abort, { once: true });
			});
		});

		const save = saveReadingInfo(99, 2, 'reading', 20, 7);
		await patchStarted;
		await reserveReadingSave(99, 4, 'reading', 40, 7);
		await invalidateReadingSaves();
		await expect(save).rejects.toThrow();
		expect(patchSignal?.aborted).toBe(true);
		await expect(readReadingSaveRecord('outbox', 'user:7:book:99')).resolves.toMatchObject({
			position: 4,
			userId: 7,
		});
	});

	test('logout flush失敗後もcurrent accountのoutboxを残す', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ message: 'temporary failure' }), { status: 503 }));
		const unregister = registerReadingFlush(99, () => {
			void saveReadingInfo(99, 8, 'reading', 80, 7).catch(() => undefined);
		}, 7);
		try {
			await expect(flushReadingSaves(7)).resolves.toBe(false);
			await expect(readReadingSaveRecord('outbox', 'user:7:book:99')).resolves.toMatchObject({ position: 8, userId: 7 });
		} finally {
			unregister();
		}
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

	test('新しいブラウザのローカルsequenceが古いとき、409後にサーバー版を読み直して最新位置を再送する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const patchBodies: Array<Record<string, unknown>> = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method === 'PATCH') {
				const body = JSON.parse(String(options.body)) as Record<string, unknown>;
				patchBodies.push(body);
				if (patchBodies.length === 1) {
					return new Response(JSON.stringify({ code: 'STALE_READING_SAVE' }), { status: 409 });
				}
				return new Response(JSON.stringify({
					bookId: 99,
					currentPosition: '9',
					currentPage: 9,
					progressPercentage: 80,
					readStatus: 'reading',
					saveSequence: '51',
				}), { headers: { 'Content-Type': 'application/json' } });
			}
			return new Response(JSON.stringify({
				bookId: 99,
				currentPosition: '50',
				currentPage: 50,
				progressPercentage: 50,
				readStatus: 'reading',
				saveSequence: '50',
			}), { headers: { 'Content-Type': 'application/json' } });
		});

		await expect(saveReadingInfo(99, 9, 'reading', 80, 7)).resolves.toMatchObject({ currentPage: 9 });

		expect(patchBodies).toHaveLength(2);
		expect(typeof patchBodies[0].saveSequence).toBe('string');
		expect(BigInt(String(patchBodies[0].saveSequence))).toBeLessThan(50n);
		expect(patchBodies[1]).toMatchObject({ currentPage: 9, readStatus: 'reading', progressPercentage: 80, saveSequence: '51' });
	});

	test('409 reconciliation後の再送失敗は更新済みsequenceとoutboxを保持する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const bookId = 9309;
		const userId = 904;
		const originalSequence = await reserveReadingSave(bookId, 9, 'reading', 80, userId);
		const patchSequences: string[] = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method === 'PATCH') {
				const body = JSON.parse(String(options.body)) as { saveSequence: string };
				patchSequences.push(body.saveSequence);
				if (patchSequences.length === 1) {
					return new Response(JSON.stringify({ code: 'STALE_READING_SAVE' }), { status: 409 });
				}
				return new Response(JSON.stringify({ message: 'temporary failure' }), { status: 503 });
			}
			return new Response(JSON.stringify({
				bookId,
				currentPosition: '50',
				currentPage: 50,
				progressPercentage: 50,
				readStatus: 'reading',
				saveSequence: '50',
			}), { headers: { 'Content-Type': 'application/json' } });
		});

		await expect(saveReadingInfo(bookId, 9, 'reading', 80, userId, undefined, false, originalSequence))
			.rejects.toMatchObject({ status: 503, saveSequence: '51' });

		expect(patchSequences).toEqual(['1', '51']);
		await expect(readReadingSaveRecord('outbox', `user:${userId}:book:${bookId}`)).resolves.toMatchObject({
			position: 9,
			saveSequence: '51',
		});
	});

	test('legacy outboxの100万超sequenceは409 rebase後に更新し、後続予約もserverより進む', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const bookId = 9308;
		const userId = 903;
		const scopeKey = `user:${userId}:book:${bookId}`;
		await writeReadingSaveRecord('outbox', {
			scopeKey,
			bookId,
			userId,
			position: 8,
			readStatus: 'reading',
			progressPercentage: 80,
			hasProgressPercentage: true,
			saveSequence: '1000001',
		});

		const patchSequences: string[] = [];
		let getCount = 0;
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method === 'PATCH') {
				const body = JSON.parse(String(options.body)) as { saveSequence: string };
				patchSequences.push(body.saveSequence);
				if (patchSequences.length === 1) return new Response(JSON.stringify({ code: 'STALE_READING_SAVE' }), { status: 409 });
				return new Response(JSON.stringify({ bookId, currentPosition: '8', currentPage: 8, progressPercentage: 80, readStatus: 'reading', saveSequence: body.saveSequence }), {
					headers: { 'Content-Type': 'application/json' },
				});
			}
			getCount += 1;
			const saveSequence = getCount === 1 ? '0' : '1';
			return new Response(JSON.stringify({ bookId, currentPosition: '8', currentPage: 8, progressPercentage: 80, readStatus: 'reading', saveSequence }), {
				headers: { 'Content-Type': 'application/json' },
			});
		});

		await expect(fetchReadingInfo(bookId, userId)).resolves.toMatchObject({ currentPage: 8 });
		expect(patchSequences).toEqual(['1000001', '1']);
		await expect(reserveReadingSave(bookId, 9, 'reading', 90, userId)).resolves.toBe('2');
	});

	test('ログアウト世代が変わったsequence conflictはread-backを中止しoutboxを保持する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		let releaseFirstPatch!: () => void;
		let markFirstPatch!: () => void;
		const firstPatchStarted = new Promise<void>((resolve) => { markFirstPatch = resolve; });
		const firstPatchMayFinish = new Promise<void>((resolve) => { releaseFirstPatch = resolve; });
		const requests: string[] = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method === 'PATCH') {
				requests.push('PATCH');
				markFirstPatch();
				await firstPatchMayFinish;
				return new Response(JSON.stringify({ code: 'STALE_READING_SAVE' }), { status: 409 });
			}
			requests.push('GET');
			return new Response(JSON.stringify({ bookId: 99, currentPosition: '1', currentPage: 1, progressPercentage: 0, readStatus: 'reading', saveSequence: '0' }), {
				headers: { 'Content-Type': 'application/json' },
			});
		});

		const save = saveReadingInfo(99, 2, 'reading', 20, 7);
		await firstPatchStarted;
		const invalidation = invalidateReadingSaves();
		releaseFirstPatch();
		await invalidation;
		await expect(save).rejects.toThrow('Reading save invalidated by authentication change');

		expect(requests).toEqual(['PATCH']);
		await expect(readReadingSaveRecord('outbox', 'user:7:book:99')).resolves.toMatchObject({
			position: 2,
			userId: 7,
		});
	});

	test('サーバー版がJavaScript safe integerを超えてもdecimal stringのまま1つ進める', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const sequences: string[] = [];
		let patchCount = 0;
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method === 'PATCH') {
				const body = JSON.parse(String(options.body)) as { saveSequence: string };
				sequences.push(body.saveSequence);
				patchCount += 1;
				if (patchCount === 1) return new Response(JSON.stringify({ code: 'STALE_READING_SAVE' }), { status: 409 });
				return new Response(JSON.stringify({ bookId: 99, currentPosition: '9', currentPage: 9, progressPercentage: 80, readStatus: 'reading', saveSequence: body.saveSequence }), {
					headers: { 'Content-Type': 'application/json' },
				});
			}
			return new Response(JSON.stringify({ bookId: 99, currentPosition: '9007199254740993', currentPage: 1, progressPercentage: 50, readStatus: 'reading', saveSequence: '9007199254740993' }), {
				headers: { 'Content-Type': 'application/json' },
			});
		});

		await saveReadingInfo(99, 9, 'reading', 80, 7);
		expect(sequences[1]).toBe('9007199254740994');
	});
});
