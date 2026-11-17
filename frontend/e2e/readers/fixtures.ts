import { createStoredZip } from '../../scripts/aozoraAssets';

// Small reproducible publications with real ZIP/OPF/nav/spine/CSS and ruby.
// No uploaded books or binary snapshots are committed.
export function epubFixture({ vertical = false, rtl = false, titleOnly = false, headings = true, mixed = false, paragraphs = 22, responsive = false } = {}) {
	const entries: Array<{ name: string; data: Buffer }> = [];
	const add = (name: string, value: string) => entries.push({ name, data: Buffer.from(value) });
	add('mimetype', 'application/epub+zip');
	add(
		'META-INF/container.xml',
		'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
	);
	add(
		'OPS/book.opf',
		`<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">belib-regression</dc:identifier><dc:title>読書回帰試験</dc:title><dc:language>ja</dc:language><meta property="dcterms:modified">2026-10-27T00:00:00Z</meta></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>${[1, 2, 3, 4].map((i) => `<item id="s${i}" href="s${i}.xhtml" media-type="application/xhtml+xml"/>`).join('')}</manifest><spine page-progression-direction="${rtl ? 'rtl' : 'ltr'}">${[1, 2, 3, 4].map((i) => `<itemref idref="s${i}"/>`).join('')}</spine></package>`,
	);
	const toc = titleOnly
		? '<li><a href="s1.xhtml">読書回帰試験</a></li>'
		: '<li><a href="s1.xhtml">第一章</a><ol><li><a href="s2.xhtml">第二章</a></li><li><a href="s3.xhtml">第三章</a><ol><li><a href="s4.xhtml">第四章</a></li></ol></li></ol></li>';
	add(
		'OPS/nav.xhtml',
		`<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>目次</title></head><body><nav epub:type="toc"><ol>${toc}</ol></nav></body></html>`,
	);
	for (let i = 1; i <= 4; i++) {
		const body =
			`<a href="s${i === 4 ? 1 : i + 1}.xhtml">本文リンク</a>` +
			Array.from(
				{ length: paragraphs },
				(_, p) =>
					`<p>第${i}章第${String(p + 1).padStart(2, '0')}段。${Array.from({ length: 12 }, (_, n) => `章${i}段${p + 1}文${n + 1}。文章は原文の順番どおりに続く。句読点、括弧（読書）、<ruby>漢字<rt>かんじ</rt></ruby>を含む。知れぬ。見える。`).join('')}</p>`,
			).join('');
		add(
			`OPS/s${i}.xhtml`,
			`<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja"><head><title>第${i}章</title><style>body{writing-mode:${(mixed ? i % 2 === 1 : vertical) ? 'vertical-rl' : 'horizontal-tb'};font-family:"Noto Serif CJK JP",serif;line-height:1.8}p{margin:0 0 1em}rt{font-size:.5em}${responsive ? '@media(prefers-color-scheme:dark){body{writing-mode:vertical-rl}}' : ''}</style></head><body>${headings ? `<h2 id="chapter${i}">第${i}章</h2>` : ''}${body}<a href="s${i === 4 ? 1 : i + 1}.xhtml">本文リンク</a></body></html>`,
		);
	}
	return createStoredZip(entries);
}

