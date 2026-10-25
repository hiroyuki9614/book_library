import 'dotenv/config';
import { execFileSync } from 'node:child_process';
import { writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
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
	const databaseUrl = process.env.DATABASE_URL;
	const storageDriver = process.env.BOOK_FILE_STORAGE_DRIVER;
	if (!databaseUrl) throw new Error('DATABASE_URL is required');
	if (storageDriver !== 'local' && storageDriver !== 'r2') throw new Error('BOOK_FILE_STORAGE_DRIVER must be local or r2');
	const outputPath = outputPathFromArgs(process.argv.slice(2));
	const gitRevision = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
	const { prisma } = await import('../src/lib/prisma.js');
	try {
		const snapshot = await createPortfolioSnapshot(prisma, {
			databaseUrl,
			storageDriver,
			gitRevision,
			demoUserEmail: process.env.DEMO_USER_EMAIL?.trim().toLowerCase(),
		});
		await writeFile(outputPath, `${JSON.stringify(snapshot, null, 2)}\n`, { flag: 'wx', mode: 0o600 });
		console.log(JSON.stringify({
			outputPath,
			gitRevision,
			storageDriver,
			bookCount: snapshot.books.length,
			bookFileCount: snapshot.bookFiles.total,
			demoAccountExists: snapshot.demoAccount.exists,
		}, null, 2));
	} finally {
		await prisma.$disconnect();
	}
}

main().catch((error) => {
	console.error(error instanceof Error ? error.message : 'Portfolio preflight failed');
	process.exitCode = 1;
});
