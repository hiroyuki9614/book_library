import { Hono } from 'hono';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import type { PrismaVariables } from '../../lib/prisma.js';
import bookMetadataRoutes from './bookMetadataRoutes.js';

const mocks = vi.hoisted(() => ({
	getSession: vi.fn(),
	userFindFirst: vi.fn(),
	bookFindUnique: vi.fn(),
	bookUpdate: vi.fn(),
	categoryFindUnique: vi.fn(),
}));

vi.mock('../../lib/auth.js', () => ({
	auth: { api: { getSession: mocks.getSession } },
}));

const app = new Hono<PrismaVariables>();
app.use('*', async (c, next) => {
	c.set('prisma', {
		user: { findFirst: mocks.userFindFirst },
		book: { findUnique: mocks.bookFindUnique, update: mocks.bookUpdate },
		category: { findUnique: mocks.categoryFindUnique },
	} as never);
	await next();
});
app.route('/', bookMetadataRoutes);

const validBody = {
	title: ' Updated Book ',
	authorName: ' Author ',
	publisher: ' Publisher ',
	publishedAt: '2026-08-25',
	categoryId: 3,
	pageTurnDirection: 'rtl',
	description: ' Description ',
	publicationScope: 'all_users',
};

beforeEach(() => {
	vi.resetAllMocks();
	mocks.getSession.mockResolvedValue({ user: { id: '1' } });
	mocks.userFindFirst.mockResolvedValue({ id: 1, role: { name: 'admin' } });
	mocks.bookFindUnique.mockResolvedValue({ id: 10, deletedAt: null });
	mocks.categoryFindUnique.mockResolvedValue({ id: 3, isActive: true });
	mocks.bookUpdate.mockResolvedValue({
		id: 10,
		title: 'Updated Book',
		authorName: 'Author',
		publisher: 'Publisher',
		publishedAt: new Date('2026-08-25T00:00:00.000Z'),
		categoryId: 3,
		pageTurnDirection: 'rtl',
		description: 'Description',
		deletedAt: null,
		category: { id: 3, name: '開発' },
	});
});

describe('admin book metadata editing', () => {
	test('書籍情報と公開範囲を更新しReadingInfoには触れない', async () => {
		const response = await app.request('/books/10', {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(validBody),
		});

		expect(response.status).toBe(200);
		expect(mocks.bookUpdate).toHaveBeenCalledWith(expect.objectContaining({
			where: { id: 10 },
			data: expect.objectContaining({
				title: 'Updated Book',
				authorName: 'Author',
				publisher: 'Publisher',
				publishedAt: new Date('2026-08-25T00:00:00.000Z'),
				category: { connect: { id: 3 } },
				pageTurnDirection: 'rtl',
				description: 'Description',
				roleBookPermissions: {
					deleteMany: {},
					create: [
						{ role: { connect: { name: 'admin' } } },
						{ role: { connect: { name: 'user' } } },
					],
				},
			}),
		}));
		expect(await response.json()).toMatchObject({
			id: 10,
			title: 'Updated Book',
			publicationScope: 'all_users',
			category: { id: 3, name: '開発' },
	});
	});

	test('admin_onlyではuser権限を作成しない', async () => {
		const response = await app.request('/books/10', {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ ...validBody, publicationScope: 'admin_only' }),
		});

		expect(response.status).toBe(200);
		expect(mocks.bookUpdate).toHaveBeenCalledWith(expect.objectContaining({
			data: expect.objectContaining({
				roleBookPermissions: {
					deleteMany: {},
					create: [{ role: { connect: { name: 'admin' } } }],
				},
			}),
		}));
	});

	test('削除済み書籍の編集を拒否する', async () => {
		mocks.bookFindUnique.mockResolvedValue({ id: 10, deletedAt: new Date('2026-08-25T00:00:00.000Z') });
		const response = await app.request('/books/10', {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(validBody),
		});
		expect(response.status).toBe(409);
		expect(await response.json()).toMatchObject({ code: 'BOOK_DELETED' });
		expect(mocks.bookUpdate).not.toHaveBeenCalled();
	});

	test('inactiveカテゴリと不正metadataを拒否する', async () => {
		mocks.categoryFindUnique.mockResolvedValue({ id: 3, isActive: false });
		const inactive = await app.request('/books/10', {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(validBody),
		});
		expect(inactive.status).toBe(400);
		expect(await inactive.json()).toMatchObject({ code: 'INVALID_CATEGORY' });

		const invalid = await app.request('/books/10', {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ ...validBody, title: '' }),
		});
		expect(invalid.status).toBe(400);
		expect(await invalid.json()).toMatchObject({ code: 'INVALID_BOOK' });
	});

	test('一般ユーザーは編集APIを利用できない', async () => {
		mocks.userFindFirst.mockResolvedValue({ id: 2, role: { name: 'user' } });
		const response = await app.request('/books/10', {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(validBody),
		});
		expect(response.status).toBe(403);
		expect(mocks.bookUpdate).not.toHaveBeenCalled();
	});
});
