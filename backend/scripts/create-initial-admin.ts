import 'dotenv/config';
import { readInitialAdminConfig } from '../src/lib/initialAdminConfig.js';
import { createInitialAdmin } from '../src/lib/initialAdmin.js';

async function main() {
	let config;
	try {
		config = readInitialAdminConfig(process.env);
	} catch (error) {
		console.error(error instanceof Error ? error.message : 'Initial admin configuration is invalid');
		process.exitCode = 1;
		return;
	}

	const { prisma } = await import('../src/lib/prisma.js');
	try {
		const result = await createInitialAdmin(prisma, config);
		console.log(result.created ? `Initial admin created (userId=${result.userId})` : `Initial admin already exists (userId=${result.userId})`);
	} catch {
		console.error('Initial admin creation failed');
		process.exitCode = 1;
	} finally {
		await prisma.$disconnect();
	}
}

void main();
