import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { Pool } from 'pg';
import { PrismaPg } from '@prisma/adapter-pg';
import { hashPassword } from 'better-auth/crypto';
import { PrismaClient } from '../prisma/generated/prisma/client.js';

const databaseUrl = process.env.E2E_DATABASE_URL;
const mvpPassword = process.env.E2E_MVP_PASSWORD;
const storageRoot = process.env.E2E_BOOK_FILE_STORAGE_ROOT;
const metadataPath = process.env.E2E_MVP_METADATA_PATH;
const taskSuffix = '20260811';
const permissionedEmail = `belib-mvp-real-e2e-permissioned-${taskSuffix}@example.com`;
const permissionedPeerEmail = `belib-mvp-real-e2e-permissioned-peer-${taskSuffix}@example.com`;
const unpermissionedEmail = `belib-mvp-real-e2e-unpermissioned-${taskSuffix}@example.com`;
const permissionedRoleName = `belib_e2e_permissioned_${taskSuffix}`;
const unpermissionedRoleName = `belib_e2e_unpermissioned_${taskSuffix}`;
const categoryName = `BeLib E2E Category ${taskSuffix}`;
const bookTitle = `BeLib MVP Real E2E Book ${taskSuffix}`;
const adminOnlyBookTitle = `BeLib MVP Admin Only E2E Book ${taskSuffix}`;
const pdfFileName = `belib-mvp-real-e2e-${taskSuffix}.pdf`;

if (!databaseUrl || !mvpPassword || !storageRoot || !metadataPath) {
	throw new Error('E2E_DATABASE_URL, E2E_MVP_PASSWORD, E2E_BOOK_FILE_STORAGE_ROOT, and E2E_MVP_METADATA_PATH are required');
}

function createTwoPagePdf() {
	const pageOneContent = 'BT /F1 24 Tf 72 720 Td (BeLib E2E Page 1) Tj ET\n';
	const pageTwoContent = 'BT /F1 24 Tf 72 720 Td (BeLib E2E Page 2) Tj ET\n';
	const bodies = [
		'<< /Type /Catalog /Pages 2 0 R >>',
		'<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>',
		'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R >>',
		`<< /Length ${Buffer.byteLength(pageOneContent, 'binary')} >>\nstream\n${pageOneContent}endstream`,
		'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>',
		`<< /Length ${Buffer.byteLength(pageTwoContent, 'binary')} >>\nstream\n${pageTwoContent}endstream`,
		'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
	];
	const header = '%PDF-1.4\n%\xFF\xFF\xFF\xFF\n';
	const offsets: number[] = [0];
	let document = header;

	for (const [index, body] of bodies.entries()) {
		offsets.push(Buffer.byteLength(document, 'binary'));
		const object = `${index + 1} 0 obj\n${body}\nendobj\n`;
		document += object;
	}

	const xrefOffset = Buffer.byteLength(document, 'binary');
	document += `xref\n0 ${bodies.length + 1}\n0000000000 65535 f \n`;
	for (const offset of offsets.slice(1)) {
		document += `${String(offset).padStart(10, '0')} 00000 n \n`;
	}
	document += `trailer\n<< /Size ${bodies.length + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`;

	return Buffer.from(document, 'binary');
}

async function createCredentialUser(email: string, name: string, roleId: number, password: string) {
	const user = await prisma.user.create({ data: { email, name, roleId } });
	await prisma.account.create({
		data: {
			accountId: String(user.id),
			providerId: 'credential',
			password: await hashPassword(password),
			userId: user.id,
		},
	});
	return user;
}

const pool = new Pool({ connectionString: databaseUrl });
const adapter = new PrismaPg(pool);
const prisma = new PrismaClient({ adapter });

try {
	const userRole = await prisma.role.upsert({ where: { name: 'user' }, update: {}, create: { name: 'user' } });
	const adminRole = await prisma.role.upsert({ where: { name: 'admin' }, update: {}, create: { name: 'admin' } });
	const permissionedRole = await prisma.role.create({ data: { name: permissionedRoleName } });
	const unpermissionedRole = await prisma.role.create({ data: { name: unpermissionedRoleName } });

	const permissionedUser = await createCredentialUser(permissionedEmail, 'BeLib E2E Permissioned User', permissionedRole.id, mvpPassword);
	const permissionedPeerUser = await createCredentialUser(permissionedPeerEmail, 'BeLib E2E Permissioned Peer User', permissionedRole.id, mvpPassword);
	const unpermissionedUser = await createCredentialUser(unpermissionedEmail, 'BeLib E2E Unpermissioned User', unpermissionedRole.id, mvpPassword);
	if (!permissionedUser.id || !permissionedPeerUser.id || !unpermissionedUser.id || !userRole.id || !adminRole.id) {
		throw new Error('E2E users were not created');
	}

	const category = await prisma.category.create({ data: { name: categoryName, displayOrder: 99 } });
	const book = await prisma.book.create({
		data: {
			title: bookTitle,
			categoryId: category.id,
			roleBookPermissions: { create: [{ role: { connect: { id: permissionedRole.id } } }] },
		},
	});
	const adminOnlyBook = await prisma.book.create({
		data: {
			title: adminOnlyBookTitle,
			categoryId: category.id,
			roleBookPermissions: { create: [{ role: { connect: { id: adminRole.id } } }] },
		},
	});

	const pdf = createTwoPagePdf();
	await mkdir(storageRoot, { recursive: true });
	const storedPath = join(storageRoot, pdfFileName);
	await writeFile(storedPath, pdf, { flag: 'wx' });
	await prisma.bookFile.create({
		data: {
			extension: 'pdf',
			mimeType: 'application/pdf',
			fileUrl: pdfFileName,
			originalFileName: pdfFileName,
			storedFileName: pdfFileName,
			fileSize: pdf.length,
			fileHash: createHash('sha256').update(pdf).digest('hex'),
			bookId: book.id,
		},
	});
	await writeFile(metadataPath, JSON.stringify({ bookId: book.id, bookTitle, adminOnlyBookId: adminOnlyBook.id, adminOnlyBookTitle }));

	process.stdout.write(String(book.id));
} finally {
	await prisma.$disconnect();
	await pool.end();
}
