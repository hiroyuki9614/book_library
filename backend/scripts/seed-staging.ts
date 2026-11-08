import { createHash, randomUUID } from 'node:crypto';
import { lstat, open, readFile, realpath, unlink } from 'node:fs/promises';
import { userInfo } from 'node:os';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { hashPassword, verifyPassword } from 'better-auth/crypto';
import { PrismaPg } from '@prisma/adapter-pg';
import { Pool } from 'pg';
import { PrismaClient } from '../prisma/generated/prisma/client.js';

export const STAGING_FIXTURE_ROOT = '/var/lib/belib/books';

const STAGING_DEFAULT_CATEGORY = '未分類';
const SYNTHETIC_AUTHOR = 'BeLib Staging Fixture';
const ADMIN_EMAIL = 'staging-admin@belib.invalid';
const READER_EMAIL = 'staging-reader@belib.invalid';
const FIXTURE_DESCRIPTION = 'Deterministic synthetic fixture for isolated BeLib staging.';
const EPUB_TITLE = 'BeLib i5 Synthetic EPUB Fixture';
const PDF_TITLE = 'BeLib i5 Synthetic PDF Fixture';
const SYNTHETIC_BOOK_TITLES = [EPUB_TITLE, PDF_TITLE] as const;
const MAX_CREDENTIAL_INPUT_BYTES = 256;

type SyntheticFixture = {
	fileName: string;
	extension: 'epub' | 'pdf';
	mimeType: 'application/epub+zip' | 'application/pdf';
	title: string;
	bytes: Buffer;
};

type SeedCredentials = {
	adminPassword: string;
	readerPassword: string;
};

class StagingSeedError extends Error {
	constructor(message: string) {
		super(message);
		this.name = 'StagingSeedError';
	}
}

function fail(message: string): never {
	throw new StagingSeedError(message);
}

function getDatabasePassword(databaseUrl: string) {
	const match = /^postgresql:\/\/belib_staging:([a-f0-9]{64})@db:5432\/belib_staging$/.exec(databaseUrl);
	if (!match) {
		fail('DATABASE_URL must target db:5432/belib_staging as belib_staging with the staging-only password format');
	}
	return match[1]!;
}

export function assertStagingSeedEnvironment(env: NodeJS.ProcessEnv) {
	const forbiddenName = Object.keys(env).find(
		(name) =>
			/^(?:R2|AWS|S3|PROD|PRODUCTION)_/i.test(name) ||
			/^BELIB_STAGE_/i.test(name) ||
			/^PG(?:HOST|PORT|DATABASE|USER|PASSWORD|SERVICE|SERVICEFILE|SSLMODE)$/i.test(name) ||
			name.startsWith('VITE_PUBLIC_DEMO_') ||
			['BELIB_FRONTEND_IMAGE', 'BELIB_FRONTEND_TAG', 'DATABASE_DIRECT_URL', 'DIRECT_URL'].includes(name),
	);
	if (forbiddenName) {
		fail('Cloud-storage, production, and public-demo environment variables are forbidden for the staging seed');
	}

	if (env.ENVIRONMENT !== 'test') {
		fail('ENVIRONMENT must be exactly test');
	}
	if (env.BOOK_FILE_STORAGE_DRIVER !== 'local' || env.BOOK_FILE_STORAGE_ROOT !== STAGING_FIXTURE_ROOT) {
		fail('Book storage must use the exact isolated local fixture root');
	}
	if (!env.BETTER_AUTH_SECRET || !/^[a-f0-9]{64}$/.test(env.BETTER_AUTH_SECRET)) {
		fail('BETTER_AUTH_SECRET must be a staging-only 64-character lowercase hex value');
	}
	if (!env.DATABASE_URL) {
		fail('DATABASE_URL is required');
	}
	const dbPassword = getDatabasePassword(env.DATABASE_URL);
	if (dbPassword === env.BETTER_AUTH_SECRET) {
		fail('The staging database password and Better Auth secret must be distinct');
	}
}

export function assertSeedRuntimeUser(uid: number | undefined, euid: number | undefined, username: string) {
	if (uid === undefined || euid === undefined || uid === 0 || euid === 0 || uid !== euid || username !== 'node') {
		fail('The staging seed must run as the nonroot node account');
	}
}

