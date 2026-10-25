import { describe, expect, test, vi } from 'vitest';
import {
	applyExistingBookProtection,
	assertBackupEvidence,
	createPortfolioSnapshot,
	databaseIdentityHash,
	planExistingBookProtection,
	rollbackExistingBookProtection,
	type PortfolioSnapshot,
} from '../src/lib/portfolioProtection.js';

const databaseUrl = 'postgresql://portfolio:test@db.example.test:5432/belib';

function snapshot(): PortfolioSnapshot {
	return {
		version: 1,
		createdAt: '2026-10-25T00:00:00.000Z',
		gitRevision: 'a'.repeat(40),
		databaseIdentityHash: databaseIdentityHash(databaseUrl),
		storageDriver: 'r2',
		demoAccount: { exists: false, activeGeneralUser: false },
		books: [
			{ id: 10, hadAdminPermission: true, hadUserPermission: true },
			{ id: 11, hadAdminPermission: false, hadUserPermission: false },
		],
		bookFiles: { total: 2, localKeyFormat: 0, r2KeyFormat: 2, unknownKeyFormat: 0 },
	};
}

function mutablePrisma() {
	const permissions = new Set(['1:10', '2:10', '2:99']);
	const client = {
		role: {
			findUnique: vi.fn(({ where }: { where: { name: string } }) =>
				where.name === 'admin' ? { id: 1 } : where.name === 'user' ? { id: 2 } : null),
		},
		book: {
			findMany: vi.fn(({ where }: { where: { id: { in: number[] } } }) =>
				[10, 11, 99].filter((id) => where.id.in.includes(id)).map((id) => ({ id }))),
		},
		roleBookPermission: {
			findMany: vi.fn(({ where }: { where: { bookId: { in: number[] }; roleId: { in: number[] } } }) =>
				[...permissions].map((key) => {
					const [roleId, bookId] = key.split(':').map(Number);
					return { roleId, bookId };
				}).filter(({ roleId, bookId }) => where.bookId.in.includes(bookId) && where.roleId.in.includes(roleId))),
			createMany: vi.fn(({ data }: { data: Array<{ roleId: number; bookId: number }> }) => {
				for (const item of data) permissions.add(`${item.roleId}:${item.bookId}`);
				return { count: data.length };
			}),
			deleteMany: vi.fn(({ where }: { where: { roleId: number; bookId: { in: number[] } } }) => {
				let count = 0;
				for (const bookId of where.bookId.in) count += Number(permissions.delete(`${where.roleId}:${bookId}`));
				return { count };
			}),
		},
		$transaction: <T>(callback: (transaction: typeof client) => Promise<T>) => callback(client),
	};
	return { client, permissions };
}

describe('existing portfolio book protection', () => {
	test('preflightは書名や実キーを含めずID・権限・キーフォーマットだけを記録する', async () => {
		const prisma = {
			role: { findUnique: vi.fn(({ where }) => ({ id: where.name === 'admin' ? 1 : 2 })) },
			book: { findMany: vi.fn().mockResolvedValue([
				{ id: 10, title: 'private title must not leak', roleBookPermissions: [{ roleId: 1 }, { roleId: 2 }] },
			]) },
			bookFile: { findMany: vi.fn().mockResolvedValue([
				{ fileUrl: 'books/12345678-1234-1234-1234-123456789abc.epub' },
				{ fileUrl: '12345678-1234-1234-1234-123456789abc.pdf' },
			]) },
			user: { findFirst: vi.fn().mockResolvedValue({ deletedAt: null, role: { name: 'user' } }) },
		};

		const result = await createPortfolioSnapshot(prisma, {
			databaseUrl,
			gitRevision: 'b'.repeat(40),
			storageDriver: 'r2',
			demoUserEmail: 'demo@example.com',
			now: new Date('2026-10-25T00:00:00.000Z'),
		});

		expect(result.books).toEqual([{ id: 10, hadAdminPermission: true, hadUserPermission: true }]);
		expect(JSON.stringify(result)).not.toContain('private title');
		expect(JSON.stringify(result)).not.toContain('12345678-1234');
		expect(result.bookFiles).toEqual({ total: 2, localKeyFormat: 1, r2KeyFormat: 1, unknownKeyFormat: 0 });
	});

	test('dry-runは初期ID集合だけの最小差分を返す', async () => {
		const { client } = mutablePrisma();
		await expect(planExistingBookProtection(client, snapshot())).resolves.toEqual({
			targetBooks: 2,
			adminPermissionsToAdd: 1,
			userPermissionsToRemove: 1,
			alreadyProtected: 0,
		});
	});

	test('executeは新規デモ書籍を変更せず、対象へadmin権限を保証してuser権限を外す', async () => {
		const { client, permissions } = mutablePrisma();
		await expect(applyExistingBookProtection(client, snapshot())).resolves.toEqual({ protectedBooks: 2 });
		expect([...permissions].sort()).toEqual(['1:10', '1:11', '2:99']);
	});

	test('rollbackはスナップショット時点の対象権限だけを復元する', async () => {
		const { client, permissions } = mutablePrisma();
		await applyExistingBookProtection(client, snapshot());
		await expect(rollbackExistingBookProtection(client, snapshot())).resolves.toEqual({ restoredBooks: 2 });
		expect([...permissions].sort()).toEqual(['1:10', '2:10', '2:99']);
	});

	test('書込み用バックアップ証拠はDB・snapshot hash・restore drillを照合する', () => {
		const value = {
			databaseIdentityHash: snapshot().databaseIdentityHash,
			snapshotSha256: 'c'.repeat(64),
			backupId: 'operator-backup-id',
			checksum: 'sha256:operator-checksum',
			completedAt: '2026-10-25T01:00:00.000Z',
			restoreVerified: true,
		};
		expect(() => assertBackupEvidence(value, snapshot(), 'c'.repeat(64))).not.toThrow();
		expect(() => assertBackupEvidence({ ...value, restoreVerified: false }, snapshot(), 'c'.repeat(64))).toThrow();
	});
});
