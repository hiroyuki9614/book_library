import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const password = process.env.E2E_MVP_PASSWORD;
const permissionedEmail = 'belib-mvp-real-e2e-permissioned-20260811@example.com';
const permissionedPeerEmail = 'belib-mvp-real-e2e-permissioned-peer-20260811@example.com';
const unpermissionedEmail = 'belib-mvp-real-e2e-unpermissioned-20260811@example.com';
const metadataPath = process.env.E2E_MVP_METADATA_PATH;

if (!metadataPath || !password) {
	throw new Error('E2E_MVP_METADATA_PATH and E2E_MVP_PASSWORD are required');
}

let bookId: number;
let bookTitle: string;
let adminOnlyBookId: number;
let adminOnlyBookTitle: string;

test.beforeAll(() => {
	const metadata = JSON.parse(readFileSync(metadataPath, 'utf8')) as { bookId: number; bookTitle: string; adminOnlyBookId: number; adminOnlyBookTitle: string };
	bookId = metadata.bookId;
	bookTitle = metadata.bookTitle;
	adminOnlyBookId = metadata.adminOnlyBookId;
	adminOnlyBookTitle = metadata.adminOnlyBookTitle;
});

async function login(page: Page, email: string) {
	await page.goto('/login');
	await page.locator('input[name="userInput"]').fill(email);
	await page.locator('input[name="passwordInput"]').fill(password);
	await page.getByRole('button', { name: 'Submit' }).click();
	await expect(page).toHaveURL('/');
}

test('authorized user logs in, reads protected PDF, persists reading state, and cannot read another user\'s progress', async ({ page, browser }) => {
	await login(page, permissionedEmail);
	await expect(page.getByPlaceholder('Search books or authors...')).toBeVisible();
	await expect(page.getByText(bookTitle, { exact: true })).toBeVisible();
	await expect(page.getByText(adminOnlyBookTitle, { exact: true })).toHaveCount(0);

	const sessionResponse = await page.evaluate(async () => {
		const response = await fetch(`${location.protocol}//localhost:3000/api/v1/me`, { credentials: 'include' });
		return { status: response.status, body: await response.json() };
	});
	expect(sessionResponse.status).toBe(200);
	expect(sessionResponse.body.email).toBe(permissionedEmail);

	const pdfResponsePromise = page.waitForResponse((response) => response.url().endsWith(`/api/v1/books/${bookId}/file`) && response.request().method() === 'GET');
	await page.getByText(bookTitle, { exact: true }).click();
	await expect(page).toHaveURL(`/reader/${bookId}`);
	const pdfResponse = await pdfResponsePromise;
	expect(pdfResponse.status()).toBe(200);
	expect(pdfResponse.headers()['content-type']).toContain('application/pdf');

	const pageInput = page.getByRole('spinbutton');
	await expect(pageInput).toHaveValue('1', { timeout: 30_000 });
	const saveResponsePromise = page.waitForResponse((response) => response.url().endsWith(`/api/v1/books/${bookId}/reading-info`) && response.request().method() === 'PATCH');
	await page.getByRole('button', { name: '>' }).click();
	await expect(pageInput).toHaveValue('2');
	const saveResponse = await saveResponsePromise;
	expect(saveResponse.status()).toBe(200);
	expect(await saveResponse.json()).toEqual(expect.objectContaining({ currentPage: 2, readStatus: 'reading' }));

	const persistedResponse = await page.evaluate(async (id) => {
		const response = await fetch(`http://localhost:3000/api/v1/books/${id}/reading-info`, { credentials: 'include' });
		return { status: response.status, body: await response.json() };
	}, bookId);
	expect(persistedResponse.status).toBe(200);
	expect(persistedResponse.body).toEqual(expect.objectContaining({ currentPage: 2, readStatus: 'reading' }));

	const restoreResponsePromise = page.waitForResponse((response) => response.url().endsWith(`/api/v1/books/${bookId}/reading-info`) && response.request().method() === 'GET');
	await page.reload();
	const restoreResponse = await restoreResponsePromise;
	expect(restoreResponse.status()).toBe(200);
	expect((await restoreResponse.json()).currentPage).toBe(2);
	await expect(page.getByRole('spinbutton')).toHaveValue('2', { timeout: 30_000 });

	const peerContext = await browser.newContext();
	try {
		const peerPage = await peerContext.newPage();
		await login(peerPage, permissionedPeerEmail);
		const peerReadingInfo = await peerPage.evaluate(async (id) => {
			const response = await fetch(`http://localhost:3000/api/v1/books/${id}/reading-info`, { credentials: 'include' });
			return { status: response.status, body: await response.json() };
		}, bookId);
		expect(peerReadingInfo).toEqual({ status: 200, body: { bookId, currentPage: 1, readStatus: 'unread' } });
	} finally {
		await peerContext.close();
	}
});

test('unpermissioned user cannot see the book or access protected book APIs', async ({ browser }) => {
	const context = await browser.newContext();
	const page = await context.newPage();
	try {
		await login(page, unpermissionedEmail);
		await expect(page.getByText(bookTitle, { exact: true })).toHaveCount(0);
		await expect(page.getByText(adminOnlyBookTitle, { exact: true })).toHaveCount(0);

		const statuses = await page.evaluate(async (id) => {
			const detail = await fetch(`http://localhost:3000/api/v1/books/${id}`, { credentials: 'include' });
			const file = await fetch(`http://localhost:3000/api/v1/books/${id}/file`, { credentials: 'include' });
			return { detail: detail.status, file: file.status };
		}, bookId);
		expect(statuses).toEqual({ detail: 403, file: 403 });

		const adminOnlyStatuses = await page.evaluate(async (id) => {
			const detail = await fetch(`http://localhost:3000/api/v1/books/${id}`, { credentials: 'include' });
			const file = await fetch(`http://localhost:3000/api/v1/books/${id}/file`, { credentials: 'include' });
			return { detail: detail.status, file: file.status };
		}, adminOnlyBookId);
		expect(adminOnlyStatuses).toEqual({ detail: 403, file: 403 });
	} finally {
		await context.close();
	}
});

test('unauthenticated browser cannot use protected APIs or enter the reader', async ({ page }) => {
	await page.goto('/login');

	const statuses = await page.evaluate(async (id) => {
		const list = await fetch('http://localhost:3000/api/v1/books', { credentials: 'include' });
		const detail = await fetch(`http://localhost:3000/api/v1/books/${id}`, { credentials: 'include' });
		return { list: list.status, detail: detail.status };
	}, bookId);
	expect(statuses).toEqual({ list: 401, detail: 401 });

	await page.goto(`/reader/${bookId}`);
	await expect(page).toHaveURL('/login');
});
