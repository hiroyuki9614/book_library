import { Hono } from 'hono';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import type { PrismaVariables } from '../../lib/prisma.js';

const mocks = vi.hoisted(() => ({
	getSession: vi.fn(),
	userFindFirst: vi.fn(),
	bookFindUnique: vi.fn(),
	permissionFindUnique: vi.fn(),
	getBookFileAccess: vi.fn(),
}));

vi.mock('../../lib/auth.js', () => ({
	auth: { api: { getSession: mocks.getSession } },
}));

vi.mock('../../lib/bookStorage.js', () => ({
	BookStorageNotFoundError: class BookStorageNotFoundError extends Error {},
	getBookFileAccess: mocks.getBookFileAccess,
}));

import booksRoutes from './routes.js';

const app = new Hono<PrismaVariables>();
app.use('*', async (c, next) => {
	c.set('prisma', {
		user: { findFirst: mocks.userFindFirst },
		book: { findUnique: mocks.bookFindUnique },
		roleBookPermission: { findUnique: mocks.permissionFindUnique },
	} as never);
	await next();
});
app.route('/', booksRoutes);

const book = {
	id: 1,
	title: 'R2 PDF',
	authorName: null,
	publishedAt: null,
	publisher: null,
	description: null,
	deletedAt: null,
	pageTurnDirection: 'ltr',
	createdAt: new Date('2026-09-12T00:00:00.000Z'),
	updatedAt: new Date('2026-09-12T00:00:00.000Z'),
	category: { id: 1, name: '技術書' },
	bookFiles: [
		{
			extension: 'pdf',
			mimeType: 'application/pdf',
			fileSize: 10,
			originalFileName: 'r2.pdf',
			fileUrl: 'books/example.pdf',
		},
	],
};

describe('R2 protected file redirect', () => {
	beforeEach(() => {
		vi.resetAllMocks();
		mocks.getSession.mockResolvedValue({ user: { id: '7' } });
		mocks.userFindFirst.mockResolvedValue({ id: 7, roleId: 2 });
		mocks.bookFindUnique.mockResolvedValue(book);
		mocks.permissionFindUnique.mockResolvedValue({ id: 1 });
		mocks.getBookFileAccess.mockResolvedValue({
			kind: 'redirect',
			url: 'https://signed.example/books/example.pdf',
			expiresAt: '2026-09-12T07:00:00.000Z',
		});
	});

	test('認証・権限確認後にR2署名URLへredirectする', async () => {
		const response = await app.request('/1/file');

		expect(response.status).toBe(302);
		expect(response.headers.get('location')).toBe('https://signed.example/books/example.pdf');
		expect(response.headers.get('cache-control')).toBe('private, no-store');
		expect(response.headers.get('x-belib-file-url-expires-at')).toBe('2026-09-12T07:00:00.000Z');
		expect(mocks.permissionFindUnique).toHaveBeenCalled();
		expect(mocks.getBookFileAccess).toHaveBeenCalledWith('books/example.pdf');
	});

	test('未認証なら署名URLを発行しない', async () => {
		mocks.getSession.mockResolvedValue(null);

		const response = await app.request('/1/file');

		expect(response.status).toBe(401);
		expect(mocks.getBookFileAccess).not.toHaveBeenCalled();
	});
});