// Four chapters, eight explicit columns each. Stable source-page labels expose
// skipped/duplicated screens and the physical order of native horizontal spreads.
// Generated in memory; no EPUB binary or staging dataset is needed.
export function directionEpubFixture({ opfDirection = 'rtl', bodyDirection = 'rtl', htmlDirection }: { opfDirection?: 'ltr' | 'rtl'; bodyDirection?: 'ltr' | 'rtl'; htmlDirection?: 'ltr' | 'rtl' } = {}) {
	const entries: Array<{ name: string; data: Buffer }> = [];
	const add = (name: string, value: string) => entries.push({ name, data: Buffer.from(value) });
	const chapters = [1, 2, 3, 4];
	add('mimetype', 'application/epub+zip');
	add('META-INF/container.xml', '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>');
	add('OPS/book.opf', `<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">belib-owner-ltr-regression</dc:identifier><dc:title>Direction regression</dc:title><dc:language>en</dc:language><meta property="dcterms:modified">2026-11-09T00:00:00Z</meta></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>${chapters.map(i => `<item id="s${i}" href="s${i}.xhtml" media-type="application/xhtml+xml"/>`).join('')}</manifest><spine page-progression-direction="${opfDirection}">${chapters.map(i => `<itemref idref="s${i}"/>`).join('')}</spine></package>`);
	add('OPS/nav.xhtml', `<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc"><ol>${chapters.map(i => `<li><a href="s${i}.xhtml">Chapter ${i}</a></li>`).join('')}</ol></nav></body></html>`);
	for (const chapter of chapters) {
		const pages = Array.from({ length: 8 }, (_, i) => {
			const number = (chapter - 1) * 8 + i + 1;
			return `<p data-source-page="${number}">SOURCE PAGE ${String(number).padStart(2, '0')}. Chapter ${chapter}, column ${i + 1}. Read in source order. العربية <span dir="ltr">LTR</span></p>`;
		}).join('');
		add(`OPS/s${chapter}.xhtml`, `<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter ${chapter}</title><style>${htmlDirection ? `html{direction:${htmlDirection}!important}` : ''}body{writing-mode:horizontal-tb;direction:${bodyDirection};font:18px/1.5 serif}p{margin:0;break-after:column}p:last-child{break-after:auto}</style></head><body>${pages}</body></html>`);
	}
	return createStoredZip(entries);
}

/**
 * Stable two-page PDF with real outlines and page destinations, generated in memory.
 * Chromium page.pdf({ outline: true }) may omit bookmarks in CI, making TOC tests invalid.
 */
export function outlinedPdfFixture(): Buffer {
	const text = (title: string, description: string) =>
		`BT\n/F1 24 Tf\n72 720 Td\n(${title}) Tj\n/F1 12 Tf\n0 -48 Td\n(${description}) Tj\nET\n`;
	const stream = (content: string) => `<< /Length ${Buffer.byteLength(content, 'ascii')} >>\nstream\n${content}endstream`;
	const objects = [
		'<< /Type /Catalog /Pages 2 0 R /Outlines 7 0 R /PageMode /UseOutlines >>',
		'<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>',
		'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 10 0 R >> >> /Contents 5 0 R >>',
		'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 10 0 R >> >> /Contents 6 0 R >>',
		stream(text('Chapter One', 'First PDF page.')),
		stream(text('Chapter Two', 'Second PDF page.')),
		'<< /Type /Outlines /First 8 0 R /Last 9 0 R /Count 2 >>',
		'<< /Title (Chapter One) /Parent 7 0 R /Next 9 0 R /Dest [3 0 R /Fit] >>',
		'<< /Title (Chapter Two) /Parent 7 0 R /Prev 8 0 R /Dest [4 0 R /Fit] >>',
		'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
	];
	const chunks = ['%PDF-1.4\n'];
	const offsets = [0];
	let position = Buffer.byteLength(chunks[0], 'ascii');
	for (const [index, object] of objects.entries()) {
		offsets.push(position);
		const chunk = `${index + 1} 0 obj\n${object}\nendobj\n`;
		chunks.push(chunk);
		position += Buffer.byteLength(chunk, 'ascii');
	}
	chunks.push(
		`xref\n0 ${offsets.length}\n0000000000 65535 f \n`
		+ offsets.slice(1).map((offset) => `${String(offset).padStart(10, '0')} 00000 n \n`).join('')
		+ `trailer\n<< /Size ${offsets.length} /Root 1 0 R >>\nstartxref\n${position}\n%%EOF\n`,
	);
	return Buffer.from(chunks.join(''), 'ascii');
}
