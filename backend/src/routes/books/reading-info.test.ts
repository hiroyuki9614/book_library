import { Hono } from 'hono';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import type { PrismaVariables } from '../../lib/prisma.js';
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

		const response = await app.request('/1/reading-info', { headers: READING_HEADERS });

		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({ bookId: 1, currentPosition: '3', currentPage: 3, progressPercentage: 25, readStatus: 'reading' });
		expect(mocks.readingInfoFindUnique).toHaveBeenCalledWith({
			where: { userId_bookId: { userId: 7, bookId: 1 } },
		});
	});

	test('GETはReadingInfo未作成時に1ページ目を返す', async () => {
		mocks.readingInfoFindUnique.mockResolvedValue(null);

		const response = await app.request('/1/reading-info', { headers: READING_HEADERS });

		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({ bookId: 1, currentPosition: null, currentPage: 1, progressPercentage: 0, readStatus: 'unread' });
	});

	test('PATCHはユーザーと書籍単位でcompletedを降格させずに更新する', async () => {

		const response = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: JSON_READING_HEADERS,
			body: JSON.stringify({ currentPage: 3, progressPercentage: 25 }),
		});

		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({ bookId: 1, currentPosition: '3', currentPage: 3, progressPercentage: 25, readStatus: 'reading' });
		expect(mocks.readingInfoUpdateMany).toHaveBeenCalledWith({
			where: { userId_bookId: { userId: 7, bookId: 1 }, readStatus: { not: 'completed' } },
			data: { currentPosition: '3', readStatus: 'reading', progressPercentage: 25 },
		});
		expect(mocks.readingInfoUpsert).not.toHaveBeenCalled();
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
		expect(mocks.bookFindUnique).not.toHaveBeenCalled();
		expect(mocks.readingInfoUpdateMany).not.toHaveBeenCalled();
	});

	test('閲覧権限がないユーザーはreading-infoを403で拒否される', async () => {
		mocks.permissionFindUnique.mockResolvedValue(null);

		const response = await app.request('/1/reading-info', { headers: READING_HEADERS });

		expect(response.status).toBe(403);
		expect(await response.json()).toMatchObject({ code: 'FORBIDDEN' });
	});
});
