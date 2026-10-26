import { describe, expect, test, vi } from 'vitest';
import { createHash } from 'node:crypto';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as runtimeModule from '../src/lib/portfolioRuntime.js';
import { assertRuntimeRevision, portfolioApiBaseUrl, readPortfolioStorageIdentity, reportPortfolioFailure } from '../src/lib/portfolioRuntime.js';
import { assertSamePortfolioTarget, assertStorageCompatibility, loadPortfolioGate, verifyLivePortfolioGate } from '../src/lib/portfolioGate.js';
import { createPortfolioSnapshot, type PortfolioSnapshot } from '../src/lib/portfolioProtection.js';

const runtime = {
	gitRevision: 'a'.repeat(40), imageId: `sha256:${'b'.repeat(64)}`,
	databaseUrl: 'postgresql://synthetic:synthetic@localhost/test',
	storageDriver: 'r2' as const, storageIdentityHash: 'c'.repeat(64),
};

function prismaFixture() {
	return {
		role: { findUnique: vi.fn(({ where }) => ({ id: where.name === 'admin' ? 1 : 2 })) },
		book: { findMany: vi.fn().mockResolvedValue([{ id: 1, roleBookPermissions: [{ roleId: 1 }, { roleId: 2 }] }]) },
		bookFile: { findMany: vi.fn().mockResolvedValue([{ fileUrl: 'books/12345678-1234-1234-1234-123456789abc.pdf' }]) },
		roleBookPermission: { findMany: vi.fn().mockResolvedValue([{ bookId: 1, roleId: 1 }, { bookId: 1, roleId: 2 }]) },
	};
}

