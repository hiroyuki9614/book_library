import { describe, expect, test, vi } from 'vitest';
import { createAdminSetupRoutes } from './admin.js';

const validAdmin = {
	name: '管理者',
	email: 'admin@example.com',
	password: 'password123',
};

describe('initial admin setup routes', () => {
	test('管理者が未登録なら最初の管理者を登録できる', async () => {
		const createAdmin = vi.fn(async (input: typeof validAdmin) => ({
			id: 1,
			name: input.name,
			email: input.email,
		}));
		const routes = createAdminSetupRoutes({
			hasAdmin: vi.fn(async () => false),
			createAdmin,
		});

		const response = await routes.request('/', {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify(validAdmin),
		});

		expect(response.status).toBe(201);
		expect(await response.json()).toEqual({
			id: 1,
			name: '管理者',
			email: 'admin@example.com',
		});
		expect(createAdmin).toHaveBeenCalledWith(validAdmin);
	});

	test('管理者が既に存在する場合は追加登録を拒否する', async () => {
		const createAdmin = vi.fn();
		const routes = createAdminSetupRoutes({
			hasAdmin: vi.fn(async () => true),
			createAdmin,
		});

		const response = await routes.request('/', {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify(validAdmin),
		});

		expect(response.status).toBe(409);
		expect(await response.json()).toMatchObject({ code: 'ADMIN_ALREADY_EXISTS' });
		expect(createAdmin).not.toHaveBeenCalled();
	});

	test('8文字未満のパスワードを拒否する', async () => {
		const routes = createAdminSetupRoutes({
			hasAdmin: vi.fn(async () => false),
			createAdmin: vi.fn(),
		});

		const response = await routes.request('/', {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ ...validAdmin, password: 'short' }),
		});

		expect(response.status).toBe(400);
		expect(await response.json()).toMatchObject({ code: 'VALIDATION_ERROR' });
	});
});
