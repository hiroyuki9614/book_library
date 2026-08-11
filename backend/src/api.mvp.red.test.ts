import { beforeAll, describe, expect, test, vi } from 'vitest';

vi.mock('@hono/node-server', () => ({
	serve: vi.fn(),
}));

vi.mock('./lib/auth.js', () => ({
	auth: {
		handler: vi.fn(() => new Response(JSON.stringify({ message: 'Auth route is mocked in contract tests', code: 'AUTH_MOCK' }), { status: 501, headers: { 'content-type': 'application/json' } })),
		api: {
			getSession: vi.fn(({ headers }: { headers: Headers }) => {
				if (!headers.get('Authorization')) {
					return null;
				}

				return {
					user: { id: '1' },
					session: { id: 'test-session' },
				};
			}),
		},
	},
}));

vi.mock('./lib/prisma.js', () => ({
	prisma: {
		user: {
			findFirst: vi.fn(() => ({
				id: 1,
				name: 'Test User',
				email: 'test@example.com',
				role: { name: 'user' },
			})),
		},
	},
	default: async (c: { set: (key: string, value: unknown) => void }, next: () => Promise<void>) => {
		c.set('prisma', {
			user: {
				findFirst: vi.fn(() => ({ id: 1, roleId: 2 })),
			},
			book: {
				count: vi.fn(() => 1),
				findMany: vi.fn(() => [
					{
						id: 1,
						title: 'Test Book',
						authorName: 'Test Author',
						publishedAt: null,
						publisher: null,
						description: null,
						deletedAt: null,
						pageTurnDirection: 'ltr',
						createdAt: new Date(),
						updatedAt: new Date(),
						category: { id: 1, name: '技術書' },
						bookFiles: [],
					},
				]),
				findUnique: vi.fn(({ where }: { where: { id: number } }) =>
					(where.id === 1 || where.id === 2)
						? {
							id: 1,
							title: 'Test Book',
							authorName: 'Test Author',
							publishedAt: null,
							publisher: null,
							description: null,
							deletedAt: null,
							pageTurnDirection: 'ltr',
							createdAt: new Date(),
							updatedAt: new Date(),
							category: { id: 1, name: '技術書' },
							bookFiles: [],
						}
						: null),
			},
			roleBookPermission: {
				findUnique: vi.fn(({ where }: { where: { roleId_bookId: { bookId: number } } }) => (where.roleId_bookId.bookId === 1 ? { id: 1 } : null)),
			},
			readingInfo: {
				findUnique: vi.fn(() => null),
				upsert: vi.fn(({ create }: { create: { currentPosition: string; readStatus: string } }) => create),
			},
		});
		return next();
	},
}));

let app: (typeof import('./index.js'))['app'];

beforeAll(async () => {
	({ app } = await import('./index.js'));
});

const JSON_HEADERS = {
	Authorization: 'Bearer test-admin-token',
	'Content-Type': 'application/json',
};

const AUTH_HEADERS = {
	Authorization: 'Bearer test-user-token',
};

async function expectJsonObject(response: Response) {
	expect(response.headers.get('content-type')).toContain('application/json');
	return await response.json();
}

