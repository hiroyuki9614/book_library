import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, beforeEach, describe, expect, test, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
	getSession: vi.fn(),
	userFindFirst: vi.fn(),
	bookFindUnique: vi.fn(),
	permissionFindUnique: vi.fn(),
	readingInfoFindUnique: vi.fn(),
}));

vi.mock('@hono/node-server', () => ({ serve: vi.fn() }));
vi.mock('./lib/auth.js', () => ({
	auth: {
		handler: vi.fn(() => new Response(null, { status: 404 })),
		api: { getSession: mocks.getSession },
	},
}));
vi.mock('./lib/prisma.js', () => {
	const prisma = {
		user: { findFirst: mocks.userFindFirst },
		book: { findUnique: mocks.bookFindUnique, findMany: vi.fn(), count: vi.fn() },
		roleBookPermission: { findUnique: mocks.permissionFindUnique },
		readingInfo: { findUnique: mocks.readingInfoFindUnique },
	};
	return {
		prisma,
		default: async (c: { set: (key: string, value: unknown) => void }, next: () => Promise<void>) => {
			c.set('prisma', prisma);
			await next();
		},
	};
});

const book = {
	id: 1,
	title: 'Mounted Phase 3 PDF',
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
			fileSize: 18,
			originalFileName: 'mounted-phase-3.pdf',
			fileUrl: 'mounted-phase-3.pdf',
		},
	],
};

let app: (typeof import('./index.js'))['app'];
let storageRoot: string;

beforeAll(async () => {
	storageRoot = await mkdtemp(join(tmpdir(), 'belib-phase3-mounted-'));
	await writeFile(join(storageRoot, 'mounted-phase-3.pdf'), '%PDF-1.7 mounted');
	process.env.BOOK_FILE_STORAGE_ROOT = storageRoot;
	({ app } = await import('./index.js'));
});

beforeEach(() => {
	vi.resetAllMocks();
	mocks.getSession.mockImplementation(({ headers }: { headers: Headers }) =>
		headers.get('Cookie') ? { user: { id: '7' } } : null,
	);
	mocks.userFindFirst.mockResolvedValue({ id: 7, roleId: 2 });
	mocks.bookFindUnique.mockResolvedValue(book);
	mocks.permissionFindUnique.mockResolvedValue({ id: 1 });
	mocks.readingInfoFindUnique.mockResolvedValue(null);
});

describe('mounted Phase 3 API path', () => {
	test('cookie sessionからPDF file endpointまで実際のapp経路で到達する', async () => {
		const response = await app.request('/api/v1/books/1/file', {
			headers: { Cookie: 'better-auth.session_token=test-session' },
		});

		expect(response.status).toBe(200);
		expect(response.headers.get('content-type')).toBe('application/pdf');
		expect(await response.text()).toBe('%PDF-1.7 mounted');
	});

	test('mounted file endpointは未認証を401で拒否する', async () => {
		const response = await app.request('/api/v1/books/1/file');

		expect(response.status).toBe(401);
	});

	test('mounted detail endpointはRoleBookPermissionがない場合403を返す', async () => {
		mocks.permissionFindUnique.mockResolvedValue(null);

		const response = await app.request('/api/v1/books/1', {
			headers: { Cookie: 'better-auth.session_token=test-session' },
		});

		expect(response.status).toBe(403);
	});
});

afterAll(async () => {
	await rm(storageRoot, { recursive: true, force: true });
});
