import { Hono } from 'hono';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import type { PrismaVariables } from '../../lib/prisma.js';
import { MAX_CURRENT_POSITION_LENGTH } from './readingProgress.js';
import stateRoutes from './stateRoutes.js';

const mocks = vi.hoisted(() => ({
	getSession: vi.fn(),
	userFindFirst: vi.fn(),
	bookCount: vi.fn(),
	bookFindMany: vi.fn(),
	bookFindUnique: vi.fn(),
	permissionFindUnique: vi.fn(),
	readingInfoFindUnique: vi.fn(),
	readingInfoUpsert: vi.fn(),
	readingInfoUpdateMany: vi.fn(),
	readingInfoCreate: vi.fn(),
	readingInfoUpdate: vi.fn(),
}));

vi.mock('../../lib/auth.js', () => ({
	auth: { api: { getSession: mocks.getSession } },
}));

const app = new Hono<PrismaVariables>();
app.use('*', async (c, next) => {
	c.set('prisma', {
		user: { findFirst: mocks.userFindFirst },
		book: {
			count: mocks.bookCount,
			findMany: mocks.bookFindMany,
			findUnique: mocks.bookFindUnique,
		},
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
app.route('/', stateRoutes);

const book = {
	id: 1,
	title: 'TypeScript実践',
	authorName: 'Author',
	publishedAt: null,
	publisher: null,
	description: null,
	pageTurnDirection: 'ltr',
	createdAt: new Date('2026-08-01T00:00:00.000Z'),
	updatedAt: new Date('2026-08-02T00:00:00.000Z'),
	category: { id: 17, name: '技術書' },
	bookFiles: [{ extension: 'epub', mimeType: 'application/epub+zip', fileSize: 100, originalFileName: 'book.epub' }],
	readingInfos: [{ currentPosition: 'epubcfi(/6/2!/4/1:0)', progressPercentage: 42, readStatus: 'reading' }],
};

beforeEach(() => {
	vi.resetAllMocks();
	mocks.getSession.mockResolvedValue({ user: { id: '7' } });
	mocks.userFindFirst.mockResolvedValue({ id: 7, roleId: 2 });
	mocks.bookFindUnique.mockResolvedValue({ id: 1, deletedAt: null });
	mocks.permissionFindUnique.mockResolvedValue({ id: 1 });
	mocks.readingInfoFindUnique.mockResolvedValue(null);
	mocks.readingInfoUpdateMany.mockResolvedValue({ count: 1 });
});

const READING_HEADERS = { 'X-BeLib-User-Id': '7' };
const JSON_READING_HEADERS = { ...READING_HEADERS, 'Content-Type': 'application/json' };

describe('book state routes', () => {
	test('一覧は現在ユーザーのReadingInfoをreadStatusへ反映する', async () => {
		mocks.bookCount.mockResolvedValue(1);
		mocks.bookFindMany.mockResolvedValue([book]);

		const response = await app.request('/');
		expect(response.status).toBe(200);
		expect(await response.json()).toMatchObject({
			total: 1,
			books: [{ id: 1, readStatus: 'reading', progressPercentage: 42, fileType: 'epub' }],
		});
		expect(mocks.bookFindMany).toHaveBeenCalledWith(expect.objectContaining({
			include: expect.objectContaining({ readingInfos: { where: { userId: 7 }, take: 1 } }),
		}));
	});

	test('一覧はcookie session userと期待userが異なる場合に409を返し書籍検索しない', async () => {
		const response = await app.request('/', { headers: { 'X-BeLib-User-Id': '8' } });

		expect(response.status).toBe(409);
		expect(await response.json()).toMatchObject({
			message: 'Book list request is bound to a different authenticated user',
			code: 'READING_SESSION_MISMATCH',
		});
		expect(mocks.userFindFirst).toHaveBeenCalledWith({
			where: { id: 7 },
			select: { id: true, roleId: true },
		});
		expect(mocks.bookCount).not.toHaveBeenCalled();
		expect(mocks.bookFindMany).not.toHaveBeenCalled();
	});

	test('期待user headerがあっても未認証なら401を返す', async () => {
		mocks.getSession.mockResolvedValue(null);

		const response = await app.request('/', { headers: READING_HEADERS });

		expect(response.status).toBe(401);
		expect(await response.json()).toMatchObject({ code: 'UNAUTHORIZED' });
		expect(mocks.bookCount).not.toHaveBeenCalled();
		expect(mocks.bookFindMany).not.toHaveBeenCalled();
	});

	test('一覧はcookie session userと一致した期待userに許可書籍と本人の進捗を返す', async () => {
		mocks.bookCount.mockResolvedValue(1);
		mocks.bookFindMany.mockResolvedValue([book]);

		const response = await app.request('/', { headers: READING_HEADERS });

		expect(response.status).toBe(200);
		expect(await response.json()).toMatchObject({
			total: 1,
			books: [{ id: 1, title: 'TypeScript実践', readStatus: 'reading', progressPercentage: 42 }],
		});
		expect(mocks.bookFindMany).toHaveBeenCalledWith(expect.objectContaining({
			where: { deletedAt: null, roleBookPermissions: { some: { roleId: 2 } } },
			include: expect.objectContaining({ readingInfos: { where: { userId: 7 }, take: 1 } }),
		}));
	});

	test('一覧は未読0%、読了100%、未計測の読書中をnullへ正規化する', async () => {
		mocks.bookCount.mockResolvedValue(3);
		mocks.bookFindMany.mockResolvedValue([
			{ ...book, id: 1, readingInfos: [{ currentPosition: null, progressPercentage: null, readStatus: 'unread' }] },
			{ ...book, id: 2, readingInfos: [{ currentPosition: '12', progressPercentage: null, readStatus: 'completed' }] },
			{ ...book, id: 3, readingInfos: [{ currentPosition: 'epubcfi(start)', progressPercentage: null, readStatus: 'reading' }] },
		]);

		const response = await app.request('/');

		expect(await response.json()).toMatchObject({
			books: [
				{ id: 1, progressPercentage: 0 },
				{ id: 2, progressPercentage: 100 },
				{ id: 3, progressPercentage: null },
			],
		});
	});

	test('利用停止後でも既存sessionは書籍一覧を利用できる', async () => {
		mocks.userFindFirst.mockResolvedValue({ id: 7, roleId: 2, deletedAt: new Date('2026-08-24T00:00:00.000Z') });
		mocks.bookCount.mockResolvedValue(1);
		mocks.bookFindMany.mockResolvedValue([book]);

		const response = await app.request('/');
		expect(response.status).toBe(200);
		expect(mocks.userFindFirst).toHaveBeenCalledWith({
			where: { id: 7 },
			select: { id: true, roleId: true },
		});
	});

	test('タイトル・著者検索とカテゴリ絞り込みをbackend queryへ適用する', async () => {
		mocks.bookCount.mockResolvedValue(0);
		mocks.bookFindMany.mockResolvedValue([]);

		const response = await app.request('/?q=typescript&categoryId=17');
		expect(response.status).toBe(200);
		const call = mocks.bookFindMany.mock.calls[0][0];
		expect(call.where.categoryId).toBe(17);
		expect(call.where.OR).toEqual([
			{ title: { contains: 'typescript', mode: 'insensitive' } },
			{ authorName: { contains: 'typescript', mode: 'insensitive' } },
		]);
	});

	test('EPUB CFIをcurrentPositionとして保存・復元できる', async () => {
		const cfi = `epubcfi(/6/4!/4/2/${'8/'.repeat(160)}8:0)`;
		expect(cfi.length).toBeGreaterThan(333);
		mocks.readingInfoFindUnique.mockResolvedValue({ currentPosition: cfi, progressPercentage: 42, readStatus: 'reading' });

		const patch = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: JSON_READING_HEADERS,
			body: JSON.stringify({ currentPosition: cfi, progressPercentage: 42, readStatus: 'reading' }),
		});
		expect(patch.status).toBe(200);
		expect(await patch.json()).toEqual({ bookId: 1, currentPosition: cfi, currentPage: 1, progressPercentage: 42, readStatus: 'reading' });
		expect(mocks.readingInfoUpdateMany).toHaveBeenCalledWith({
			where: { userId: 7, bookId: 1, readStatus: { not: 'completed' } },
			data: { currentPosition: cfi, readStatus: 'reading', progressPercentage: 42 },
		});
		expect(mocks.readingInfoUpsert).not.toHaveBeenCalled();

		mocks.readingInfoFindUnique.mockResolvedValue({ currentPosition: cfi, progressPercentage: 42, readStatus: 'reading' });
		const get = await app.request('/1/reading-info', { headers: READING_HEADERS });
		expect(await get.json()).toEqual({ bookId: 1, currentPosition: cfi, currentPage: 1, progressPercentage: 42, readStatus: 'reading' });
	});

	test('currentPositionの上限値を受け入れ、上限超過と非文字列をDB更新前に拒否する', async () => {
		const maxPosition = 'x'.repeat(MAX_CURRENT_POSITION_LENGTH);
		mocks.readingInfoUpsert.mockImplementation(({ create }) => create);

		const accepted = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: JSON_READING_HEADERS,
			body: JSON.stringify({ currentPosition: maxPosition, readStatus: 'completed' }),
		});

		expect(accepted.status).toBe(200);
		expect(mocks.readingInfoUpsert).toHaveBeenCalledWith(expect.objectContaining({
			create: expect.objectContaining({ currentPosition: maxPosition }),
		}));
		expect((await accepted.json()).currentPosition).toBe(maxPosition);

		for (const currentPosition of ['x'.repeat(MAX_CURRENT_POSITION_LENGTH + 1), 7]) {
			const writesBefore = {
				updateMany: mocks.readingInfoUpdateMany.mock.calls.length,
				create: mocks.readingInfoCreate.mock.calls.length,
				update: mocks.readingInfoUpdate.mock.calls.length,
				upsert: mocks.readingInfoUpsert.mock.calls.length,
			};
			const response = await app.request('/1/reading-info', {
				method: 'PATCH',
				headers: JSON_READING_HEADERS,
				body: JSON.stringify({ currentPosition }),
			});
			expect(response.status).toBe(400);
			expect(await response.json()).toMatchObject({ code: 'INVALID_CURRENT_POSITION' });
			expect(mocks.readingInfoUpdateMany).toHaveBeenCalledTimes(writesBefore.updateMany);
			expect(mocks.readingInfoCreate).toHaveBeenCalledTimes(writesBefore.create);
			expect(mocks.readingInfoUpdate).toHaveBeenCalledTimes(writesBefore.update);
			expect(mocks.readingInfoUpsert).toHaveBeenCalledTimes(writesBefore.upsert);
		}
		expect(mocks.readingInfoUpsert).toHaveBeenCalledTimes(1);
	});

	test('権限のあるreading-info GETはヒントなしで互換動作する', async () => {
		mocks.readingInfoFindUnique.mockResolvedValue({ currentPosition: '3', progressPercentage: 25, readStatus: 'reading' });

		const response = await app.request('/1/reading-info');

		expect(response.status).toBe(200);
		expect(await response.json()).toMatchObject({ bookId: 1, currentPage: 3, progressPercentage: 25, readStatus: 'reading' });
	});

	test('reading-info GETは一致する期待user headerを受け入れ本人の記録を読む', async () => {
		mocks.readingInfoFindUnique.mockResolvedValue({ currentPosition: '3', progressPercentage: 25, readStatus: 'reading' });

		const response = await app.request('/1/reading-info', { headers: READING_HEADERS });

		expect(response.status).toBe(200);
		expect(mocks.readingInfoFindUnique).toHaveBeenCalledWith({ where: { userId_bookId: { userId: 7, bookId: 1 } } });
	});

	test('reading-info GETは異なる期待user headerを409で拒否し読書記録に触れない', async () => {
		const response = await app.request('/1/reading-info', { headers: { 'X-BeLib-User-Id': '8' } });

		expect(response.status).toBe(409);
		expect(await response.json()).toMatchObject({ code: 'READING_SESSION_MISMATCH' });
		expect(mocks.bookFindUnique).toHaveBeenCalledWith({ where: { id: 1 }, select: { id: true, deletedAt: true } });
		expect(mocks.permissionFindUnique).toHaveBeenCalledWith({ where: { roleId_bookId: { roleId: 2, bookId: 1 } }, select: { id: true } });
		expect(mocks.readingInfoFindUnique).not.toHaveBeenCalled();
	});

	test('reading-info GETは期待user headerがあっても未認証なら401を返す', async () => {
		mocks.getSession.mockResolvedValue(null);

		const response = await app.request('/1/reading-info', { headers: READING_HEADERS });

		expect(response.status).toBe(401);
		expect(await response.json()).toMatchObject({ code: 'UNAUTHORIZED' });
		expect(mocks.bookFindUnique).not.toHaveBeenCalled();
		expect(mocks.readingInfoFindUnique).not.toHaveBeenCalled();
	});

	test('権限のないreading-infoは不一致ヒントがあっても403を優先する', async () => {
		mocks.permissionFindUnique.mockResolvedValue(null);

		const response = await app.request('/1/reading-info', { headers: { 'X-BeLib-User-Id': '8' } });

		expect(response.status).toBe(403);
		expect(await response.json()).toMatchObject({ code: 'FORBIDDEN' });
		expect(mocks.readingInfoFindUnique).not.toHaveBeenCalled();
	});

	test('PATCHは明示された不一致ヒントを権限確認後に409で拒否する', async () => {
		const response = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: { ...JSON_READING_HEADERS, 'X-BeLib-User-Id': '8' },
			body: JSON.stringify({ currentPage: 3, progressPercentage: 25 }),
		});

		expect(response.status).toBe(409);
		expect(await response.json()).toMatchObject({ code: 'READING_SESSION_MISMATCH' });
		expect(mocks.readingInfoUpdateMany).not.toHaveBeenCalled();
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

	test('PDF最終ページをcompletedとして保存でき、currentPage互換を維持する', async () => {
		mocks.readingInfoUpsert.mockResolvedValue({ currentPosition: '12', progressPercentage: 100, readStatus: 'completed' });

		const response = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: JSON_READING_HEADERS,
			body: JSON.stringify({ currentPage: 12, readStatus: 'completed' }),
		});

		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({ bookId: 1, currentPosition: '12', currentPage: 12, progressPercentage: 100, readStatus: 'completed' });
		expect(mocks.readingInfoUpsert).toHaveBeenCalledWith({
			where: { userId_bookId: { userId: 7, bookId: 1 } },
			create: { userId: 7, bookId: 1, currentPosition: '12', readStatus: 'completed', progressPercentage: 100 },
			update: { currentPosition: '12', readStatus: 'completed', progressPercentage: 100 },
		});
	});

	test('一度completedになった書籍は途中位置へ更新してもcompleted・100%を維持する', async () => {
		mocks.readingInfoUpdateMany
			.mockResolvedValueOnce({ count: 0 })
			.mockResolvedValueOnce({ count: 1 });
		mocks.readingInfoFindUnique
			.mockResolvedValueOnce({ currentPosition: '12', progressPercentage: 100, readStatus: 'completed' })
			.mockResolvedValueOnce({ currentPosition: '3', progressPercentage: 100, readStatus: 'completed' });

		const response = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: JSON_READING_HEADERS,
			body: JSON.stringify({ currentPage: 3, readStatus: 'reading' }),
		});

		expect(response.status).toBe(200);
		expect(mocks.readingInfoFindUnique).toHaveBeenNthCalledWith(1, {
			where: { userId_bookId: { userId: 7, bookId: 1 } },
			select: { currentPosition: true, progressPercentage: true, readStatus: true },
		});
		expect(mocks.readingInfoUpdateMany).toHaveBeenNthCalledWith(1, {
			where: { userId: 7, bookId: 1, readStatus: { not: 'completed' } },
			data: { currentPosition: '3', readStatus: 'reading' },
		});
		expect(mocks.readingInfoUpdateMany).toHaveBeenNthCalledWith(2, {
			where: { userId: 7, bookId: 1, readStatus: 'completed' },
			data: { currentPosition: '3', readStatus: 'completed', progressPercentage: 100 },
		});
		expect(mocks.readingInfoUpdate).not.toHaveBeenCalled();
		expect(await response.json()).toEqual({ bookId: 1, currentPosition: '3', currentPage: 3, progressPercentage: 100, readStatus: 'completed' });
	});
});
