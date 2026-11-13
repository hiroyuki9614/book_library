import { Hono } from 'hono';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import type { PrismaVariables } from '../../lib/prisma.js';
import { MAX_CURRENT_POSITION_LENGTH } from './readingProgress.js';
import booksRoutes from './routes.js';

const mocks = vi.hoisted(() => ({
	getSession: vi.fn(),
	userFindFirst: vi.fn(),
	bookFindUnique: vi.fn(),
	permissionFindUnique: vi.fn(),
	readingInfoFindUnique: vi.fn(),
	readingInfoUpsert: vi.fn(),
	readingInfoUpdateMany: vi.fn(),
	readingInfoCreate: vi.fn(),
	readingInfoUpdate: vi.fn(),
}));

vi.mock('../../lib/auth.js', () => ({
	auth: {
		api: {
			getSession: mocks.getSession,
		},
	},
}));

vi.mock('../../lib/bookStorage.js', () => ({
	BookStorageNotFoundError: class extends Error {},
	getBookFileAccess: vi.fn(),
}));

const app = new Hono<PrismaVariables>();
app.use('*', async (c, next) => {
	c.set('prisma', {
		user: { findFirst: mocks.userFindFirst },
		book: { findUnique: mocks.bookFindUnique },
		roleBookPermission: { findUnique: mocks.permissionFindUnique },
		readingInfo: {
			findUnique: mocks.readingInfoFindUnique,
			upsert: mocks.readingInfoUpsert,
			updateMany: mocks.readingInfoUpdateMany,
			create: mocks.readingInfoCreate,
			update: mocks.readingInfoUpdate,
		},
	} as never);
	await next();
});
app.route('/', booksRoutes);

beforeEach(() => {
	vi.resetAllMocks();
	mocks.getSession.mockResolvedValue({ user: { id: '7' } });
	mocks.userFindFirst.mockResolvedValue({ id: 7, roleId: 2 });
	mocks.bookFindUnique.mockResolvedValue({
		id: 1,
		title: 'Phase 4 PDF',
		authorName: null,
		publishedAt: null,
		publisher: null,
		description: null,
		deletedAt: null,
		pageTurnDirection: 'ltr',
		createdAt: new Date('2026-08-01T00:00:00.000Z'),
		updatedAt: new Date('2026-08-01T00:00:00.000Z'),
		category: { id: 1, name: '技術書' },
		bookFiles: [],
	});
	mocks.permissionFindUnique.mockResolvedValue({ id: 1 });
	mocks.readingInfoFindUnique.mockResolvedValue({ currentPosition: '3', progressPercentage: 25, readStatus: 'reading' });
	mocks.readingInfoUpdateMany.mockResolvedValue({ count: 1 });
});

const READING_HEADERS = { 'X-BeLib-User-Id': '7' };
const JSON_READING_HEADERS = { ...READING_HEADERS, 'Content-Type': 'application/json' };

