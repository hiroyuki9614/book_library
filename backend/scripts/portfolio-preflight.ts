import 'dotenv/config';
import { writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { readPortfolioRuntime, reportPortfolioFailure } from '../src/lib/portfolioRuntime.js';
import { assertStorageCompatibility } from '../src/lib/portfolioGate.js';
import { createPortfolioSnapshot } from '../src/lib/portfolioProtection.js';

function outputPathFromArgs(args: string[]) {
	const index = args.indexOf('--output');
	if (index < 0 || !args[index + 1]) throw new Error('--output <new snapshot path> is required');
	if (args.some((arg) => ['--execute', '--write', '--apply'].includes(arg))) {
		throw new Error('portfolio preflight is read-only');
	}
	return resolve(args[index + 1]);
}

async function main() {
	const outputPath = outputPathFromArgs(process.argv.slice(2));
	const runtime = await readPortfolioRuntime();
	const { prisma } = await import('../src/lib/prisma.js');
	try {
		const snapshot = await createPortfolioSnapshot(prisma, {
			...runtime,
			demoUserEmail: process.env.DEMO_USER_EMAIL?.trim().toLowerCase(),
		});
		await writeFile(outputPath, `${JSON.stringify(snapshot, null, 2)}\n`, { flag: 'wx', mode: 0o600 });
		console.log(JSON.stringify({
			outputPath,
			gitRevision: runtime.gitRevision,
			imageId: runtime.imageId,
			storageDriver: runtime.storageDriver,
			bookCount: snapshot.books.length,
			bookFileCount: snapshot.bookFiles.total,
			demoAccountExists: snapshot.demoAccount.exists,
		}, null, 2));
		assertStorageCompatibility(snapshot);
	} finally {
		await prisma.$disconnect();
	}
}

main().catch(reportPortfolioFailure);