export function assertFreshOrSeededCatalog(
	books: Array<{ title: string; authorName: string | null; description: string | null; deletedAt: Date | null }>,
) {
	const seen = new Set<string>();
	for (const book of books) {
		if (
			!SYNTHETIC_BOOK_TITLES.includes(book.title as (typeof SYNTHETIC_BOOK_TITLES)[number]) ||
			book.authorName !== SYNTHETIC_AUTHOR ||
			book.description !== FIXTURE_DESCRIPTION ||
			book.deletedAt !== null ||
			seen.has(book.title)
		) {
			fail('The staging seed requires a fresh book catalog or only its own active synthetic fixtures');
		}
		seen.add(book.title);
	}
}

export async function readStagingSeedCredentials(
	input: AsyncIterable<Uint8Array>,
	env: NodeJS.ProcessEnv,
): Promise<SeedCredentials> {
	assertStagingSeedEnvironment(env);
	const databasePassword = getDatabasePassword(env.DATABASE_URL!);
	const authSecret = env.BETTER_AUTH_SECRET!;
	if (!input || typeof input[Symbol.asyncIterator] !== 'function') {
		fail('The staging seed requires credential input on stdin');
	}
	const chunks: Buffer[] = [];
	let totalBytes = 0;
	try {
		for await (const chunk of input) {
			totalBytes += chunk.byteLength;
			if (totalBytes > MAX_CREDENTIAL_INPUT_BYTES) {
				fail('The staging seed credential input is too large');
			}
			chunks.push(Buffer.from(chunk));
		}
	} catch (error) {
		if (error instanceof StagingSeedError) throw error;
		fail('The staging seed credential input cannot be read');
	}
	if (totalBytes === 0) fail('The staging seed requires credential input on stdin');
	const contents = Buffer.concat(chunks, totalBytes).toString('utf8');
	const lines = contents.split(/\r?\n/);
	if (lines.at(-1) === '') lines.pop();
	if (lines.length !== 2) {
		fail('The credentials file must contain exactly the admin and reader password lines');
	}
	const adminMatch = /^admin_password=([a-f0-9]{64})$/.exec(lines[0]!);
	const readerMatch = /^reader_password=([a-f0-9]{64})$/.exec(lines[1]!);
	if (!adminMatch || !readerMatch) {
		fail('The credentials file must contain two independent 64-character lowercase hex passwords');
	}
	const adminPassword = adminMatch[1]!;
	const readerPassword = readerMatch[1]!;
	if (
		adminPassword === readerPassword ||
		adminPassword === databasePassword ||
		readerPassword === databasePassword ||
		adminPassword === authSecret ||
		readerPassword === authSecret
	) {
		fail('Seed account passwords must be independent of each other and both staging secrets');
	}
	return { adminPassword, readerPassword };
}

function crc32(bytes: Buffer) {
	let crc = 0xffffffff;
	for (const byte of bytes) {
		crc ^= byte;
		for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
	}
	return (crc ^ 0xffffffff) >>> 0;
}

function createStoredZip(entries: Array<{ name: string; bytes: Buffer }>) {
	const localParts: Buffer[] = [];
	const centralParts: Buffer[] = [];
	let localOffset = 0;
	const fixedDosDate = ((2000 - 1980) << 9) | (1 << 5) | 1;

	for (const entry of entries) {
		const name = Buffer.from(entry.name, 'utf8');
		const checksum = crc32(entry.bytes);
		const localHeader = Buffer.alloc(30);
		localHeader.writeUInt32LE(0x04034b50, 0);
		localHeader.writeUInt16LE(20, 4);
		localHeader.writeUInt16LE(0x0800, 6);
		localHeader.writeUInt16LE(0, 8);
		localHeader.writeUInt16LE(0, 10);
		localHeader.writeUInt16LE(fixedDosDate, 12);
		localHeader.writeUInt32LE(checksum, 14);
		localHeader.writeUInt32LE(entry.bytes.length, 18);
		localHeader.writeUInt32LE(entry.bytes.length, 22);
		localHeader.writeUInt16LE(name.length, 26);
		localHeader.writeUInt16LE(0, 28);
		localParts.push(localHeader, name, entry.bytes);

		const centralHeader = Buffer.alloc(46);
		centralHeader.writeUInt32LE(0x02014b50, 0);
		centralHeader.writeUInt16LE(20, 4);
		centralHeader.writeUInt16LE(20, 6);
		centralHeader.writeUInt16LE(0x0800, 8);
		centralHeader.writeUInt16LE(0, 10);
		centralHeader.writeUInt16LE(0, 12);
		centralHeader.writeUInt16LE(fixedDosDate, 14);
		centralHeader.writeUInt32LE(checksum, 16);
		centralHeader.writeUInt32LE(entry.bytes.length, 20);
		centralHeader.writeUInt32LE(entry.bytes.length, 24);
		centralHeader.writeUInt16LE(name.length, 28);
		centralHeader.writeUInt16LE(0, 30);
		centralHeader.writeUInt16LE(0, 32);
		centralHeader.writeUInt16LE(0, 34);
		centralHeader.writeUInt16LE(0, 36);
		centralHeader.writeUInt32LE(0, 38);
		centralHeader.writeUInt32LE(localOffset, 42);
		centralParts.push(centralHeader, name);
		localOffset += localHeader.length + name.length + entry.bytes.length;
	}

	const centralDirectory = Buffer.concat(centralParts);
	const end = Buffer.alloc(22);
	end.writeUInt32LE(0x06054b50, 0);
	end.writeUInt16LE(entries.length, 8);
	end.writeUInt16LE(entries.length, 10);
	end.writeUInt32LE(centralDirectory.length, 12);
	end.writeUInt32LE(localOffset, 16);
	return Buffer.concat([...localParts, centralDirectory, end]);
}

