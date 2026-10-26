import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { isAbsolute, resolve } from 'node:path';

export type GeneratedDemoWork = {
	id: string;
	format: 'epub' | 'pdf';
	outputFileName: string;
	filePath: string;
	fileSize: number;
	sha256: string;
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

export type GeneratedDemoManifest = {
	version: 1;
	generatedAt: string;
	usagePolicyUrl: string;
	works: GeneratedDemoWork[];
};

export function assertGeneratedDemoManifest(value: unknown): asserts value is GeneratedDemoManifest {
	const manifest = value as Partial<GeneratedDemoManifest> | null;
	if (
		!manifest || manifest.version !== 1 || manifest.usagePolicyUrl !== 'https://www.aozora.gr.jp/guide/kijyunn.html' ||
		!Array.isArray(manifest.works) || manifest.works.length < 2 ||
		!manifest.works.some((work) => work.format === 'epub') || !manifest.works.some((work) => work.format === 'pdf') ||
		manifest.works.some((work) =>
			!work.title?.startsWith('【デモ】') || !['epub', 'pdf'].includes(work.format) ||
			typeof work.filePath !== 'string' || !Number.isSafeInteger(work.fileSize) || work.fileSize < 1 ||
			!/^\w[\w-]*$/.test(work.id) || !/^[0-9a-f]{64}$/.test(work.sha256) ||
			new URL(work.cardUrl).hostname !== 'www.aozora.gr.jp' || new URL(work.sourceUrl).hostname !== 'www.aozora.gr.jp' ||
			!Array.isArray(work.inputBy) || !Array.isArray(work.proofreadBy) || !work.basisBook || !work.conversionNote)
	) throw new Error('generated demo manifest is invalid');
}

export async function verifyGeneratedDemoFiles(manifest: GeneratedDemoManifest, baseDirectory = '.') {
	const verified = [];
	for (const work of manifest.works) {
		const filePath = isAbsolute(work.filePath) ? work.filePath : resolve(baseDirectory, work.filePath);
		const bytes = await readFile(filePath);
		const sha256 = createHash('sha256').update(bytes).digest('hex');
		const expectedMime = work.format === 'epub' ? 'application/epub+zip' : 'application/pdf';
		const validHeader = work.format === 'epub'
			? bytes.length >= 38 && bytes.readUInt32LE(0) === 0x04034b50 && bytes.subarray(30, 38).toString() === 'mimetype'
			: bytes.subarray(0, 5).toString('ascii') === '%PDF-';
		if (bytes.length !== work.fileSize || sha256 !== work.sha256 || !validHeader) {
			throw new Error(`generated demo file verification failed: ${work.id}`);
		}
		verified.push({ work, bytes, mimeType: expectedMime });
	}
	return verified;
}

export function demoBookDescription(work: GeneratedDemoWork, usagePolicyUrl: string) {
	return [
		'採用面接用の公開デモ書籍です。共有アカウントでは読書位置が他の利用者と共有されます。',
		`出典図書カード: ${work.cardUrl}`,
		`原典ファイル: ${work.sourceUrl}（最終更新 ${work.sourceLastUpdated}）`,
		`底本: ${work.basisBook}`,
		`入力: ${work.inputBy.join('、')} / 校正: ${work.proofreadBy.join('、')}`,
		`著者没年月日: ${work.authorDeathDate}`,
		`変換履歴: ${work.conversionNote}`,
		`利用規準: ${usagePolicyUrl}`,
	].join('\n');
}
