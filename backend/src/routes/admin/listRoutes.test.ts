import { Hono } from 'hono';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import type { PrismaVariables } from '../../lib/prisma.js';
import listRoutes from './listRoutes.js';

const mocks = vi.hoisted(() => ({
	getSession: vi.fn(),
	userFindFirst: vi.fn(),
	bookFindMany: vi.fn(),
}));

vi.mock('../../lib/auth.js', () => ({
	auth: { api: { getSession: mocks.getSession } },
}));

const app = new Hono<PrismaVariables>();
app.use('*', async (c, next) => {
	c.set('prisma', {
		user: { findFirst: mocks.userFindFirst },
		book: { findMany: mocks.bookFindMany },
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
	test('DBに保存済みの書籍・file・公開範囲を返す', async () => {
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
				category: { id: 17, name: '技術書' },
				bookFiles: [],
				roleBookPermissions: [{ role: { name: 'admin' } }],
			},
		]);

		const response = await app.request('/books');
		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({
			books: [
				expect.objectContaining({
					id: 10,
					publicationScope: 'all_users',
					file: expect.objectContaining({ extension: 'epub', originalFileName: 'persisted.epub' }),
				}),
				expect.objectContaining({ id: 11, publicationScope: 'admin_only', file: null }),
			],
		});
	});

	test('一般ユーザーはadmin listを403で拒否される', async () => {
		mocks.userFindFirst.mockResolvedValue({ id: 2, role: { name: 'user' } });
		const response = await app.request('/books');
		expect(response.status).toBe(403);
		expect(mocks.bookFindMany).not.toHaveBeenCalled();
	});
});