export function createStagingEpubFixture() {
	const entries = [
		{ name: 'mimetype', bytes: Buffer.from('application/epub+zip') },
		{
			name: 'META-INF/container.xml',
			bytes: Buffer.from(
				'<?xml version="1.0" encoding="UTF-8"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
			),
		},
		{
			name: 'OEBPS/content.opf',
			bytes: Buffer.from(
				'<?xml version="1.0" encoding="UTF-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="book-id">urn:uuid:9d65aa4d-2bce-4e48-8fd4-4f4f8b230201</dc:identifier><dc:title>BeLib i5 Synthetic EPUB Fixture</dc:title><dc:language>en</dc:language><meta property="dcterms:modified">2026-11-07T00:00:00Z</meta></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="chapter"/></spine></package>',
			),
		},
		{
			name: 'OEBPS/nav.xhtml',
			bytes: Buffer.from(
				'<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc"><h1>Contents</h1><ol><li><a href="chapter.xhtml">Synthetic chapter</a></li></ol></nav></body></html>',
			),
		},
		{
			name: 'OEBPS/chapter.xhtml',
			bytes: Buffer.from(
				'<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en"><head><title>Synthetic chapter</title></head><body><h1>Synthetic staging fixture</h1><p>This deterministic EPUB contains only synthetic text.</p></body></html>',
			),
		},
	];
	return createStoredZip(entries);
}

export function createStagingPdfFixture() {
	const pageContent = (number: number) =>
		`BT\n/F1 18 Tf\n40 340 Td\n(Synthetic BeLib staging PDF - Page ${number}) Tj\nET\n`;
	const streamObject = (content: string) =>
		`<< /Length ${Buffer.byteLength(content, 'ascii')} >>\nstream\n${content}endstream`;
	const objects = [
		'<< /Type /Catalog /Pages 2 0 R >>',
		'<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>',
		'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Resources << /Font << /F1 6 0 R >> >> /Contents 4 0 R >>',
		streamObject(pageContent(1)),
		'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Resources << /Font << /F1 6 0 R >> >> /Contents 7 0 R >>',
		'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
		streamObject(pageContent(2)),
	];
	const parts = [Buffer.from('%PDF-1.4\n%\xe2\xe3\xcf\xd3\n', 'binary')];
	const offsets = [0];
	let currentOffset = parts[0]!.length;
	for (const [index, object] of objects.entries()) {
		offsets.push(currentOffset);
		const chunk = Buffer.from(`${index + 1} 0 obj\n${object}\nendobj\n`, 'ascii');
		parts.push(chunk);
		currentOffset += chunk.length;
	}
	const xrefOffset = currentOffset;
	const xref = [
		`xref\n0 ${objects.length + 1}\n`,
		'0000000000 65535 f \n',
		...offsets.slice(1).map((offset) => `${String(offset).padStart(10, '0')} 00000 n \n`),
		`trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`,
	].join('');
	parts.push(Buffer.from(xref, 'ascii'));
	return Buffer.concat(parts);
}

