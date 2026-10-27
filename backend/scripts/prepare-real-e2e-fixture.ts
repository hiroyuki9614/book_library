import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { Pool } from 'pg';
import { PrismaPg } from '@prisma/adapter-pg';
import { hashPassword } from 'better-auth/crypto';
import { PrismaClient } from '../prisma/generated/prisma/client.js';

const databaseUrl = process.env.E2E_DATABASE_URL;
const mvpPassword = process.env.E2E_MVP_PASSWORD;
const adminEmail = process.env.E2E_ADMIN_EMAIL;
const storageRoot = process.env.E2E_BOOK_FILE_STORAGE_ROOT;
const metadataPath = process.env.E2E_MVP_METADATA_PATH;
const aozoraManifestPath = process.env.E2E_AOZORA_MANIFEST_PATH;
const taskSuffix = '20261025';
const demoEmail = `belib-portfolio-demo-${taskSuffix}@example.com`;
const categoryName = '小説';
const pdfTitle = `【デモ】BeLib PDF ${taskSuffix}`;
const epubTitle = `【デモ】BeLib EPUB ${taskSuffix}`;
const privateTitle = `Private Existing Book ${taskSuffix}`;

if (!databaseUrl || !mvpPassword || !adminEmail || !storageRoot || !metadataPath) {
	throw new Error('E2E_DATABASE_URL, E2E_MVP_PASSWORD, E2E_ADMIN_EMAIL, E2E_BOOK_FILE_STORAGE_ROOT, and E2E_MVP_METADATA_PATH are required');
}

function createTwoPagePdf() {
	const pageOneContent = 'BT /F1 24 Tf 72 720 Td (BeLib Demo Page 1) Tj ET\n';
	const pageTwoContent = 'BT /F1 24 Tf 72 720 Td (BeLib Demo Page 2) Tj ET\n';
	const bodies = [
		'<< /Type /Catalog /Pages 2 0 R /Outlines 8 0 R /PageMode /UseOutlines >>',
		'<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>',
		'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R >>',
		`<< /Length ${Buffer.byteLength(pageOneContent, 'binary')} >>\nstream\n${pageOneContent}endstream`,
		'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>',
		`<< /Length ${Buffer.byteLength(pageTwoContent, 'binary')} >>\nstream\n${pageTwoContent}endstream`,
		'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
		'<< /Type /Outlines /First 9 0 R /Last 9 0 R /Count 1 >>',
		'<< /Title (First chapter) /Parent 8 0 R /Dest [3 0 R /Fit] >>',
	];
	const header = '%PDF-1.4\n%\xFF\xFF\xFF\xFF\n';
	const offsets: number[] = [];
	let document = header;
	for (const [index, body] of bodies.entries()) {
		offsets.push(Buffer.byteLength(document, 'binary'));
		document += `${index + 1} 0 obj\n${body}\nendobj\n`;
	}
	const xrefOffset = Buffer.byteLength(document, 'binary');
	document += `xref\n0 ${bodies.length + 1}\n0000000000 65535 f \n`;
	for (const offset of offsets) document += `${String(offset).padStart(10, '0')} 00000 n \n`;
	document += `trailer\n<< /Size ${bodies.length + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`;
	return Buffer.from(document, 'binary');
}

function crc32(buffer: Buffer) {
	let crc = 0xffffffff;
	for (const byte of buffer) {
		crc ^= byte;
		for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
	}
	return (crc ^ 0xffffffff) >>> 0;
}

