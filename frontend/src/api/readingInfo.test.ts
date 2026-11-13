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
		const request = indexedDB.open(READING_SAVE_TEST_DB_NAME, 2);
		request.onupgradeneeded = () => {
			const database = request.result;
			if (!database.objectStoreNames.contains('sequence')) database.createObjectStore('sequence', { autoIncrement: true });
			if (!database.objectStoreNames.contains('sequence-by-scope')) database.createObjectStore('sequence-by-scope', { keyPath: 'scopeKey' });
			if (!database.objectStoreNames.contains('outbox')) database.createObjectStore('outbox', { keyPath: 'scopeKey' });
		};
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

async function seedReadingSaveBaseline(bookId: number, userId: number, sequence = '0') {
	await writeReadingSaveRecord('sequence-by-scope', {
		scopeKey: `user:${userId}:book:${bookId}`,
		saveSequence: sequence,
		serverSequence: sequence,
	});
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
		const request = vi.mocked(fetch).mock.calls.find(([, options]) => options?.method === 'PATCH')?.[1];
		const body = JSON.parse(String(request?.body)) as Record<string, unknown>;
		expect(body).toEqual({ ...expected, saveSequence: expect.any(String), expectedSaveSequence: expect.any(String) });
		expect(BigInt(String(body.saveSequence))).toBeGreaterThan(BigInt(String(body.expectedSaveSequence)));
	}

	test('一時的なIndexedDB open失敗後に再試行し、sequenceをユーザーと書籍ごとに保つ', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const bookId = 9401;
		const userId = 9402;
		await seedReadingSaveBaseline(bookId, userId);
		await seedReadingSaveBaseline(bookId, userId + 1);
		await seedReadingSaveBaseline(bookId + 1, userId);
		const openError = new DOMException('Temporary IndexedDB failure', 'UnknownError');
		const failedRequest = {
			error: openError,
			set onerror(handler: IDBOpenDBRequest['onerror'] | null) {
				queueMicrotask(() => handler?.call(this as unknown as IDBOpenDBRequest, new Event('error')));
			},
		} as IDBOpenDBRequest;
		const openSpy = vi.spyOn(indexedDB, 'open').mockReturnValueOnce(failedRequest);
		const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => new Response(JSON.stringify({
			bookId,
			currentPosition: '2',
			currentPage: 2,
			progressPercentage: 20,
			readStatus: 'reading',
			saveSequence: '1',
			...(options?.method === 'PATCH' ? {} : { readingSaveProtocol: 'cas-v1' }),
		}), {
				headers: { 'Content-Type': 'application/json' },
		}));

		await expect(saveReadingInfo(bookId, 2, 'reading', 20, userId)).rejects.toThrow('Temporary IndexedDB failure');
		expect(fetchMock).not.toHaveBeenCalled();

		openSpy.mockRestore();
		await expect(saveReadingInfo(bookId, 2, 'reading', 20, userId)).resolves.toMatchObject({ currentPage: 2 });
		expect(fetchMock).toHaveBeenCalledTimes(2);
		const patchRequest = fetchMock.mock.calls.find(([, options]) => options?.method === 'PATCH')?.[1];
		expect(JSON.parse(String(patchRequest?.body))).toMatchObject({ saveSequence: '1' });
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
			new Response(JSON.stringify({ bookId: 7, currentPosition: '3', currentPage: 3, progressPercentage: 30, readStatus: 'reading', readingSaveProtocol: 'cas-v1' }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' },
			}),
		);

		await expect(fetchReadingInfo(7, 7)).resolves.toEqual({ bookId: 7, currentPosition: '3', currentPage: 3, progressPercentage: 30, readStatus: 'reading', readingSaveProtocol: 'cas-v1', saveSequence: '0' });
		expect(fetch).toHaveBeenCalledWith(
			'http://localhost:3000/api/v1/books/7/reading-info',
			expect.objectContaining({
				credentials: 'include',
				headers: { 'X-BeLib-User-Id': '7' },
			}),
		);
	});

	test('CAS対応マーカーのない旧backendではoutbox replayとアカウント切替後の保存をPATCHしない', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const bookId = 9410;
		const oldUserId = 9411;
		const newUserId = 9412;
		await seedReadingSaveBaseline(bookId, oldUserId, '4');
		await seedReadingSaveBaseline(bookId, newUserId, '4');
		await reserveReadingSave(bookId, 8, 'reading', 80, oldUserId);

		const methods: string[] = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			methods.push(options?.method ?? 'GET');
			return new Response(JSON.stringify({ bookId, currentPosition: '4', currentPage: 4, progressPercentage: 40, readStatus: 'reading', saveSequence: '4' }), {
				headers: { 'Content-Type': 'application/json' },
			});
		});

		await expect(fetchReadingInfo(bookId, oldUserId)).rejects.toMatchObject({ code: 'READING_SAVE_PROTOCOL_UNSUPPORTED' });
		await expect(readReadingSaveRecord('outbox', `user:${oldUserId}:book:${bookId}`)).resolves.toMatchObject({ position: 8 });

		await invalidateReadingSaves();
		await expect(saveReadingInfo(bookId, 9, 'reading', 90, newUserId)).rejects.toMatchObject({ code: 'READING_SAVE_PROTOCOL_UNSUPPORTED' });
		expect(methods).toEqual(['GET', 'GET']);
		expect(methods.filter((method) => method === 'PATCH')).toEqual([]);
		await expect(readReadingSaveRecord('outbox', `user:${oldUserId}:book:${bookId}`)).resolves.toMatchObject({ position: 8 });
	});

	test('不正なversion-3 outboxは競合時に消去され、旧payloadを送らず新しい保存から復旧する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const bookId = 9413;
		const userId = 9414;
		const scopeKey = `user:${userId}:book:${bookId}`;
		await seedReadingSaveBaseline(bookId, userId, '0');
		await writeReadingSaveRecord('outbox', {
			scopeKey,
			bookId,
			userId,
			position: 8,
			readStatus: 'reading',
			progressPercentage: 80,
			hasProgressPercentage: true,
			saveSequence: 'not-a-sequence',
			sequenceVersion: 3,
			expectedSaveSequence: '0',
		});

		const requests: Array<{ method: string; page?: number }> = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			const method = options?.method ?? 'GET';
			if (method === 'PATCH') {
				const body = JSON.parse(String(options?.body)) as { currentPage: number; saveSequence: string };
				requests.push({ method, page: body.currentPage });
				return new Response(JSON.stringify({ bookId, currentPosition: String(body.currentPage), currentPage: body.currentPage, progressPercentage: body.currentPage * 10, readStatus: 'reading', saveSequence: body.saveSequence }), {
					headers: { 'Content-Type': 'application/json' },
				});
			}
			requests.push({ method });
			return new Response(JSON.stringify({ bookId, currentPosition: '1', currentPage: 1, progressPercentage: 0, readStatus: 'reading', saveSequence: '0', readingSaveProtocol: 'cas-v1' }), {
				headers: { 'Content-Type': 'application/json' },
			});
		});

		await expect(fetchReadingInfo(bookId, userId)).resolves.toMatchObject({ currentPage: 1, saveConflict: true });
		await expect(readReadingSaveRecord('outbox', scopeKey)).resolves.toBeUndefined();
		await expect(saveReadingInfo(bookId, 9, 'reading', 90, userId)).resolves.toMatchObject({ currentPage: 9 });
		expect(requests).toEqual([{ method: 'GET' }, { method: 'PATCH', page: 9 }]);
		await expect(readReadingSaveRecord('outbox', scopeKey)).resolves.toBeUndefined();
	});

	test('malformed outboxは別アカウントへ流れず、元アカウントの新しい操作だけが復旧する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const bookId = 9415;
		const oldUserId = 9416;
		const newUserId = 9417;
		const oldScopeKey = `user:${oldUserId}:book:${bookId}`;
		await seedReadingSaveBaseline(bookId, oldUserId, '0');
		await seedReadingSaveBaseline(bookId, newUserId, '0');
		await writeReadingSaveRecord('outbox', {
			scopeKey: oldScopeKey,
			bookId,
			userId: oldUserId,
			position: 7,
			readStatus: 'reading',
			progressPercentage: 70,
			hasProgressPercentage: true,
			saveSequence: 'invalid',
			sequenceVersion: 3,
			expectedSaveSequence: '0',
		});

		const patches: Array<{ userId: string | null; page: number }> = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method === 'PATCH') {
				const body = JSON.parse(String(options.body)) as { currentPage: number; saveSequence: string };
				patches.push({ userId: new Headers(options.headers).get('X-BeLib-User-Id'), page: body.currentPage });
				return new Response(JSON.stringify({ bookId, currentPosition: String(body.currentPage), currentPage: body.currentPage, progressPercentage: 90, readStatus: 'reading', saveSequence: body.saveSequence }), {
					headers: { 'Content-Type': 'application/json' },
				});
			}
			return new Response(JSON.stringify({ bookId, currentPosition: '1', currentPage: 1, progressPercentage: 0, readStatus: 'reading', saveSequence: '0', readingSaveProtocol: 'cas-v1' }), {
				headers: { 'Content-Type': 'application/json' },
			});
		});

		await expect(fetchReadingInfo(bookId, newUserId)).resolves.toMatchObject({ currentPage: 1 });
		await expect(readReadingSaveRecord('outbox', oldScopeKey)).resolves.toMatchObject({ position: 7 });
		await expect(fetchReadingInfo(bookId, oldUserId)).resolves.toMatchObject({ currentPage: 1, saveConflict: true });
		await expect(readReadingSaveRecord('outbox', oldScopeKey)).resolves.toBeUndefined();
		await expect(saveReadingInfo(bookId, 9, 'reading', 90, oldUserId)).resolves.toMatchObject({ currentPage: 9 });
		expect(patches).toEqual([{ userId: String(oldUserId), page: 9 }]);
	});

	test('PDF現在ページとcompleted状態をPATCHで保存する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		await seedReadingSaveBaseline(7, 7);
		vi.spyOn(globalThis, 'fetch')
			.mockResolvedValueOnce(new Response(JSON.stringify({ bookId: 7, currentPosition: '3', currentPage: 3, progressPercentage: 0, readStatus: 'reading', saveSequence: '0', readingSaveProtocol: 'cas-v1' }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' },
			}))
			.mockResolvedValueOnce(
			new Response(JSON.stringify({ bookId: 7, currentPosition: '4', currentPage: 4, progressPercentage: 100, readStatus: 'completed', saveSequence: '1' }), {
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
			saveSequence: '1',
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
		await seedReadingSaveBaseline(7, 7);
		const cfi = 'epubcfi(/6/2!/4/1:0)';
		vi.spyOn(globalThis, 'fetch')
			.mockResolvedValueOnce(new Response(JSON.stringify({ bookId: 7, currentPosition: cfi, currentPage: 1, progressPercentage: 30, readStatus: 'reading', saveSequence: '0', readingSaveProtocol: 'cas-v1' }), {
				status: 200,
				headers: { 'Content-Type': 'application/json' },
			}))
			.mockResolvedValueOnce(
				new Response(JSON.stringify({ bookId: 7, currentPosition: cfi, currentPage: 1, progressPercentage: 30, readStatus: 'reading', saveSequence: '1' }), {
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
		await seedReadingSaveBaseline(99, 7);
		await seedReadingSaveBaseline(99, 8);
		let release!: () => void;
		const firstFinished = new Promise<void>((resolve) => { release = resolve; });
		const calls: string[] = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method !== 'PATCH') {
				return new Response(JSON.stringify({ bookId: 99, currentPosition: '1', currentPage: 1, progressPercentage: 0, readStatus: 'reading', saveSequence: '0', readingSaveProtocol: 'cas-v1' }), {
					headers: { 'Content-Type': 'application/json' },
				});
			}
			const body = JSON.parse(String(options?.body)) as { currentPage: number };
			calls.push(`PATCH ${body.currentPage}`);
			if (body.currentPage === 2) await firstFinished;
			return new Response(JSON.stringify({ bookId: 99, currentPosition: String(body.currentPage), currentPage: body.currentPage, progressPercentage: body.currentPage === 2 ? 20 : 80, readStatus: 'reading', saveSequence: String((JSON.parse(String(options?.body)) as { saveSequence: string }).saveSequence) }), {
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
		await seedReadingSaveBaseline(firstBook, 901);
		await seedReadingSaveBaseline(secondBook, 901);

		await expect(reserveReadingSave(firstBook, 1, 'reading', 10, 901)).resolves.toBe('1');
		await expect(reserveReadingSave(secondBook, 1, 'reading', 10, 901)).resolves.toBe('1');
		await expect(reserveReadingSave(firstBook, 2, 'reading', 20, 901)).resolves.toBe('2');
	});

	test('旧ブラウザ全体のsequence履歴が100万を超えても、新しい書籍は1から始まる', async () => {
		await seedReadingSaveBaseline(9303, 901);
		await seedReadingSaveBaseline(9304, 901);
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
		await seedReadingSaveBaseline(9305, 901);
		const reservations = await Promise.all([
			reserveReadingSave(9305, 2, 'reading', 20, 901),
			reserveReadingSave(9305, 9, 'reading', 90, 901),
		]);

		expect(reservations).toEqual(['1', '2']);
		await expect(readReadingSaveRecord('outbox', 'user:901:book:9305')).resolves.toMatchObject({
			position: 9,
			saveSequence: '2',
			sequenceVersion: 3,
			expectedSaveSequence: '0',
		});
	});

	test('pagehideの後続PATCHが409になったとき同一browserの先行PATCH ACKだけで一度だけ進め直す', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const bookId = 93051;
		const userId = 901;
		await seedReadingSaveBaseline(bookId, userId, '0');
		let releaseFirst!: () => void;
		let markFirstStarted!: () => void;
		let markSecondStarted!: () => void;
		let releaseSecondConflict!: () => void;
		const firstStarted = new Promise<void>((resolve) => { markFirstStarted = resolve; });
		const secondStarted = new Promise<void>((resolve) => { markSecondStarted = resolve; });
		const firstMayResolve = new Promise<void>((resolve) => { releaseFirst = resolve; });
		const secondMayConflict = new Promise<void>((resolve) => { releaseSecondConflict = resolve; });
		const bodies: Array<Record<string, unknown>> = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method !== 'PATCH') {
				const acknowledged = bodies.length > 0;
				return new Response(JSON.stringify({ bookId, currentPosition: acknowledged ? '2' : '1', currentPage: acknowledged ? 2 : 1, progressPercentage: acknowledged ? 20 : 0, readStatus: 'reading', saveSequence: acknowledged ? '1' : '0', readingSaveProtocol: 'cas-v1' }), { headers: { 'Content-Type': 'application/json' } });
			}
			if (options?.method === 'PATCH') {
				const body = JSON.parse(String(options.body)) as Record<string, unknown>;
				bodies.push(body);
				if (bodies.length === 1) {
					markFirstStarted();
					await firstMayResolve;
					return new Response(JSON.stringify({ bookId, currentPosition: '2', currentPage: 2, progressPercentage: 20, readStatus: 'reading', saveSequence: '1' }), { headers: { 'Content-Type': 'application/json' } });
				}
				if (bodies.length === 2) {
					markSecondStarted();
					await secondMayConflict;
					return new Response(JSON.stringify({ code: 'STALE_READING_SAVE' }), { status: 409 });
				}
				return new Response(JSON.stringify({ bookId, currentPosition: '3', currentPage: 3, progressPercentage: 30, readStatus: 'reading', saveSequence: '2' }), { headers: { 'Content-Type': 'application/json' } });
			}
			return new Response(JSON.stringify({ bookId, currentPosition: '2', currentPage: 2, progressPercentage: 20, readStatus: 'reading', saveSequence: '1' }), { headers: { 'Content-Type': 'application/json' } });
		});

		const first = saveReadingInfo(bookId, 2, 'reading', 20, userId);
		await firstStarted;
		const pagehide = saveReadingInfo(bookId, 3, 'reading', 30, userId, undefined, true);
		await secondStarted;
		releaseFirst();
		await expect(first).resolves.toMatchObject({ saveSequence: '1' });
		releaseSecondConflict();
		await expect(pagehide).resolves.toMatchObject({ saveSequence: '2' });

		expect(bodies).toHaveLength(3);
		expect(bodies[0]).toMatchObject({ saveSequence: '1', expectedSaveSequence: '0' });
		expect(bodies[1]).toMatchObject({ saveSequence: '2', expectedSaveSequence: '0' });
		expect(bodies[2]).toMatchObject({ saveSequence: '2', expectedSaveSequence: '1' });
		await expect(readReadingSaveRecord('outbox', `user:${userId}:book:${bookId}`)).resolves.toBeUndefined();
	});

	test('先行PATCHのACKが不明ならpagehide競合を再採番せず、authoritative conflictとして止める', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const bookId = 93052;
		const userId = 901;
		await seedReadingSaveBaseline(bookId, userId, '0');
		let rejectFirst!: () => void;
		let markFirstStarted!: () => void;
		let markSecondStarted!: () => void;
		let releaseSecondConflict!: () => void;
		const firstStarted = new Promise<void>((resolve) => { markFirstStarted = resolve; });
		const secondStarted = new Promise<void>((resolve) => { markSecondStarted = resolve; });
		const firstMayReject = new Promise<void>((resolve) => { rejectFirst = resolve; });
		const secondMayConflict = new Promise<void>((resolve) => { releaseSecondConflict = resolve; });
		const bodies: Array<Record<string, unknown>> = [];
		const requestMethods: string[] = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method !== 'PATCH') {
				requestMethods.push('GET');
				return new Response(JSON.stringify({ bookId, currentPosition: '1', currentPage: 1, progressPercentage: 0, readStatus: 'reading', saveSequence: '0', readingSaveProtocol: 'cas-v1' }), { headers: { 'Content-Type': 'application/json' } });
			}
			if (options?.method === 'PATCH') {
				const body = JSON.parse(String(options.body)) as Record<string, unknown>;
				bodies.push(body);
				if (bodies.length === 1) {
					markFirstStarted();
					await firstMayReject;
					throw new TypeError('Lost acknowledgement');
				}
				markSecondStarted();
				await secondMayConflict;
				return new Response(JSON.stringify({ code: 'STALE_READING_SAVE' }), { status: 409 });
			}
			return new Response(JSON.stringify({ bookId, currentPosition: '2', currentPage: 2, progressPercentage: 20, readStatus: 'reading', saveSequence: '1', readingSaveProtocol: 'cas-v1' }), { headers: { 'Content-Type': 'application/json' } });
		});

		const first = saveReadingInfo(bookId, 2, 'reading', 20, userId);
		await firstStarted;
		const pagehide = saveReadingInfo(bookId, 3, 'reading', 30, userId, undefined, true);
		await secondStarted;
		rejectFirst();
		await expect(first).rejects.toThrow('Lost acknowledgement');
		releaseSecondConflict();
		await expect(pagehide).rejects.toMatchObject({ status: 409 });

		expect(bodies).toHaveLength(2);
		expect(bodies[0]).toMatchObject({ saveSequence: '1', expectedSaveSequence: '0' });
		expect(bodies[1]).toMatchObject({ saveSequence: '2', expectedSaveSequence: '0' });
		expect(requestMethods).toEqual(['GET', 'GET']);
		await expect(readReadingSaveRecord('outbox', `user:${userId}:book:${bookId}`)).resolves.toBeUndefined();
	});

	test('アカウント切替後もsequenceは各ユーザーと書籍の組み合わせで分離する', async () => {
		await seedReadingSaveBaseline(9306, 7);
		await seedReadingSaveBaseline(9306, 8);
		await expect(reserveReadingSave(9306, 2, 'reading', 20, 7)).resolves.toBe('1');
		await invalidateReadingSaves();

		await expect(reserveReadingSave(9306, 8, 'reading', 80, 8)).resolves.toBe('1');
		await expect(reserveReadingSave(9306, 3, 'reading', 30, 7)).resolves.toBe('2');
	});

	test('永続化したoutbox保存は次の読書情報取得時に再送される', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		await seedReadingSaveBaseline(9307, 902);
		const sequence = await reserveReadingSave(9307, 6, 'reading', 60, 902);
		await expect(readReadingSaveRecord('outbox', 'user:902:book:9307')).resolves.toMatchObject({
			position: 6,
			saveSequence: sequence,
			sequenceVersion: 3,
			expectedSaveSequence: '0',
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
			return new Response(JSON.stringify({ bookId: 9307, currentPosition: '1', currentPage: 1, progressPercentage: 10, readStatus: 'reading', saveSequence: '0', readingSaveProtocol: 'cas-v1' }), {
				headers: { 'Content-Type': 'application/json' },
			});
		});

		await expect(fetchReadingInfo(9307, 902)).resolves.toMatchObject({ currentPage: 6 });
		expect(calls).toEqual(['GET', 'PATCH']);
		await expect(readReadingSaveRecord('outbox', 'user:902:book:9307')).resolves.toBeUndefined();
	});

	test('明示的な認証無効化はoutboxを保ち、同じユーザーだけが再送する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const bookId = 99;
		const oldUserId = 7;
		const newUserId = 8;
		await seedReadingSaveBaseline(bookId, oldUserId);
		await seedReadingSaveBaseline(bookId, newUserId);
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
				const body = JSON.parse(String(options.body)) as { saveSequence: string };
				return new Response(JSON.stringify({ bookId, currentPosition: '7', currentPage: 7, progressPercentage: 70, readStatus: 'reading', saveSequence: body.saveSequence }), {
					headers: { 'Content-Type': 'application/json' },
				});
			}
			return new Response(JSON.stringify({ bookId, currentPosition: '1', currentPage: 1, progressPercentage: 0, readStatus: 'unread', saveSequence: '0', readingSaveProtocol: 'cas-v1' }), {
				headers: { 'Content-Type': 'application/json' },
			});
		});

		await expect(fetchReadingInfo(bookId, newUserId)).resolves.toMatchObject({ currentPage: 1 });
		expect(patchUserIds).toEqual([]);
		await expect(readReadingSaveRecord('outbox', `user:${oldUserId}:book:${bookId}`)).resolves.toMatchObject({ position: 7 });

		await expect(fetchReadingInfo(bookId, oldUserId)).resolves.toMatchObject({ currentPage: 7 });
		expect(patchUserIds).toEqual([String(oldUserId)]);
		await expect(readReadingSaveRecord('outbox', `user:${oldUserId}:book:${bookId}`)).resolves.toBeUndefined();
	});

	test('認証無効化で中断されたPATCHは最新のdurable outboxを破棄しない', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		await seedReadingSaveBaseline(99, 7);
		let markPatchStarted!: () => void;
		const patchStarted = new Promise<void>((resolve) => { markPatchStarted = resolve; });
		let patchSignal: AbortSignal | undefined;
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method !== 'PATCH') {
				return new Response(JSON.stringify({ bookId: 99, currentPosition: '1', currentPage: 1, progressPercentage: 0, readStatus: 'reading', saveSequence: '0', readingSaveProtocol: 'cas-v1' }), {
					headers: { 'Content-Type': 'application/json' },
				});
			}
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
		await seedReadingSaveBaseline(99, 7);
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
		await seedReadingSaveBaseline(99, 7);
		let activeSessionUserId = 7;
		let release!: () => void;
		const firstFinished = new Promise<void>((resolve) => { release = resolve; });
		const requests: Array<{ page: number; expectedUserId: string | null; activeUserId: number }> = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method !== 'PATCH') {
				return new Response(JSON.stringify({ bookId: 99, currentPosition: '1', currentPage: 1, progressPercentage: 0, readStatus: 'reading', saveSequence: '0', readingSaveProtocol: 'cas-v1' }), {
					headers: { 'Content-Type': 'application/json' },
				});
			}
			const body = JSON.parse(String(options?.body)) as { currentPage: number };
			const headers = new Headers(options?.headers);
			const requestSessionUserId = activeSessionUserId;
			requests.push({ page: body.currentPage, expectedUserId: headers.get('X-BeLib-User-Id'), activeUserId: requestSessionUserId });
			if (body.currentPage === 2) await firstFinished;
			if (headers.get('X-BeLib-User-Id') !== String(requestSessionUserId)) {
				return new Response(JSON.stringify({ code: 'READING_SESSION_MISMATCH' }), { status: 409 });
			}
			return new Response(JSON.stringify({ bookId: 99, currentPosition: String(body.currentPage), currentPage: body.currentPage, progressPercentage: 20, readStatus: 'reading', saveSequence: String((JSON.parse(String(options?.body)) as { saveSequence: string }).saveSequence) }), {
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
		await seedReadingSaveBaseline(99, 7);
		let release!: () => void;
		const firstFinished = new Promise<void>((resolve) => {
			release = resolve;
		});
		const calls: string[] = [];
		let position = '1';
		let serverSequence = '0';
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method === 'PATCH') {
				const body = JSON.parse(String(options.body)) as { currentPage: number; saveSequence: string };
				const page = String(body.currentPage);
				calls.push(`PATCH ${page}`);
				if (page === '2') await firstFinished;
				position = page;
				serverSequence = body.saveSequence;
			} else calls.push(`GET ${position}`);
			return new Response(JSON.stringify({ bookId: 99, currentPosition: position, currentPage: Number(position), readStatus: 'reading', saveSequence: serverSequence, readingSaveProtocol: 'cas-v1' }), {
				headers: { 'Content-Type': 'application/json' },
			});
		});
		const older = saveReadingInfo(99, 2, 'reading', undefined, 7);
		const newer = saveReadingInfo(99, 9, 'reading', undefined, 7);
		const reopened = fetchReadingInfo(99, 7);
		await vi.waitFor(() => expect(calls.filter((call) => call.startsWith('PATCH'))).toEqual(['PATCH 2']));
		release();
		await Promise.all([older, newer]);
		await expect(reopened).resolves.toMatchObject({ currentPosition: '9' });
		expect(calls.filter((call) => call.startsWith('PATCH'))).toEqual(['PATCH 2', 'PATCH 9']);
		expect(calls.at(-1)).toBe('GET 9');
	});

	test('新しいブラウザはserver baselineをGETしてからexpected-base付きで1回だけ保存する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const patchBodies: Array<Record<string, unknown>> = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method === 'PATCH') {
				const body = JSON.parse(String(options.body)) as Record<string, unknown>;
				patchBodies.push(body);
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
				readingSaveProtocol: 'cas-v1',
			}), { headers: { 'Content-Type': 'application/json' } });
		});

		await expect(saveReadingInfo(99, 9, 'reading', 80, 7)).resolves.toMatchObject({ currentPage: 9 });

		expect(patchBodies).toHaveLength(1);
		expect(patchBodies[0]).toMatchObject({ currentPage: 9, readStatus: 'reading', progressPercentage: 80, saveSequence: '51', expectedSaveSequence: '50' });
	});

	test('409は同じpayloadをrebaseせず、authoritative位置を返して該当outboxだけを消す', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const bookId = 9309;
		const userId = 904;
		await seedReadingSaveBaseline(bookId, userId);
		const originalSequence = await reserveReadingSave(bookId, 9, 'reading', 80, userId);
		const patchSequences: string[] = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method === 'PATCH') {
				const body = JSON.parse(String(options.body)) as { saveSequence: string };
				patchSequences.push(body.saveSequence);
				return new Response(JSON.stringify({ code: 'STALE_READING_SAVE' }), { status: 409 });
			}
			return new Response(JSON.stringify({
				bookId,
				currentPosition: '50',
				currentPage: 50,
				progressPercentage: 50,
				readStatus: 'reading',
				saveSequence: '50',
				readingSaveProtocol: 'cas-v1',
			}), { headers: { 'Content-Type': 'application/json' } });
		});

		await expect(saveReadingInfo(bookId, 9, 'reading', 80, userId, undefined, false, originalSequence))
			.rejects.toMatchObject({ status: 409 });

		expect(patchSequences).toEqual([originalSequence]);
		await expect(readReadingSaveRecord('outbox', `user:${userId}:book:${bookId}`)).resolves.toBeUndefined();
	});

	test('lost ACK後に別端末の保存が進んでいれば、古いoutboxを再PATCHせず競合として回収する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const bookId = 9310;
		const userId = 905;
		await seedReadingSaveBaseline(bookId, userId, '0');
		const fetchMock = vi.spyOn(globalThis, 'fetch')
			.mockResolvedValueOnce(new Response(JSON.stringify({ bookId, currentPosition: '1', currentPage: 1, progressPercentage: 0, readStatus: 'reading', saveSequence: '0', readingSaveProtocol: 'cas-v1' }), { headers: { 'Content-Type': 'application/json' } }))
			.mockRejectedValueOnce(new TypeError('Connection lost after request may have reached server'))
			.mockResolvedValueOnce(new Response(JSON.stringify({
				bookId,
				currentPosition: '9',
				currentPage: 9,
				progressPercentage: 90,
				readStatus: 'reading',
				saveSequence: '1',
				readingSaveProtocol: 'cas-v1',
			}), { headers: { 'Content-Type': 'application/json' } }));

		await expect(saveReadingInfo(bookId, 5, 'reading', 50, userId)).rejects.toThrow('Connection lost');
		await expect(readReadingSaveRecord('outbox', `user:${userId}:book:${bookId}`)).resolves.toMatchObject({
			position: 5,
			expectedSaveSequence: '0',
		});

		await expect(fetchReadingInfo(bookId, userId)).resolves.toMatchObject({ currentPage: 9, saveConflict: true });
		expect(fetchMock).toHaveBeenCalledTimes(3);
		expect(fetchMock.mock.calls.map(([, options]) => options?.method ?? 'GET')).toEqual(['GET', 'PATCH', 'GET']);
		await expect(readReadingSaveRecord('outbox', `user:${userId}:book:${bookId}`)).resolves.toBeUndefined();
	});

	test('base不明のlegacy outboxは送信せず競合を返し、次の明示保存はserver baselineから始める', async () => {
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
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method === 'PATCH') {
				const body = JSON.parse(String(options.body)) as { saveSequence: string };
				patchSequences.push(body.saveSequence);
			}
			return new Response(JSON.stringify({ bookId, currentPosition: '8', currentPage: 8, progressPercentage: 80, readStatus: 'reading', saveSequence: '0', readingSaveProtocol: 'cas-v1' }), {
				headers: { 'Content-Type': 'application/json' },
			});
		});

		await expect(fetchReadingInfo(bookId, userId)).resolves.toMatchObject({ currentPage: 8, saveConflict: true });
		expect(patchSequences).toEqual([]);
		await expect(readReadingSaveRecord('outbox', scopeKey)).resolves.toBeUndefined();
		await expect(reserveReadingSave(bookId, 9, 'reading', 90, userId)).resolves.toBe('1');
	});

	test('ログアウト世代が変わったsequence conflictはread-backを中止しoutboxを保持する', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		let releaseFirstPatch!: () => void;
		let markFirstPatch!: () => void;
		const firstPatchStarted = new Promise<void>((resolve) => { markFirstPatch = resolve; });
		const firstPatchMayFinish = new Promise<void>((resolve) => { releaseFirstPatch = resolve; });
		const requests: string[] = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method !== 'PATCH') {
				requests.push('GET');
				return new Response(JSON.stringify({ bookId: 99, currentPosition: '1', currentPage: 1, progressPercentage: 0, readStatus: 'reading', saveSequence: '0', readingSaveProtocol: 'cas-v1' }), {
					headers: { 'Content-Type': 'application/json' },
				});
			}
			if (options?.method === 'PATCH') {
				requests.push('PATCH');
				markFirstPatch();
				await firstPatchMayFinish;
				return new Response(JSON.stringify({ code: 'STALE_READING_SAVE' }), { status: 409 });
			}
			return new Response(JSON.stringify({ bookId: 99, currentPosition: '1', currentPage: 1, progressPercentage: 0, readStatus: 'reading', saveSequence: '0', readingSaveProtocol: 'cas-v1' }), {
				headers: { 'Content-Type': 'application/json' },
			});
		});

		const save = saveReadingInfo(99, 2, 'reading', 20, 7);
		await firstPatchStarted;
		const invalidation = invalidateReadingSaves();
		releaseFirstPatch();
		await invalidation;
		await expect(save).rejects.toThrow('Reading save invalidated by authentication change');

		expect(requests).toEqual(['GET', 'PATCH']);
		await expect(readReadingSaveRecord('outbox', 'user:7:book:99')).resolves.toMatchObject({
			position: 2,
			userId: 7,
		});
	});

	test('サーバー版がJavaScript safe integerを超えてもdecimal stringのまま1つ進める', async () => {
		vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:3000');
		const sequences: string[] = [];
		vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, options) => {
			if (options?.method === 'PATCH') {
				const body = JSON.parse(String(options.body)) as { saveSequence: string };
				sequences.push(body.saveSequence);
				return new Response(JSON.stringify({ bookId: 99, currentPosition: '9', currentPage: 9, progressPercentage: 80, readStatus: 'reading', saveSequence: body.saveSequence }), {
					headers: { 'Content-Type': 'application/json' },
				});
			}
			return new Response(JSON.stringify({ bookId: 99, currentPosition: '9007199254740993', currentPage: 1, progressPercentage: 50, readStatus: 'reading', saveSequence: '9007199254740993', readingSaveProtocol: 'cas-v1' }), {
				headers: { 'Content-Type': 'application/json' },
			});
		});

		await saveReadingInfo(99, 9, 'reading', 80, 7);
		expect(sequences).toEqual(['9007199254740994']);
	});
});
