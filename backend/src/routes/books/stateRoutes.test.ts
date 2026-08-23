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
	readingInfos: [{ currentPosition: 'epubcfi(/6/2!/4/1:0)', readStatus: 'reading' }],
};

beforeEach(() => {
	vi.resetAllMocks();
	mocks.getSession.mockResolvedValue({ user: { id: '7' } });
	mocks.userFindFirst.mockResolvedValue({ id: 7, roleId: 2 });
	mocks.bookFindUnique.mockResolvedValue({ id: 1, deletedAt: null });
	mocks.permissionFindUnique.mockResolvedValue({ id: 1 });
});

describe('book state routes', () => {
	test('一覧は現在ユーザーのReadingInfoをreadStatusへ反映する', async () => {
		mocks.bookCount.mockResolvedValue(1);
		mocks.bookFindMany.mockResolvedValue([book]);

		const response = await app.request('/');
		expect(response.status).toBe(200);
		expect(await response.json()).toMatchObject({
			total: 1,
			books: [{ id: 1, readStatus: 'reading', fileType: 'epub' }],
		});
		expect(mocks.bookFindMany).toHaveBeenCalledWith(expect.objectContaining({
			include: expect.objectContaining({ readingInfos: { where: { userId: 7 }, take: 1 } }),
		}));
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
		mocks.readingInfoUpsert.mockResolvedValue({ currentPosition: cfi, readStatus: 'reading' });

		const patch = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ currentPosition: cfi, readStatus: 'reading' }),
		});
		expect(patch.status).toBe(200);
		expect(await patch.json()).toEqual({ bookId: 1, currentPosition: cfi, currentPage: 1, readStatus: 'reading' });
		expect(mocks.readingInfoUpsert).toHaveBeenCalledWith(expect.objectContaining({
			create: expect.objectContaining({ currentPosition: cfi, readStatus: 'reading' }),
			update: { currentPosition: cfi, readStatus: 'reading' },
		}));

		mocks.readingInfoFindUnique.mockResolvedValue({ currentPosition: cfi, readStatus: 'reading' });
		const get = await app.request('/1/reading-info');
		expect(await get.json()).toEqual({ bookId: 1, currentPosition: cfi, currentPage: 1, readStatus: 'reading' });
	});

	test('PDF最終ページをcompletedとして保存でき、currentPage互換を維持する', async () => {
		mocks.readingInfoUpsert.mockResolvedValue({ currentPosition: '12', readStatus: 'completed' });

		const response = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ currentPage: 12, readStatus: 'completed' }),
		});

		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({ bookId: 1, currentPosition: '12', currentPage: 12, readStatus: 'completed' });
	});
});
