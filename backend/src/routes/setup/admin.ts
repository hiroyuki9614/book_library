import { hashPassword } from 'better-auth/crypto';
import { Hono } from 'hono';
import { z } from 'zod';

const adminRegistrationSchema = z.object({
	name: z.string().trim().min(1).max(255),
	email: z.string().trim().toLowerCase().email().max(254),
	password: z.string().min(8),
});

type AdminRegistrationInput = z.infer<typeof adminRegistrationSchema>;

type AdminSetupDependencies = {
	hasAdmin: () => Promise<boolean>;
	createAdmin: (input: AdminRegistrationInput) => Promise<{
		id: number;
		name: string;
		email: string;
	}>;
};

export function createAdminSetupRoutes(dependencies: AdminSetupDependencies) {
	const routes = new Hono();

	routes.get('/status', async (c) => {
		return c.json({ canRegister: !(await dependencies.hasAdmin()) });
	});

	routes.post('/', async (c) => {
		let body: unknown;

		try {
			body = await c.req.json();
		} catch {
			return c.json(
				{ message: 'JSON形式のリクエストを送信してください。', code: 'INVALID_JSON' },
				400,
			);
		}

		const result = adminRegistrationSchema.safeParse(body);

		if (!result.success) {
			return c.json(
				{ message: '入力内容を確認してください。', code: 'VALIDATION_ERROR' },
				400,
			);
		}

		if (await dependencies.hasAdmin()) {
			return c.json(
				{ message: '管理者は既に登録されています。', code: 'ADMIN_ALREADY_EXISTS' },
				409,
			);
		}

		try {
			const admin = await dependencies.createAdmin(result.data);
			return c.json(admin, 201);
		} catch (error) {
			if (error instanceof Error && error.message === 'ADMIN_ALREADY_EXISTS') {
				return c.json(
					{ message: '管理者は既に登録されています。', code: 'ADMIN_ALREADY_EXISTS' },
					409,
				);
			}

			if (error instanceof Error && error.message === 'EMAIL_ALREADY_EXISTS') {
				return c.json(
					{ message: 'このメールアドレスは既に使用されています。', code: 'EMAIL_ALREADY_EXISTS' },
					409,
				);
			}

			throw error;
		}
	});

	return routes;
}

async function getPrisma() {
	const { prisma } = await import('../../lib/prisma.js');
	return prisma;
}

const adminSetupRoutes = createAdminSetupRoutes({
	hasAdmin: async () => {
		const prisma = await getPrisma();
		const admin = await prisma.user.findFirst({
			where: { role: { name: 'admin' } },
			select: { id: true },
		});

		return admin !== null;
	},
	createAdmin: async ({ name, email, password }) => {
		const prisma = await getPrisma();
		const passwordHash = await hashPassword(password);

		return prisma.$transaction(
			async (transaction) => {
				const existingAdmin = await transaction.user.findFirst({
					where: { role: { name: 'admin' } },
					select: { id: true },
				});

				if (existingAdmin) {
					throw new Error('ADMIN_ALREADY_EXISTS');
				}

				const existingUser = await transaction.user.findUnique({
					where: { email },
					select: { id: true },
				});

				if (existingUser) {
					throw new Error('EMAIL_ALREADY_EXISTS');
				}

				const adminRole = await transaction.role.upsert({
					where: { name: 'admin' },
					update: {},
					create: { name: 'admin' },
				});

				const admin = await transaction.user.create({
					data: {
						name,
						email,
						roleId: adminRole.id,
					},
					select: {
						id: true,
						name: true,
						email: true,
					},
				});

				await transaction.account.create({
					data: {
						accountId: String(admin.id),
						providerId: 'credential',
						userId: admin.id,
						password: passwordHash,
					},
				});

				return admin;
			},
			{ isolationLevel: 'Serializable' },
		);
	},
});

export default adminSetupRoutes;
