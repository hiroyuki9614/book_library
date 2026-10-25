import { describe, expect, test, vi } from 'vitest';
import { createDemoUser } from '../src/lib/demoUser.js';
import { readDemoUserConfig } from '../src/lib/demoUserConfig.js';

function createPrisma(overrides: Record<string, unknown> = {}) {
	const transaction = {
		role: { findUnique: vi.fn().mockResolvedValue({ id: 2, name: 'user' }) },
		user: {
			findFirst: vi.fn().mockResolvedValue(null),
			create: vi.fn().mockResolvedValue({ id: 9 }),
		},
		account: {
			findFirst: vi.fn().mockResolvedValue(null),
			create: vi.fn().mockResolvedValue({ id: 10 }),
		},
		...overrides,
	};
	return {
		transaction,
		prisma: { $transaction: <T>(callback: (tx: typeof transaction) => Promise<T>) => callback(transaction) },
	};
}

const config = { email: 'demo@example.com', name: 'Portfolio Demo', password: 'long-demo-password' };

describe('portfolio demo user', () => {
	test('設定を正規化し、短いパスワードを拒否する', () => {
		expect(readDemoUserConfig({
			DEMO_USER_EMAIL: ' Demo@Example.COM ',
			DEMO_USER_NAME: ' Demo Reader ',
			DEMO_USER_PASSWORD: 'twelve-chars-plus',
		} as NodeJS.ProcessEnv)).toEqual({
			email: 'demo@example.com',
			name: 'Demo Reader',
			password: 'twelve-chars-plus',
		});
		expect(() => readDemoUserConfig({
			DEMO_USER_EMAIL: 'demo@example.com',
			DEMO_USER_NAME: 'Demo',
			DEMO_USER_PASSWORD: 'shortpass',
		} as NodeJS.ProcessEnv)).toThrow('at least 12 characters');
	});

	test('userロールのBetter Auth credentialモデルへ原子的に作成する', async () => {
		const { prisma, transaction } = createPrisma();
		const hashPassword = vi.fn().mockResolvedValue('better-auth-hash');

		await expect(createDemoUser(prisma, config, { hashPassword })).resolves.toEqual({ created: true, userId: 9 });
		expect(transaction.user.create).toHaveBeenCalledWith({
			data: { email: config.email, name: config.name, roleId: 2 },
		});
		expect(transaction.account.create).toHaveBeenCalledWith({
			data: { accountId: '9', providerId: 'credential', password: 'better-auth-hash', userId: 9 },
		});
	});

	test('dry-run never creates an account or hashes a password', async () => {
		const { prisma, transaction } = createPrisma();
		const hashPassword = vi.fn();
		await expect(createDemoUser(prisma, config, { dryRun: true, hashPassword })).resolves.toEqual({ created: false });
		expect(transaction.user.create).not.toHaveBeenCalled();
		expect(transaction.account.create).not.toHaveBeenCalled();
		expect(hashPassword).not.toHaveBeenCalled();
	});

	test('同じ資格情報なら再実行してもユーザーを増やさない', async () => {
		const { prisma, transaction } = createPrisma({
			user: {
				findFirst: vi.fn().mockResolvedValue({ id: 9, roleId: 2, deletedAt: null }),
				create: vi.fn(),
			},
			account: {
				findFirst: vi.fn().mockResolvedValue({ password: 'stored-hash' }),
				create: vi.fn(),
			},
		});
		const verifyPassword = vi.fn().mockResolvedValue(true);

		await expect(createDemoUser(prisma, config, { verifyPassword })).resolves.toEqual({ created: false, userId: 9 });
		expect(transaction.user.create).not.toHaveBeenCalled();
		expect(transaction.account.create).not.toHaveBeenCalled();
	});

	test('既存メールがadminまたは資格情報不一致なら上書きしない', async () => {
		const { prisma, transaction } = createPrisma({
			user: {
				findFirst: vi.fn().mockResolvedValue({ id: 1, roleId: 1, deletedAt: null }),
				create: vi.fn(),
			},
		});

		await expect(createDemoUser(prisma, config)).rejects.toThrow('not an active general user');
		expect(transaction.user.create).not.toHaveBeenCalled();
	});
});