function createEpub() {
	const entries = [
		{ name: 'mimetype', data: Buffer.from('application/epub+zip') },
		{ name: 'META-INF/container.xml', data: Buffer.from('<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>') },
		{ name: 'OEBPS/content.opf', data: Buffer.from('<?xml version="1.0" encoding="UTF-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">urn:belib:e2e</dc:identifier><dc:title>BeLib EPUB Demo</dc:title><dc:language>ja</dc:language><meta property="dcterms:modified">2026-10-25T00:00:00Z</meta></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="chapter-1" href="chapter-1.xhtml" media-type="application/xhtml+xml"/><item id="chapter-2" href="chapter-2.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="chapter-1"/><itemref idref="chapter-2"/></spine></package>') },
		{ name: 'OEBPS/nav.xhtml', data: Buffer.from('<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>目次</title></head><body><nav epub:type="toc"><ol><li><a href="chapter-1.xhtml">第一章</a><ol><li><a href="chapter-1.xhtml#section-1">第一節</a></li></ol></li><li><a href="chapter-2.xhtml">第二章</a></li></ol></nav></body></html>') },
		{ name: 'OEBPS/chapter-1.xhtml', data: Buffer.from('<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja"><head><title>第一章</title></head><body><h1>第一章</h1><section id="section-1"><h2>第一節</h2><p><ruby>吾輩<rt>わがはい</rt></ruby>はデモ猫である。</p><p>EPUB CFIの保存と復元を確認する本文です。</p></section></body></html>') },
		{ name: 'OEBPS/chapter-2.xhtml', data: Buffer.from('<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja"><head><title>第二章</title></head><body><h1>第二章</h1><p>iframe内の端タップで到達する確認用本文です。</p></body></html>') },
	];
	const local: Buffer[] = [];
	const central: Buffer[] = [];
	let offset = 0;
	for (const entry of entries) {
		const name = Buffer.from(entry.name);
		const localHeader = Buffer.alloc(30);
		localHeader.writeUInt32LE(0x04034b50, 0);
		localHeader.writeUInt16LE(20, 4);
		localHeader.writeUInt16LE(0x0800, 6);
		localHeader.writeUInt32LE(crc32(entry.data), 14);
		localHeader.writeUInt32LE(entry.data.length, 18);
		localHeader.writeUInt32LE(entry.data.length, 22);
		localHeader.writeUInt16LE(name.length, 26);
		local.push(localHeader, name, entry.data);
		const centralHeader = Buffer.alloc(46);
		centralHeader.writeUInt32LE(0x02014b50, 0);
		centralHeader.writeUInt16LE(20, 4);
		centralHeader.writeUInt16LE(20, 6);
		centralHeader.writeUInt16LE(0x0800, 8);
		centralHeader.writeUInt32LE(crc32(entry.data), 16);
		centralHeader.writeUInt32LE(entry.data.length, 20);
		centralHeader.writeUInt32LE(entry.data.length, 24);
		centralHeader.writeUInt16LE(name.length, 28);
		centralHeader.writeUInt32LE(offset, 42);
		central.push(centralHeader, name);
		offset += localHeader.length + name.length + entry.data.length;
	}
	const directory = Buffer.concat(central);
	const end = Buffer.alloc(22);
	end.writeUInt32LE(0x06054b50, 0);
	end.writeUInt16LE(entries.length, 8);
	end.writeUInt16LE(entries.length, 10);
	end.writeUInt32LE(directory.length, 12);
	end.writeUInt32LE(offset, 16);
	return Buffer.concat([...local, directory, end]);
}

async function loadDemoAssets() {
	if (!aozoraManifestPath) {
		return {
			pdf: { title: pdfTitle, bytes: createTwoPagePdf(), direction: 'ltr' as const },
			epub: { title: epubTitle, bytes: createEpub(), direction: 'ltr' as const },
		};
	}
	const absoluteManifestPath = resolve(aozoraManifestPath);
	const manifest = JSON.parse(await readFile(absoluteManifestPath, 'utf8')) as {
		works?: Array<{ format?: unknown; title?: unknown; filePath?: unknown; pageTurnDirection?: unknown }>;
	};
	const load = async (format: 'epub' | 'pdf') => {
		const work = manifest.works?.find((entry) => entry.format === format);
		if (!work || typeof work.title !== 'string' || typeof work.filePath !== 'string') {
			throw new Error(`E2E Aozora manifest is missing ${format}`);
		}
		const path = isAbsolute(work.filePath) ? work.filePath : join(dirname(absoluteManifestPath), work.filePath);
		return {
			title: work.title,
			bytes: await readFile(path),
			direction: work.pageTurnDirection === 'rtl' ? 'rtl' as const : 'ltr' as const,
		};
	};
	return { pdf: await load('pdf'), epub: await load('epub') };
}

