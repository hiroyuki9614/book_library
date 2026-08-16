import { Pool } from 'pg';
import { PrismaPg } from '@prisma/adapter-pg';
import { PrismaClient } from '../prisma/generated/prisma/client.js';

const databaseUrl = process.env.MIGRATION_TEST_DATABASE_URL;

if (!databaseUrl) {
	throw new Error('MIGRATION_TEST_DATABASE_URL is required');
}

const pool = new Pool({ connectionString: databaseUrl });
const prisma = new PrismaClient({ adapter: new PrismaPg(pool) });
const suffix = Date.now();

try {
	const columnResult = await pool.query<{
		data_type: string;
		character_maximum_length: number | null;
		is_nullable: string;
	}>(
		`SELECT data_type, character_maximum_length, is_nullable
		 FROM information_schema.columns
		 WHERE table_schema = 'public'
		   AND table_name = 'book_files'
		   AND column_name = 'file_hash'`,
	);
	const column = columnResult.rows[0];
	if (!column || column.data_type !== 'character varying' || column.character_maximum_length !== 64 || column.is_nullable !== 'NO') {
		throw new Error(`file_hash column contract mismatch: ${JSON.stringify(column ?? null)}`);
	}

	const indexResult = await pool.query<{ indexdef: string }>(
		`SELECT indexdef
		 FROM pg_indexes
		 WHERE schemaname = 'public'
		   AND tablename = 'book_files'
		   AND indexname = 'book_files_file_hash_key'`,
	);
	const index = indexResult.rows[0];
	if (!index || !index.indexdef.includes('CREATE UNIQUE INDEX')) {
		throw new Error(`file_hash unique constraint missing: ${JSON.stringify(index ?? null)}`);
	}

	const category = await prisma.category.create({ data: { name: `Wave2E migration verification ${suffix}` } });
	const book = await prisma.book.create({ data: { title: `Wave2E migration verification ${suffix}`, categoryId: category.id } });
	const fileHash = String(suffix).padStart(64, '0').slice(-64);
	const fileData = {
		extension: 'pdf',
		mimeType: 'application/pdf',
		fileUrl: `wave2e-${suffix}.pdf`,
		originalFileName: `wave2e-${suffix}.pdf`,
		storedFileName: `wave2e-${suffix}.pdf`,
		fileSize: 7,
		fileHash,
		bookId: book.id,
	};

	const created = await prisma.bookFile.create({ data: fileData });
	const readBack = await prisma.bookFile.findUnique({ where: { fileHash } });
	if (!readBack || readBack.id !== created.id || readBack.fileHash !== fileHash) {
		throw new Error('BookFile Prisma read-back failed');
	}

	let duplicateErrorCode: string | null = null;
	try {
		await prisma.bookFile.create({ data: fileData });
	} catch (error) {
		duplicateErrorCode = (error as { code?: string }).code ?? null;
	}
	if (duplicateErrorCode !== 'P2002') {
		throw new Error(`duplicate fileHash was not rejected: ${duplicateErrorCode}`);
	}

	await prisma.bookFile.delete({ where: { id: created.id } });
	await prisma.book.delete({ where: { id: book.id } });
	await prisma.category.delete({ where: { id: category.id } });

	console.log(JSON.stringify({ column, index: index.indexdef, readBackId: readBack.id, duplicateErrorCode }));
} finally {
	await prisma.$disconnect();
	await pool.end();
}
