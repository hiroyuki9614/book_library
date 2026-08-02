import { describe, expect, test, vi } from 'vitest';
import { createInitialAdmin } from '../src/lib/initialAdmin.js';

const VALID_PARAMS = {
	email: 'admin@example.test',
	name: 'Initial Admin',
	password: 'a-secure-password',
};

function createPrismaDouble(overrides: Record<string, unknown> = {}) {
	const tx = {
		role: {
			upsert: vi.fn().mockResolvedValue({ id: 10, name: 'admin' }),
		},
		user: {
			findUnique: vi.fn().mockResolvedValue(null),
			create: vi.fn().mockResolvedValue({ id: 20, roleId: 10 }),
		},
		account: {
			create: vi.fn().mockResolvedValue({ id: 30 }),
		},
		...overrides,
	};

	return {
		tx,
		$transaction: vi.fn(async (callback: (transaction: typeof tx) => Promise<unknown>) => callback(tx)),
	};
}

describe('createInitialAdmin', () => {
	test('rejects a missing password without touching the database', async () => {
		const prisma = createPrismaDouble();

		await expect(createInitialAdmin(prisma, { ...VALID_PARAMS, password: '' })).rejects.toThrow('password is required');
		expect(prisma.$transaction).not.toHaveBeenCalled();
	});

	test('rejects a whitespace-only required value', async () => {
		const prisma = createPrismaDouble();

		await expect(createInitialAdmin(prisma, { ...VALID_PARAMS, name: '   ' })).rejects.toThrow('name is required');
		expect(prisma.$transaction).not.toHaveBeenCalled();
	});

	test('rejects passwords shorter than eight characters', async () => {
		const prisma = createPrismaDouble();

		await expect(createInitialAdmin(prisma, { ...VALID_PARAMS, password: '1234567' })).rejects.toThrow('at least 8 characters');
		expect(prisma.$transaction).not.toHaveBeenCalled();
	});

	test('does not elevate an existing general user', async () => {
		const prisma = createPrismaDouble();
		prisma.tx.user.findUnique.mockResolvedValue({ id: 40, roleId: 11 });

		await expect(createInitialAdmin(prisma, VALID_PARAMS)).rejects.toThrow('already exists and is not an admin');
		expect(prisma.tx.user.create).not.toHaveBeenCalled();
		expect(prisma.tx.account.create).not.toHaveBeenCalled();
	});

	test('does not change the password of an existing admin', async () => {
		const prisma = createPrismaDouble();
		prisma.tx.user.findUnique.mockResolvedValue({ id: 40, roleId: 10 });

		const result = await createInitialAdmin(prisma, VALID_PARAMS);

		expect(result).toEqual({ created: false, userId: 40 });
		expect(prisma.tx.user.create).not.toHaveBeenCalled();
		expect(prisma.tx.account.create).not.toHaveBeenCalled();
	});

	test('creates the user and credential account in one transaction', async () => {
		const prisma = createPrismaDouble();

		const result = await createInitialAdmin(prisma, VALID_PARAMS, {
			hashPassword: vi.fn().mockResolvedValue('hashed-password'),
		});

		expect(result).toEqual({ created: true, userId: 20 });
		expect(prisma.$transaction).toHaveBeenCalledTimes(1);
		expect(prisma.tx.account.create).toHaveBeenCalledWith({
		data: {
			accountId: '20',
			providerId: 'credential',
			password: 'hashed-password',
			userId: 20,
		},
	});
	});

	test('does not expose the password when database creation fails', async () => {
		const prisma = createPrismaDouble();
		prisma.tx.account.create.mockRejectedValue(new Error('database write failed'));

		await expect(createInitialAdmin(prisma, VALID_PARAMS)).rejects.not.toThrow(VALID_PARAMS.password);
	});
});