async function createCredentialUser(prisma: PrismaClient, email: string, roleId: number, password: string) {
	const user = await prisma.user.create({ data: { email, name: 'BeLib Portfolio Demo', roleId } });
	await prisma.account.create({
		data: { accountId: String(user.id), providerId: 'credential', password: await hashPassword(password), userId: user.id },
	});
	return user;
}

async function addBookFile(prisma: PrismaClient, bookId: number, name: string, format: 'pdf' | 'epub', bytes: Buffer) {
	await writeFile(join(storageRoot!, name), bytes, { flag: 'wx' });
	await prisma.bookFile.create({
		data: {
			extension: format,
			mimeType: format === 'pdf' ? 'application/pdf' : 'application/epub+zip',
			fileUrl: name,
			originalFileName: name,
			storedFileName: name,
			fileSize: bytes.length,
			fileHash: createHash('sha256').update(bytes).digest('hex'),
			bookId,
		},
	});
}

const pool = new Pool({ connectionString: databaseUrl });
const adapter = new PrismaPg(pool);
const prisma = new PrismaClient({ adapter });

try {
	const demoAssets = await loadDemoAssets();
	const userRole = await prisma.role.upsert({ where: { name: 'user' }, update: {}, create: { name: 'user' } });
	const adminRole = await prisma.role.findUniqueOrThrow({ where: { name: 'admin' } });
	const admin = await prisma.user.findUniqueOrThrow({ where: { email: adminEmail } });
	if (admin.roleId !== adminRole.id) throw new Error('E2E admin role mismatch');
	await createCredentialUser(prisma, demoEmail, userRole.id, mvpPassword);
	const category = await prisma.category.create({ data: { name: categoryName, displayOrder: 99 } });
	const pdfBook = await prisma.book.create({
		data: { title: demoAssets.pdf.title, categoryId: category.id, pageTurnDirection: demoAssets.pdf.direction, roleBookPermissions: { create: [{ roleId: adminRole.id }, { roleId: userRole.id }] } },
	});
	const epubBook = await prisma.book.create({
		data: { title: demoAssets.epub.title, categoryId: category.id, pageTurnDirection: demoAssets.epub.direction, roleBookPermissions: { create: [{ roleId: adminRole.id }, { roleId: userRole.id }] } },
	});
	const privateBook = await prisma.book.create({
		data: { title: privateTitle, categoryId: category.id, roleBookPermissions: { create: [{ roleId: adminRole.id }] } },
	});
	await mkdir(storageRoot, { recursive: true });
	await addBookFile(prisma, pdfBook.id, `belib-demo-${taskSuffix}.pdf`, 'pdf', demoAssets.pdf.bytes);
	await addBookFile(prisma, epubBook.id, `belib-demo-${taskSuffix}.epub`, 'epub', demoAssets.epub.bytes);
	await addBookFile(prisma, privateBook.id, `belib-private-${taskSuffix}.pdf`, 'pdf', Buffer.from('%PDF-1.4\nprivate fixture'));
	await writeFile(metadataPath, JSON.stringify({
		demoEmail,
		pdfBookId: pdfBook.id,
		pdfTitle: demoAssets.pdf.title,
		epubBookId: epubBook.id,
		epubTitle: demoAssets.epub.title,
		privateBookId: privateBook.id,
		privateTitle,
	}));
	process.stdout.write(String(pdfBook.id));
} finally {
	await prisma.$disconnect();
	await pool.end();
}
