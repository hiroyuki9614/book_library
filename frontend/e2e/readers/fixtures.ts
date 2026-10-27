import { createStoredZip } from '../../scripts/aozoraAssets';

// Small reproducible publications with real ZIP/OPF/nav/spine/CSS and ruby.
// No uploaded books or binary snapshots are committed.
export function epubFixture({ vertical = false, rtl = false, titleOnly = false, headings = true, mixed = false, paragraphs = 22 } = {}) {
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
			`<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja"><head><title>第${i}章</title><style>body{writing-mode:${(mixed ? i % 2 === 1 : vertical) ? 'vertical-rl' : 'horizontal-tb'};font-family:"Noto Serif CJK JP",serif;line-height:1.8}p{margin:0 0 1em}rt{font-size:.5em}</style></head><body>${headings ? `<h2 id="chapter${i}">第${i}章</h2>` : ''}${body}<a href="s${i === 4 ? 1 : i + 1}.xhtml">本文リンク</a></body></html>`,
		);
	}
	return createStoredZip(entries);
}
