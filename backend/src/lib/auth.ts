import { betterAuth } from 'better-auth';
import { prismaAdapter } from 'better-auth/adapters/prisma';
import { prisma } from './prisma.js';

const appPort = process.env.APP_PORT ?? '3000';

export const auth = betterAuth({
	baseURL: process.env.BETTER_AUTH_URL ?? `http://localhost:${appPort}`,
	database: prismaAdapter(prisma, {
		provider: 'postgresql',
	}),
	emailAndPassword: {
		enabled: true,
	},
});
