import { createHash } from 'node:crypto';
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, beforeEach, describe, expect, test, vi } from 'vitest';
import { MAX_BOOK_FILE_SIZE, validateBookFileUploadMetadata } from './routes.js';

const mocks = vi.hoisted(() => ({
	getSession: vi.fn(),
	userFindFirst: vi.fn(),
	categoryFindUnique: vi.fn(),
	categoryFindMany: vi.fn(),
	bookCreate: vi.fn(),
	bookFindUnique: vi.fn(),
	bookFileCreate: vi.fn(),
	bookFileFindFirst: vi.fn(),
	bookFileFindUnique: vi.fn(),
}));

vi.mock('@hono/node-server', () => ({
	serve: vi.fn(),
}));

vi.mock('../../lib/auth.js', () => ({
	auth: {
		api: {
			getSession: mocks.getSession,
		},
	},
}));

vi.mock('../../lib/prisma.js', () => ({
	prisma: {},
	default: async (c: { set: (key: string, value: unknown) => void }, next: () => Promise<void>) => {
		c.set('prisma', {
			user: { findFirst: mocks.userFindFirst },
			category: { findUnique: mocks.categoryFindUnique, findMany: mocks.categoryFindMany },
			book: { create: mocks.bookCreate, findUnique: mocks.bookFindUnique },
			bookFile: {
				create: mocks.bookFileCreate,
				findFirst: mocks.bookFileFindFirst,
				findUnique: mocks.bookFileFindUnique,
			},
		});
		await next();
	},
}));

let app: (typeof import('../../index.js'))['app'];
let storageRoot: string;

function createMinimalEpubBytes() {
	const fileName = Buffer.from('mimetype');
	const mimetype = Buffer.from('application/epub+zip');
	const header = Buffer.alloc(30);
	header.writeUInt32LE(0x04034b50, 0);
	header.writeUInt16LE(20, 4);
	header.writeUInt16LE(0, 6);
	header.writeUInt16LE(0, 8);
	header.writeUInt32LE(mimetype.length, 18);
	header.writeUInt32LE(mimetype.length, 22);
	header.writeUInt16LE(fileName.length, 26);
	header.writeUInt16LE(0, 28);
	return Buffer.concat([header, fileName, mimetype]);
}

function createRegistrationForm(file: Blob, fileName: string, publicationScope: 'all_users' | 'admin_only' = 'all_users') {
	const formData = new FormData();
	formData.append('title', 'Registered Book');
	formData.append('authorName', 'Author');
	formData.append('publisher', 'Publisher');
	formData.append('publishedAt', '2026-08-23');
	formData.append('categoryId', '17');
	formData.append('pageTurnDirection', 'ltr');
	formData.append('description', 'Description');
	formData.append('publicationScope', publicationScope);
	formData.append('file', file, fileName);
	return formData;
}

beforeAll(async () => {
	storageRoot = await mkdtemp(join(tmpdir(), 'belib-admin-registration-'));
	process.env.BOOK_FILE_STORAGE_ROOT = storageRoot;
	({ app } = await import('../../index.js'));
});

afterAll(async () => {
	await rm(storageRoot, { recursive: true, force: true });
});

beforeEach(() => {
	vi.resetAllMocks();
	mocks.getSession.mockResolvedValue({ user: { id: '1' } });
	mocks.userFindFirst.mockResolvedValue({ id: 1, role: { name: 'admin' } });
	mocks.categoryFindUnique.mockResolvedValue({ id: 17, isActive: true });
	mocks.bookFileFindUnique.mockResolvedValue(null);
	mocks.bookFileFindFirst.mockResolvedValue(null);
});

