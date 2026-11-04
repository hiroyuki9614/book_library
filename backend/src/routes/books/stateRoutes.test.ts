import { Hono } from 'hono';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import type { PrismaVariables } from '../../lib/prisma.js';
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
		const cfi = 'epubcfi(/6/4!/4/2/8:0)';
		mocks.readingInfoFindUnique.mockResolvedValue({ currentPosition: cfi, progressPercentage: 42, readStatus: 'reading' });

		const patch = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ currentPosition: cfi, progressPercentage: 42, readStatus: 'reading' }),
		});
		expect(patch.status).toBe(200);
		expect(await patch.json()).toEqual({ bookId: 1, currentPosition: cfi, currentPage: 1, progressPercentage: 42, readStatus: 'reading' });
		expect(mocks.readingInfoUpdateMany).toHaveBeenCalledWith({
			where: { userId_bookId: { userId: 7, bookId: 1 }, readStatus: { not: 'completed' } },
			data: { currentPosition: cfi, readStatus: 'reading', progressPercentage: 42 },
		});
		expect(mocks.readingInfoUpsert).not.toHaveBeenCalled();

		mocks.readingInfoFindUnique.mockResolvedValue({ currentPosition: cfi, progressPercentage: 42, readStatus: 'reading' });
		const get = await app.request('/1/reading-info');
		expect(await get.json()).toEqual({ bookId: 1, currentPosition: cfi, currentPage: 1, progressPercentage: 42, readStatus: 'reading' });
	});

	test('PDF最終ページをcompletedとして保存でき、currentPage互換を維持する', async () => {
		mocks.readingInfoUpsert.mockResolvedValue({ currentPosition: '12', progressPercentage: 100, readStatus: 'completed' });

		const response = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
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

	test('一度completedになった書籍は途中位置を保存してもreadingへ戻らない', async () => {
		mocks.readingInfoFindUnique.mockResolvedValue({ readStatus: 'completed' });
		mocks.readingInfoUpdateMany.mockResolvedValue({ count: 0 });

		const response = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ currentPage: 3, readStatus: 'reading' }),
		});

		expect(response.status).toBe(200);
		expect(mocks.readingInfoFindUnique).toHaveBeenCalledWith({
			where: { userId_bookId: { userId: 7, bookId: 1 } },
			select: { currentPosition: true, progressPercentage: true, readStatus: true },
		});
		expect(mocks.readingInfoUpdateMany).toHaveBeenCalledWith({
			where: { userId_bookId: { userId: 7, bookId: 1 }, readStatus: { not: 'completed' } },
			data: { currentPosition: '3', readStatus: 'reading' },
		});
		expect(mocks.readingInfoUpdate).not.toHaveBeenCalled();
		expect(await response.json()).toEqual({ bookId: 1, currentPosition: null, currentPage: 1, progressPercentage: 100, readStatus: 'completed' });
	});
});
