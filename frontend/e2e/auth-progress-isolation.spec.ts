import { expect, test, type Page, type Response } from '@playwright/test';
import { readFileSync } from 'node:fs';

const apiOrigin = 'http://localhost:3000';
const adminEmail = process.env.E2E_ADMIN_EMAIL;
const adminPassword = process.env.E2E_ADMIN_PASSWORD;
const demoPassword = process.env.E2E_MVP_PASSWORD;
const metadataPath = process.env.E2E_MVP_METADATA_PATH;

if (!adminEmail || !adminPassword || !demoPassword || !metadataPath) {
	throw new Error('E2E_ADMIN_EMAIL, E2E_ADMIN_PASSWORD, E2E_MVP_PASSWORD, and E2E_MVP_METADATA_PATH are required');
}

const metadata = JSON.parse(readFileSync(metadataPath, 'utf8')) as {
	demoEmail: string;
	pdfBookId: number;
	pdfTitle: string;
};

type Role = 'admin' | 'user';

type MeResponse = {
	id: number;
	role: Role;
};

type ReadingInfoResponse = {
	bookId: number;
	currentPage: number;
	progressPercentage: number | null;
	readStatus: 'unread' | 'reading' | 'completed';
};

function isApiResponse(response: Response, pathname: string) {
	return response.request().method() !== 'OPTIONS'
		&& new URL(response.url()).origin === apiOrigin
		&& new URL(response.url()).pathname === pathname;
}

async function sessionCookieHeader(response: Response) {
	const headers = await response.headersArray();
	return headers
		.filter(({ name }) => name.toLowerCase() === 'set-cookie')
		.map(({ value }) => value)
		.join('\n');
}

async function assertSessionCookiePresent(response: Response) {
	const header = await sessionCookieHeader(response);
	const hasSessionCookie = /session_token=/i.test(header);
	// Keep the cookie value out of assertion output while proving that the
	// cross-origin sign-in response set the Better Auth session cookie.
	expect(hasSessionCookie, 'Better Auth sign-in must set a session cookie').toBe(true);
}

async function assertSessionCookieCleared(response: Response) {
	const header = await sessionCookieHeader(response);
	const clearsSessionCookie = /session_token=/i.test(header)
		&& (/(?:^|;)\s*max-age=0(?:;|$)/i.test(header) || /expires=thu, 01 jan 1970/i.test(header));
	// Keep the cookie value out of assertion output while proving that the
	// cross-origin sign-out response expires the Better Auth session cookie.
	expect(clearsSessionCookie, 'Better Auth sign-out must clear the session cookie').toBe(true);
}

async function assertBrowserSessionCookie(page: Page, present: boolean) {
	const hasSessionCookie = (await page.context().cookies(apiOrigin))
		.some(({ name, value }) => /session_token/i.test(name) && value.length > 0);
	expect(hasSessionCookie, `browser session cookie should be ${present ? 'present' : 'cleared'}`).toBe(present);
}

async function login(page: Page, email: string, password: string, role: Role) {
	await page.goto('/login');
	await expect(page.locator('input[name="userInput"]')).toBeVisible();

	const signInResponse = page.waitForResponse((response) => isApiResponse(response, '/api/auth/sign-in/email')
		&& response.request().method() === 'POST');
	const meResponse = page.waitForResponse((response) => isApiResponse(response, '/api/v1/me')
		&& response.status() === 200);
	await page.locator('input[name="userInput"]').fill(email);
	await page.locator('input[name="passwordInput"]').fill(password);
	await page.getByRole('button', { name: 'Submit', exact: true }).click();

	const [signIn, me] = await Promise.all([signInResponse, meResponse]);
	expect(signIn.status()).toBe(200);
	await assertSessionCookiePresent(signIn);
	const user = await me.json() as MeResponse;
	expect(user.role).toBe(role);
	await assertBrowserSessionCookie(page, true);
	await expect(page).toHaveURL('/');
	await expect(page.getByRole('textbox', { name: '書籍を検索' })).toBeVisible();
	return user;
}

async function logout(page: Page) {
	const signOutResponse = page.waitForResponse((response) => isApiResponse(response, '/api/auth/sign-out')
		&& response.request().method() === 'POST');
	const unauthenticatedMeResponse = page.waitForResponse((response) => isApiResponse(response, '/api/v1/me')
		&& response.status() === 401);
	await page.getByRole('button', { name: 'Toggle Sidebar', exact: true }).click();
	await page.getByRole('button', { name: 'ログアウト', exact: true }).click();

	const [signOut, me] = await Promise.all([signOutResponse, unauthenticatedMeResponse]);
	expect(signOut.status()).toBe(200);
	await assertSessionCookieCleared(signOut);
	expect(me.status()).toBe(401);
	await expect(page).toHaveURL('/login');
	await assertBrowserSessionCookie(page, false);
}