describe('admin book registration', () => {
	test('activeカテゴリを管理画面用APIから取得できる', async () => {
		mocks.categoryFindMany.mockResolvedValue([{ id: 17, name: '技術書' }]);

		const response = await app.request('/api/v1/admin/categories');

		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({ categories: [{ id: 17, name: '技術書' }] });
	});

	test('未認証ユーザーはfull registrationを401で拒否する', async () => {
		mocks.getSession.mockResolvedValue(null);
		const formData = createRegistrationForm(new Blob(['%PDF-1.7'], { type: 'application/pdf' }), 'sample.pdf');

		const response = await app.request('/api/v1/admin/book-registrations', { method: 'POST', body: formData });

		expect(response.status).toBe(401);
		expect(mocks.bookCreate).not.toHaveBeenCalled();
	});

	test('PDFをmetadataと同時にprotected storageとBookFileへ登録できる', async () => {
		mocks.bookCreate.mockResolvedValue({
			id: 42,
			title: 'Registered Book',
			authorName: 'Author',
			publisher: 'Publisher',
			publishedAt: new Date('2026-08-23T00:00:00.000Z'),
			categoryId: 17,
			pageTurnDirection: 'ltr',
			description: 'Description',
			category: { id: 17, name: '技術書' },
			bookFiles: [{ id: 100, extension: 'pdf', mimeType: 'application/pdf', originalFileName: 'sample.pdf', fileSize: 8 }],
		});
		const formData = createRegistrationForm(new Blob(['%PDF-1.7'], { type: 'application/pdf' }), 'sample.pdf');

		const response = await app.request('/api/v1/admin/book-registrations', { method: 'POST', body: formData });

		expect(response.status).toBe(201);
		expect(await response.json()).toMatchObject({
			id: 42,
			publicationScope: 'all_users',
			file: { extension: 'pdf', mimeType: 'application/pdf', originalFileName: 'sample.pdf' },
		});
		const createInput = mocks.bookCreate.mock.calls[0][0];
		expect(createInput.data.roleBookPermissions.create).toEqual([
			{ role: { connect: { name: 'admin' } } },
			{ role: { connect: { name: 'user' } } },
		]);
		const storedFile = createInput.data.bookFiles.create[0];
		expect(storedFile.storedFileName).toMatch(/^[\da-f]{8}-[\da-f-]+\.pdf$/);
		expect(await readFile(join(storageRoot, storedFile.storedFileName))).toEqual(Buffer.from('%PDF-1.7'));
	});

	test('EPUBを内容検証してmetadataと同時登録できる', async () => {
		const epubBytes = createMinimalEpubBytes();
		mocks.bookCreate.mockResolvedValue({
			id: 43,
			title: 'Registered Book',
			authorName: 'Author',
			publisher: 'Publisher',
			publishedAt: new Date('2026-08-23T00:00:00.000Z'),
			categoryId: 17,
			pageTurnDirection: 'ltr',
			description: 'Description',
			category: { id: 17, name: '技術書' },
			bookFiles: [{ id: 101, extension: 'epub', mimeType: 'application/epub+zip', originalFileName: 'sample.epub', fileSize: epubBytes.length }],
		});
		const formData = createRegistrationForm(new Blob([new Uint8Array(epubBytes)], { type: 'application/epub+zip' }), 'sample.epub', 'admin_only');

		const response = await app.request('/api/v1/admin/book-registrations', { method: 'POST', body: formData });

		expect(response.status).toBe(201);
		expect(await response.json()).toMatchObject({
			id: 43,
			publicationScope: 'admin_only',
			file: { extension: 'epub', mimeType: 'application/epub+zip', originalFileName: 'sample.epub' },
		});
		const createInput = mocks.bookCreate.mock.calls[0][0];
		expect(createInput.data.roleBookPermissions.create).toEqual([{ role: { connect: { name: 'admin' } } }]);
		const storedFile = createInput.data.bookFiles.create[0];
		expect(storedFile.storedFileName).toMatch(/^[\da-f]{8}-[\da-f-]+\.epub$/);
		expect(await readFile(join(storageRoot, storedFile.storedFileName))).toEqual(epubBytes);
	});

	test('拡張子だけEPUBの不正ZIPは登録しない', async () => {
		const formData = createRegistrationForm(new Blob(['not an epub'], { type: 'application/epub+zip' }), 'broken.epub');

		const response = await app.request('/api/v1/admin/book-registrations', { method: 'POST', body: formData });

		expect(response.status).toBe(400);
		expect(await response.json()).toMatchObject({ code: 'INVALID_BOOK_FILE' });
		expect(mocks.bookCreate).not.toHaveBeenCalled();
	});

	test('公開範囲未選択では書籍を作成しない', async () => {
		const formData = createRegistrationForm(new Blob(['%PDF-1.7'], { type: 'application/pdf' }), 'sample.pdf');
		formData.delete('publicationScope');

		const response = await app.request('/api/v1/admin/book-registrations', { method: 'POST', body: formData });

		expect(response.status).toBe(400);
		expect(await response.json()).toMatchObject({ code: 'INVALID_BOOK' });
		expect(mocks.bookCreate).not.toHaveBeenCalled();
	});

	test('同一hashのファイルは論理削除本を含め重複登録しない', async () => {
		const bytes = Buffer.from('%PDF-1.7');
		mocks.bookFileFindUnique.mockResolvedValue({ id: 99 });
		const formData = createRegistrationForm(new Blob([bytes], { type: 'application/pdf' }), 'duplicate.pdf');

		const response = await app.request('/api/v1/admin/book-registrations', { method: 'POST', body: formData });

		expect(response.status).toBe(409);
		expect(await response.json()).toMatchObject({ code: 'DUPLICATE_FILE' });
		expect(mocks.bookFileFindUnique).toHaveBeenCalledWith({
			where: { fileHash: createHash('sha256').update(bytes).digest('hex') },
			select: { id: true },
		});
		expect(mocks.bookCreate).not.toHaveBeenCalled();
	});

	test('DB登録失敗時は先に保存したbook fileをcleanupする', async () => {
		mocks.bookCreate.mockRejectedValue(new Error('database unavailable'));
		const before = await readdir(storageRoot);
		const formData = createRegistrationForm(new Blob(['%PDF-1.7'], { type: 'application/pdf' }), 'cleanup.pdf');

		const response = await app.request('/api/v1/admin/book-registrations', { method: 'POST', body: formData });

		expect(response.status).toBe(500);
		expect(await readdir(storageRoot)).toEqual(before);
	});

	test('200MB超過は巨大fixtureなしでmetadata段階で拒否する', () => {
		const result = validateBookFileUploadMetadata({
			name: 'large.epub',
			type: 'application/epub+zip',
			size: MAX_BOOK_FILE_SIZE + 1,
			arrayBuffer: async () => new ArrayBuffer(0),
		});

		expect(result).toMatchObject({ valid: false, status: 413, code: 'FILE_TOO_LARGE' });
	});

	test('既存bookへのfile追加APIもEPUBを受け付け、1冊1fileを強制する', async () => {
		const epubBytes = createMinimalEpubBytes();
		mocks.bookFindUnique.mockResolvedValue({ id: 42, deletedAt: null });
		mocks.bookFileCreate.mockResolvedValue({ id: 102, bookId: 42, extension: 'epub' });
		const formData = new FormData();
		formData.append('file', new Blob([new Uint8Array(epubBytes)], { type: 'application/epub+zip' }), 'existing.epub');

		const response = await app.request('/api/v1/admin/books/42/files', { method: 'POST', body: formData });
		expect(response.status).toBe(201);
		expect(mocks.bookFileCreate).toHaveBeenCalledWith(expect.objectContaining({
			data: expect.objectContaining({ extension: 'epub', mimeType: 'application/epub+zip', bookId: 42 }),
		}));

		mocks.bookFileFindFirst.mockResolvedValue({ id: 102 });
		const secondResponse = await app.request('/api/v1/admin/books/42/files', { method: 'POST', body: formData });
		expect(secondResponse.status).toBe(409);
		expect(await secondResponse.json()).toMatchObject({ code: 'BOOK_FILE_ALREADY_EXISTS' });
	});
});