describe('MVP API contract RED tests based on docs/api.yaml', () => {
	describe('GET /api/v1/me', () => {
		test('認証済みユーザー自身の情報を返す', async () => {
			const response = await app.request('/api/v1/me', { method: 'GET', headers: AUTH_HEADERS });
			expect(response.status).toBe(200);
			const body = await expectJsonObject(response);
			expect(body).toMatchObject({ id: expect.any(Number), name: expect.any(String), email: expect.any(String), role: expect.any(String) });
		});

		test('未認証ユーザーを401で拒否する', async () => {
			const response = await app.request('/api/v1/me', { method: 'GET' });
			expect(response.status).toBe(401);
			const body = await expectJsonObject(response);
			expect(body).toMatchObject({ message: expect.any(String), code: expect.any(String) });
		});
	});

	describe('book viewing APIs', () => {
		test('GET /api/v1/books はロールで許可された書籍一覧をページング形式で返す', async () => {
			const response = await app.request('/api/v1/books?page=1&limit=20', { method: 'GET', headers: AUTH_HEADERS });
			expect(response.status).toBe(200);
			const body = await expectJsonObject(response);
			expect(body).toMatchObject({ total: expect.any(Number), page: 1, limit: 20, books: expect.any(Array) });
			expect(body.books[0]).toMatchObject({
				id: expect.any(Number),
				title: expect.any(String),
				readStatus: expect.stringMatching(/^(unread|reading|completed)$/),
				hasFile: expect.any(Boolean),
				createdAt: expect.any(String),
				updatedAt: expect.any(String),
			});
		});

		test('GET /api/v1/books/{bookId} は閲覧許可された書籍詳細を返す', async () => {
			const response = await app.request('/api/v1/books/1', { method: 'GET', headers: AUTH_HEADERS });
			expect(response.status).toBe(200);
			const body = await expectJsonObject(response);
			expect(body).toMatchObject({
				id: 1,
				title: expect.any(String),
				readStatus: expect.stringMatching(/^(unread|reading|completed)$/),
				hasFile: expect.any(Boolean),
				createdAt: expect.any(String),
				updatedAt: expect.any(String),
			});
		});

		test('GET /api/v1/books/{bookId} はロールに閲覧権限がない書籍を403で拒否する', async () => {
			const response = await app.request('/api/v1/books/2', { method: 'GET', headers: AUTH_HEADERS });
			expect(response.status).toBe(403);
			const body = await expectJsonObject(response);
			expect(body).toMatchObject({ message: expect.any(String), code: expect.any(String) });
		});

		test.skip('GET /api/v1/books/{bookId}/file は閲覧許可された書籍ファイルを返す', async () => {
			const response = await app.request('/api/v1/books/1/file', { method: 'GET', headers: AUTH_HEADERS });
			expect(response.status).toBe(200);
			expect(response.headers.get('content-type')).toMatch(/application\/(pdf|epub\+zip)/);
		});
	});

	describe.skip('admin book management APIs', () => {
		test('POST /api/v1/admin/books は管理者が書籍を登録できる', async () => {
			const response = await app.request('/api/v1/admin/books', {
				method: 'POST',
				headers: JSON_HEADERS,
				body: JSON.stringify({ title: 'テスト書籍', author: 'テスト著者', categoryId: 1 }),
			});
			expect(response.status).toBe(201);
			const body = await expectJsonObject(response);
			expect(body).toMatchObject({ id: expect.any(Number), title: 'テスト書籍', author: 'テスト著者', categoryId: 1, readStatus: 'unread', hasFile: false });
		});

		test('POST /api/v1/admin/books は一般ユーザーを403で拒否する', async () => {
			const response = await app.request('/api/v1/admin/books', {
				method: 'POST',
				headers: { ...JSON_HEADERS, Authorization: 'Bearer test-user-token' },
				body: JSON.stringify({ title: '一般ユーザー登録不可' }),
			});
			expect(response.status).toBe(403);
			const body = await expectJsonObject(response);
			expect(body).toMatchObject({ message: expect.any(String), code: expect.any(String) });
		});

		test('PATCH /api/v1/admin/books/{bookId} は管理者が書籍情報を更新できる', async () => {
			const response = await app.request('/api/v1/admin/books/1', {
				method: 'PATCH',
				headers: JSON_HEADERS,
				body: JSON.stringify({ title: '更新後タイトル', author: null, categoryId: 2 }),
			});
			expect(response.status).toBe(200);
			const body = await expectJsonObject(response);
			expect(body).toMatchObject({ id: 1, title: '更新後タイトル', author: null, categoryId: 2 });
		});

		test('DELETE /api/v1/admin/books/{bookId} は管理者が書籍を削除できる', async () => {
			const response = await app.request('/api/v1/admin/books/1', { method: 'DELETE', headers: AUTH_HEADERS });
			expect(response.status).toBe(204);
			expect(await response.text()).toBe('');
		});
	});

	describe.skip('admin book file APIs', () => {
		test('GET /api/v1/admin/books/{bookId}/files は書籍ファイル情報を返す', async () => {
			const response = await app.request('/api/v1/admin/books/1/files', { method: 'GET', headers: JSON_HEADERS });
			expect(response.status).toBe(200);
			const body = await expectJsonObject(response);
			expect(body).toMatchObject({ id: expect.any(Number), bookId: 1, fileUrl: expect.any(String), fileSize: expect.any(Number), fileType: expect.stringMatching(/^(epub|pdf)$/), mimeType: expect.any(String) });
		});

		test('POST /api/v1/admin/books/{bookId}/files はEPUB/PDFファイル情報を登録できる', async () => {
			const formData = new FormData();
			formData.append('file', new Blob(['%PDF-1.7'], { type: 'application/pdf' }), 'sample.pdf');
			formData.append('fileType', 'pdf');
			const response = await app.request('/api/v1/admin/books/1/files', {
				method: 'POST',
				headers: { Authorization: 'Bearer test-admin-token' },
				body: formData,
			});
			expect(response.status).toBe(201);
			const body = await expectJsonObject(response);
			expect(body).toMatchObject({ id: expect.any(Number), bookId: 1, fileSize: expect.any(Number), fileType: 'pdf', mimeType: 'application/pdf' });
		});
	});

	describe('reading info APIs', () => {
		test('GET /api/v1/books/{bookId}/reading-info は自分の読書進捗を返す', async () => {
			const response = await app.request('/api/v1/books/1/reading-info', { method: 'GET', headers: AUTH_HEADERS });
			expect(response.status).toBe(200);
			const body = await expectJsonObject(response);
			expect(body).toMatchObject({ bookId: 1, currentPage: 1, readStatus: 'unread' });
		});

		test('PATCH /api/v1/books/{bookId}/reading-info はcurrentPageを保存してreadingを返す', async () => {
			const response = await app.request('/api/v1/books/1/reading-info', {
				method: 'PATCH',
				headers: JSON_HEADERS,
				body: JSON.stringify({ currentPage: 3 }),
			});
			expect(response.status).toBe(200);
			const body = await expectJsonObject(response);
			expect(body).toEqual({ bookId: 1, currentPage: 3, readStatus: 'reading' });
		});

		test('PATCH /api/v1/books/{bookId}/reading-info はclient指定のreadStatusを無視してreadingを返す', async () => {
			const response = await app.request('/api/v1/books/1/reading-info', {
				method: 'PATCH',
				headers: JSON_HEADERS,
				body: JSON.stringify({ readStatus: 'completed', currentPage: 4 }),
			});
			expect(response.status).toBe(200);
			const body = await expectJsonObject(response);
			expect(body).toEqual({ bookId: 1, currentPage: 4, readStatus: 'reading' });
		});

		test('PATCH /api/v1/books/{bookId}/reading-info はapi.yaml上のfinishedをDB正規値ではないため400で拒否する', async () => {
			const response = await app.request('/api/v1/books/1/reading-info', {
				method: 'PATCH',
				headers: JSON_HEADERS,
				body: JSON.stringify({ readStatus: 'finished' }),
			});
			expect(response.status).toBe(400);
			const body = await expectJsonObject(response);
			expect(body).toMatchObject({ message: expect.any(String), code: expect.any(String) });
		});
	});

	describe.skip('admin user management APIs', () => {
		test('GET /api/v1/admin/users は管理者がユーザー一覧を取得できる', async () => {
			const response = await app.request('/api/v1/admin/users?page=1&limit=20', { method: 'GET', headers: JSON_HEADERS });
			expect(response.status).toBe(200);
			const body = await expectJsonObject(response);
			expect(body).toMatchObject({ total: expect.any(Number), page: 1, limit: 20, users: expect.any(Array) });
			expect(body.users[0]).toMatchObject({ id: expect.any(Number), name: expect.any(String), email: expect.any(String), role: expect.any(String) });
		});

		test('POST /api/v1/admin/users は管理者がユーザーを作成できる', async () => {
			const response = await app.request('/api/v1/admin/users', {
				method: 'POST',
				headers: JSON_HEADERS,
				body: JSON.stringify({ name: '新規ユーザー', email: 'new-user@example.com', password: 'password-1234', roleId: 2 }),
			});
			expect(response.status).toBe(201);
			const body = await expectJsonObject(response);
			expect(body).toMatchObject({ id: expect.any(Number), name: '新規ユーザー', email: 'new-user@example.com', role: expect.any(String) });
			expect(body).not.toHaveProperty('password');
			expect(body).not.toHaveProperty('passwordHash');
		});

		test('PATCH /api/v1/admin/users/{userId} は管理者がユーザー情報を更新できる', async () => {
			const response = await app.request('/api/v1/admin/users/2', {
				method: 'PATCH',
				headers: JSON_HEADERS,
				body: JSON.stringify({ name: '更新ユーザー', roleId: 2 }),
			});
			expect(response.status).toBe(200);
			const body = await expectJsonObject(response);
			expect(body).toMatchObject({ id: 2, name: '更新ユーザー', email: expect.any(String), role: expect.any(String) });
		});

		test('DELETE /api/v1/admin/users/{userId} は管理者がユーザーを削除できる', async () => {
			const response = await app.request('/api/v1/admin/users/2', { method: 'DELETE', headers: JSON_HEADERS });
			expect(response.status).toBe(204);
			expect(await response.text()).toBe('');
		});
	});
});
