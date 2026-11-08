import { chmod, lstat, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { randomBytes } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import { afterEach, describe, expect, test } from 'vitest';
import {
	assertDirectoryWritableByNode,
	assertFreshOrSeededCatalog,
	assertSeedRuntimeUser,
	assertStagingSeedEnvironment,
	createStagingEpubFixture,
	createStagingPdfFixture,
	readStagingSeedCredentials,
	STAGING_FIXTURE_ROOT,
	withStagingFixtureFiles,
} from './seed-staging.js';

const databasePassword = randomBytes(32).toString('hex');
const authSecret = randomBytes(32).toString('hex');
const adminPassword = randomBytes(32).toString('hex');
const readerPassword = randomBytes(32).toString('hex');

const validEnvironment = (): NodeJS.ProcessEnv => ({
	DATABASE_URL: `postgresql://belib_staging:${databasePassword}@db:5432/belib_staging`,
	BETTER_AUTH_SECRET: authSecret,
	ENVIRONMENT: 'test',
	BOOK_FILE_STORAGE_DRIVER: 'local',
	BOOK_FILE_STORAGE_ROOT: STAGING_FIXTURE_ROOT,
});

const temporaryDirectories: string[] = [];

function syntheticFixture(fileName: string, bytes = Buffer.from(`synthetic content for ${fileName}`)) {
	const isPdf = fileName.endsWith('.pdf');
	return {
		fileName,
		extension: isPdf ? ('pdf' as const) : ('epub' as const),
		mimeType: isPdf ? ('application/pdf' as const) : ('application/epub+zip' as const),
		title: fileName,
		bytes,
	};
}

afterEach(async () => {
	await Promise.all(temporaryDirectories.splice(0).map((path) => rm(path, { recursive: true, force: true })));
});

function testCrc32(bytes: Buffer) {
	let crc = 0xffffffff;
	for (const byte of bytes) {
		crc ^= byte;
		for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
	}
	return (crc ^ 0xffffffff) >>> 0;
}

function readEpubEntries(bytes: Buffer) {
	const entries: Array<{ name: string; method: number; checksum: number; bytes: Buffer }> = [];
	let offset = 0;
	while (bytes.readUInt32LE(offset) === 0x04034b50) {
		const method = bytes.readUInt16LE(offset + 8);
		const compressedSize = bytes.readUInt32LE(offset + 18);
		const nameLength = bytes.readUInt16LE(offset + 26);
		const extraLength = bytes.readUInt16LE(offset + 28);
		const nameStart = offset + 30;
		const dataStart = nameStart + nameLength + extraLength;
		entries.push({
			name: bytes.subarray(nameStart, nameStart + nameLength).toString('utf8'),
			method,
			checksum: bytes.readUInt32LE(offset + 14),
			bytes: bytes.subarray(dataStart, dataStart + compressedSize),
		});
		offset = dataStart + compressedSize;
	}
	return { entries, centralOffset: offset };
}

describe('offline synthetic staging seed safety', () => {
	test('accepts only the exact staging database identity and local fixture root', () => {
		expect(() => assertStagingSeedEnvironment(validEnvironment())).not.toThrow();
	});

	test('rejects a shared database password and Better Auth secret', () => {
		const env = validEnvironment();
		env.BETTER_AUTH_SECRET = databasePassword;
		expect(() => assertStagingSeedEnvironment(env)).toThrow(/must be distinct/);
	});

	test.each([
		['wrong host', (env: NodeJS.ProcessEnv) => (env.DATABASE_URL = `postgresql://belib_staging:${databasePassword}@production-db:5432/belib_staging`)],
		['wrong database user', (env: NodeJS.ProcessEnv) => (env.DATABASE_URL = `postgresql://production:${databasePassword}@db:5432/belib_staging`)],
		['wrong database name', (env: NodeJS.ProcessEnv) => (env.DATABASE_URL = `postgresql://belib_staging:${databasePassword}@db:5432/production`)],
		['wrong storage root', (env: NodeJS.ProcessEnv) => (env.BOOK_FILE_STORAGE_ROOT = '/srv/books')],
		['non-local storage', (env: NodeJS.ProcessEnv) => (env.BOOK_FILE_STORAGE_DRIVER = 'r2')],
		['R2 configuration', (env: NodeJS.ProcessEnv) => (env.R2_ACCESS_KEY_ID = 'synthetic-only-test-value')],
		['production configuration', (env: NodeJS.ProcessEnv) => (env.PRODUCTION_DATABASE_URL = 'synthetic-only-test-value')],
	])('fails closed for %s', (_caseName, changeEnvironment) => {
		const env = validEnvironment();
		changeEnvironment(env);
		expect(() => assertStagingSeedEnvironment(env)).toThrow();
	});

	test('allows an empty catalog or only the two active synthetic seed books', () => {
		expect(() => assertFreshOrSeededCatalog([])).not.toThrow();
		expect(() =>
			assertFreshOrSeededCatalog([
				{
					title: 'BeLib i5 Synthetic EPUB Fixture',
					authorName: 'BeLib Staging Fixture',
					description: 'Deterministic synthetic fixture for isolated BeLib staging.',
					deletedAt: null,
				},
				{
					title: 'BeLib i5 Synthetic PDF Fixture',
					authorName: 'BeLib Staging Fixture',
					description: 'Deterministic synthetic fixture for isolated BeLib staging.',
					deletedAt: null,
				},
			]),
		).not.toThrow();
	});

	test('refuses to seed over any other, duplicate, or soft-deleted book', () => {
		expect(() =>
			assertFreshOrSeededCatalog([
				{ title: 'Existing book', authorName: 'A reader', description: null, deletedAt: null },
			]),
		).toThrow(/fresh book catalog/);
		expect(() =>
			assertFreshOrSeededCatalog([
				{
					title: 'BeLib i5 Synthetic EPUB Fixture',
					authorName: 'BeLib Staging Fixture',
					description: 'Deterministic synthetic fixture for isolated BeLib staging.',
					deletedAt: null,
				},
				{
					title: 'BeLib i5 Synthetic EPUB Fixture',
					authorName: 'BeLib Staging Fixture',
					description: 'Deterministic synthetic fixture for isolated BeLib staging.',
					deletedAt: null,
				},
			]),
		).toThrow(/fresh book catalog/);
		expect(() =>
			assertFreshOrSeededCatalog([
				{
					title: 'BeLib i5 Synthetic EPUB Fixture',
					authorName: 'BeLib Staging Fixture',
					description: 'Deterministic synthetic fixture for isolated BeLib staging.',
					deletedAt: new Date('2026-01-01T00:00:00.000Z'),
				},
			]),
		).toThrow(/fresh book catalog/);
	});

	test('accepts the image node account, rejects UID 0, and verifies volume write access', async () => {
		expect(() => assertSeedRuntimeUser(1000, 1000, 'node')).not.toThrow();
		expect(() => assertSeedRuntimeUser(0, 0, 'root')).toThrow(/nonroot node account/);
		expect(() => assertSeedRuntimeUser(1000, 0, 'node')).toThrow(/nonroot node account/);
		expect(() => assertSeedRuntimeUser(1000, 1000, 'hiroyuki')).toThrow(/nonroot node account/);

		const directory = await mkdtemp(join(tmpdir(), 'belib-staging-seed-test-'));
		temporaryDirectories.push(directory);
		await expect(assertDirectoryWritableByNode(directory)).resolves.toBeUndefined();
		await chmod(directory, 0o555);
		await expect(assertDirectoryWritableByNode(directory)).rejects.toThrow(/already be writable by the nonroot node account/);
		await chmod(directory, 0o700);
	});

	test('reads two distinct disposable passwords from bounded stdin input', async () => {
		const input = Readable.from([Buffer.from(`admin_password=${adminPassword}\nreader_password=${readerPassword}\n`)]);
		const result = await readStagingSeedCredentials(input, validEnvironment());
		expect(result.adminPassword === adminPassword).toBe(true);
		expect(result.readerPassword === readerPassword).toBe(true);
	});

	test('does not skip missing, malformed, oversized, or reused stdin credentials', async () => {
		await expect(readStagingSeedCredentials(Readable.from([]), validEnvironment())).rejects.toThrow(/requires credential input on stdin/);
		await expect(
			readStagingSeedCredentials(Readable.from([Buffer.from(`admin_password=${adminPassword}\n`)]), validEnvironment()),
		).rejects.toThrow(/exactly the admin and reader password lines/);
		await expect(
			readStagingSeedCredentials(Readable.from([Buffer.alloc(257, 0x61)]), validEnvironment()),
		).rejects.toThrow(/input is too large/);
		await expect(
			readStagingSeedCredentials(
				Readable.from([Buffer.from(`admin_password=${databasePassword}\nreader_password=${readerPassword}\n`)]),
				validEnvironment(),
			),
		).rejects.toThrow(/independent of each other and both staging secrets/);
	});

	test('retains fixture and unrelated files when the injected database transaction fails', async () => {
		const directory = await mkdtemp(join(tmpdir(), 'belib-staging-seed-test-'));
		temporaryDirectories.push(directory);
		const createdFixture = syntheticFixture('created.epub');
		const existingFixture = syntheticFixture('existing.pdf');
		const existingPath = join(directory, existingFixture.fileName);
		const unrelatedPath = join(directory, 'unrelated.txt');
		await writeFile(existingPath, existingFixture.bytes, { mode: 0o644 });
		await chmod(existingPath, 0o644);
		await writeFile(unrelatedPath, 'leave this file alone');

		await expect(
			withStagingFixtureFiles(directory, [createdFixture, existingFixture], async () => {
				throw new Error('injected database transaction failure');
			}),
		).rejects.toThrow('injected database transaction failure');

		expect(await readFile(join(directory, createdFixture.fileName))).toEqual(createdFixture.bytes);
		expect(await readFile(existingPath)).toEqual(existingFixture.bytes);
		expect(await readFile(unrelatedPath, 'utf8')).toBe('leave this file alone');

		let retryCompleted = false;
		await withStagingFixtureFiles(directory, [createdFixture, existingFixture], async () => {
			retryCompleted = true;
		});
		expect(retryCompleted).toBe(true);
		expect(await readFile(join(directory, createdFixture.fileName))).toEqual(createdFixture.bytes);
	});

	test('retains an incomplete file after a write failure and refuses to overwrite it', async () => {
		const directory = await mkdtemp(join(tmpdir(), 'belib-staging-seed-test-'));
		temporaryDirectories.push(directory);
		const incompleteFixture = syntheticFixture('incomplete-write.epub');
		(incompleteFixture as { bytes: Buffer }).bytes = {} as unknown as Buffer;
		const filePath = join(directory, incompleteFixture.fileName);

		await expect(withStagingFixtureFiles(directory, [incompleteFixture], async () => undefined)).rejects.toThrow(
			/could not be written/,
		);
		expect((await lstat(filePath)).isFile()).toBe(true);
		expect(await readFile(filePath)).toEqual(Buffer.alloc(0));
		await chmod(filePath, 0o644);

		const completeFixture = syntheticFixture(incompleteFixture.fileName);
		await expect(withStagingFixtureFiles(directory, [completeFixture], async () => undefined)).rejects.toThrow(
			/differs from the deterministic content/,
		);
		expect(await readFile(filePath)).toEqual(Buffer.alloc(0));
	});

	test('keeps successful fixture files and reuses identical files on a repeated seed', async () => {
		const directory = await mkdtemp(join(tmpdir(), 'belib-staging-seed-test-'));
		temporaryDirectories.push(directory);
		const fixture = syntheticFixture('idempotent.epub');
		const filePath = join(directory, fixture.fileName);
		let transactionCount = 0;

		await withStagingFixtureFiles(directory, [fixture], async () => {
			transactionCount += 1;
		});
		const original = await lstat(filePath);
		await withStagingFixtureFiles(directory, [fixture], async () => {
			transactionCount += 1;
		});

		expect(transactionCount).toBe(2);
		expect((await lstat(filePath)).ino).toBe(original.ino);
		expect(await readFile(filePath)).toEqual(fixture.bytes);
	});

	test('fails closed for traversal and symlink fixture paths and leaves directories untouched', async () => {
		const directory = await mkdtemp(join(tmpdir(), 'belib-staging-seed-test-'));
		const outsideDirectory = await mkdtemp(join(tmpdir(), 'belib-staging-seed-outside-'));
		temporaryDirectories.push(directory, outsideDirectory);
		const outsideBytes = Buffer.from('outside fixture target');
		const outsidePath = join(outsideDirectory, 'outside.epub');
		const symlinkPath = join(directory, 'escaped.epub');
		const unrelatedPath = join(directory, 'unrelated.txt');
		await writeFile(outsidePath, outsideBytes);
		await symlink(outsidePath, symlinkPath);
		await writeFile(unrelatedPath, 'leave this file alone');

		const createdFixture = syntheticFixture('created-before-symlink.epub');
		await expect(
			withStagingFixtureFiles(directory, [createdFixture, syntheticFixture('escaped.epub', outsideBytes)], async () => {
				throw new Error('transaction must not run');
			}),
		).rejects.toThrow(/regular file/);
		expect(await readFile(join(directory, createdFixture.fileName))).toEqual(createdFixture.bytes);
		expect((await lstat(symlinkPath)).isSymbolicLink()).toBe(true);
		expect(await readFile(outsidePath)).toEqual(outsideBytes);
		expect(await readFile(unrelatedPath, 'utf8')).toBe('leave this file alone');
		await expect(
			withStagingFixtureFiles(directory, [syntheticFixture('../outside.epub')], async () => undefined),
		).rejects.toThrow(/single file inside the isolated fixture root/);
		expect(await readFile(join(directory, createdFixture.fileName))).toEqual(createdFixture.bytes);

		const mismatchedFixture = syntheticFixture('do-not-overwrite.epub');
		const mismatchedPath = join(directory, mismatchedFixture.fileName);
		const existingBytes = Buffer.from('existing file requiring inspection');
		await writeFile(mismatchedPath, existingBytes);
		await chmod(mismatchedPath, 0o644);
		await expect(withStagingFixtureFiles(directory, [mismatchedFixture], async () => undefined)).rejects.toThrow(
			/differs from the deterministic content/,
		);
		expect(await readFile(mismatchedPath)).toEqual(existingBytes);

		const replacedSymlinkFixture = syntheticFixture('owned-replaced-by-symlink.epub', outsideBytes);
		const replacedSymlinkPath = join(directory, replacedSymlinkFixture.fileName);
		await expect(
			withStagingFixtureFiles(directory, [replacedSymlinkFixture], async () => {
				await rm(replacedSymlinkPath);
				await symlink(outsidePath, replacedSymlinkPath);
				throw new Error('injected database transaction failure');
			}),
		).rejects.toThrow('injected database transaction failure');
		expect((await lstat(replacedSymlinkPath)).isSymbolicLink()).toBe(true);
		expect(await readFile(outsidePath)).toEqual(outsideBytes);

		const replacedDirectoryFixture = syntheticFixture('owned-replaced-by-directory.epub');
		const replacedDirectoryPath = join(directory, replacedDirectoryFixture.fileName);
		await expect(
			withStagingFixtureFiles(directory, [replacedDirectoryFixture], async () => {
				await rm(replacedDirectoryPath);
				await mkdir(replacedDirectoryPath);
				throw new Error('injected database transaction failure');
			}),
		).rejects.toThrow('injected database transaction failure');
		expect((await lstat(replacedDirectoryPath)).isDirectory()).toBe(true);
		expect(await readFile(unrelatedPath, 'utf8')).toBe('leave this file alone');
	});

	test('creates a deterministic valid EPUB ZIP with the required first uncompressed mimetype entry', () => {
		const epub = createStagingEpubFixture();
		expect(epub.subarray(0, 4).toString('binary')).toBe('PK\u0003\u0004');
		expect(epub).toEqual(createStagingEpubFixture());
		expect(epub.length).toBeLessThan(16_384);

		const { entries, centralOffset } = readEpubEntries(epub);
		expect(entries[0]).toMatchObject({ name: 'mimetype', method: 0 });
		expect(entries[0]?.bytes.toString('utf8')).toBe('application/epub+zip');
		for (const entry of entries) expect(testCrc32(entry.bytes)).toBe(entry.checksum);
		expect(epub.readUInt32LE(centralOffset)).toBe(0x02014b50);
		const endOffset = epub.length - 22;
		expect(epub.readUInt32LE(endOffset)).toBe(0x06054b50);
		expect(epub.readUInt16LE(endOffset + 10)).toBe(entries.length);
		expect(epub.readUInt32LE(endOffset + 16)).toBe(centralOffset);
		let centralCursor = centralOffset;
		for (const entry of entries) {
			expect(epub.readUInt32LE(centralCursor)).toBe(0x02014b50);
			const nameLength = epub.readUInt16LE(centralCursor + 28);
			const extraLength = epub.readUInt16LE(centralCursor + 30);
			const commentLength = epub.readUInt16LE(centralCursor + 32);
			expect(epub.subarray(centralCursor + 46, centralCursor + 46 + nameLength).toString('utf8')).toBe(entry.name);
			expect(epub.readUInt32LE(centralCursor + 16)).toBe(entry.checksum);
			expect(epub.readUInt32LE(centralCursor + 42)).toBeLessThan(centralOffset);
			centralCursor += 46 + nameLength + extraLength + commentLength;
		}
		expect(centralCursor).toBe(endOffset);
		expect(endOffset - centralOffset).toBe(epub.readUInt32LE(endOffset + 12));
		const content = new Map(entries.map((entry) => [entry.name, entry.bytes.toString('utf8')]));
		expect(content.get('META-INF/container.xml')).toContain('OEBPS/content.opf');
		expect(content.get('OEBPS/content.opf')).toContain('<spine><itemref idref="chapter"/></spine>');
		expect(content.get('OEBPS/nav.xhtml')).toContain('epub:type="toc"');
		expect(content.get('OEBPS/chapter.xhtml')).toContain('only synthetic text');
	});

	test('creates a deterministic two-page PDF with valid cross-reference offsets', () => {
		const pdf = createStagingPdfFixture();
		const source = pdf.toString('latin1');
		expect(pdf.subarray(0, 8).toString('ascii')).toBe('%PDF-1.4');
		expect(pdf).toEqual(createStagingPdfFixture());
		expect(source).toContain('/Count 2');
		expect([...source.matchAll(/\/Type \/Page\b/g)]).toHaveLength(2);
		expect(source).toContain('Synthetic BeLib staging PDF - Page 1');
		expect(source).toContain('Synthetic BeLib staging PDF - Page 2');

		const xrefOffset = Number(/startxref\n(\d+)/.exec(source)?.[1]);
		const xrefLines = source.slice(xrefOffset).split('\n');
		const objectOffsets = xrefLines.slice(3, 10).map((line) => Number(line.slice(0, 10)));
		for (const [index, offset] of objectOffsets.entries()) {
			expect(source.slice(offset, offset + `${index + 1} 0 obj`.length)).toBe(`${index + 1} 0 obj`);
		}
	});
});
