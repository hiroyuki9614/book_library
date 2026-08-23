import { Hono } from 'hono';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import { getMe } from './route.js';

const mocks = vi.hoisted(() => ({
	getSession: vi.fn(),
	findFirst: vi.fn(),
}));

vi.mock('../../lib/auth.js', () => ({
	auth: {
		api: {
			getSession: mocks.getSession,
		},
	},
}));

vi.mock('../../lib/prisma.js', () => ({
	prisma: {
		user: {
			findFirst: mocks.findFirst,
		},
	},
}));

const app = new Hono();
app.get('/me', getMe);

describe('GET /me', () => {
	beforeEach(() => {
		vi.resetAllMocks();
	});

	test('未認証ユーザーを401で拒否してユーザー情報を返さない', async () => {
		mocks.getSession.mockResolvedValue(null);

		const response = await app.request('/me');

		expect(response.status).toBe(401);
		expect(await response.json()).toEqual({
			message: 'Authentication required',
			code: 'UNAUTHORIZED',
		});
		expect(mocks.findFirst).not.toHaveBeenCalled();
	});

	test('認証済みユーザーのロールをDBから取得して返す', async () => {
		mocks.getSession.mockResolvedValue({
			user: { id: '1' },
		});
		mocks.findFirst.mockResolvedValue({
			id: 1,
			name: 'Admin',
			email: 'admin@example.com',
			role: { name: 'admin' },
		});

		const response = await app.request('/me');

		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({
			id: 1,
			name: 'Admin',
			email: 'admin@example.com',
			role: 'admin',
		});
	});

	test('ユーザーIDを数値へ変換できないセッションを401で拒否する', async () => {
		mocks.getSession.mockResolvedValue({
			user: { id: 'not-a-number' },
		});

		const response = await app.request('/me');

		expect(response.status).toBe(401);
		expect(await response.json()).toEqual({
			message: 'Authentication required',
			code: 'UNAUTHORIZED',
		});
		expect(mocks.findFirst).not.toHaveBeenCalled();
	});

	test('セッションのユーザーが削除済みまたは存在しない場合を401で拒否する', async () => {
		mocks.getSession.mockResolvedValue({
			user: { id: '1' },
		});
		mocks.findFirst.mockResolvedValue(null);

		const response = await app.request('/me');

		expect(response.status).toBe(401);
		expect(await response.json()).toEqual({
			message: 'Authentication required',
			code: 'UNAUTHORIZED',
		});
	});

	test('利用停止後も既存セッションは有効期限まで利用できる', async () => {
		mocks.getSession.mockResolvedValue({
			user: { id: '1' },
		});
		mocks.findFirst.mockImplementation(async (query: { where?: { deletedAt?: null } }) => {
			if (query.where?.deletedAt === null) {
				return null;
			}

			return {
				id: 1,
				name: 'Suspended User',
				email: 'suspended@example.com',
				role: { name: 'user' },
			};
		});

		const response = await app.request('/me');

		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({
			id: 1,
			name: 'Suspended User',
			email: 'suspended@example.com',
			role: 'user',
		});
	});
});
