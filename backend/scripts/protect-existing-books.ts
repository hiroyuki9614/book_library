import 'dotenv/config';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import {
	applyExistingBookProtection,
	assertBackupEvidence,
	assertPortfolioSnapshot,
	databaseIdentityHash,
	planExistingBookProtection,
	rollbackExistingBookProtection,
} from '../src/lib/portfolioProtection.js';

function option(args: string[], name: string) {
	const index = args.indexOf(name);
	return index >= 0 ? args[index + 1] : undefined;
}

async function main() {
	const args = process.argv.slice(2);
	const snapshotPath = option(args, '--snapshot');
	if (!snapshotPath) throw new Error('--snapshot <path> is required');
	const databaseUrl = process.env.DATABASE_URL;
	if (!databaseUrl) throw new Error('DATABASE_URL is required');

	const snapshotBytes = await readFile(resolve(snapshotPath));
	const snapshotSha256 = createHash('sha256').update(snapshotBytes).digest('hex');
	const snapshot: unknown = JSON.parse(snapshotBytes.toString('utf8'));
	assertPortfolioSnapshot(snapshot);
	if (snapshot.databaseIdentityHash !== databaseIdentityHash(databaseUrl)) {
		throw new Error('snapshot belongs to a different database identity');
	}

	const execute = args.includes('--execute');
	const rollback = args.includes('--rollback');
	if (execute && rollback) throw new Error('choose either --execute or --rollback');
	const { prisma } = await import('../src/lib/prisma.js');
	try {
		const plan = await planExistingBookProtection(prisma, snapshot);
		console.log(JSON.stringify({ mode: execute ? 'execute' : rollback ? 'rollback' : 'dry-run', snapshotSha256, ...plan }, null, 2));
		if (!execute && !rollback) return;

		const confirmation = option(args, '--confirm-snapshot-sha256');
		if (confirmation !== snapshotSha256) throw new Error('--confirm-snapshot-sha256 must exactly match the dry-run output');
		const evidencePath = option(args, '--backup-evidence');
		if (!evidencePath) throw new Error('--backup-evidence <path> is required for writes');
		const evidence: unknown = JSON.parse(await readFile(resolve(evidencePath), 'utf8'));
		assertBackupEvidence(evidence, snapshot, snapshotSha256);

		const result = rollback
			? await rollbackExistingBookProtection(prisma, snapshot)
			: await applyExistingBookProtection(prisma, snapshot);
		console.log(JSON.stringify({ success: true, ...result }, null, 2));
	} finally {
		await prisma.$disconnect();
	}
}

main().catch((error) => {
	console.error(error instanceof Error ? error.message : 'Existing-book protection failed');
	process.exitCode = 1;
});
