import { expect, test, type Page } from '@playwright/test';
import { mkdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const password = process.env.E2E_MVP_PASSWORD;
const adminEmail = process.env.E2E_ADMIN_EMAIL;
const adminPassword = process.env.E2E_ADMIN_PASSWORD;
const metadataPath = process.env.E2E_MVP_METADATA_PATH;
const capturePortfolioScreenshots = process.env.E2E_CAPTURE_PORTFOLIO_SCREENSHOTS === 'true';

async function captureScreenshot(page: Page, name: string) {
	if (!capturePortfolioScreenshots) return;
	const directory = resolve(process.cwd(), '../docs/images');
	mkdirSync(directory, { recursive: true });
	await page.screenshot({ path: resolve(directory, name), type: 'jpeg', quality: 82, fullPage: true });
}

if (!metadataPath || !password || !adminEmail || !adminPassword) {
	throw new Error('E2E_MVP_METADATA_PATH, E2E_MVP_PASSWORD, and admin credentials are required');
}

const metadata = JSON.parse(readFileSync(metadataPath, 'utf8')) as {
	demoEmail: string;
	pdfBookId: number;
	pdfTitle: string;
	epubBookId: number;
	epubTitle: string;
	privateBookId: number;
	privateTitle: string;
};

async function login(page: Page, email: string, loginPassword = password) {
	await page.goto('/login');
	await page.locator('input[name="userInput"]').fill(email);
	await page.locator('input[name="passwordInput"]').fill(loginPassword);
	await page.getByRole('button', { name: 'Submit' }).click();
	await expect(page).toHaveURL('/');
}

test('demo user sees only public EPUB/PDF and cannot access a private book or admin API', async ({ page }) => {
	await login(page, metadata.demoEmail);
	await expect(page.getByText(metadata.pdfTitle, { exact: true })).toBeVisible();
	await expect(page.getByText(metadata.epubTitle, { exact: true })).toBeVisible();
	await expect(page.getByText(metadata.privateTitle, { exact: true })).toHaveCount(0);
	await captureScreenshot(page, 'portfolio-demo-shelf.jpg');

	const statuses = await page.evaluate(async ({ privateBookId }) => {
		const options = { credentials: 'include' as const };
		const detail = await fetch(`http://localhost:3000/api/v1/books/${privateBookId}`, options);
		const file = await fetch(`http://localhost:3000/api/v1/books/${privateBookId}/file`, options);
		const readingGet = await fetch(`http://localhost:3000/api/v1/books/${privateBookId}/reading-info`, options);
		const readingPatch = await fetch(`http://localhost:3000/api/v1/books/${privateBookId}/reading-info`, {
			...options,
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ currentPage: 1 }),
		});
		const admin = await fetch('http://localhost:3000/api/v1/admin/books', options);
		const search = await fetch(`http://localhost:3000/api/v1/books?q=${encodeURIComponent('Private Existing Book')}`, options);
		return { detail: detail.status, file: file.status, readingGet: readingGet.status, readingPatch: readingPatch.status, admin: admin.status, search: await search.json() };
	}, { privateBookId: metadata.privateBookId });

	expect(statuses).toMatchObject({ detail: 403, file: 403, readingGet: 403, readingPatch: 403, admin: 403 });
	expect(statuses.search).toMatchObject({ total: 0, books: [] });
});

test('demo user reads protected PDF and restores its saved page', async ({ page }) => {
	await login(page, metadata.demoEmail);
	const fileResponse = page.waitForResponse((response) => response.url().endsWith(`/api/v1/books/${metadata.pdfBookId}/file`));
	await page.getByText(metadata.pdfTitle, { exact: true }).click();
	await expect(page).toHaveURL(`/reader/${metadata.pdfBookId}`);
	expect((await fileResponse).headers()['content-type']).toContain('application/pdf');

	const pageInput = page.getByRole('spinbutton');
	await expect(pageInput).toHaveValue('1', { timeout: 30_000 });
	const saveResponse = page.waitForResponse((response) => response.url().endsWith(`/api/v1/books/${metadata.pdfBookId}/reading-info`) && response.request().method() === 'PATCH');
	await page.getByRole('button', { name: '次のページへ' }).click();
	await expect(pageInput).toHaveValue('2');
	expect((await saveResponse).status()).toBe(200);
	await page.reload();
	await expect(page.getByRole('spinbutton')).toHaveValue('2', { timeout: 30_000 });
});

test('demo user opens protected EPUB and persists/restores a CFI', async ({ page }) => {
	await login(page, metadata.demoEmail);
	const fileResponse = page.waitForResponse((response) => response.url().endsWith(`/api/v1/books/${metadata.epubBookId}/file`));
	await page.getByText(metadata.epubTitle, { exact: true }).click();
	await expect(page).toHaveURL(`/reader/${metadata.epubBookId}`);
	expect((await fileResponse).headers()['content-type']).toContain('application/epub+zip');
	await expect(page.getByRole('button', { name: '目次' })).toBeVisible({ timeout: 30_000 });

	const cfi = 'epubcfi(/6/2!/4/2/1:0)';
	const result = await page.evaluate(async ({ bookId, cfi }) => {
		const saved = await fetch(`http://localhost:3000/api/v1/books/${bookId}/reading-info`, {
			method: 'PATCH',
			credentials: 'include',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ currentPosition: cfi, readStatus: 'reading' }),
		});
		const restored = await fetch(`http://localhost:3000/api/v1/books/${bookId}/reading-info`, { credentials: 'include' });
		return { saveStatus: saved.status, restoreStatus: restored.status, body: await restored.json() };
	}, { bookId: metadata.epubBookId, cfi });
	expect(result).toEqual({ saveStatus: 200, restoreStatus: 200, body: { bookId: metadata.epubBookId, currentPosition: cfi, currentPage: 1, readStatus: 'reading' } });
	await page.reload();
	await expect(page.getByRole('button', { name: '目次' })).toBeVisible({ timeout: 30_000 });
});

test('admin sees private and demo books', async ({ page }) => {
	await login(page, adminEmail, adminPassword);
	await expect(page.getByText(metadata.pdfTitle, { exact: true })).toBeVisible();
	await expect(page.getByText(metadata.epubTitle, { exact: true })).toBeVisible();
	await expect(page.getByText(metadata.privateTitle, { exact: true })).toBeVisible();
});

test('unauthenticated browser receives 401 and public signup is disabled', async ({ page }) => {
	await page.goto('/login');
	await captureScreenshot(page, 'portfolio-demo-login.jpg');
	const statuses = await page.evaluate(async ({ privateBookId }) => {
		const list = await fetch('http://localhost:3000/api/v1/books', { credentials: 'include' });
		const detail = await fetch(`http://localhost:3000/api/v1/books/${privateBookId}`, { credentials: 'include' });
		const signup = await fetch('http://localhost:3000/api/auth/sign-up/email', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ email: 'blocked@example.com', name: 'Blocked', password: 'blocked-password' }),
		});
		return { list: list.status, detail: detail.status, signup: signup.status };
	}, { privateBookId: metadata.privateBookId });
	expect(statuses).toEqual({ list: 401, detail: 401, signup: 400 });
	await page.goto(`/reader/${metadata.privateBookId}`);
	await expect(page).toHaveURL('/login');
});