function createSyntheticFixtures(): SyntheticFixture[] {
	return [
		{
			fileName: 'belib-staging-synthetic.epub',
			extension: 'epub',
			mimeType: 'application/epub+zip',
			title: EPUB_TITLE,
			bytes: createStagingEpubFixture(),
		},
		{
			fileName: 'belib-staging-synthetic.pdf',
			extension: 'pdf',
			mimeType: 'application/pdf',
			title: PDF_TITLE,
			bytes: createStagingPdfFixture(),
		},
	];
}

async function assertFixtureRoot(envRoot: string) {
	if (envRoot !== STAGING_FIXTURE_ROOT) {
		fail('Book fixture root must be exactly /var/lib/belib/books');
	}
	let metadata;
	try {
		metadata = await lstat(STAGING_FIXTURE_ROOT);
	} catch {
		fail('The isolated local fixture volume is not mounted');
	}
	if (!metadata.isDirectory() || metadata.isSymbolicLink()) {
		fail('The isolated local fixture root must be a real directory');
	}
	let resolvedRoot: string;
	try {
		resolvedRoot = await realpath(STAGING_FIXTURE_ROOT);
	} catch {
		fail('The isolated local fixture root cannot be resolved');
	}
	if (resolvedRoot !== STAGING_FIXTURE_ROOT) {
		fail('The isolated local fixture root resolves outside its exact mount path');
	}
	return STAGING_FIXTURE_ROOT;
}


export async function assertDirectoryWritableByNode(directory: string) {
	const probePath = resolve(directory, `.belib-staging-seed-write-probe-${randomUUID()}`);
	let probeHandle: Awaited<ReturnType<typeof open>> | undefined;
	let created = false;
	try {
		probeHandle = await open(probePath, 'wx', 0o600);
		created = true;
		await probeHandle.close();
		probeHandle = undefined;
		await unlink(probePath);
		created = false;
	} catch {
		if (probeHandle) await probeHandle.close().catch(() => undefined);
		if (created) await unlink(probePath).catch(() => undefined);
		fail('The staging book volume must already be writable by the nonroot node account; the seed will not change its owner or permissions');
	}
}

function getFixtureFilePath(root: string, fileName: string) {
	if (root !== resolve(root) || !fileName || fileName === '.' || fileName === '..' || /[\\/]/.test(fileName)) {
		fail('A synthetic fixture path must be a single file inside the isolated fixture root');
	}
	const filePath = resolve(root, fileName);
	if (resolve(filePath, '..') !== root) {
		fail('A synthetic fixture path must be a single file inside the isolated fixture root');
	}
	return filePath;
}

async function assertSafeFixtureDirectory(root: string) {
	if (root !== resolve(root)) {
		fail('The isolated local fixture root must be an absolute canonical directory');
	}
	let metadata;
	try {
		metadata = await lstat(root);
	} catch {
		fail('The isolated local fixture root cannot be inspected');
	}
	if (!metadata.isDirectory() || metadata.isSymbolicLink()) {
		fail('The isolated local fixture root must be a real directory');
	}
	let resolvedRoot: string;
	try {
		resolvedRoot = await realpath(root);
	} catch {
		fail('The isolated local fixture root cannot be resolved');
	}
	if (resolvedRoot !== root) {
		fail('The isolated local fixture root resolves outside its exact path');
	}
}

async function assertOrWriteFixtureFile(root: string, fixture: SyntheticFixture) {
	await assertSafeFixtureDirectory(root);
	const filePath = getFixtureFilePath(root, fixture.fileName);
	const assertExisting = async () => {
		let metadata;
		try {
			metadata = await lstat(filePath);
		} catch {
			fail('An existing synthetic fixture file cannot be inspected');
		}
		if (!metadata.isFile() || metadata.isSymbolicLink() || (metadata.mode & 0o004) === 0) {
			fail('An existing synthetic fixture must be a readable regular file');
		}
		try {
			if ((await realpath(filePath)) !== filePath) {
				fail('An existing synthetic fixture resolves outside its exact path');
			}
		} catch (error) {
			if (error instanceof StagingSeedError) throw error;
			fail('An existing synthetic fixture path cannot be resolved');
		}
		let current: Buffer;
		try {
			current = await readFile(filePath);
		} catch {
			fail('An existing synthetic fixture file cannot be read');
		}
		if (!current.equals(fixture.bytes)) {
			fail('An existing synthetic fixture differs from the deterministic content; refusing to overwrite');
		}
	};

	let fileHandle: Awaited<ReturnType<typeof open>>;
	try {
		fileHandle = await open(filePath, 'wx', 0o644);
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code !== 'EEXIST') {
			fail('A synthetic fixture file could not be created on the local staging volume');
		}
		await assertExisting();
		return;
	}

	let writeFailed = false;
	try {
		await fileHandle.writeFile(fixture.bytes);
		await fileHandle.chmod(0o644);
	} catch {
		writeFailed = true;
	} finally {
		await fileHandle.close().catch(() => {
			writeFailed = true;
		});
	}
	if (writeFailed) {
		fail('A synthetic fixture file could not be written to the local staging volume');
	}
}
export async function withStagingFixtureFiles<T>(
	root: string,
	fixtures: SyntheticFixture[],
	transaction: () => Promise<T>,
): Promise<T> {
	for (const fixture of fixtures) await assertOrWriteFixtureFile(root, fixture);
	return transaction();
}

