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
}));

vi.mock('../../lib/auth.js', () => ({
	auth: { api: { getSession: mocks.getSession } },
}));

const app = new Hono<PrismaVariables>();
app.use('*', async (c, next) => {
	c.set('prisma', {
		user: { findFirst: mocks.userFindFirst },
		book: { findUnique: mocks.bookFindUnique },
		roleBookPermission: { findUnique: mocks.permissionFindUnique },
		readingInfo: { findUnique: mocks.readingInfoFindUnique },
	} as never);
	await next();
});
app.route('/', booksRoutes);

let storageRoot: string;

const baseBook = {
	id: 2,
	title: 'Protected EPUB',
	authorName: 'BeLib',
	publishedAt: null,
	publisher: null,
	description: null,
	deletedAt: null,
	pageTurnDirection: 'ltr',
	createdAt: new Date('2026-08-23T00:00:00.000Z'),
	updatedAt: new Date('2026-08-23T00:00:00.000Z'),
	category: { id: 1, name: '技術書' },
};

const epubFile = {
	extension: 'epub',
	mimeType: 'application/epub+zip',
	fileSize: 12,
	originalFileName: 'protected.epub',
	fileUrl: 'protected.epub',
};

const pdfFile = {
	extension: 'pdf',
	mimeType: 'application/pdf',
	fileSize: 14,
	originalFileName: 'fallback.pdf',
	fileUrl: 'fallback.pdf',
};

beforeAll(async () => {
	storageRoot = await mkdtemp(join(tmpdir(), 'belib-epub-'));
	await writeFile(join(storageRoot, 'protected.epub'), 'PK EPUB DATA');
	await writeFile(join(storageRoot, 'fallback.pdf'), '%PDF fallback');
	process.env.BOOK_FILE_STORAGE_ROOT = storageRoot;
});

beforeEach(() => {
	vi.resetAllMocks();
	mocks.getSession.mockResolvedValue({ user: { id: '7' } });
	mocks.userFindFirst.mockResolvedValue({ id: 7, roleId: 2 });
	mocks.permissionFindUnique.mockResolvedValue({ id: 1 });
	mocks.readingInfoFindUnique.mockResolvedValue({ readStatus: 'unread' });
});

describe('protected EPUB viewing API', () => {
	test('権限のあるEPUBをapplication/epub+zipとして返す', async () => {
		mocks.bookFindUnique.mockResolvedValue({ ...baseBook, bookFiles: [epubFile] });

		const response = await app.request('/2/file');

		expect(response.status).toBe(200);
		expect(response.headers.get('content-type')).toBe('application/epub+zip');
		expect(await response.text()).toBe('PK EPUB DATA');
	});

	test('EPUBとPDFが両方ある場合はEPUBを閲覧対象として優先する', async () => {
		mocks.bookFindUnique.mockResolvedValue({ ...baseBook, bookFiles: [pdfFile, epubFile] });

		const detailResponse = await app.request('/2');
		const fileResponse = await app.request('/2/file');

		expect(detailResponse.status).toBe(200);
		expect(await detailResponse.json()).toMatchObject({ fileType: 'epub' });
		expect(fileResponse.status).toBe(200);
		expect(fileResponse.headers.get('content-type')).toBe('application/epub+zip');
		expect(await fileResponse.text()).toBe('PK EPUB DATA');
	});
});

afterAll(async () => {
	await rm(storageRoot, { recursive: true, force: true });
});
