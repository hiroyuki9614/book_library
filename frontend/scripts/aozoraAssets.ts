import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export type AozoraWork = {
	id: string;
	format: 'epub' | 'pdf';
	outputFileName: string;
	title: string;
	authorName: string;
	publisher: string;
	publishedAt: string;
	pageTurnDirection: 'ltr' | 'rtl';
	cardUrl: string;
	sourceUrl: string;
	sourceLastUpdated: string;
	authorDeathDate: string;
	basisBook: string;
	inputBy: string[];
	proofreadBy: string[];
	conversionNote: string;
};

export type AozoraCatalog = {
	version: 1;
	usagePolicyUrl: string;
	works: AozoraWork[];
};

const AOZORA_ORIGIN = 'https://www.aozora.gr.jp';

export function validateCatalog(value: unknown): AozoraCatalog {
	const catalog = value as Partial<AozoraCatalog> | null;
	if (!catalog || catalog.version !== 1 || catalog.usagePolicyUrl !== `${AOZORA_ORIGIN}/guide/kijyunn.html` || !Array.isArray(catalog.works) || catalog.works.length < 2) {
		throw new Error('Aozora demo catalog is invalid');
	}
	const ids = new Set<string>();
	for (const work of catalog.works) {
		if (
			!work || typeof work.id !== 'string' || ids.has(work.id) ||
			(work.format !== 'epub' && work.format !== 'pdf') ||
			!work.title.startsWith('【デモ】') ||
			!work.outputFileName.endsWith(`.${work.format}`) ||
			new URL(work.cardUrl).origin !== AOZORA_ORIGIN || new URL(work.sourceUrl).origin !== AOZORA_ORIGIN ||
			!/^\d{4}-\d{2}-\d{2}$/.test(work.authorDeathDate) ||
			!work.basisBook || !work.conversionNote || !Array.isArray(work.inputBy) || !Array.isArray(work.proofreadBy)
		) throw new Error(`Aozora demo catalog entry is invalid: ${work?.id ?? 'unknown'}`);
		ids.add(work.id);
	}
	if (!catalog.works.some((work) => work.format === 'epub') || !catalog.works.some((work) => work.format === 'pdf')) {
		throw new Error('Aozora demo catalog must contain both EPUB and PDF');
	}
	return catalog as AozoraCatalog;
}

function crc32(buffer: Buffer) {
	let crc = 0xffffffff;
	for (const byte of buffer) {
		crc ^= byte;
		for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
	}
	return (crc ^ 0xffffffff) >>> 0;
}

function dosDateTime(date = new Date('2026-01-01T00:00:00.000Z')) {
	const year = Math.max(1980, date.getUTCFullYear());
	return {
		date: ((year - 1980) << 9) | ((date.getUTCMonth() + 1) << 5) | date.getUTCDate(),
		time: (date.getUTCHours() << 11) | (date.getUTCMinutes() << 5) | Math.floor(date.getUTCSeconds() / 2),
	};
}

