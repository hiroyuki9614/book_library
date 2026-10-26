// Only invoked by the disposable Compose CI project, never by the production runbook.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

async function main() {
	assert.equal(process.env.PORTFOLIO_ISOLATED_SMOKE, 'synthetic-disposable-only');
	assert.equal(process.env.ENVIRONMENT, 'test');
	assert.equal(new URL(process.env.DATABASE_URL!).pathname, '/ci_smoke');
	const { prisma } = await import('../src/lib/prisma.js');
	try {
		assert.equal(await prisma.book.count(), 0);
		assert.equal(await prisma.user.count(), 0);
		for (const name of ['admin', 'user']) await prisma.role.upsert({ where: { name }, create: { name }, update: {} });
		const directory = await mkdtemp(join(tmpdir(), 'portfolio-smoke-'));
		const snapshot = join(directory, 'snapshot.json');
		const manifest = join(directory, 'manifest.json');
		const works = [];
		for (const format of ['pdf', 'epub']) {
			const bytes = format === 'pdf' ? Buffer.from('%PDF-1.7\nsynthetic') : Buffer.concat([Buffer.from([0x50, 0x4b, 0x03, 0x04]), Buffer.alloc(26), Buffer.from('mimetypeapplication/epub+zip')]);
			const filePath = `${format}.${format}`;
			await writeFile(join(directory, filePath), bytes);
			works.push({ id: format, format, filePath, outputFileName: filePath, title: `【デモ】synthetic ${format}`,
				fileSize: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'),
				cardUrl: 'https://www.aozora.gr.jp/card', sourceUrl: 'https://www.aozora.gr.jp/source',
				inputBy: [], proofreadBy: [], basisBook: 'synthetic', conversionNote: 'synthetic smoke only' });
		}
		await writeFile(manifest, JSON.stringify({ version: 1, usagePolicyUrl: 'https://www.aozora.gr.jp/guide/kijyunn.html', works }));
		const environment = { ...process.env, DEMO_USER_EMAIL: 'synthetic@example.test', DEMO_USER_NAME: 'Synthetic', DEMO_USER_PASSWORD: 'synthetic-smoke-password' };
		async function command(name: string, args: string[], succeeds = true) {
			const code = await new Promise((resolve, reject) => {
				const child = spawn('npm', ['run', name, '--', ...args], { env: environment, stdio: 'ignore' });
				child.on('error', reject);
				child.on('close', resolve);
			});
			assert.equal(code === 0, succeeds, `${name}: unexpected exit status`);
		}
		await command('portfolio:preflight', ['--output', snapshot]);
		const bytes = await readFile(snapshot);
		const snapshotHash = createHash('sha256').update(bytes).digest('hex');
		await command('portfolio:protect-existing', ['--snapshot', snapshot]);
		await command('create:demo-user', ['--snapshot', snapshot]);
		await command('portfolio:register-demo-books', ['--snapshot', snapshot, '--manifest', manifest]);
		await command('portfolio:protect-existing', ['--snapshot', snapshot, '--rollback'], false);
		// Each mutation must refuse missing actual backup/restore approval evidence.
		for (const name of ['portfolio:protect-existing', 'create:demo-user', 'portfolio:register-demo-books']) {
			await command(name, ['--snapshot', snapshot, '--confirm-snapshot-sha256', snapshotHash, '--manifest', manifest, '--execute'], false);
		}
		await command('portfolio:preflight', ['--output', snapshot + '.write', '--execute'], false);
		assert.equal(await prisma.user.count(), 0);
		assert.equal(await prisma.account.count(), 0);
		assert.equal(await prisma.book.count(), 0);
		assert.equal(await prisma.bookFile.count(), 0);
		assert.equal(await prisma.roleBookPermission.count(), 0);
		console.log('All four runtime management commands passed read-only/dry-run and missing-evidence smoke');
	} finally {
		await prisma.$disconnect();
	}
}

main().catch(() => { console.error('Isolated portfolio runtime smoke failed'); process.exitCode = 1; });
