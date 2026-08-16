import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Hono } from 'hono';
import { afterAll, beforeAll, beforeEach, describe, expect, test, vi } from 'vitest';
import type { PrismaVariables } from '../../lib/prisma.js';
import booksRoutes from './routes.js';

const mocks = vi.hoisted(() => ({
	getSession: vi.fn(),
	userFindFirst: vi.fn(),
	bookFindUnique: vi.fn(),
	permissionFindUnique: vi.fn(),
	readingInfoFindUnique: vi.fn(),
	bookFindMany: vi.fn(),
	bookCount: vi.fn(),
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
		book: {
			findUnique: mocks.bookFindUnique,
			findMany: mocks.bookFindMany,
			count: mocks.bookCount,
		},
		roleBookPermission: { findUnique: mocks.permissionFindUnique },
		readingInfo: { findUnique: mocks.readingInfoFindUnique },
	} as never);
	await next();
});
app.route('/', booksRoutes);

let storageRoot: string;

const book = {
	id: 1,
	title: 'Phase 3 PDF',
	authorName: 'BeLib',
	publishedAt: null,
	publisher: null,
	description: null,
	deletedAt: null,
	pageTurnDirection: 'ltr',
	createdAt: new Date('2026-08-01T00:00:00.000Z'),
	updatedAt: new Date('2026-08-01T00:00:00.000Z'),
	category: { id: 1, name: '技術書' },
	bookFiles: [
		{
			extension: 'pdf',
			mimeType: 'application/pdf',
			fileSize: 14,
			originalFileName: 'phase-3.pdf',
			fileUrl: 'phase-3.pdf',
		},
	],
};

beforeAll(async () => {
	storageRoot = await mkdtemp(join(tmpdir(), 'belib-phase3-'));
	await writeFile(join(storageRoot, 'phase-3.pdf'), '%PDF-1.7 phase 3');
	process.env.BOOK_FILE_STORAGE_ROOT = storageRoot;
});

beforeEach(() => {
	vi.resetAllMocks();
	mocks.getSession.mockResolvedValue({ user: { id: '7' } });
	mocks.userFindFirst.mockResolvedValue({ id: 7, roleId: 2 });
	mocks.bookFindUnique.mockResolvedValue(book);
	mocks.permissionFindUnique.mockResolvedValue({ id: 1 });
	mocks.readingInfoFindUnique.mockResolvedValue({ readStatus: 'unread' });
});

