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
	await page.setViewportSize({ width: 393, height: 851 });
	await login(page, metadata.demoEmail);
	const fileResponse = page.waitForResponse((response) => response.url().endsWith(`/api/v1/books/${metadata.pdfBookId}/file`));
	await page.getByText(metadata.pdfTitle, { exact: true }).click();
	await expect(page).toHaveURL(`/reader/${metadata.pdfBookId}`);
	expect((await fileResponse).headers()['content-type']).toContain('application/pdf');

	const mobileControls = (await page.evaluate(() => innerWidth)) < 768;
	if (mobileControls) {
		await page.getByRole('button', { name: '操作メニューを開く', exact: true }).click();
		await expect(page.getByRole('heading', { name: '操作メニュー', exact: true })).toBeVisible();
	}
	const pageInput = page.getByRole('spinbutton');
	await expect(pageInput).toHaveValue('1', { timeout: 30_000 });
	await expect(page.locator('output[aria-live="polite"]:visible')).toHaveText('1 / 2 ページ 50%');
	await expect(page.getByText('PDF本文を描画しています…')).toHaveCount(0, { timeout: 30_000 });
	await page.getByRole('button', { name: '目次' }).click();
	await expect(page.getByRole('button', { name: 'First chapter' })).toBeVisible();
	await page.getByRole('button', { name: 'First chapter' }).click();
	if (mobileControls) {
		await page.getByRole('button', { name: '閉じる', exact: true }).click();
	}
	await expect(page.getByRole('dialog')).toBeHidden();

	const saveResponse = page.waitForResponse((response) => response.url().endsWith(`/api/v1/books/${metadata.pdfBookId}/reading-info`)
		&& response.request().method() === 'PATCH'
		&& response.request().postDataJSON()?.readStatus === 'completed');
	const viewport = page.getByTestId('pdf-viewport');
	const shown = () => page.evaluate(() => {
		const viewport = document.querySelector('[data-testid="pdf-viewport"]')!.getBoundingClientRect();
		return [...document.querySelectorAll<HTMLElement>('[data-pdf-page]')].map(el => {
			const r = el.getBoundingClientRect();
			return {page:Number(el.dataset.pdfPage),area:Math.max(0,Math.min(r.bottom,viewport.bottom)-Math.max(r.top,viewport.top))*Math.max(0,Math.min(r.right,viewport.right)-Math.max(r.left,viewport.left))};
		}).sort((a,b)=>b.area-a.area)[0]?.page;
	});
	await expect.poll(shown).toBe(1);
	const firstImage = await page.locator('[data-pdf-page="1"] img').screenshot();
	const viewportBounds = await viewport.boundingBox();
	expect(viewportBounds).not.toBeNull();
	// Locator hit testing waits until the closing TOC overlay releases pointer input.
	await viewport.click({position:{x:viewportBounds!.width-5,y:viewportBounds!.height/2}});

	if (mobileControls) {
		await page.getByRole('button', { name: '操作メニューを開く', exact: true }).click();
		await expect(page.getByRole('heading', { name: '操作メニュー', exact: true })).toBeVisible();
	}
	await expect(pageInput).toHaveValue('2');
	await expect.poll(shown).toBe(2);
	expect((await page.locator('[data-pdf-page="2"] img').screenshot()).equals(firstImage)).toBe(false);
	await expect(page.locator('output[aria-live="polite"]:visible')).toHaveText('2 / 2 ページ 100%');
	if (mobileControls) await page.getByRole('button', { name: '閉じる', exact: true }).click();
	expect((await saveResponse).status()).toBe(200);
	await page.setViewportSize({ width: 851, height: 393 });
	await expect(viewport).toBeVisible();
	await page.reload();
	if ((await page.evaluate(() => innerWidth)) < 768) {
		await page.getByRole('button', { name: '操作メニューを開く', exact: true }).click();
	}
	await expect(page.getByRole('spinbutton')).toHaveValue('2', { timeout: 30_000 });
	await expect.poll(shown).toBe(2);
	await expect(page.locator('[data-pdf-page="2"] img')).toBeVisible();
});

test('demo user opens protected EPUB and persists/restores a CFI', async ({ page }) => {
	await page.setViewportSize({ width: 393, height: 851 });
	await login(page, metadata.demoEmail);
	const fileResponse = page.waitForResponse((response) => response.url().endsWith(`/api/v1/books/${metadata.epubBookId}/file`));
	await page.getByText(metadata.epubTitle, { exact: true }).click();
	await expect(page).toHaveURL(`/reader/${metadata.epubBookId}`);
	expect((await fileResponse).headers()['content-type']).toContain('application/epub+zip');
	await expect(page.getByRole('button', { name: '目次' })).toBeVisible({ timeout: 30_000 });
	await expect(page.getByText(/本文位置: \d+ \/ \d+/)).toBeVisible();
	await page.getByRole('button', { name: '目次' }).click();
	await expect(page.getByRole('button', { name: '第一節' })).toBeVisible();
	await page.getByRole('button', { name: '第一章' }).click();
	await expect(page.getByRole('button', { name: '目次移動前へ戻る' })).toBeVisible();
	await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy','false');
	await expect(page.frameLocator('[data-publication-pane="primary"] iframe').first().getByRole('heading', { name: '第一章' })).toBeVisible();

	const epubBody = page.frameLocator('[data-publication-pane="primary"] iframe').first().locator('body');
	const epubBounds = await epubBody.boundingBox();
	expect(epubBounds).not.toBeNull();
	const saveResponse = page.waitForResponse((response) => response.url().endsWith(`/api/v1/books/${metadata.epubBookId}/reading-info`)
		&& response.request().method() === 'PATCH'
		&& response.request().postDataJSON()?.readStatus === 'completed');
	await epubBody.click({ position: { x: epubBounds!.width - 5, y: epubBounds!.height / 2 } });
	await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy','false');
	await expect(page.frameLocator('[data-publication-pane="primary"] iframe').first().getByRole('heading', { name: '第二章' })).toBeVisible();
	expect((await saveResponse).status()).toBe(200);
	await expect(page.getByText(/100%/)).toBeVisible();

	const result = await page.evaluate(async ({ bookId }) => {
		const restored = await fetch(`http://localhost:3000/api/v1/books/${bookId}/reading-info`, { credentials: 'include' });
		return { restoreStatus: restored.status, body: await restored.json() };
	}, { bookId: metadata.epubBookId });
	expect(result.restoreStatus).toBe(200);
	expect(result.body).toMatchObject({ bookId: metadata.epubBookId, currentPage: 1, readStatus: 'completed' });
	expect(result.body.currentPosition).toMatch(/^epubcfi\(/);
	await page.setViewportSize({ width: 851, height: 393 });
	await page.reload();
	await expect(page.getByRole('button', { name: '目次' })).toBeVisible({ timeout: 30_000 });
	await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy','false');
	await expect(page.frameLocator('[data-publication-pane="primary"] iframe').first().getByRole('heading', { name: '第二章' })).toBeVisible();
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
