import { createHash } from 'node:crypto';

export const PORTFOLIO_SNAPSHOT_VERSION = 2;

export type PortfolioBookSnapshot = {
	id: number;
	hadAdminPermission: boolean;
	hadUserPermission: boolean;
};

export type PortfolioSnapshot = {
	version: typeof PORTFOLIO_SNAPSHOT_VERSION;
	createdAt: string;
	gitRevision: string;
	imageId: string;
	storageIdentityHash: string;
	databaseIdentityHash: string;
	fileInventorySha256: string;
	storageDriver: 'local' | 'r2';
	demoAccount: {
		exists: boolean;
		activeGeneralUser: boolean;
	};
	books: PortfolioBookSnapshot[];
	bookFiles: {
		total: number;
		localKeyFormat: number;
		r2KeyFormat: number;
		unknownKeyFormat: number;
	};
};

type CoordinatedBackupEvidence = {
	databaseIdentityHash: string;
	snapshotSha256: string;
	backupId: string;
	databaseSha256: string;
	storageBackupId: string;
	storageManifestSha256: string;
	storageIdentityHash: string;
	restoreReportSha256: string;
	restoreTarget: string;
	approvedBy: string;
	consistencyBoundary: string;
	completedAt: string;
	restoreVerified: boolean;
};

// Scoped to the R2 portfolio operation: existing objects are never rewritten/deleted.
export type MvpDatabaseBackupEvidence = {
	policy: 'portfolio-mvp-db-v1';
	databaseIdentityHash: string;
	storageIdentityHash: string;
	snapshotSha256: string;
	backupId: string;
	databaseSha256: string;
	databaseBytes: number;
	databaseFormat: 'postgres-custom';
	privateAccessVerified: true;
	archiveReadable: true;
	existingR2ObjectsVerified: true;
	recoveryProcedureRef: string;
	approvedBy: string;
	consistencyBoundary: string;
	completedAt: string;
};
export type BackupEvidence = CoordinatedBackupEvidence | MvpDatabaseBackupEvidence;

type PortfolioPrisma = any;

export function databaseIdentityHash(databaseUrl: string) {
	const parsed = new URL(databaseUrl);
	const identity = `${parsed.protocol}//${parsed.username}@${parsed.hostname}:${parsed.port || '5432'}${parsed.pathname}`;
	return createHash('sha256').update(identity).digest('hex');
}

function classifyStorageKey(fileUrl: string) {
	if (/^books\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(epub|pdf)$/.test(fileUrl)) return 'r2';
	if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(epub|pdf)$/.test(fileUrl)) return 'local';
	return 'unknown';
}

export async function createPortfolioSnapshot(
	prisma: PortfolioPrisma,
	input: {
		databaseUrl: string;
		gitRevision: string;
		imageId: string;
		storageIdentityHash: string;
		storageDriver: 'local' | 'r2';
		demoUserEmail?: string;
		now?: Date;
	},
): Promise<PortfolioSnapshot> {
	const [adminRole, userRole, books, bookFiles, demoUser] = await Promise.all([
		prisma.role.findUnique({ where: { name: 'admin' }, select: { id: true } }),
		prisma.role.findUnique({ where: { name: 'user' }, select: { id: true } }),
		prisma.book.findMany({
			orderBy: { id: 'asc' },
			select: {
				id: true,
				roleBookPermissions: { select: { roleId: true } },
			},
		}),
		prisma.bookFile.findMany({ orderBy: { id: 'asc' }, select: { id: true, bookId: true, fileUrl: true, fileSize: true, fileHash: true } }),
		input.demoUserEmail
			? prisma.user.findFirst({
					where: { email: { equals: input.demoUserEmail, mode: 'insensitive' } },
					select: { deletedAt: true, role: { select: { name: true } } },
				})
			: Promise.resolve(null),
	]);

	if (!adminRole || !userRole) {
		throw new Error('admin and user roles must exist before the portfolio preflight');
	}

	const keyCounts = { local: 0, r2: 0, unknown: 0 };
	for (const file of bookFiles) keyCounts[classifyStorageKey(file.fileUrl) as keyof typeof keyCounts] += 1;

	return {
		version: PORTFOLIO_SNAPSHOT_VERSION,
		createdAt: (input.now ?? new Date()).toISOString(),
		gitRevision: input.gitRevision,
		imageId: input.imageId,
		storageIdentityHash: input.storageIdentityHash,
		databaseIdentityHash: databaseIdentityHash(input.databaseUrl),
		storageDriver: input.storageDriver,
		fileInventorySha256: createHash('sha256').update(JSON.stringify(bookFiles)).digest('hex'),
		demoAccount: {
			exists: Boolean(demoUser),
			activeGeneralUser: Boolean(demoUser && !demoUser.deletedAt && demoUser.role.name === 'user'),
		},
		books: books.map((book: { id: number; roleBookPermissions: Array<{ roleId: number }> }) => ({
			id: book.id,
			hadAdminPermission: book.roleBookPermissions.some((permission) => permission.roleId === adminRole.id),
			hadUserPermission: book.roleBookPermissions.some((permission) => permission.roleId === userRole.id),
		})),
		bookFiles: {
			total: bookFiles.length,
			localKeyFormat: keyCounts.local,
			r2KeyFormat: keyCounts.r2,
			unknownKeyFormat: keyCounts.unknown,
		},
	};
}