function createStoredZip(entries: Array<{ name: string; data: Buffer }>) {
	const localParts: Buffer[] = [];
	const centralParts: Buffer[] = [];
	let offset = 0;
	const stamp = dosDateTime();

	for (const entry of entries) {
		const name = Buffer.from(entry.name);
		const checksum = crc32(entry.data);
		const local = Buffer.alloc(30);
		local.writeUInt32LE(0x04034b50, 0);
		local.writeUInt16LE(20, 4);
		local.writeUInt16LE(0x0800, 6);
		local.writeUInt16LE(0, 8);
		local.writeUInt16LE(stamp.time, 10);
		local.writeUInt16LE(stamp.date, 12);
		local.writeUInt32LE(checksum, 14);
		local.writeUInt32LE(entry.data.length, 18);
		local.writeUInt32LE(entry.data.length, 22);
		local.writeUInt16LE(name.length, 26);
		localParts.push(local, name, entry.data);

		const central = Buffer.alloc(46);
		central.writeUInt32LE(0x02014b50, 0);
		central.writeUInt16LE(20, 4);
		central.writeUInt16LE(20, 6);
		central.writeUInt16LE(0x0800, 8);
		central.writeUInt16LE(0, 10);
		central.writeUInt16LE(stamp.time, 12);
		central.writeUInt16LE(stamp.date, 14);
		central.writeUInt32LE(checksum, 16);
		central.writeUInt32LE(entry.data.length, 20);
		central.writeUInt32LE(entry.data.length, 24);
		central.writeUInt16LE(name.length, 28);
		central.writeUInt32LE(offset, 42);
		centralParts.push(central, name);
		offset += local.length + name.length + entry.data.length;
	}

	const centralDirectory = Buffer.concat(centralParts);
	const end = Buffer.alloc(22);
	end.writeUInt32LE(0x06054b50, 0);
	end.writeUInt16LE(entries.length, 8);
	end.writeUInt16LE(entries.length, 10);
	end.writeUInt32LE(centralDirectory.length, 12);
	end.writeUInt32LE(offset, 16);
	return Buffer.concat([...localParts, centralDirectory, end]);
}

function escapeXml(value: string) {
	return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
}

export function createEpub(work: AozoraWork, body: string) {
	const title = escapeXml(work.title);
	const author = escapeXml(work.authorName);
	const content = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja"><head><meta charset="UTF-8"/><title>${title}</title><style>body{font-family:serif;line-height:1.9;padding:1em}ruby rt{font-size:.55em}.conversion{border-top:1px solid #999;margin-top:2em;padding-top:1em;font-size:.8em}</style></head><body>${body}<section class="conversion"><h2>変換履歴</h2><p>${escapeXml(work.conversionNote)}</p><p>出典: <a href="${escapeXml(work.cardUrl)}">青空文庫 図書カード</a></p></section></body></html>`;
	const nav = `<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="ja"><head><title>目次</title></head><body><nav epub:type="toc"><h1>目次</h1><ol><li><a href="content.xhtml">${title}</a></li></ol></nav></body></html>`;
	const opf = `<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id" dir="${work.pageTurnDirection}"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="book-id">urn:belib:aozora:${escapeXml(work.id)}</dc:identifier><dc:title>${title}</dc:title><dc:creator>${author}</dc:creator><dc:language>ja</dc:language><meta property="dcterms:modified">2026-10-25T00:00:00Z</meta></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="content" href="content.xhtml" media-type="application/xhtml+xml"/></manifest><spine page-progression-direction="${work.pageTurnDirection}"><itemref idref="content"/></spine></package>`;
	const container = '<?xml version="1.0" encoding="UTF-8"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>';
	return createStoredZip([
		{ name: 'mimetype', data: Buffer.from('application/epub+zip') },
		{ name: 'META-INF/container.xml', data: Buffer.from(container) },
		{ name: 'OEBPS/content.opf', data: Buffer.from(opf) },
		{ name: 'OEBPS/nav.xhtml', data: Buffer.from(nav) },
		{ name: 'OEBPS/content.xhtml', data: Buffer.from(content) },
	]);
}

function extractBody(xhtml: string) {
	const match = xhtml.match(/<body[^>]*>([\s\S]*?)<\/body>/i);
	if (!match) throw new Error('Aozora source does not contain a body element');
	return match[1]
		.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
		.replace(/<iframe\b[^>]*>[\s\S]*?<\/iframe>/gi, '')
		.replace(/<form\b[^>]*>[\s\S]*?<\/form>/gi, '');
}

async function inlineImages(body: string, sourceUrl: string) {
	const matches = [...body.matchAll(/<img\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/gi)];
	let result = body;
	for (const match of matches) {
		const imageUrl = new URL(match[1], sourceUrl);
		if (imageUrl.origin !== AOZORA_ORIGIN) throw new Error('Aozora source referenced an external image');
		const response = await fetch(imageUrl);
		if (!response.ok) throw new Error(`Failed to fetch Aozora image (${response.status})`);
		const bytes = Buffer.from(await response.arrayBuffer());
		const mediaType = response.headers.get('content-type')?.split(';', 1)[0] || 'image/png';
		result = result.replace(match[1], `data:${mediaType};base64,${bytes.toString('base64')}`);
	}
	return result;
}

