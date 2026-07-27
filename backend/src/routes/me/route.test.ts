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
});
