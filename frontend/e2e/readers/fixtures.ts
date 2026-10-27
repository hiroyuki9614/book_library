import { createStoredZip } from '../../scripts/aozoraAssets';

export function epubFixture({ vertical = true, rtl = true } = {}) {
	const entries: Array<{ name: string; data: Buffer }> = [];
	const add = (name: string, value: string) => entries.push({ name, data: Buffer.from(value) });
	add('mimetype', 'application/epub+zip');
	add('META-INF/container.xml', '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>');
	add('OPS/book.opf', `<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">belib-real-reader</dc:identifier><dc:title>実描画回帰試験</dc:title><dc:language>ja</dc:language><meta property="dcterms:modified">2026-10-27T00:00:00Z</meta></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>${[1, 2, 3].map((index) => `<item id="s${index}" href="s${index}.xhtml" media-type="application/xhtml+xml"/>`).join('')}</manifest><spine page-progression-direction="${rtl ? 'rtl' : 'ltr'}">${[1, 2, 3].map((index) => `<itemref idref="s${index}"/>`).join('')}</spine></package>`);
	add('OPS/nav.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>目次</title></head><body><nav epub:type="toc"><ol><li><a href="s1.xhtml">第一章</a><ol><li><a href="s2.xhtml">第二章</a><ol><li><a href="s3.xhtml">第三章</a></li></ol></li></ol></li></ol></nav></body></html>');
	for (let chapter = 1; chapter <= 3; chapter++) {
		const paragraphs = Array.from({ length: 28 }, (_, paragraph) => `<p>第${chapter}章第${paragraph + 1}段。${'縦書きの読書位置を確実に復元するための本文です。'.repeat(12)}</p>`).join('');
		add(`OPS/s${chapter}.xhtml`, `<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja"><head><title>第${chapter}章</title><style>body{writing-mode:${vertical ? 'vertical-rl' : 'horizontal-tb'};font-family:serif;line-height:1.8}p{margin:0 0 1em}</style></head><body><h2>第${chapter}章</h2>${paragraphs}</body></html>`);
	}
	return createStoredZip(entries);
}