export function assertPortfolioSnapshot(value: unknown): asserts value is PortfolioSnapshot {
	const snapshot = value as Partial<PortfolioSnapshot> | null;
	if (
		!snapshot || snapshot.version !== PORTFOLIO_SNAPSHOT_VERSION ||
		typeof snapshot.createdAt !== 'string' || !Number.isFinite(Date.parse(snapshot.createdAt)) ||
		typeof snapshot.gitRevision !== 'string' || !/^[0-9a-f]{40}$/.test(snapshot.gitRevision) ||
		typeof snapshot.fileInventorySha256 !== 'string' || !/^[0-9a-f]{64}$/.test(snapshot.fileInventorySha256) ||
		typeof snapshot.imageId !== 'string' || !/^sha256:[0-9a-f]{64}$/.test(snapshot.imageId) ||
		typeof snapshot.storageIdentityHash !== 'string' || !/^[0-9a-f]{64}$/.test(snapshot.storageIdentityHash) ||
		typeof snapshot.databaseIdentityHash !== 'string' || !/^[0-9a-f]{64}$/.test(snapshot.databaseIdentityHash) ||
		(snapshot.storageDriver !== 'local' && snapshot.storageDriver !== 'r2') ||
		typeof snapshot.demoAccount?.exists !== 'boolean' || typeof snapshot.demoAccount?.activeGeneralUser !== 'boolean' ||
		!snapshot.bookFiles || !['total', 'localKeyFormat', 'r2KeyFormat', 'unknownKeyFormat'].every((key) =>
			Number.isSafeInteger(snapshot.bookFiles?.[key as keyof PortfolioSnapshot['bookFiles']]) &&
			(snapshot.bookFiles?.[key as keyof PortfolioSnapshot['bookFiles']] ?? -1) >= 0) ||
		!Array.isArray(snapshot.books) || snapshot.books.some((book) =>
			!Number.isSafeInteger(book?.id) || book.id < 1 ||
			typeof book.hadAdminPermission !== 'boolean' || typeof book.hadUserPermission !== 'boolean') ||
		new Set(snapshot.books.map((book) => book.id)).size !== snapshot.books.length
	) {
		throw new Error('portfolio snapshot is invalid');
	}
}

export function assertBackupEvidence(value: unknown, snapshot: PortfolioSnapshot, snapshotSha256: string): asserts value is BackupEvidence {
	const mvp = value as Partial<MvpDatabaseBackupEvidence> | null;
	if (mvp?.policy === 'portfolio-mvp-db-v1') {
		if (
			snapshot.storageDriver !== 'r2' ||
			mvp.databaseIdentityHash !== snapshot.databaseIdentityHash ||
			mvp.storageIdentityHash !== snapshot.storageIdentityHash ||
			mvp.snapshotSha256 !== snapshotSha256 ||
			typeof mvp.databaseSha256 !== 'string' || !/^[0-9a-f]{64}$/.test(mvp.databaseSha256) ||
			!Number.isSafeInteger(mvp.databaseBytes) || (mvp.databaseBytes ?? 0) <= 0 ||
			mvp.databaseFormat !== 'postgres-custom' || mvp.privateAccessVerified !== true ||
			mvp.archiveReadable !== true || mvp.existingR2ObjectsVerified !== true ||
			!['backupId', 'recoveryProcedureRef', 'approvedBy', 'consistencyBoundary'].every((key) =>
				typeof mvp[key as keyof MvpDatabaseBackupEvidence] === 'string' &&
				(mvp[key as keyof MvpDatabaseBackupEvidence] as string).trim()) ||
			typeof mvp.completedAt !== 'string' || !Number.isFinite(Date.parse(mvp.completedAt)) ||
			Date.parse(mvp.completedAt) < Date.parse(snapshot.createdAt) || Date.parse(mvp.completedAt) > Date.now()
		) throw new Error('MVP database backup evidence does not match the snapshot or is incomplete');
		return;
	}
	// Unknown policy names must not silently fall back to another evidence format.
	if (mvp && 'policy' in mvp) throw new Error('Unknown portfolio backup policy');
	const evidence = value as Partial<CoordinatedBackupEvidence> | null;
	if (
		!evidence || evidence.databaseIdentityHash !== snapshot.databaseIdentityHash ||
		evidence.snapshotSha256 !== snapshotSha256 || evidence.restoreVerified !== true ||
		typeof evidence.backupId !== 'string' || !evidence.backupId.trim() ||
		evidence.storageIdentityHash !== snapshot.storageIdentityHash ||
		!['databaseSha256', 'storageManifestSha256', 'restoreReportSha256'].every((key) =>
			typeof evidence[key as keyof CoordinatedBackupEvidence] === 'string' && /^[0-9a-f]{64}$/.test(evidence[key as keyof CoordinatedBackupEvidence] as string)) ||
		!['storageBackupId', 'restoreTarget', 'approvedBy', 'consistencyBoundary'].every((key) =>
			typeof evidence[key as keyof CoordinatedBackupEvidence] === 'string' && (evidence[key as keyof CoordinatedBackupEvidence] as string).trim()) ||
		typeof evidence.completedAt !== 'string' || !Number.isFinite(Date.parse(evidence.completedAt)) ||
		Date.parse(evidence.completedAt) < Date.parse(snapshot.createdAt) || Date.parse(evidence.completedAt) > Date.now()
	) {
		throw new Error('backup evidence does not match the snapshot or is incomplete');
	}
}