async function assertConnectedDatabase(prisma: PrismaClient) {
	let identity;
	try {
		identity = await prisma.$queryRaw<Array<{ database: string; username: string }>>`
			SELECT current_database() AS "database", current_user AS "username"
		`;
	} catch {
		fail('The staging database identity check failed');
	}
	if (identity.length !== 1 || identity[0]?.database !== 'belib_staging' || identity[0]?.username !== 'belib_staging') {
		fail('The connected database is not exactly belib_staging as belib_staging');
	}
}

async function getSeedUser(
	transaction: Parameters<Parameters<PrismaClient['$transaction']>[0]>[0],
	input: { email: string; name: string; roleId: number },
) {
	const existing = await transaction.user.findUnique({ where: { email: input.email } });
	if (existing) {
		if (existing.name !== input.name || existing.roleId !== input.roleId || existing.deletedAt !== null) {
			fail('A synthetic seed email is already owned by a different or disabled user record');
		}
		return existing;
	}
	return transaction.user.create({ data: input });
}

async function upsertCredentialAccount(
	transaction: Parameters<Parameters<PrismaClient['$transaction']>[0]>[0],
	userId: number,
	password: string,
) {
	const existing = await transaction.account.findMany({ where: { userId, providerId: 'credential' } });
	if (existing.length > 1) {
		fail('A synthetic seed user has multiple credential accounts');
	}
	const account = existing[0];
	if (account) {
		if (account.accountId !== String(userId)) {
			fail('A synthetic seed credential account has an unexpected identifier');
		}
		if (account.password && (await verifyPassword({ hash: account.password, password }))) return;
		await transaction.account.update({
			where: { id: account.id },
			data: { password: await hashPassword(password) },
		});
		return;
	}
	await transaction.account.create({
		data: {
			accountId: String(userId),
			providerId: 'credential',
			password: await hashPassword(password),
			userId,
		},
	});
}

async function getSeedBook(
	transaction: Parameters<Parameters<PrismaClient['$transaction']>[0]>[0],
	input: { title: string; categoryId: number },
) {
	const matches = await transaction.book.findMany({
		where: { title: input.title, authorName: SYNTHETIC_AUTHOR },
	});
	if (matches.length > 1) {
		fail('A synthetic fixture book has duplicate database records');
	}
	const existing = matches[0];
	if (existing) {
		if (
			existing.categoryId !== input.categoryId ||
			existing.description !== FIXTURE_DESCRIPTION ||
			existing.pageTurnDirection !== 'ltr' ||
			existing.deletedAt !== null
		) {
			fail('An existing synthetic fixture book has unexpected metadata');
		}
		return existing;
	}
	return transaction.book.create({
		data: {
			title: input.title,
			authorName: SYNTHETIC_AUTHOR,
			description: FIXTURE_DESCRIPTION,
			categoryId: input.categoryId,
			pageTurnDirection: 'ltr',
		},
	});
}

async function getSeedCategory(transaction: Parameters<Parameters<PrismaClient['$transaction']>[0]>[0]) {
	const category = await transaction.category.findUnique({ where: { name: STAGING_DEFAULT_CATEGORY } });
	if (!category || category.displayOrder !== 0 || !category.isActive) {
		fail('The migration-managed 未分類 category is missing or inactive');
	}
	return category;
}

