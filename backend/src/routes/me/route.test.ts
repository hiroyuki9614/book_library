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
		expect(mocks.findFirst).not.toHaveBeenCalled();
	});

	test('認証済みユーザーのロールをDBから取得して返す', async () => {
		mocks.getSession.mockResolvedValue({ user: { id: '1' } });
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
		expect(mocks.findFirst).toHaveBeenCalledWith(expect.objectContaining({
			where: { id: 1 },
		}));
	});

	test('利用停止後でも既存sessionはexpiryまで/meを利用できる', async () => {
		mocks.getSession.mockResolvedValue({ user: { id: '2' } });
		mocks.findFirst.mockResolvedValue({
			id: 2,
			name: 'Disabled User',
			email: 'disabled@example.com',
			role: { name: 'user' },
			deletedAt: new Date('2026-08-24T00:00:00.000Z'),
		});

		const response = await app.request('/me');
		expect(response.status).toBe(200);
		expect(await response.json()).toMatchObject({ id: 2, role: 'user' });
		expect(mocks.findFirst).toHaveBeenCalledWith(expect.objectContaining({ where: { id: 2 } }));
	});

	test('ユーザーIDを数値へ変換できないセッションを401で拒否する', async () => {
		mocks.getSession.mockResolvedValue({ user: { id: 'not-a-number' } });
		const response = await app.request('/me');
		expect(response.status).toBe(401);
		expect(mocks.findFirst).not.toHaveBeenCalled();
	});

	test('セッションのユーザーが物理的に存在しない場合を401で拒否する', async () => {
		mocks.getSession.mockResolvedValue({ user: { id: '1' } });
		mocks.findFirst.mockResolvedValue(null);
		const response = await app.request('/me');
		expect(response.status).toBe(401);
	});
});
