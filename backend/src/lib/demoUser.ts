import { hashPassword as defaultHashPassword, verifyPassword as defaultVerifyPassword } from 'better-auth/crypto';
import type { DemoUserConfig } from './demoUserConfig.js';

export type DemoUserPrisma = {
	$transaction: <T>(callback: (transaction: any) => Promise<T>) => Promise<T>;
};

type DemoUserOptions = {
	dryRun?: boolean;
	hashPassword?: typeof defaultHashPassword;
	verifyPassword?: typeof defaultVerifyPassword;
};

export async function createDemoUser(
	prisma: DemoUserPrisma,
	config: DemoUserConfig,
	options: DemoUserOptions = {},
) {
	const hashPassword = options.hashPassword ?? defaultHashPassword;
	const verifyPassword = options.verifyPassword ?? defaultVerifyPassword;

	return prisma.$transaction(async (transaction) => {
		const userRole = await transaction.role.findUnique({ where: { name: 'user' } });
		if (!userRole) {
			throw new Error('the user role does not exist');
		}

		const existingUser = await transaction.user.findFirst({
			where: { email: { equals: config.email, mode: 'insensitive' } },
		});
		if (existingUser) {
			if (existingUser.roleId !== userRole.id || existingUser.deletedAt) {
				throw new Error('the existing demo account is not an active general user');
			}
			const credentialAccount = await transaction.account.findFirst({
				where: { userId: existingUser.id, providerId: 'credential' },
				select: { password: true },
			});
			if (!credentialAccount?.password || !(await verifyPassword({ hash: credentialAccount.password, password: config.password }))) {
				throw new Error('the existing demo account credentials do not match');
			}
			return { created: false, userId: existingUser.id };
		}

		if (options.dryRun) return { created: false };

		const user = await transaction.user.create({
			data: {
				email: config.email,
				name: config.name,
				roleId: userRole.id,
			},
		});
		await transaction.account.create({
			data: {
				accountId: String(user.id),
				providerId: 'credential',
				password: await hashPassword(config.password),
				userId: user.id,
			},
		});

		return { created: true, userId: user.id };
	});
}
