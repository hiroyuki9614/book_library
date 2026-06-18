import 'dotenv/config';
import { Pool } from 'pg';
import { PrismaPg } from '@prisma/adapter-pg';
import { PrismaClient } from './generated/prisma/client.js';

const connectionString = process.env.DATABASE_URL;

if (!connectionString) {
	throw new Error('DATABASE_URL is not defined');
}

const pool = new Pool({ connectionString });
const adapter = new PrismaPg(pool);
const prisma = new PrismaClient({ adapter });

async function main() {
	await prisma.role.upsert({
		where: { name: 'user' },
		update: {},
		create: {
			name: 'user',
		},
	});

	await prisma.role.upsert({
		where: { name: 'admin' },
		update: {},
		create: {
			name: 'admin',
		},
	});

	const userRole = await prisma.role.findUniqueOrThrow({
		where: { name: 'user' },
	});

	const adminRole = await prisma.role.findUniqueOrThrow({
		where: { name: 'admin' },
	});

	await prisma.user.upsert({
		where: { email: 'dummy@example.com' },
		update: {},
		create: {
			email: 'dummy@example.com',
			name: 'Dummy User',
			passwordHash: 'dummyhash',
			roleId: userRole.id,
		},
	});

	await prisma.user.upsert({
		where: { email: 'admin@example.com' },
		update: {},
		create: {
			email: 'admin@example.com',
			name: 'Admin User',
			passwordHash: 'adminhash',
			roleId: adminRole.id,
		},
	});
}

main()
	.catch((error) => {
		console.error(error);
		process.exitCode = 1;
	})
	.finally(async () => {
		await prisma.$disconnect();
		await pool.end();
	});
