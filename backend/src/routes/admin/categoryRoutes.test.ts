import { Hono } from 'hono';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import type { PrismaVariables } from '../../lib/prisma.js';
import categoryRoutes from './categoryRoutes.js';

const mocks = vi.hoisted(() => ({
	getSession: vi.fn(),
	userFindFirst: vi.fn(),
	categoryFindMany: vi.fn(),
	categoryFindFirst: vi.fn(),
	categoryFindUnique: vi.fn(),
	categoryCreate: vi.fn(),
	categoryUpdate: vi.fn(),
	bookUpdateMany: vi.fn(),
}));

vi.mock('../../lib/auth.js', () => ({
	auth: { api: { getSession: mocks.getSession } },
}));

const app = new Hono<PrismaVariables>();
app.use('*', async (c, next) => {
	const tx = {
		book: { updateMany: mocks.bookUpdateMany },
		category: { update: mocks.categoryUpdate },
	};
	c.set('prisma', {
		user: { findFirst: mocks.userFindFirst },
		category: {
			findMany: mocks.categoryFindMany,
			findFirst: mocks.categoryFindFirst,
			findUnique: mocks.categoryFindUnique,
			create: mocks.categoryCreate,
			update: mocks.categoryUpdate,
		},
		book: { updateMany: mocks.bookUpdateMany },
		$transaction: (callback: (client: typeof tx) => unknown) => callback(tx),
	} as never);
	await next();
});
app.route('/', categoryRoutes);

beforeEach(() => {
	vi.resetAllMocks();
	mocks.getSession.mockResolvedValue({ user: { id: '1' } });
	mocks.userFindFirst.mockResolvedValue({ id: 1, role: { name: 'admin' } });
});

describe('admin category management', () => {
	test('activeカテゴリをdisplayOrder順で返す', async () => {
		mocks.categoryFindMany.mockResolvedValue([
			{ id: 1, name: '未分類', displayOrder: 0, isActive: true },
			{ id: 2, name: '技術書', displayOrder: 1, isActive: true },
		]);

		const response = await app.request('/categories');
		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({
			categories: [
				{ id: 1, name: '未分類', displayOrder: 0, isActive: true },
				{ id: 2, name: '技術書', displayOrder: 1, isActive: true },
			],
		});
		expect(mocks.categoryFindMany).toHaveBeenCalledWith({
			where: { isActive: true },
			orderBy: [{ displayOrder: 'asc' }, { id: 'asc' }],
			select: { id: true, name: true, displayOrder: true, isActive: true },
		});
	});

	test('新規カテゴリを末尾のdisplayOrderで作成する', async () => {
		mocks.categoryFindFirst.mockResolvedValue({ displayOrder: 4 });
		mocks.categoryCreate.mockResolvedValue({ id: 8, name: '科学', displayOrder: 5, isActive: true });

		const response = await app.request('/categories', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ name: ' 科学 ' }),
		});

		expect(response.status).toBe(201);
		expect(mocks.categoryCreate).toHaveBeenCalledWith({
			data: { name: '科学', displayOrder: 5, isActive: true },
			select: { id: true, name: true, displayOrder: true, isActive: true },
		});
	});

	test('通常カテゴリは名称変更できるが未分類は変更できない', async () => {
		mocks.categoryFindUnique.mockResolvedValueOnce({ id: 2, name: '技術書', isActive: true });
		mocks.categoryUpdate.mockResolvedValueOnce({ id: 2, name: '開発', displayOrder: 1, isActive: true });

		const updated = await app.request('/categories/2', {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ name: '開発' }),
		});
		expect(updated.status).toBe(200);
		expect(await updated.json()).toMatchObject({ id: 2, name: '開発' });

		mocks.categoryFindUnique.mockResolvedValueOnce({ id: 1, name: '未分類', isActive: true });
		const protectedResponse = await app.request('/categories/1', {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ name: 'その他' }),
		});
		expect(protectedResponse.status).toBe(409);
		expect(await protectedResponse.json()).toMatchObject({ code: 'PROTECTED_CATEGORY' });
	});

	test('使用中カテゴリ削除時に書籍を未分類へ移してカテゴリをinactiveにする', async () => {
		mocks.categoryFindUnique
			.mockResolvedValueOnce({ id: 2, name: '技術書', isActive: true })
			.mockResolvedValueOnce({ id: 1, isActive: true });
		mocks.bookUpdateMany.mockResolvedValue({ count: 3 });
		mocks.categoryUpdate.mockResolvedValue({ id: 2, name: '技術書', isActive: false });

		const response = await app.request('/categories/2/delete', { method: 'PATCH' });
		expect(response.status).toBe(200);
		expect(mocks.bookUpdateMany).toHaveBeenCalledWith({
			where: { categoryId: 2 },
			data: { categoryId: 1 },
		});
		expect(mocks.categoryUpdate).toHaveBeenCalledWith({
			where: { id: 2 },
			data: { isActive: false },
			select: { id: true, name: true, isActive: true },
		});
		expect(await response.json()).toEqual({
			movedBookCount: 3,
			category: { id: 2, name: '技術書', isActive: false },
		});
	});

	test('未分類の削除と一般ユーザーのカテゴリ管理を拒否する', async () => {
		mocks.categoryFindUnique.mockResolvedValue({ id: 1, name: '未分類', isActive: true });
		const protectedResponse = await app.request('/categories/1/delete', { method: 'PATCH' });
		expect(protectedResponse.status).toBe(409);
		expect(await protectedResponse.json()).toMatchObject({ code: 'PROTECTED_CATEGORY' });

		mocks.userFindFirst.mockResolvedValue({ id: 2, role: { name: 'user' } });
		const forbidden = await app.request('/categories');
		expect(forbidden.status).toBe(403);
	});
});
