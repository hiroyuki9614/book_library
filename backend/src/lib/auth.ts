import { APIError, betterAuth } from 'better-auth';
import { prismaAdapter } from 'better-auth/adapters/prisma';
import { prisma } from './prisma.js';

const appPort = process.env.APP_PORT ?? '3000';
const authSecret = process.env.BETTER_AUTH_SECRET;
const frontendUrl = process.env.FRONTEND_URL ?? 'http://localhost:5173';

if (!authSecret) {
	throw new Error('BETTER_AUTH_SECRET is not defined');
}

export const auth = betterAuth({
	baseURL: process.env.BETTER_AUTH_URL ?? `http://localhost:${appPort}`,
	trustedOrigins: [frontendUrl],
	secret: authSecret,
	database: prismaAdapter(prisma, {
		provider: 'postgresql',
	}),
	advanced: {
		database: {
			generateId: 'serial',
		},
	},
	user: {
		additionalFields: {
			roleId: {
				type: 'number',
				required: true,
				input: false,
			},
		},
	},
	databaseHooks: {
		user: {
			create: {
				before: async (user) => {
					const defaultRole = await prisma.role.findUnique({
						where: { name: 'user' },
					});

					if (!defaultRole) {
						throw new Error('Default user role is not defined');
					}

					return {
						data: {
							...user,
							roleId: defaultRole.id,
						},
					};
				},
			},
		},
		session: {
			create: {
				before: async (session) => {
					const userId = Number(session.userId);
					if (!Number.isInteger(userId)) {
						throw APIError.from('UNAUTHORIZED', {
							code: 'INVALID_EMAIL_OR_PASSWORD',
							message: 'Invalid email or password',
						});
					}

					const user = await prisma.user.findUnique({
						where: { id: userId },
						select: { deletedAt: true },
					});

					if (user?.deletedAt) {
						throw APIError.from('UNAUTHORIZED', {
							code: 'INVALID_EMAIL_OR_PASSWORD',
							message: 'Invalid email or password',
						});
					}
				},
			},
		},
	},
	emailAndPassword: {
		enabled: true,
	},
});
