import 'dotenv/config';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import {
	assertGeneratedDemoManifest,
	demoBookDescription,
	verifyGeneratedDemoFiles,
} from '../src/lib/portfolioDemoManifest.js';

function option(args: string[], name: string) {
	const index = args.indexOf(name);
	return index >= 0 ? args[index + 1] : undefined;
}

function sessionCookie(response: Response) {
	const headers = response.headers as Headers & { getSetCookie?: () => string[] };
	const values = headers.getSetCookie?.() ?? [headers.get('set-cookie') ?? ''];
	const cookie = values.filter(Boolean).map((value) => value.split(';', 1)[0]).join('; ');
	if (!cookie) throw new Error('admin sign-in did not return a session cookie');
	return cookie;
}

async function responseBody(response: Response) {
	const value = await response.json().catch(() => null) as { code?: unknown; message?: unknown } | null;
	return {
		code: typeof value?.code === 'string' ? value.code : undefined,
		message: typeof value?.message === 'string' ? value.message : undefined,
	};
}

async function main() {
	const args = process.argv.slice(2);
	const manifestPath = option(args, '--manifest');
	if (!manifestPath) throw new Error('--manifest <generated manifest path> is required');
	const bytes = await readFile(resolve(manifestPath));
	const manifestSha256 = createHash('sha256').update(bytes).digest('hex');
	const manifest: unknown = JSON.parse(bytes.toString('utf8'));
	assertGeneratedDemoManifest(manifest);
	const verified = await verifyGeneratedDemoFiles(manifest, dirname(resolve(manifestPath)));
	console.log(JSON.stringify({
		mode: args.includes('--execute') ? 'execute' : 'dry-run',
		manifestSha256,
		works: verified.map(({ work }) => ({ id: work.id, format: work.format, fileSize: work.fileSize, sha256: work.sha256 })),
	}, null, 2));
	if (!args.includes('--execute')) return;

	if (option(args, '--confirm-manifest-sha256') !== manifestSha256) {
		throw new Error('--confirm-manifest-sha256 must exactly match the dry-run output');
	}
	const apiBaseUrl = process.env.PORTFOLIO_API_BASE_URL?.replace(/\/$/, '');
	const adminEmail = process.env.PORTFOLIO_ADMIN_EMAIL;
	const adminPassword = process.env.PORTFOLIO_ADMIN_PASSWORD;
	const categoryId = Number(process.env.PORTFOLIO_DEMO_CATEGORY_ID);
	if (!apiBaseUrl || (!apiBaseUrl.startsWith('https://') && !/^http:\/\/(localhost|127\.0\.0\.1)(:|\/|$)/.test(apiBaseUrl))) {
		throw new Error('PORTFOLIO_API_BASE_URL must use HTTPS or localhost');
	}
	if (!adminEmail || !adminPassword) throw new Error('PORTFOLIO_ADMIN_EMAIL and PORTFOLIO_ADMIN_PASSWORD are required');
	if (!Number.isSafeInteger(categoryId) || categoryId < 1) throw new Error('PORTFOLIO_DEMO_CATEGORY_ID must be a positive integer');

	const signIn = await fetch(`${apiBaseUrl}/api/auth/sign-in/email`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ email: adminEmail, password: adminPassword }),
	});
	if (!signIn.ok) throw new Error(`admin sign-in failed (${signIn.status})`);
	const cookie = sessionCookie(signIn);
	const currentBooksResponse = await fetch(`${apiBaseUrl}/api/v1/admin/books`, { headers: { Cookie: cookie } });
	if (!currentBooksResponse.ok) throw new Error(`admin preflight failed (${currentBooksResponse.status})`);
	const currentBooks = await currentBooksResponse.json() as { books?: Array<{ title?: unknown }> };
	const existingTitles = new Set(currentBooks.books?.map((book) => book.title).filter((title): title is string => typeof title === 'string'));
	const collision = verified.find(({ work }) => existingTitles.has(work.title));
	if (collision) throw new Error(`a book with the demo title already exists: ${collision.work.id}`);

	const registered = [];
	for (const { work, bytes: fileBytes, mimeType } of verified) {
		const form = new FormData();
		form.set('title', work.title);
		form.set('authorName', work.authorName);
		form.set('publisher', work.publisher);
		form.set('publishedAt', work.publishedAt);
		form.set('categoryId', String(categoryId));
		form.set('pageTurnDirection', work.pageTurnDirection);
		form.set('description', demoBookDescription(work, manifest.usagePolicyUrl));
		form.set('publicationScope', 'all_users');
		form.set('file', new Blob([fileBytes], { type: mimeType }), work.outputFileName);
		const response = await fetch(`${apiBaseUrl}/api/v1/admin/book-registrations`, {
			method: 'POST',
			headers: { Cookie: cookie },
			body: form,
		});
		if (!response.ok) {
			const error = await responseBody(response);
			throw new Error(`demo registration failed: ${work.id} (${response.status}${error.code ? ` ${error.code}` : ''})`);
		}
		const result = await response.json() as { id: number };
		registered.push({ sourceId: work.id, bookId: result.id });
	}
	console.log(JSON.stringify({ registered }, null, 2));
}

main().catch((error) => {
	console.error(error instanceof Error ? error.message : 'Portfolio demo registration failed');
	process.exitCode = 1;
});
