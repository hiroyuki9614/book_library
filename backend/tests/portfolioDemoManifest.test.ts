import { createHash } from 'node:crypto';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, test } from 'vitest';
import {
	assertGeneratedDemoManifest,
	demoBookDescription,
	verifyGeneratedDemoFiles,
	type GeneratedDemoManifest,
} from '../src/lib/portfolioDemoManifest.js';

const directories: string[] = [];

afterEach(async () => {
	await Promise.all(directories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })));
});

describe('portfolio demo book manifest', () => {
	test('生成物のhash・size・EPUB/PDF headerを登録前に照合する', async () => {
		const directory = await mkdtemp(join(tmpdir(), 'belib-demo-manifest-'));
		directories.push(directory);
		const epub = Buffer.concat([Buffer.from([0x50, 0x4b, 0x03, 0x04]), Buffer.alloc(26), Buffer.from('mimetypeapplication/epub+zip')]);
		const pdf = Buffer.from('%PDF-1.7\nportfolio demo');
		const epubPath = join(directory, 'demo.epub');
		const pdfPath = join(directory, 'demo.pdf');
		await writeFile(epubPath, epub);
		await writeFile(pdfPath, pdf);
		const base = {
			authorName: '夏目 漱石', publisher: '青空文庫', publishedAt: '1905-01-01', pageTurnDirection: 'ltr' as const,
			cardUrl: 'https://www.aozora.gr.jp/cards/000148/card789.html', sourceUrl: 'https://www.aozora.gr.jp/cards/000148/files/source.html',
			sourceLastUpdated: '2018-02-05', authorDeathDate: '1916-12-09', basisBook: '底本', inputBy: ['入力者'], proofreadBy: ['校正者'], conversionNote: '変換履歴',
		};
		const manifest: GeneratedDemoManifest = {
			version: 1,
			generatedAt: '2026-10-25T00:00:00.000Z',
			usagePolicyUrl: 'https://www.aozora.gr.jp/guide/kijyunn.html',
			works: [
				{ ...base, id: 'demo-epub', format: 'epub', outputFileName: 'demo.epub', title: '【デモ】EPUB', filePath: epubPath, fileSize: epub.length, sha256: createHash('sha256').update(epub).digest('hex') },
				{ ...base, id: 'demo-pdf', format: 'pdf', outputFileName: 'demo.pdf', title: '【デモ】PDF', filePath: pdfPath, fileSize: pdf.length, sha256: createHash('sha256').update(pdf).digest('hex') },
			],
		};

		expect(() => assertGeneratedDemoManifest(manifest)).not.toThrow();
		await expect(verifyGeneratedDemoFiles(manifest)).resolves.toHaveLength(2);
		await writeFile(pdfPath, Buffer.from('%PDF-tampered'));
		await expect(verifyGeneratedDemoFiles(manifest)).rejects.toThrow('demo-pdf');
	});

	test('説明文へ出典・底本・入力校正・変換履歴・共有進捗の注意を残す', () => {
		const description = demoBookDescription({
			id: 'demo', format: 'pdf', filePath: '/tmp/demo.pdf', fileSize: 10, sha256: 'a'.repeat(64), outputFileName: 'demo.pdf',
			title: '【デモ】作品', authorName: '夏目 漱石', publisher: '青空文庫', publishedAt: '1905-01-01', pageTurnDirection: 'ltr',
			cardUrl: 'https://www.aozora.gr.jp/card', sourceUrl: 'https://www.aozora.gr.jp/source', sourceLastUpdated: '2026-01-01',
			authorDeathDate: '1916-12-09', basisBook: '底本情報', inputBy: ['入力者'], proofreadBy: ['校正者'], conversionNote: 'PDFへ変換',
		}, 'https://www.aozora.gr.jp/guide/kijyunn.html');
		expect(description).toContain('読書位置が他の利用者と共有');
		expect(description).toContain('底本情報');
		expect(description).toContain('入力者');
		expect(description).toContain('校正者');
		expect(description).toContain('PDFへ変換');
	});
});