async function patchReadingInfo(page: Page, bookId: number, currentPage: number, progressPercentage: number, readStatus: 'reading' | 'completed') {
	return page.evaluate(async ({ apiOrigin: origin, bookId: id, currentPage: pageNumber, progressPercentage: percentage, readStatus: status }) => {
		const response = await fetch(`${origin}/api/v1/books/${id}/reading-info`, {
			credentials: 'include',
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ currentPage: pageNumber, progressPercentage: percentage, readStatus: status }),
		});
		return { status: response.status, body: await response.json() as ReadingInfoResponse };
	}, { apiOrigin, bookId, currentPage, progressPercentage, readStatus });
}

async function readReadingInfo(page: Page, bookId: number) {
	return page.evaluate(async ({ apiOrigin: origin, bookId: id }) => {
		const response = await fetch(`${origin}/api/v1/books/${id}/reading-info`, { credentials: 'include' });
		return { status: response.status, body: await response.json() as ReadingInfoResponse };
	}, { apiOrigin, bookId });
}

async function assertShelfProgress(page: Page, title: string, expectedProgress: number) {
	const listResponse = page.waitForResponse((response) => {
		if (!isApiResponse(response, '/api/v1/books') || response.request().method() !== 'GET' || response.status() !== 200) return false;
		const url = new URL(response.url());
		return url.searchParams.get('page') === '1' && url.searchParams.get('limit') === '100';
	});
	await page.reload();
	const list = await listResponse;
	const body = await list.json() as { books: Array<{ title: string; progressPercentage: number | null }> };
	const book = body.books.find((entry) => entry.title === title);
	expect(book?.progressPercentage).toBe(expectedProgress);

	await page.getByRole('textbox', { name: '書籍を検索' }).fill(title);
	const row = page.getByRole('link', { name: `${title}を読む` });
	await expect(row).toBeVisible();
	await expect(row.getByText(`${expectedProgress}%`, { exact: true })).toBeVisible();
	await expect(row.getByRole('progressbar', { name: `${title} の読書進捗` }))
		.toHaveAttribute('aria-valuenow', String(expectedProgress));
}

test('real Better Auth logout and account-isolated reading progress survive browser reloads', async ({ page }) => {
	await page.setViewportSize({ width: 393, height: 851 });

	const admin = await login(page, adminEmail, adminPassword, 'admin');
	const adminSave = await patchReadingInfo(page, metadata.pdfBookId, 1, 50, 'reading');
	expect(adminSave).toMatchObject({
		status: 200,
		body: { bookId: metadata.pdfBookId, currentPage: 1, progressPercentage: 50, readStatus: 'reading' },
	});
	await assertShelfProgress(page, metadata.pdfTitle, 50);

	await logout(page);

	const reloggedAdmin = await login(page, adminEmail, adminPassword, 'admin');
	expect(reloggedAdmin.id).toBe(admin.id);
	const adminAfterRelogin = await readReadingInfo(page, metadata.pdfBookId);
	expect(adminAfterRelogin).toMatchObject({
		status: 200,
		body: { bookId: metadata.pdfBookId, currentPage: 1, progressPercentage: 50, readStatus: 'reading' },
	});
	await assertShelfProgress(page, metadata.pdfTitle, 50);

	await logout(page);

	const demo = await login(page, metadata.demoEmail, demoPassword, 'user');
	expect(demo.id).not.toBe(admin.id);
	const demoSave = await patchReadingInfo(page, metadata.pdfBookId, 2, 100, 'completed');
	expect(demoSave).toMatchObject({
		status: 200,
		body: { bookId: metadata.pdfBookId, currentPage: 2, progressPercentage: 100, readStatus: 'completed' },
	});
	await assertShelfProgress(page, metadata.pdfTitle, 100);

	await logout(page);

	const finalAdmin = await login(page, adminEmail, adminPassword, 'admin');
	expect(finalAdmin.id).toBe(admin.id);
	const adminAfterDemoSave = await readReadingInfo(page, metadata.pdfBookId);
	expect(adminAfterDemoSave).toMatchObject({
		status: 200,
		body: { bookId: metadata.pdfBookId, currentPage: 1, progressPercentage: 50, readStatus: 'reading' },
	});
	await assertShelfProgress(page, metadata.pdfTitle, 50);
	await logout(page);
});
