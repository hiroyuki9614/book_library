import 'dotenv/config';
import { readDemoUserConfig } from '../src/lib/demoUserConfig.js';
import { createDemoUser } from '../src/lib/demoUser.js';

async function main() {
	let config;
	try {
		config = readDemoUserConfig(process.env);
	} catch (error) {
		console.error(error instanceof Error ? error.message : 'Demo user configuration is invalid');
		process.exitCode = 1;
		return;
	}

	const { prisma } = await import('../src/lib/prisma.js');
	try {
		const result = await createDemoUser(prisma, config);
		console.log(result.created ? `Demo user created (userId=${result.userId})` : `Demo user already exists (userId=${result.userId})`);
	} catch (error) {
		console.error(error instanceof Error ? error.message : 'Demo user creation failed');
		process.exitCode = 1;
	} finally {
		await prisma.$disconnect();
	}
}

void main();