describe('reading-info API', () => {
	test('GETは認証済みで権限のあるユーザー自身の現在ページを返す', async () => {
		mocks.readingInfoFindUnique.mockResolvedValue({ currentPosition: '3', progressPercentage: 25, readStatus: 'reading' });

		const response = await app.request('/1/reading-info');

		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({ bookId: 1, currentPosition: '3', currentPage: 3, progressPercentage: 25, readStatus: 'reading', saveSequence: '0' });
		expect(mocks.readingInfoFindUnique).toHaveBeenCalledWith({
			where: { userId_bookId: { userId: 7, bookId: 1 } },
		});
	});

	test('認証済みreading-info GETはサーバー管理のsaveSequenceを安全な文字列で返す', async () => {
		mocks.readingInfoFindUnique.mockResolvedValue({ currentPosition: '50', progressPercentage: 50, readStatus: 'reading', saveSequence: 50n });

		const response = await app.request('/1/reading-info', { headers: READING_HEADERS });

		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({
			bookId: 1,
			currentPosition: '50',
			currentPage: 50,
			progressPercentage: 50,
			readStatus: 'reading',
			saveSequence: '50',
		});
	});

	test('PATCHはPostgreSQL BIGINTを超えるsaveSequenceをDB更新前に拒否する', async () => {
		const response = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: JSON_READING_HEADERS,
			body: JSON.stringify({ currentPage: 51, saveSequence: '9223372036854775808' }),
		});

		expect(response.status).toBe(400);
		expect(await response.json()).toMatchObject({ code: 'INVALID_SAVE_SEQUENCE' });
		expect(mocks.readingInfoUpdateMany).not.toHaveBeenCalled();
		expect(mocks.readingInfoCreate).not.toHaveBeenCalled();
	});

	test('PATCHはヒントなしの既存callerを受け入れる', async () => {
		const response = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ currentPage: 3, progressPercentage: 25 }),
		});

		expect(response.status).toBe(200);
		expect(await response.json()).toMatchObject({ bookId: 1, currentPage: 3, progressPercentage: 25, readStatus: 'reading' });
	});

	test('PATCHはcurrentPositionの上限値を受け入れ、上限超過と非文字列を拒否する', async () => {
		const maxPosition = 'x'.repeat(MAX_CURRENT_POSITION_LENGTH);
		mocks.readingInfoUpdateMany.mockResolvedValueOnce({ count: 0 });
		mocks.readingInfoFindUnique.mockResolvedValueOnce(null);
		mocks.readingInfoCreate.mockImplementation(({ data }) => data);

		const accepted = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: JSON_READING_HEADERS,
			body: JSON.stringify({ currentPosition: maxPosition, readStatus: 'completed' }),
		});
		expect(accepted.status).toBe(200);
		expect((await accepted.json()).currentPosition).toBe(maxPosition);

		for (const currentPosition of ['x'.repeat(MAX_CURRENT_POSITION_LENGTH + 1), 7]) {
			const response = await app.request('/1/reading-info', {
				method: 'PATCH',
				headers: JSON_READING_HEADERS,
				body: JSON.stringify({ currentPosition }),
			});
			expect(response.status).toBe(400);
			expect(await response.json()).toMatchObject({ code: 'INVALID_CURRENT_POSITION' });
		}
		expect(mocks.readingInfoCreate).toHaveBeenCalledTimes(1);
	});

	test('GETはReadingInfo未作成時に1ページ目を返す', async () => {
		mocks.readingInfoFindUnique.mockResolvedValue(null);

		const response = await app.request('/1/reading-info', { headers: READING_HEADERS });

		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({ bookId: 1, currentPosition: null, currentPage: 1, progressPercentage: 0, readStatus: 'unread', saveSequence: '0' });
	});

	test('PATCHはユーザーと書籍単位でcompletedを降格させずに更新する', async () => {

		const response = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: JSON_READING_HEADERS,
			body: JSON.stringify({ currentPage: 3, progressPercentage: 25 }),
		});

		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({ bookId: 1, currentPosition: '3', currentPage: 3, progressPercentage: 25, readStatus: 'reading', saveSequence: '0' });
		expect(mocks.readingInfoUpdateMany).toHaveBeenCalledWith({
			where: { userId: 7, bookId: 1, readStatus: { not: 'completed' }, saveSequence: { equals: 0n } },
			data: { currentPosition: '3', readStatus: 'reading', progressPercentage: 25, saveSequence: 0n },
		});
		expect(mocks.readingInfoUpsert).not.toHaveBeenCalled();
	});

	test('PATCHは255文字を超えるEPUBのcurrentPositionを保存する', async () => {
		const currentPosition = 'x'.repeat(333);
		mocks.readingInfoFindUnique.mockResolvedValue({ currentPosition, progressPercentage: 25, readStatus: 'reading' });

		const response = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: JSON_READING_HEADERS,
			body: JSON.stringify({ currentPosition, progressPercentage: 25 }),
		});

		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({ bookId: 1, currentPosition, currentPage: 1, progressPercentage: 25, readStatus: 'reading', saveSequence: '0' });
		expect(mocks.readingInfoUpdateMany).toHaveBeenCalledWith({
			where: { userId: 7, bookId: 1, readStatus: { not: 'completed' }, saveSequence: { equals: 0n } },
			data: { currentPosition, readStatus: 'reading', progressPercentage: 25, saveSequence: 0n },
		});
	});

	test('遅い古いPATCHが新しいPATCHを追い越してもCASで拒否され最終位置を壊さない', async () => {
		type ReadingRow = {
			currentPosition: string;
			progressPercentage: number;
			readStatus: 'reading' | 'completed';
			saveSequence: bigint;
		};
		let row: ReadingRow = {
			currentPosition: '1',
			progressPercentage: 0,
			readStatus: 'reading',
			saveSequence: 0n,
		};
		let releaseOlder!: () => void;
		let markOlderStarted!: () => void;
		const olderMayContinue = new Promise<void>((resolve) => { releaseOlder = resolve; });
		const olderStarted = new Promise<void>((resolve) => { markOlderStarted = resolve; });

		mocks.readingInfoFindUnique.mockImplementation(async ({ select }: { select?: unknown } = {}) => {
			if (select) {
				return {
					currentPosition: row.currentPosition,
					progressPercentage: row.progressPercentage,
					readStatus: row.readStatus,
					saveSequence: row.saveSequence,
				};
			}
			return { ...row };
		});
		mocks.readingInfoUpdateMany.mockImplementation(async ({ where, data }: {
			where: { saveSequence: { equals?: bigint; gte?: bigint; lt?: bigint }; readStatus?: { not: string } };
			data: { currentPosition: string; readStatus: 'reading' | 'completed'; progressPercentage?: number; saveSequence: bigint };
		}) => {
			if (data.currentPosition === '2') {
				markOlderStarted();
				await olderMayContinue;
			}
			const sequenceMatches = where.saveSequence.equals !== undefined
				? row.saveSequence === where.saveSequence.equals
				: (where.saveSequence.lt === undefined || row.saveSequence < where.saveSequence.lt)
					&& (where.saveSequence.gte === undefined || row.saveSequence >= where.saveSequence.gte);
			if (!sequenceMatches) return { count: 0 };
			if (where.readStatus?.not === row.readStatus) return { count: 0 };
			row = {
				currentPosition: data.currentPosition,
				progressPercentage: data.progressPercentage ?? row.progressPercentage,
				readStatus: data.readStatus,
				saveSequence: data.saveSequence,
			};
			return { count: 1 };
		});

		const older = app.request('/1/reading-info', {
			method: 'PATCH',
			headers: JSON_READING_HEADERS,
			body: JSON.stringify({ currentPage: 2, progressPercentage: 20, saveSequence: 1, expectedSaveSequence: 0 }),
		});
		await olderStarted;

		const newerResponse = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: JSON_READING_HEADERS,
			body: JSON.stringify({ currentPage: 9, progressPercentage: 80, saveSequence: 2, expectedSaveSequence: 0 }),
		});
		expect(newerResponse.status).toBe(200);
		expect(await newerResponse.json()).toEqual({ bookId: 1, currentPosition: '9', currentPage: 9, progressPercentage: 80, readStatus: 'reading', saveSequence: '2' });

		releaseOlder();
		const olderResponse = await older;
		expect(olderResponse.status).toBe(409);
		expect(await olderResponse.json()).toMatchObject({ code: 'STALE_READING_SAVE' });
		expect(row).toEqual({ currentPosition: '9', progressPercentage: 80, readStatus: 'reading', saveSequence: 2n });
	});

	test('PATCHは16384文字を超えるcurrentPositionを認証と権限確認後に400で拒否する', async () => {
		const currentPosition = 'x'.repeat(16_385);

		const response = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: JSON_READING_HEADERS,
			body: JSON.stringify({ currentPosition }),
		});

		expect(response.status).toBe(400);
		expect(await response.json()).toEqual({
			message: 'currentPosition must be a non-empty string of 16384 characters or fewer',
			code: 'INVALID_CURRENT_POSITION',
		});
		expect(mocks.getSession).toHaveBeenCalled();
		expect(mocks.userFindFirst).toHaveBeenCalledWith({ where: { id: 7 }, select: { id: true, roleId: true } });
		expect(mocks.permissionFindUnique).toHaveBeenCalledWith({ where: { roleId_bookId: { roleId: 2, bookId: 1 } } });
		expect(mocks.readingInfoUpsert).not.toHaveBeenCalled();
		expect(mocks.readingInfoUpdateMany).not.toHaveBeenCalled();
		expect(mocks.readingInfoCreate).not.toHaveBeenCalled();
	});

	test('PATCHは正の整数でないcurrentPageを400で拒否する', async () => {
		const response = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: JSON_READING_HEADERS,
			body: JSON.stringify({ currentPage: 0 }),
		});

		expect(response.status).toBe(400);
		expect(await response.json()).toMatchObject({ code: 'INVALID_CURRENT_PAGE' });
		expect(mocks.readingInfoUpsert).not.toHaveBeenCalled();
	});

	test('PATCHは範囲外のprogressPercentageを400で拒否する', async () => {
		const response = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: JSON_READING_HEADERS,
			body: JSON.stringify({ currentPage: 3, progressPercentage: 101 }),
		});

		expect(response.status).toBe(400);
		expect(await response.json()).toMatchObject({ code: 'INVALID_PROGRESS_PERCENTAGE' });
		expect(mocks.readingInfoUpsert).not.toHaveBeenCalled();
	});

	test('PATCHはJSONオブジェクトでないbodyを400で拒否しDBへ書き込まない', async () => {
		for (const body of [null, [], 'reading', 3, true]) {
			const response = await app.request('/1/reading-info', {
				method: 'PATCH',
				headers: JSON_READING_HEADERS,
				body: JSON.stringify(body),
			});

			expect(response.status).toBe(400);
			expect(await response.json()).toMatchObject({ code: 'INVALID_REQUEST' });
		}

		expect(mocks.readingInfoUpsert).not.toHaveBeenCalled();
		expect(mocks.readingInfoUpdateMany).not.toHaveBeenCalled();
		expect(mocks.readingInfoCreate).not.toHaveBeenCalled();
		expect(mocks.readingInfoUpdate).not.toHaveBeenCalled();
	});

	test('未認証ユーザーはreading-infoを401で拒否される', async () => {
		mocks.getSession.mockResolvedValue(null);

		const response = await app.request('/1/reading-info');

		expect(response.status).toBe(401);
		expect(await response.json()).toMatchObject({ code: 'UNAUTHORIZED' });
	});

	test('別アカウントに紐づくreading-info保存はDBへ到達せず409で拒否される', async () => {
		const response = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: { 'X-BeLib-User-Id': '8', 'Content-Type': 'application/json' },
			body: JSON.stringify({ currentPage: 3, progressPercentage: 25 }),
		});

		expect(response.status).toBe(409);
		expect(await response.json()).toMatchObject({ code: 'READING_SESSION_MISMATCH' });
		expect(mocks.bookFindUnique).toHaveBeenCalled();
		expect(mocks.permissionFindUnique).toHaveBeenCalled();
		expect(mocks.readingInfoUpdateMany).not.toHaveBeenCalled();
	});

	test('GETは別アカウントのreading-info hintを409で拒否し記録に触れない', async () => {
		const response = await app.request('/1/reading-info', { headers: { 'X-BeLib-User-Id': '8' } });

		expect(response.status).toBe(409);
		expect(await response.json()).toMatchObject({ code: 'READING_SESSION_MISMATCH' });
		expect(mocks.readingInfoFindUnique).not.toHaveBeenCalled();
	});

	test('閲覧権限がないユーザーはreading-infoを403で拒否される', async () => {
		mocks.permissionFindUnique.mockResolvedValue(null);

		const response = await app.request('/1/reading-info', { headers: { 'X-BeLib-User-Id': '8' } });

		expect(response.status).toBe(403);
		expect(await response.json()).toMatchObject({ code: 'FORBIDDEN' });
	});
});