async function fetchWorkBody(work: AozoraWork) {
	const response = await fetch(work.sourceUrl, { headers: { 'User-Agent': 'BeLib portfolio demo asset builder' } });
	if (!response.ok) throw new Error(`Failed to fetch ${work.id} (${response.status})`);
	const source = new TextDecoder('shift_jis').decode(await response.arrayBuffer());
	return inlineImages(extractBody(source), work.sourceUrl);
}

function htmlDocument(work: AozoraWork, body: string) {
	return `<!doctype html><html lang="ja"><head><meta charset="UTF-8"><title>${escapeXml(work.title)}</title><style>@import url('https://fonts.googleapis.com/css2?family=Noto+Serif+JP:wght@400;700&display=swap');@page{size:A4;margin:18mm}body{font-family:"Noto Serif JP",serif;line-height:1.9;font-size:12pt}ruby rt{font-size:.55em}.conversion{border-top:1px solid #777;margin-top:2em;padding-top:1em;font-size:9pt}img{max-width:100%}</style></head><body>${body}<section class="conversion"><h2>変換履歴</h2><p>${escapeXml(work.conversionNote)}</p><p>出典: ${escapeXml(work.cardUrl)}</p></section></body></html>`;
}

export async function prepareAozoraAssets(catalogPath: string, outputDirectory: string) {
	const catalog = validateCatalog(JSON.parse(await readFile(catalogPath, 'utf8')));
	await mkdir(outputDirectory, { recursive: true });
	const generated = [];
	for (const work of catalog.works) {
		const body = await fetchWorkBody(work);
		const outputPath = join(outputDirectory, work.outputFileName);
		let bytes: Buffer;
		if (work.format === 'epub') {
			bytes = createEpub(work, body);
		} else {
			const { chromium } = await import('@playwright/test');
			const browser = await chromium.launch({ headless: true });
			try {
				const page = await browser.newPage();
				await page.setContent(htmlDocument(work, body), { waitUntil: 'networkidle' });
				await page.evaluate(() => document.fonts.ready);
				bytes = Buffer.from(await page.pdf({ format: 'A4', printBackground: true }));
			} finally {
				await browser.close();
			}
		}
		await writeFile(outputPath, bytes, { flag: 'wx' });
		generated.push({
			...work,
			filePath: work.outputFileName,
			fileSize: bytes.length,
			sha256: createHash('sha256').update(bytes).digest('hex'),
		});
	}
	const manifest = {
		version: 1,
		generatedAt: new Date().toISOString(),
		usagePolicyUrl: catalog.usagePolicyUrl,
		works: generated,
	};
	const manifestPath = join(outputDirectory, 'manifest.generated.json');
	await writeFile(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`, { flag: 'wx' });
	return { manifestPath, works: generated.map(({ id, format, fileSize, sha256 }) => ({ id, format, fileSize, sha256 })) };
}

const isEntryPoint = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isEntryPoint) {
	const scriptDirectory = dirname(fileURLToPath(import.meta.url));
	const repositoryRoot = resolve(scriptDirectory, '../..');
	const catalogPath = resolve(repositoryRoot, 'docs/portfolio/aozora-demo-books.json');
	const outputIndex = process.argv.indexOf('--output');
	const outputDirectory = outputIndex >= 0 && process.argv[outputIndex + 1]
		? resolve(process.argv[outputIndex + 1])
		: resolve(repositoryRoot, '.tmp/aozora-demo');
	prepareAozoraAssets(catalogPath, outputDirectory)
		.then((result) => console.log(JSON.stringify(result, null, 2)))
		.catch((error) => {
			console.error(error instanceof Error ? error.message : 'Aozora asset preparation failed');
			process.exitCode = 1;
		});
}