describe('protected book viewing APIs', () => {
	test('RoleBookPermissionがある一般ユーザーの一覧に書籍が表示される', async () => {
		mocks.bookCount.mockResolvedValue(1);
		mocks.bookFindMany.mockResolvedValue([book]);

		const response = await app.request('/');

		expect(response.status).toBe(200);
		expect(await response.json()).toMatchObject({
			total: 1,
			books: [{ id: 1, title: 'Phase 3 PDF', hasFile: true, fileType: 'pdf' }],
		});
		expect(mocks.bookFindMany).toHaveBeenCalledWith(
			expect.objectContaining({
				where: { deletedAt: null, roleBookPermissions: { some: { roleId: 2 } } },
			}),
		);
	});

	test('一覧は同一書籍でもrequesting user自身のReadingInfoだけを返す', async () => {
		const statusByUserId = new Map([
			[7, 'reading'],
			[8, 'completed'],
		]);
		const queriedUserIds: number[] = [];
		mocks.bookCount.mockResolvedValue(1);
		mocks.bookFindMany.mockImplementation(async (args: { include?: { readingInfos?: { where?: { userId?: number } } } }) => {
			const userId = args.include?.readingInfos?.where?.userId;
			if (userId === undefined) {
				return [book];
			}

			queriedUserIds.push(userId);
			return [{ ...book, readingInfos: [{ readStatus: statusByUserId.get(userId) }] }];
		});

		const userAResponse = await app.request('/');
		mocks.getSession.mockResolvedValue({ user: { id: '8' } });
		mocks.userFindFirst.mockResolvedValue({ id: 8, roleId: 2 });
		const userBResponse = await app.request('/');

		expect(userAResponse.status).toBe(200);
		expect(userBResponse.status).toBe(200);
		expect((await userAResponse.json()).books[0].readStatus).toBe('reading');
		expect((await userBResponse.json()).books[0].readStatus).toBe('completed');
		expect(queriedUserIds).toEqual([7, 8]);
		expect(mocks.bookFindMany).toHaveBeenLastCalledWith(
			expect.objectContaining({
				include: expect.objectContaining({
					readingInfos: { where: { userId: 8 }, select: { readStatus: true } },
				}),
			}),
		);
	});

	test('一覧はReadingInfoがないユーザーをunreadとして返す', async () => {
		mocks.bookCount.mockResolvedValue(1);
		mocks.bookFindMany.mockResolvedValue([{ ...book, readingInfos: [] }]);

		const response = await app.request('/');

		expect(response.status).toBe(200);
		expect((await response.json()).books[0].readStatus).toBe('unread');
	});

	test('RoleBookPermissionがない一般ユーザーの一覧には書籍が表示されない', async () => {
		mocks.bookCount.mockResolvedValue(0);
		mocks.bookFindMany.mockResolvedValue([]);

		const response = await app.request('/');

		expect(response.status).toBe(200);
		expect(await response.json()).toMatchObject({ total: 0, books: [] });
		expect(mocks.bookFindMany).toHaveBeenCalledWith(
			expect.objectContaining({
				where: { deletedAt: null, roleBookPermissions: { some: { roleId: 2 } } },
			}),
		);
	});

	test('認証済みで権限のあるユーザーは書籍詳細を取得できる', async () => {
		const response = await app.request('/1');

		expect(response.status).toBe(200);
		expect(await response.json()).toMatchObject({
			id: 1,
			title: 'Phase 3 PDF',
			hasFile: true,
			readStatus: 'unread',
		});
	});

	test('詳細はrequesting user自身のReadingInfoだけを返す', async () => {
		mocks.readingInfoFindUnique.mockImplementation(async ({ where }: { where: { userId_bookId: { userId: number } } }) => ({
			readStatus: where.userId_bookId.userId === 7 ? 'reading' : 'completed',
		}));

		const userAResponse = await app.request('/1');
		mocks.getSession.mockResolvedValue({ user: { id: '8' } });
		mocks.userFindFirst.mockResolvedValue({ id: 8, roleId: 2 });
		const userBResponse = await app.request('/1');

		expect((await userAResponse.json()).readStatus).toBe('reading');
		expect((await userBResponse.json()).readStatus).toBe('completed');
		expect(mocks.readingInfoFindUnique).toHaveBeenLastCalledWith({
			where: { userId_bookId: { userId: 8, bookId: 1 } },
		});
	});

	test('未認証ユーザーは書籍詳細とファイルを401で拒否される', async () => {
		mocks.getSession.mockResolvedValue(null);

		const detailResponse = await app.request('/1');
		const fileResponse = await app.request('/1/file');

		expect(detailResponse.status).toBe(401);
		expect(fileResponse.status).toBe(401);
	});

	test('RoleBookPermissionがないユーザーは書籍ファイルを403で拒否される', async () => {
		mocks.permissionFindUnique.mockResolvedValue(null);

		const response = await app.request('/1/file');

		expect(response.status).toBe(403);
		expect(await response.json()).toMatchObject({ code: 'FORBIDDEN' });
	});

	test('存在しない書籍は404を返す', async () => {
		mocks.bookFindUnique.mockResolvedValue(null);

		const response = await app.request('/999/file');

		expect(response.status).toBe(404);
		expect(await response.json()).toMatchObject({ code: 'BOOK_NOT_FOUND' });
	});

	test('書籍にPDFファイルがない場合は404を返す', async () => {
		mocks.bookFindUnique.mockResolvedValue({ ...book, bookFiles: [] });

		const response = await app.request('/1/file');

		expect(response.status).toBe(404);
		expect(await response.json()).toMatchObject({ code: 'FILE_NOT_FOUND' });
	});

	test('権限のあるPDFはapplication/pdfとしてストリーム返却される', async () => {
		const response = await app.request('/1/file');

		expect(response.status).toBe(200);
		expect(response.headers.get('content-type')).toBe('application/pdf');
		expect(await response.text()).toBe('%PDF-1.7 phase 3');
	});

	test('storage root外へ出るfileUrlは404として拒否される', async () => {
		mocks.bookFindUnique.mockResolvedValue({ ...book, bookFiles: [{ ...book.bookFiles[0], fileUrl: '../phase-3.pdf' }] });

		const response = await app.request('/1/file');

		expect(response.status).toBe(404);
		expect(await response.json()).toMatchObject({ code: 'FILE_NOT_FOUND' });
	});
});

afterAll(async () => {
	await rm(storageRoot, { recursive: true, force: true });
});
