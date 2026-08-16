import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, beforeEach, describe, expect, test, vi } from 'vitest';
import { MAX_PDF_FILE_SIZE, validatePdfUploadMetadata } from './routes.js';

const mocks = vi.hoisted(() => ({
	getSession: vi.fn(),
	userFindFirst: vi.fn(),
	categoryFindUnique: vi.fn(),
	bookCreate: vi.fn(),
	bookFindUnique: vi.fn(),
	bookFileCreate: vi.fn(),
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
			category: { findUnique: mocks.categoryFindUnique },
			book: { create: mocks.bookCreate, findUnique: mocks.bookFindUnique },
			bookFile: { create: mocks.bookFileCreate },
		});
		await next();
	},
}));

let app: (typeof import('../../index.js'))['app'];
let storageRoot: string;

beforeAll(async () => {
	storageRoot = await mkdtemp(join(tmpdir(), 'belib-phase5-'));
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
});

describe('Phase 5 admin book registration', () => {
	test('未認証ユーザーはadmin book APIを401で拒否する', async () => {
		mocks.getSession.mockResolvedValue(null);

		const response = await app.request('/api/v1/admin/books', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ title: 'Phase 5 book', categoryId: 1 }),
		});

		expect(response.status).toBe(401);
	});

	test('adminは最小metadataで書籍を1冊作成できる', async () => {
		mocks.categoryFindUnique.mockResolvedValue({ id: 1, isActive: true });
		mocks.bookCreate.mockResolvedValue({ id: 42, title: 'Phase 5 book', authorName: null, categoryId: 1 });

		const response = await app.request('/api/v1/admin/books', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ title: 'Phase 5 book', categoryId: 1 }),
		});

		expect(response.status).toBe(201);
		expect(await response.json()).toMatchObject({ id: 42, title: 'Phase 5 book', categoryId: 1 });
		expect(mocks.bookCreate).toHaveBeenCalledWith({
			data: {
				title: 'Phase 5 book',
				category: { connect: { id: 1 } },
				roleBookPermissions: { create: [{ role: { connect: { name: 'user' } } }] },
			},
			select: { id: true, title: true, authorName: true, categoryId: true },
		});
	});

	test('adminが作成したMVP書籍はuser roleのRoleBookPermissionを持つ', async () => {
		mocks.categoryFindUnique.mockResolvedValue({ id: 1, isActive: true });
		mocks.bookCreate.mockResolvedValue({ id: 42, title: 'Phase 5 visible book', authorName: null, categoryId: 1 });

		const response = await app.request('/api/v1/admin/books', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ title: 'Phase 5 visible book', categoryId: 1 }),
		});

		expect(response.status).toBe(201);
		expect(mocks.bookCreate).toHaveBeenCalledWith({
			data: {
				title: 'Phase 5 visible book',
				category: { connect: { id: 1 } },
				roleBookPermissions: { create: [{ role: { connect: { name: 'user' } } }] },
			},
			select: { id: true, title: true, authorName: true, categoryId: true },
		});
	});

	test('必須metadataが不正なら書籍を作成しない', async () => {
		const response = await app.request('/api/v1/admin/books', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ title: '   ', categoryId: 0 }),
		});

		expect(response.status).toBe(400);
		expect(await response.json()).toMatchObject({ code: 'INVALID_BOOK' });
		expect(mocks.categoryFindUnique).not.toHaveBeenCalled();
		expect(mocks.bookCreate).not.toHaveBeenCalled();
	});

	test('adminは1冊のPDFをstorage rootへ保存しBookFileを登録できる', async () => {
		mocks.bookFindUnique.mockResolvedValue({ id: 42, deletedAt: null });
		mocks.bookFileCreate.mockResolvedValue({
			id: 100,
			bookId: 42,
			extension: 'pdf',
			mimeType: 'application/pdf',
			fileUrl: 'generated.pdf',
			originalFileName: 'sample.pdf',
			storedFileName: 'generated.pdf',
			fileSize: 9,
			fileHash: 'hash',
		});
		const formData = new FormData();
		formData.append('file', new Blob(['%PDF-1.7'], { type: 'application/pdf' }), '../escape.pdf');

		const response = await app.request('/api/v1/admin/books/42/files', {
			method: 'POST',
			body: formData,
		});

		expect(response.status).toBe(201);
		expect(await response.json()).toMatchObject({ id: 100, bookId: 42, mimeType: 'application/pdf' });
		const createInput = mocks.bookFileCreate.mock.calls[0][0];
		expect(createInput.data.fileUrl).toBe(createInput.data.storedFileName);
		expect(createInput.data.fileUrl).toMatch(/^[\da-f]{8}-[\da-f-]+\.pdf$/);
		expect(createInput.data.originalFileName).toBe('../escape.pdf');
		expect(await readFile(join(storageRoot, createInput.data.fileUrl))).toEqual(Buffer.from('%PDF-1.7'));
	});

	test('非adminユーザーは書籍登録を403で拒否する', async () => {
		mocks.userFindFirst.mockResolvedValue({ id: 1, role: { name: 'user' } });

		const response = await app.request('/api/v1/admin/books', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ title: 'Forbidden', categoryId: 1 }),
		});

		expect(response.status).toBe(403);
		expect(mocks.bookCreate).not.toHaveBeenCalled();
	});

	test('PDF以外は登録せず400を返す', async () => {
		mocks.bookFindUnique.mockResolvedValue({ id: 42, deletedAt: null });
		const formData = new FormData();
		formData.append('file', new Blob(['not pdf'], { type: 'text/plain' }), 'notes.txt');

		const response = await app.request('/api/v1/admin/books/42/files', {
			method: 'POST',
			body: formData,
		});

		expect(response.status).toBe(400);
		expect(await response.json()).toMatchObject({ code: 'INVALID_PDF' });
		expect(mocks.bookFileCreate).not.toHaveBeenCalled();
	});

	test('200MB超過は巨大fixtureなしで拒否する', () => {
		const result = validatePdfUploadMetadata({
			name: 'large.pdf',
			type: 'application/pdf',
			size: MAX_PDF_FILE_SIZE + 1,
			arrayBuffer: async () => new ArrayBuffer(0),
		});

		expect(result).toMatchObject({ valid: false, status: 413, code: 'FILE_TOO_LARGE' });
	});

	test('BookFile DB登録失敗時は保存したファイルをcleanupする', async () => {
		mocks.bookFindUnique.mockResolvedValue({ id: 42, deletedAt: null });
		mocks.bookFileCreate.mockRejectedValue(new Error('database unavailable'));
		const before = await readdir(storageRoot);
		const formData = new FormData();
		formData.append('file', new Blob(['%PDF-1.7'], { type: 'application/pdf' }), 'cleanup.pdf');

		const response = await app.request('/api/v1/admin/books/42/files', {
			method: 'POST',
			body: formData,
		});

		expect(response.status).toBe(500);
		expect(await readdir(storageRoot)).toEqual(before);
	});
});
