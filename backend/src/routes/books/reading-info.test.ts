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
}));

vi.mock('../../lib/auth.js', () => ({
	auth: {
		api: {
			getSession: mocks.getSession,
		},
	},
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
});

describe('reading-info API', () => {
	test('GETは認証済みで権限のあるユーザー自身の現在ページを返す', async () => {
		mocks.readingInfoFindUnique.mockResolvedValue({ currentPosition: '3', readStatus: 'reading' });

		const response = await app.request('/1/reading-info');

		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({ bookId: 1, currentPage: 3, readStatus: 'reading' });
		expect(mocks.readingInfoFindUnique).toHaveBeenCalledWith({
			where: { userId_bookId: { userId: 7, bookId: 1 } },
		});
	});

	test('GETはReadingInfo未作成時に1ページ目を返す', async () => {
		mocks.readingInfoFindUnique.mockResolvedValue(null);

		const response = await app.request('/1/reading-info');

		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({ bookId: 1, currentPage: 1, readStatus: 'unread' });
	});

	test('PATCHはユーザーと書籍単位で現在ページをupsertする', async () => {
		mocks.readingInfoUpsert.mockResolvedValue({ currentPosition: '3', readStatus: 'reading' });

		const response = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ currentPage: 3 }),
		});

		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({ bookId: 1, currentPage: 3, readStatus: 'reading' });
		expect(mocks.readingInfoUpsert).toHaveBeenCalledWith({
			where: { userId_bookId: { userId: 7, bookId: 1 } },
			create: { userId: 7, bookId: 1, currentPosition: '3', readStatus: 'reading' },
			update: { currentPosition: '3', readStatus: 'reading' },
		});
	});

	test('PATCHは正の整数でないcurrentPageを400で拒否する', async () => {
		const response = await app.request('/1/reading-info', {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ currentPage: 0 }),
		});

		expect(response.status).toBe(400);
		expect(await response.json()).toMatchObject({ code: 'INVALID_CURRENT_PAGE' });
		expect(mocks.readingInfoUpsert).not.toHaveBeenCalled();
	});

	test('未認証ユーザーはreading-infoを401で拒否される', async () => {
		mocks.getSession.mockResolvedValue(null);

		const response = await app.request('/1/reading-info');

		expect(response.status).toBe(401);
		expect(await response.json()).toMatchObject({ code: 'UNAUTHORIZED' });
	});

	test('閲覧権限がないユーザーはreading-infoを403で拒否される', async () => {
		mocks.permissionFindUnique.mockResolvedValue(null);

		const response = await app.request('/1/reading-info');

		expect(response.status).toBe(403);
		expect(await response.json()).toMatchObject({ code: 'FORBIDDEN' });
	});
});