async function readProtectionState(prisma: PortfolioPrisma, snapshot: PortfolioSnapshot) {
	const [adminRole, userRole] = await Promise.all([
		prisma.role.findUnique({ where: { name: 'admin' }, select: { id: true } }),
		prisma.role.findUnique({ where: { name: 'user' }, select: { id: true } }),
	]);
	if (!adminRole || !userRole) throw new Error('admin and user roles are required');

	const bookIds = snapshot.books.map((book) => book.id);
	const [books, permissions] = await Promise.all([
		prisma.book.findMany({ where: { id: { in: bookIds } }, select: { id: true } }),
		prisma.roleBookPermission.findMany({
			where: { bookId: { in: bookIds }, roleId: { in: [adminRole.id, userRole.id] } },
			select: { bookId: true, roleId: true },
		}),
	]);
	if (books.length !== bookIds.length) throw new Error('one or more snapshotted books no longer exist');

	const adminBookIds = new Set<number>();
	const userBookIds = new Set<number>();
	for (const permission of permissions) {
		if (permission.roleId === adminRole.id) adminBookIds.add(permission.bookId);
		if (permission.roleId === userRole.id) userBookIds.add(permission.bookId);
	}
	return { adminRoleId: adminRole.id, userRoleId: userRole.id, bookIds, adminBookIds, userBookIds };
}

export async function planExistingBookProtection(prisma: PortfolioPrisma, snapshot: PortfolioSnapshot) {
	const state = await readProtectionState(prisma, snapshot);
	return {
		targetBooks: state.bookIds.length,
		adminPermissionsToAdd: state.bookIds.filter((id) => !state.adminBookIds.has(id)).length,
		userPermissionsToRemove: state.bookIds.filter((id) => state.userBookIds.has(id)).length,
		alreadyProtected: state.bookIds.filter((id) => state.adminBookIds.has(id) && !state.userBookIds.has(id)).length,
	};
}

export async function verifyProtectedState(prisma: PortfolioPrisma, snapshot: PortfolioSnapshot) {
	const state = await readProtectionState(prisma, snapshot);
	if (state.bookIds.some((id) => !state.adminBookIds.has(id)) || state.userBookIds.size > 0) {
		throw new Error('post-write authorization verification failed');
	}
}

export async function applyExistingBookProtection(prisma: PortfolioPrisma, snapshot: PortfolioSnapshot) {
	return prisma.$transaction(async (transaction: PortfolioPrisma) => {
		const state = await readProtectionState(transaction, snapshot);
		const missingAdminIds = state.bookIds.filter((id) => !state.adminBookIds.has(id));
		if (missingAdminIds.length > 0) {
			await transaction.roleBookPermission.createMany({
				data: missingAdminIds.map((bookId) => ({ roleId: state.adminRoleId, bookId })),
				skipDuplicates: true,
			});
		}
		await transaction.roleBookPermission.deleteMany({
			where: { roleId: state.userRoleId, bookId: { in: state.bookIds } },
		});
		await verifyProtectedState(transaction, snapshot);
		return { protectedBooks: state.bookIds.length };
	});
}

export async function rollbackExistingBookProtection(_prisma: PortfolioPrisma, _snapshot: PortfolioSnapshot): Promise<never> {
	throw new Error('Permission rollback is disabled: restoring user permissions exposes private books to all general users, including shared demo credentials. Use a safe forward fix or isolated restore.');
}
