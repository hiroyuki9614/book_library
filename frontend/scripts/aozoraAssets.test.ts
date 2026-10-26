import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { describe, expect, test, vi } from 'vitest';
import { createEpub, fetchAozoraSource, validateCatalog } from './aozoraAssets.js';

describe('Aozora portfolio assets', () => {
	test('catalogは公式青空文庫sourceだけを許可しEPUB/PDF各1冊を要求する', async () => {
		const catalog = validateCatalog(JSON.parse(await readFile(resolve('../docs/portfolio/aozora-demo-books.json'), 'utf8')));
		expect(catalog.works.map((work) => work.format).sort()).toEqual(['epub', 'pdf']);
		expect(catalog.works.every((work) => work.title.startsWith('【デモ】'))).toBe(true);
		expect(catalog.works.every((work) => new URL(work.sourceUrl).hostname === 'www.aozora.gr.jp')).toBe(true);
	});

	test('official source and image fetches reject redirects and credential-bearing/external targets', async () => {
		const request = vi.fn().mockResolvedValue(new Response('source'));
		vi.stubGlobal('fetch', request);
		try {
			await fetchAozoraSource('https://www.aozora.gr.jp/source.html');
			expect(request).toHaveBeenCalledWith(new URL('https://www.aozora.gr.jp/source.html'), expect.objectContaining({ redirect: 'error' }));
			for (const url of ['https://external.test/source', 'http://www.aozora.gr.jp/source', 'https://user:pass@www.aozora.gr.jp/source']) {
				await expect(fetchAozoraSource(url)).rejects.toThrow('official HTTPS origin');
			}
			expect(request).toHaveBeenCalledTimes(1);
			request.mockRejectedValue(new TypeError('redirect rejected'));
			await expect(fetchAozoraSource('https://www.aozora.gr.jp/redirect')).rejects.toThrow('redirect rejected');
			expect(request).toHaveBeenCalledTimes(2);
		} finally {
			vi.unstubAllGlobals();
		}
	});

	test('EPUBは非圧縮mimetypeを先頭に置き、ルビ・目次・出典を保持する', async () => {
		const catalog = validateCatalog(JSON.parse(await readFile(resolve('../docs/portfolio/aozora-demo-books.json'), 'utf8')));
		const work = catalog.works.find((entry) => entry.format === 'epub');
		expect(work).toBeDefined();
		const epub = createEpub(work!, '<h1>第一章</h1><p><ruby>吾輩<rt>わがはい</rt></ruby>は猫である。</p>');
		expect(epub.readUInt32LE(0)).toBe(0x04034b50);
		expect(epub.subarray(30, 38).toString()).toBe('mimetype');
		expect(epub.toString()).toContain('application/epub+zip');
		expect(epub.toString()).toContain('<ruby>吾輩<rt>わがはい</rt></ruby>');
		expect(epub.toString()).toContain('OEBPS/nav.xhtml');
		expect(epub.toString()).toContain(work!.cardUrl);
	});
});