async function upsertSyntheticBookFile(
	transaction: Parameters<Parameters<PrismaClient['$transaction']>[0]>[0],
	bookId: number,
	fixture: SyntheticFixture,
) {
	const fileHash = createHash('sha256').update(fixture.bytes).digest('hex');
	const pathMatches = await transaction.bookFile.findMany({ where: { fileUrl: fixture.fileName } });
	if (pathMatches.some((file) => file.bookId !== bookId || file.fileHash !== fileHash)) {
		fail('A deterministic synthetic storage path is already attached to another file record');
	}
	const existing = await transaction.bookFile.findUnique({ where: { fileHash } });
	const data = {
		extension: fixture.extension,
		mimeType: fixture.mimeType,
		fileUrl: fixture.fileName,
		originalFileName: fixture.fileName,
		storedFileName: fixture.fileName,
		fileSize: fixture.bytes.length,
		bookId,
	};
	if (existing) {
		if (
			existing.bookId !== bookId ||
			existing.extension !== fixture.extension ||
			existing.mimeType !== fixture.mimeType ||
			existing.fileUrl !== fixture.fileName ||
			existing.originalFileName !== fixture.fileName ||
			existing.storedFileName !== fixture.fileName ||
			existing.fileSize !== fixture.bytes.length
		) {
			fail('A deterministic synthetic file hash is already attached to another record');
		}
		return;
	}
	await transaction.bookFile.create({ data: { ...data, fileHash } });
}

async function main() {
	assertSeedRuntimeUser(process.getuid?.(), process.geteuid?.(), userInfo().username);
	assertStagingSeedEnvironment(process.env);
	const credentials = await readStagingSeedCredentials(process.stdin, process.env);
	const fixtureRoot = await assertFixtureRoot(process.env.BOOK_FILE_STORAGE_ROOT!);
	await assertDirectoryWritableByNode(fixtureRoot);
	const pool = new Pool({ connectionString: process.env.DATABASE_URL! });
	const prisma = new PrismaClient({ adapter: new PrismaPg(pool) });

	try {
		await assertConnectedDatabase(prisma);
		const existingBooks = await prisma.book.findMany({
			select: { title: true, authorName: true, description: true, deletedAt: true },
		});
		assertFreshOrSeededCatalog(existingBooks);
		const fixtures = createSyntheticFixtures();
		const seedTransaction = () => prisma.$transaction(async (transaction) => {
			const userRole = await transaction.role.upsert({
				where: { name: 'user' },
				update: {},
				create: { name: 'user' },
			});
			const adminRole = await transaction.role.upsert({
				where: { name: 'admin' },
				update: {},
				create: { name: 'admin' },
			});
			const admin = await getSeedUser(transaction, {
				email: ADMIN_EMAIL,
				name: 'Synthetic Staging Admin',
				roleId: adminRole.id,
			});
			const reader = await getSeedUser(transaction, {
				email: READER_EMAIL,
				name: 'Synthetic Staging Reader',
				roleId: userRole.id,
			});
			if (admin.id === reader.id || admin.roleId === reader.roleId) {
				fail('The synthetic admin and reader must be separate users with distinct roles');
			}
			await upsertCredentialAccount(transaction, admin.id, credentials.adminPassword);
			await upsertCredentialAccount(transaction, reader.id, credentials.readerPassword);

			const category = await getSeedCategory(transaction);
			const seededBooks = [];
			for (const fixture of fixtures) {
				const book = await getSeedBook(transaction, { title: fixture.title, categoryId: category.id });
				await upsertSyntheticBookFile(transaction, book.id, fixture);
				seededBooks.push(book);
			}

			const readerPermission = await transaction.roleBookPermission.findFirst({
				where: { roleId: userRole.id, bookId: { in: seededBooks.map((book) => book.id) } },
			});
			if (readerPermission) {
				fail('A synthetic fixture book already grants access to the general user role');
			}
			for (const book of seededBooks) {
				await transaction.roleBookPermission.upsert({
					where: { roleId_bookId: { roleId: adminRole.id, bookId: book.id } },
					update: {},
					create: { roleId: adminRole.id, bookId: book.id },
				});
			}
		});
		await withStagingFixtureFiles(fixtureRoot, fixtures, seedTransaction);
	} finally {
		await prisma.$disconnect();
		await pool.end();
	}

	console.log('Synthetic staging seed completed: 2 credential users, 2 books, 2 local files; general-user access is denied.');
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : '';
if (invokedPath === fileURLToPath(import.meta.url)) {
	main().catch((error: unknown) => {
		if (error instanceof StagingSeedError) {
			console.error(`Synthetic staging seed stopped: ${error.message}`);
		} else {
			console.error('Synthetic staging seed failed; internal details were suppressed.');
		}
		process.exitCode = 1;
	});
}
