import { Hono } from 'hono';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import type { PrismaVariables } from '../../lib/prisma.js';
import listRoutes from './listRoutes.js';

const mocks = vi.hoisted(() => ({
	getSession: vi.fn(),
	userFindFirst: vi.fn(),
	bookFindMany: vi.fn(),
	bookFindUnique: vi.fn(),
	bookUpdate: vi.fn(),
}));

vi.mock('../../lib/auth.js', () => ({
	auth: { api: { getSession: mocks.getSession } },
}));

const app = new Hono<PrismaVariables>();
app.use('*', async (c, next) => {
	c.set('prisma', {
		user: { findFirst: mocks.userFindFirst },
		book: {
			findMany: mocks.bookFindMany,
			findUnique: mocks.bookFindUnique,
			update: mocks.bookUpdate,
		},
	} as never);
	await next();
});
app.route('/', listRoutes);

beforeEach(() => {
	vi.resetAllMocks();
	mocks.getSession.mockResolvedValue({ user: { id: '1' } });
	mocks.userFindFirst.mockResolvedValue({ id: 1, role: { name: 'admin' } });
});

describe('admin persisted book list', () => {
	test('DBに保存済みの通常書籍・file・公開範囲を返す', async () => {
		mocks.bookFindMany.mockResolvedValue([
			{
				id: 10,
				title: 'Persisted EPUB',
				authorName: 'Author',
				publisher: null,
				publishedAt: null,
				categoryId: 17,
				pageTurnDirection: 'ltr',
				description: null,
				deletedAt: null,
				category: { id: 17, name: '技術書' },
				bookFiles: [{ id: 20, extension: 'epub', mimeType: 'application/epub+zip', originalFileName: 'persisted.epub', fileSize: 2048 }],
				roleBookPermissions: [{ role: { name: 'admin' } }, { role: { name: 'user' } }],
			},
			{
				id: 11,
				title: 'Metadata only',
				authorName: null,
				publisher: null,
				publishedAt: null,
				categoryId: 17,
				pageTurnDirection: 'rtl',
				description: null,
				deletedAt: null,
				category: { id: 17, name: '技術書' },
				bookFiles: [],
				roleBookPermissions: [{ role: { name: 'admin' } }],
			},
		]);

		const response = await app.request('/books');
		expect(response.status).toBe(200);
		expect(mocks.bookFindMany).toHaveBeenCalledWith(expect.objectContaining({ where: { deletedAt: null } }));
		expect(await response.json()).toEqual({
			books: [
				expect.objectContaining({
					id: 10,
					deletedAt: null,
					publicationScope: 'all_users',
					file: expect.objectContaining({ extension: 'epub', originalFileName: 'persisted.epub' }),
				}),
				expect.objectContaining({ id: 11, deletedAt: null, publicationScope: 'admin_only', file: null }),
			],
		});
	});

	test('削除済み一覧はdeletedAtがある書籍だけを取得する', async () => {
		const deletedAt = new Date('2026-08-24T10:00:00.000Z');
		mocks.bookFindMany.mockResolvedValue([
			{
				id: 12,
				title: 'Deleted PDF',
				authorName: null,
				publisher: null,
				publishedAt: null,
				categoryId: 17,
				pageTurnDirection: 'ltr',
				description: null,
				deletedAt,
				category: { id: 17, name: '技術書' },
				bookFiles: [{ id: 22, extension: 'pdf', mimeType: 'application/pdf', originalFileName: 'deleted.pdf', fileSize: 4096 }],
				roleBookPermissions: [{ role: { name: 'admin' } }],
			},
		]);

		const response = await app.request('/books?state=deleted');
		expect(response.status).toBe(200);
		expect(mocks.bookFindMany).toHaveBeenCalledWith(expect.objectContaining({ where: { deletedAt: { not: null } } }));
		expect(await response.json()).toEqual({
			books: [expect.objectContaining({ id: 12, deletedAt: deletedAt.toISOString(), file: expect.objectContaining({ originalFileName: 'deleted.pdf' }) })],
		});
	});

	test('論理削除はdeletedAtだけを更新する', async () => {
		mocks.bookFindUnique.mockResolvedValue({ id: 10, deletedAt: null });
		mocks.bookUpdate.mockImplementation(async ({ data }: { data: { deletedAt: Date } }) => ({ id: 10, deletedAt: data.deletedAt }));

		const response = await app.request('/books/10/delete', { method: 'PATCH' });
		expect(response.status).toBe(200);
		expect(mocks.bookUpdate).toHaveBeenCalledWith({
			where: { id: 10 },
			data: { deletedAt: expect.any(Date) },
			select: { id: true, deletedAt: true },
		});
		expect(await response.json()).toMatchObject({ id: 10, deletedAt: expect.any(String) });
	});

	test('削除済み書籍を復元するとdeletedAtだけnullへ戻す', async () => {
		mocks.bookFindUnique.mockResolvedValue({ id: 10, deletedAt: new Date('2026-08-24T10:00:00.000Z') });
		mocks.bookUpdate.mockResolvedValue({ id: 10, deletedAt: null });

		const response = await app.request('/books/10/restore', { method: 'PATCH' });
		expect(response.status).toBe(200);
		expect(mocks.bookUpdate).toHaveBeenCalledWith({
			where: { id: 10 },
			data: { deletedAt: null },
			select: { id: true, deletedAt: true },
		});
		expect(await response.json()).toEqual({ id: 10, deletedAt: null });
	});

	test('一般ユーザーはadmin listと削除操作を403で拒否される', async () => {
		mocks.userFindFirst.mockResolvedValue({ id: 2, role: { name: 'user' } });
		const listResponse = await app.request('/books');
		const deleteResponse = await app.request('/books/10/delete', { method: 'PATCH' });
		expect(listResponse.status).toBe(403);
		expect(deleteResponse.status).toBe(403);
		expect(mocks.bookFindMany).not.toHaveBeenCalled();
		expect(mocks.bookUpdate).not.toHaveBeenCalled();
	});
});
