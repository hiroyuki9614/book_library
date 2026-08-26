import { Hono } from 'hono';
import { beforeEach, describe, expect, test, vi } from 'vitest';
import type { PrismaVariables } from '../../lib/prisma.js';
import userRoutes from './userRoutes.js';

const mocks = vi.hoisted(() => ({
	getSession: vi.fn(),
	hashPassword: vi.fn(),
	userFindFirst: vi.fn(),
	userFindMany: vi.fn(),
	userUpdate: vi.fn(),
	roleFindUnique: vi.fn(),
	accountFindFirst: vi.fn(),
	accountUpdate: vi.fn(),
	txUserCreate: vi.fn(),
	txAccountCreate: vi.fn(),
}));

vi.mock('../../lib/auth.js', () => ({
	auth: { api: { getSession: mocks.getSession } },
}));

vi.mock('better-auth/crypto', () => ({
	hashPassword: mocks.hashPassword,
}));

const app = new Hono<PrismaVariables>();
app.use('*', async (c, next) => {
	const tx = {
		user: { create: mocks.txUserCreate },
		account: { create: mocks.txAccountCreate },
	};
	c.set('prisma', {
		user: {
			findFirst: mocks.userFindFirst,
			findMany: mocks.userFindMany,
			update: mocks.userUpdate,
		},
		role: { findUnique: mocks.roleFindUnique },
		account: { findFirst: mocks.accountFindFirst, update: mocks.accountUpdate },
		$transaction: (callback: (client: typeof tx) => unknown) => callback(tx),
	} as never);
	await next();
});
app.route('/', userRoutes);

const admin = { id: 1, role: { name: 'admin' } };
const generalUser = { id: 7, email: 'reader@example.com', name: 'Reader', deletedAt: null };

beforeEach(() => {
	vi.resetAllMocks();
	mocks.getSession.mockResolvedValue({ user: { id: '1' } });
	mocks.userFindFirst.mockResolvedValue(admin);
	mocks.hashPassword.mockResolvedValue('hashed-password');
	mocks.roleFindUnique.mockResolvedValue({ id: 2 });
});

describe('admin user management', () => {
	test('一般ユーザーを利用停止状態を含めて一覧表示する', async () => {
		mocks.userFindMany.mockResolvedValue([
			{ id: 7, email: 'reader@example.com', name: 'Reader', deletedAt: null, createdAt: new Date('2026-08-01T00:00:00.000Z') },
			{ id: 8, email: 'stopped@example.com', name: 'Stopped', deletedAt: new Date('2026-08-20T00:00:00.000Z'), createdAt: new Date('2026-08-02T00:00:00.000Z') },
		]);

		const response = await app.request('/users');
		expect(response.status).toBe(200);
		expect(await response.json()).toEqual({ users: [
			{ id: 7, email: 'reader@example.com', name: 'Reader', deletedAt: null, createdAt: '2026-08-01T00:00:00.000Z' },
			{ id: 8, email: 'stopped@example.com', name: 'Stopped', deletedAt: '2026-08-20T00:00:00.000Z', createdAt: '2026-08-02T00:00:00.000Z' },
		] });
		expect(mocks.userFindMany).toHaveBeenCalledWith(expect.objectContaining({ where: { role: { name: 'user' } } }));
	});

	test('メールを小文字化して一般ユーザーとcredential accountを原子的に作成する', async () => {
		mocks.userFindFirst
			.mockResolvedValueOnce(admin)
			.mockResolvedValueOnce(null);
		mocks.txUserCreate.mockResolvedValue({
			id: 9,
			email: 'new@example.com',
			name: 'New User',
			deletedAt: null,
			createdAt: new Date('2026-08-24T00:00:00.000Z'),
		});
		mocks.txAccountCreate.mockResolvedValue({ id: 20 });

		const response = await app.request('/users', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ email: ' New@Example.COM ', name: ' New User ', password: 'password123' }),
		});

		expect(response.status).toBe(201);
		expect(mocks.userFindFirst).toHaveBeenNthCalledWith(2, {
			where: { email: { equals: 'new@example.com', mode: 'insensitive' } },
			select: { id: true },
		});
		expect(mocks.hashPassword).toHaveBeenCalledWith('password123');
		expect(mocks.txUserCreate).toHaveBeenCalledWith(expect.objectContaining({
			data: { email: 'new@example.com', name: 'New User', roleId: 2 },
		}));
		expect(mocks.txAccountCreate).toHaveBeenCalledWith({
			data: { accountId: '9', providerId: 'credential', userId: 9, password: 'hashed-password' },
		});
	});

	test('利用停止中を含む既存メールアドレスの再登録を拒否する', async () => {
		mocks.userFindFirst
			.mockResolvedValueOnce(admin)
			.mockResolvedValueOnce({ id: 8 });
		const response = await app.request('/users', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ email: 'Stopped@Example.com', name: 'Duplicate', password: 'password123' }),
		});
		expect(response.status).toBe(409);
		expect(await response.json()).toMatchObject({ code: 'DUPLICATE_EMAIL' });
		expect(mocks.txUserCreate).not.toHaveBeenCalled();
	});

	test('一般ユーザーを利用停止・再開できる', async () => {
		mocks.userFindFirst
			.mockResolvedValueOnce(admin)
			.mockResolvedValueOnce(generalUser);
		mocks.userUpdate.mockResolvedValue({ ...generalUser, deletedAt: new Date('2026-08-24T01:00:00.000Z') });

		const disabled = await app.request('/users/7/disable', { method: 'PATCH' });
		expect(disabled.status).toBe(200);
		expect(mocks.userUpdate).toHaveBeenCalledWith(expect.objectContaining({ data: { deletedAt: expect.any(Date) } }));

		mocks.userFindFirst
			.mockResolvedValueOnce(admin)
			.mockResolvedValueOnce({ ...generalUser, deletedAt: new Date('2026-08-24T01:00:00.000Z') });
		mocks.userUpdate.mockResolvedValue({ ...generalUser, deletedAt: null });
		const restored = await app.request('/users/7/restore', { method: 'PATCH' });
		expect(restored.status).toBe(200);
		expect(await restored.json()).toMatchObject({ id: 7, deletedAt: null });
	});

	test('仮パスワードを再設定してもsessionを削除しない', async () => {
		mocks.userFindFirst
			.mockResolvedValueOnce(admin)
			.mockResolvedValueOnce(generalUser);
		mocks.accountFindFirst.mockResolvedValue({ id: 30 });
		mocks.accountUpdate.mockResolvedValue({ id: 30 });

		const response = await app.request('/users/7/password', {
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ password: 'temporary123' }),
		});

		expect(response.status).toBe(200);
		expect(mocks.hashPassword).toHaveBeenCalledWith('temporary123');
		expect(mocks.accountUpdate).toHaveBeenCalledWith({ where: { id: 30 }, data: { password: 'hashed-password' } });
		expect(await response.json()).toEqual({ id: 7, passwordReset: true });
	});

	test('一般ユーザーは管理APIを利用できない', async () => {
		mocks.userFindFirst.mockResolvedValue({ id: 2, role: { name: 'user' } });
		const response = await app.request('/users');
		expect(response.status).toBe(403);
		expect(mocks.userFindMany).not.toHaveBeenCalled();
	});
});
