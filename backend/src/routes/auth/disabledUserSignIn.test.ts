import { Hono } from 'hono';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import disabledUserSignIn from './disabledUserSignIn.js';

const mocks = vi.hoisted(() => ({
	findFirst: vi.fn(),
	handler: vi.fn(),
}));

vi.mock('../../lib/prisma.js', () => ({
	prisma: {
		user: {
			findFirst: mocks.findFirst,
		},
	},
}));

vi.mock('../../lib/auth.js', () => ({
	auth: {
		handler: mocks.handler,
	},
}));

const app = new Hono();
app.route('/api/auth', disabledUserSignIn);

beforeEach(() => {
	vi.resetAllMocks();
	mocks.handler.mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
});

describe('disabled user email sign-in guard', () => {
	test('利用停止ユーザーの新規ログインをgeneric 401で拒否する', async () => {
		mocks.findFirst.mockResolvedValue({ id: 7 });

		const response = await app.request('/api/auth/sign-in/email', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ email: 'Disabled@Example.com', password: 'password123' }),
		});

		expect(response.status).toBe(401);
		expect(await response.json()).toEqual({
			code: 'INVALID_EMAIL_OR_PASSWORD',
			message: 'Invalid email or password',
		});
		expect(mocks.findFirst).toHaveBeenCalledWith({
			where: {
				email: { equals: 'Disabled@Example.com', mode: 'insensitive' },
				deletedAt: { not: null },
			},
			select: { id: true },
		});
		expect(mocks.handler).not.toHaveBeenCalled();
	});

	test('activeユーザーのログインはBetter Authへ委譲する', async () => {
		mocks.findFirst.mockResolvedValue(null);

		const response = await app.request('/api/auth/sign-in/email', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ email: 'active@example.com', password: 'password123' }),
		});

		expect(response.status).toBe(200);
		expect(mocks.handler).toHaveBeenCalledTimes(1);
	});

	test('invalid request shapeはBetter Auth自身のvalidationへ委譲する', async () => {
		const response = await app.request('/api/auth/sign-in/email', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ password: 'password123' }),
		});

		expect(response.status).toBe(200);
		expect(mocks.findFirst).not.toHaveBeenCalled();
		expect(mocks.handler).toHaveBeenCalledTimes(1);
	});
});
