import { beforeEach, describe, expect, test, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
	betterAuth: vi.fn((options: unknown) => options),
	userFindUnique: vi.fn(),
}));

vi.hoisted(() => {
	process.env.BETTER_AUTH_SECRET ??= 'belib-auth-test-secret-000000000000000000000000000000';
});

vi.mock('better-auth', async (importOriginal) => {
	const actual = await importOriginal<typeof import('better-auth')>();
	return { ...actual, betterAuth: mocks.betterAuth };
});

vi.mock('./prisma.js', () => ({
	prisma: {
		role: { findUnique: vi.fn() },
		user: { findUnique: mocks.userFindUnique },
	},
}));

import { auth } from './auth.js';

type AuthOptions = {
	databaseHooks?: {
		session?: {
			create?: {
				before?: (session: { userId: string }) => Promise<void>;
			};
		};
	};
};

const sessionBefore = (auth as unknown as AuthOptions).databaseHooks?.session?.create?.before;
if (!sessionBefore) {
	throw new Error('Session creation hook is not configured');
}

describe('Better Auth session creation boundary', () => {
	beforeEach(() => {
		vi.clearAllMocks();
	});

	test('suspended user cannot create a new session', async () => {
		mocks.userFindUnique.mockResolvedValue({ deletedAt: new Date('2026-08-16T00:00:00.000Z') });

		await expect(sessionBefore?.({ userId: '42' })).rejects.toMatchObject({
			status: 'UNAUTHORIZED',
			statusCode: 401,
			body: { code: 'INVALID_EMAIL_OR_PASSWORD' },
		});
		expect(mocks.userFindUnique).toHaveBeenCalledWith({
			where: { id: 42 },
			select: { deletedAt: true },
		});
	});

	test('active user can create a new session', async () => {
		mocks.userFindUnique.mockResolvedValue({ deletedAt: null });

		await expect(sessionBefore?.({ userId: '42' })).resolves.toBeUndefined();
	});
});
