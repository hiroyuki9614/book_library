import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import {
	assertBackupEvidence, assertPortfolioSnapshot, createPortfolioSnapshot,
	verifyProtectedState, type PortfolioSnapshot,
} from './portfolioProtection.js';
import { PortfolioSafetyError, readPortfolioRuntime } from './portfolioRuntime.js';

export function option(args: string[], name: string) {
	const index = args.indexOf(name);
	return index >= 0 ? args[index + 1] : undefined;
}

export function assertStorageCompatibility(snapshot: PortfolioSnapshot) {
	const files = snapshot.bookFiles;
	if (files.total !== files.localKeyFormat + files.r2KeyFormat + files.unknownKeyFormat ||
		files.unknownKeyFormat > 0 || (snapshot.storageDriver === 'r2' ? files.localKeyFormat > 0 : files.r2KeyFormat > 0)) {
		throw new PortfolioSafetyError('Storage key format does not match the live driver; stop for a separately reviewed migration');
	}
}

export function assertSamePortfolioTarget(snapshot: PortfolioSnapshot, live: PortfolioSnapshot) {
	for (const key of ['gitRevision', 'imageId', 'databaseIdentityHash', 'storageDriver', 'storageIdentityHash', 'fileInventorySha256'] as const) {
		if (snapshot[key] !== live[key]) throw new PortfolioSafetyError('Live revision, database, storage or file inventory differs from the snapshot');
	}
	if (JSON.stringify(snapshot.books.map((book) => book.id).sort((a, b) => a - b)) !==
		JSON.stringify(live.books.map((book) => book.id).sort((a, b) => a - b))) {
		throw new PortfolioSafetyError('Book inventory changed; stop and review before taking a new baseline');
	}
	assertStorageCompatibility(snapshot);
	assertStorageCompatibility(live);
	if (live.demoAccount.exists && !live.demoAccount.activeGeneralUser) {
		throw new PortfolioSafetyError('Existing demo account is not an active general user');
	}
}

export async function loadPortfolioGate(args: string[], execute: boolean) {
	if (args.includes('--rollback')) throw new PortfolioSafetyError('Permission rollback is disabled; use a safe forward fix or isolated restore');
	const runtime = await readPortfolioRuntime();
	const path = option(args, '--snapshot');
	if (!path) throw new PortfolioSafetyError('--snapshot is required');
	const bytes = await readFile(path);
	const snapshot: unknown = JSON.parse(bytes.toString('utf8'));
	assertPortfolioSnapshot(snapshot);
	const snapshotSha256 = createHash('sha256').update(bytes).digest('hex');
	if (execute) {
		if (option(args, '--confirm-snapshot-sha256') !== snapshotSha256) {
			throw new PortfolioSafetyError('--confirm-snapshot-sha256 must match the reviewed snapshot');
		}
		const evidencePath = option(args, '--backup-evidence');
		if (!evidencePath) throw new PortfolioSafetyError('--backup-evidence is required for writes');
		assertBackupEvidence(JSON.parse(await readFile(evidencePath, 'utf8')), snapshot, snapshotSha256);
	}
	return { runtime, snapshot, snapshotSha256 };
}

export async function verifyLivePortfolioGate(prisma: any, gate: Awaited<ReturnType<typeof loadPortfolioGate>>, requireProtected: boolean) {
	const live = await createPortfolioSnapshot(prisma, { ...gate.runtime, demoUserEmail: process.env.DEMO_USER_EMAIL?.trim().toLowerCase() });
	assertSamePortfolioTarget(gate.snapshot, live);
	if (requireProtected) await verifyProtectedState(prisma, gate.snapshot);
}
