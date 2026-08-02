import { hashPassword as defaultHashPassword, verifyPassword as defaultVerifyPassword } from 'better-auth/crypto';

export type CreateAdminParams = {
	email: string;
	name: string;
	password: string;
};

export type InitialAdminPrisma = {
	$transaction: <T>(callback: (transaction: any) => Promise<T>) => Promise<T>;
};

type CreateInitialAdminOptions = {
	hashPassword?: typeof defaultHashPassword;
	verifyPassword?: typeof defaultVerifyPassword;
};

function requireNonBlank(value: string, field: string) {
	if (!value.trim()) {
		throw new Error(`${field} is required`);
	}
}

function normalizeEmail(email: string) {
	const normalizedEmail = email.trim().toLowerCase();
	if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(normalizedEmail)) {
		throw new Error('email must be a valid email address');
	}

	return normalizedEmail;
}

export async function createInitialAdmin(prisma: InitialAdminPrisma, params: CreateAdminParams, options: CreateInitialAdminOptions = {}) {
	requireNonBlank(params.email, 'email');
	requireNonBlank(params.name, 'name');
	requireNonBlank(params.password, 'password');

	if (params.password.length < 8) {
		throw new Error('password must be at least 8 characters');
	}

	const email = normalizeEmail(params.email);
	const name = params.name.trim();
	const hashPassword = options.hashPassword ?? defaultHashPassword;
	const verifyPassword = options.verifyPassword ?? defaultVerifyPassword;

	return prisma.$transaction(async (transaction) => {
		const adminRole = await transaction.role.upsert({
			where: { name: 'admin' },
			update: {},
			create: { name: 'admin' },
		});

		const existingUser = await transaction.user.findUnique({ where: { email } });
		if (existingUser) {
			if (existingUser.roleId !== adminRole.id) {
				throw new Error('an account with this email already exists and is not an admin');
			}
			if (existingUser.deletedAt) {
				throw new Error('existing admin is not ready for login');
			}

			const credentialAccount = await transaction.account.findFirst({
				where: { userId: existingUser.id, providerId: 'credential' },
				select: { password: true },
			});
			if (!credentialAccount?.password || !(await verifyPassword({ hash: credentialAccount.password, password: params.password }))) {
				throw new Error('existing admin is not ready for login');
			}

			return { created: false, userId: existingUser.id };
		}

		const user = await transaction.user.create({
			data: {
				email,
				name,
				roleId: adminRole.id,
			},
		});
		const password = await hashPassword(params.password);

		await transaction.account.create({
			data: {
				accountId: String(user.id),
				providerId: 'credential',
				password,
				userId: user.id,
			},
		});

		return { created: true, userId: user.id };
	});
}
