import 'dotenv/config';
import { randomUUID } from 'node:crypto';
import { Pool } from 'pg';
import { PrismaPg } from '@prisma/adapter-pg';
import { hashPassword } from 'better-auth/crypto';
import { PrismaClient } from '../prisma/generated/prisma/client.js';

const databaseUrl = process.env.E2E_DATABASE_URL;
if (!databaseUrl) {
	throw new Error('E2E_DATABASE_URL is required');
}

const cleanup = process.argv.includes('--cleanup');
const suffix = process.env.WAVE1A_FIXTURE_SUFFIX ?? `${Date.now()}-${process.pid}-${randomUUID().slice(0, 8)}`;
const emailPrefix = `belib-wave1a-${suffix}`;
const password = `Wave1A-${randomUUID()}`;

const pool = new Pool({ connectionString: databaseUrl });
const adapter = new PrismaPg(pool);
const prisma = new PrismaClient({ adapter });

async function createCredentialUser(email: string, name: string, roleId: number, deletedAt?: Date) {
	const user = await prisma.user.create({
		data: { email, name, roleId, deletedAt },
	});

	await prisma.account.create({
		data: {
			accountId: String(user.id),
			providerId: 'credential',
			password: await hashPassword(password),
			userId: user.id,
		},
	});

	return user;
}

try {
	if (cleanup) {
		await prisma.user.deleteMany({ where: { email: { startsWith: `${emailPrefix}-` } } });
		process.stdout.write('cleaned');
	} else {
		const role = await prisma.role.upsert({ where: { name: 'user' }, update: {}, create: { name: 'user' } });
		const active = await createCredentialUser(`${emailPrefix}-active@example.com`, 'BeLib Wave 1A Active User', role.id);
		const passwordChange = await createCredentialUser(`${emailPrefix}-password-change@example.com`, 'BeLib Wave 1A Password User', role.id);
		const passwordLogin = await createCredentialUser(`${emailPrefix}-password-login@example.com`, 'BeLib Wave 1A Password Login User', role.id);
		const suspended = await createCredentialUser(`${emailPrefix}-suspended@example.com`, 'BeLib Wave 1A Suspended User', role.id, new Date());

		process.stdout.write(
			JSON.stringify({
				suffix,
				password,
				active: { id: active.id, email: active.email },
				passwordChange: { id: passwordChange.id, email: passwordChange.email },
				passwordLogin: { id: passwordLogin.id, email: passwordLogin.email },
				suspended: { id: suspended.id, email: suspended.email },
			}),
		);
	}
} finally {
	await prisma.$disconnect();
	await pool.end();
}