describe('portfolio runtime and write gates', () => {
	test('registration cannot check one target and send admin credentials to another', () => {
		expect(portfolioApiBaseUrl({ APP_PORT: '3001' })).toBe('http://127.0.0.1:3001');
		for (const environment of [
			{ APP_PORT: '3000@external.test' }, { APP_PORT: '0' },
			{ PORTFOLIO_API_BASE_URL: 'https://external.test' },
		]) expect(() => portfolioApiBaseUrl(environment)).toThrow();
	});

	test('shared execute gate requires exact snapshot confirmation and backup evidence before database access', async () => {
		const readRuntime = vi.spyOn(runtimeModule, 'readPortfolioRuntime').mockResolvedValue(runtime);
		const directory = await mkdtemp(join(tmpdir(), 'portfolio-evidence-'));
		try {
			const snapshot = await createPortfolioSnapshot(prismaFixture(), runtime);
			const bytes = JSON.stringify(snapshot);
			const path = join(directory, 'snapshot.json');
			await writeFile(path, bytes);
			const args = ['--snapshot', path];
			await expect(loadPortfolioGate(args, false)).resolves.toMatchObject({ snapshot });
			await expect(loadPortfolioGate(args, true)).rejects.toThrow('confirm-snapshot-sha256');
			const hash = createHash('sha256').update(bytes).digest('hex');
			await expect(loadPortfolioGate([...args, '--confirm-snapshot-sha256', hash], true)).rejects.toThrow('backup-evidence');
		} finally {
			readRuntime.mockRestore();
			await rm(directory, { recursive: true, force: true });
		}
	});
	test('storage identity rejects unknown configuration and distinguishes actual local roots and R2 buckets', async () => {
		for (const environment of [ {}, { BOOK_FILE_STORAGE_DRIVER: 'local' },
			{ BOOK_FILE_STORAGE_DRIVER: 'local', BOOK_FILE_STORAGE_ROOT: 'relative' },
			{ BOOK_FILE_STORAGE_DRIVER: 'r2', R2_BUCKET_NAME: 'bucket' },
			{ BOOK_FILE_STORAGE_DRIVER: 'r2', R2_ENDPOINT: 'https://secret@r2.test', R2_BUCKET_NAME: 'bucket' },
		]) await expect(readPortfolioStorageIdentity(environment)).rejects.toThrow();
		const root = await mkdtemp(join(tmpdir(), 'portfolio-identity-'));
		try {
			const local = await readPortfolioStorageIdentity({ BOOK_FILE_STORAGE_DRIVER: 'local', BOOK_FILE_STORAGE_ROOT: root });
			expect(local.storageIdentityHash).toMatch(/^[a-f0-9]{64}$/);
			const r2 = { BOOK_FILE_STORAGE_DRIVER: 'r2', R2_ACCOUNT_ID: 'synthetic', R2_BUCKET_NAME: 'one' };
			const first = await readPortfolioStorageIdentity(r2);
			const other = await readPortfolioStorageIdentity({ ...r2, R2_BUCKET_NAME: 'two' });
			expect(first.storageIdentityHash).not.toBe(other.storageIdentityHash);
			expect(first.storageIdentityHash).not.toBe(local.storageIdentityHash);
			expect(JSON.stringify(first)).not.toContain('synthetic');
		} finally {
			await rm(root, { recursive: true, force: true });
		}
	});
	test('rejects missing, fabricated-format or mismatched revision/image evidence', () => {
		expect(assertRuntimeRevision(runtime.gitRevision, runtime.gitRevision, runtime.imageId)).toEqual({ gitRevision: runtime.gitRevision, imageId: runtime.imageId });
		for (const [built, expected, image] of [
			['unknown', runtime.gitRevision, runtime.imageId],
			[runtime.gitRevision, undefined, runtime.imageId],
			[runtime.gitRevision, 'd'.repeat(40), runtime.imageId],
			[runtime.gitRevision, runtime.gitRevision, 'latest'],
			[runtime.gitRevision, runtime.gitRevision, undefined],
		]) expect(() => assertRuntimeRevision(built!, expected, image)).toThrow();
	});

	test('rollback CLI is rejected before runtime, file or DB access', async () => {
		await expect(loadPortfolioGate(['--rollback'], true)).rejects.toThrow('rollback is disabled');
	});

	test('rejects mixed, unknown, inconsistent counts and opposite-driver keys', async () => {
		const snapshot = await createPortfolioSnapshot(prismaFixture(), runtime);
		expect(() => assertStorageCompatibility(snapshot)).not.toThrow();
		for (const bookFiles of [
			{ total: 2, localKeyFormat: 1, r2KeyFormat: 1, unknownKeyFormat: 0 },
			{ total: 1, localKeyFormat: 0, r2KeyFormat: 0, unknownKeyFormat: 1 },
			{ total: 2, localKeyFormat: 0, r2KeyFormat: 1, unknownKeyFormat: 0 },
		]) expect(() => assertStorageCompatibility({ ...snapshot, bookFiles })).toThrow();
		expect(() => assertStorageCompatibility({ ...snapshot, storageDriver: 'local' })).toThrow();
	});

	test('rejects changed runtime, storage, DB, keys and missing/additional books', async () => {
		const snapshot = await createPortfolioSnapshot(prismaFixture(), runtime);
		const changes: Partial<PortfolioSnapshot>[] = [
			{ gitRevision: 'd'.repeat(40) }, { imageId: `sha256:${'d'.repeat(64)}` },
			{ storageIdentityHash: 'd'.repeat(64) }, { databaseIdentityHash: 'd'.repeat(64) },
			{ fileInventorySha256: 'd'.repeat(64) }, { books: [] },
			{ books: [...snapshot.books, { id: 2, hadAdminPermission: true, hadUserPermission: true }] },
			{ demoAccount: { exists: true, activeGeneralUser: false } },
		];
		expect(() => assertSamePortfolioTarget(snapshot, snapshot)).not.toThrow();
		for (const change of changes) expect(() => assertSamePortfolioTarget(snapshot, { ...snapshot, ...change })).toThrow();
	});

	test('account creation and registration gate refuses still-public private books', async () => {
		const prisma = prismaFixture();
		const snapshot = await createPortfolioSnapshot(prisma, runtime);
		const gate = { snapshot, runtime, snapshotSha256: 'e'.repeat(64) };
		await expect(verifyLivePortfolioGate(prisma, gate, false)).resolves.toBeUndefined();
		await expect(verifyLivePortfolioGate(prisma, gate, true)).rejects.toThrow('authorization verification failed');
		prisma.roleBookPermission.findMany.mockResolvedValue([{ bookId: 1, roleId: 1 }]);
		await expect(verifyLivePortfolioGate(prisma, gate, true)).resolves.toBeUndefined();
	});

	test('raw dependency errors cannot leak credentials, keys or private titles to CLI logs', () => {
		const log = vi.spyOn(console, 'error').mockImplementation(() => {});
		const previous = process.exitCode;
		try {
			reportPortfolioFailure(new Error('postgresql://private:password@host/private-title'));
			expect(log.mock.calls.flat().join(' ')).not.toMatch(/password|private-title/);
			expect(process.exitCode).toBe(1);
		} finally {
			process.exitCode = previous;
			log.mockRestore();
		}
	});
});
