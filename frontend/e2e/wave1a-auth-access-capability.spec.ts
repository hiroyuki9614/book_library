import { expect, test, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

type FixtureUser = { id: number; email: string };
type Wave1AFixture = {
	suffix: string;
	password: string;
	active: FixtureUser;
	passwordChange: FixtureUser;
	passwordLogin: FixtureUser;
	suspended: FixtureUser;
};

const frontendDirectory = path.dirname(fileURLToPath(import.meta.url));
const backendDirectory = path.resolve(frontendDirectory, '../../backend');
const apiBaseUrl = process.env.E2E_API_BASE_URL ?? 'http://localhost:3000';
const metadataPath = process.env.E2E_MVP_METADATA_PATH;
if (!metadataPath) {
	throw new Error('E2E_MVP_METADATA_PATH is required');
}

const metadata = JSON.parse(readFileSync(path.resolve(metadataPath), 'utf8')) as { bookId: number };
let fixture: Wave1AFixture;

function runFixtureCommand(args: string[]) {
	const npmCommand = process.platform === 'win32' ? 'npm.cmd' : 'npm';
	return execFileSync(npmCommand, ['run', '--silent', 'prepare:wave1a-auth-fixture', '--', ...args], {
		cwd: backendDirectory,
		env: process.env,
		encoding: 'utf8',
	});
}

test.beforeAll(() => {
	fixture = JSON.parse(runFixtureCommand([])) as Wave1AFixture;
});

test.afterAll(() => {
	if (fixture?.suffix) {
		runFixtureCommand(['--cleanup']);
	}
});

async function login(page: Page, email: string, password: string) {
	await page.goto('/login');
	await page.locator('input[name="userInput"]').fill(email);
	await page.locator('input[name="passwordInput"]').fill(password);
	await page.getByRole('button', { name: 'Submit' }).click();
	await expect(page).toHaveURL('/');
}

async function fetchJson(page: Page, url: string, init?: RequestInit) {
	return page.evaluate(
		async ({ url, init }) => {
			const response = await fetch(url, { ...init, credentials: 'include' });
			return { status: response.status, body: await response.json().catch(() => null) };
		},
		{ url, init },
	);
}

test('AUTH-01 管理者がemail/passwordでloginできる', async ({ page }) => {
	await login(page, process.env.E2E_ADMIN_EMAIL ?? '', process.env.E2E_ADMIN_PASSWORD ?? '');
	await expect(page.getByPlaceholder('Search books or authors...')).toBeVisible();
});

test('AUTH-02 一般ユーザーがemail/passwordでloginできる', async ({ page }) => {
	await login(page, fixture.active.email, fixture.password);
	await expect(page.getByPlaceholder('Search books or authors...')).toBeVisible();
	await expect(page.getByText('ログイン状態： user')).toBeVisible();
});

test('AUTH-03 logout後protected routeへ戻れない', async ({ page }) => {
	await login(page, process.env.E2E_ADMIN_EMAIL ?? '', process.env.E2E_ADMIN_PASSWORD ?? '');
	await page.getByRole('button', { name: 'ログアウト' }).click();
	await page.goto('/admin');
	await expect(page).toHaveURL('/login');
});

test('AUTH-04 未認証でprotected APIを利用できない', async ({ page }) => {
	await page.goto('/login');
	const me = await fetchJson(page, `${apiBaseUrl}/api/v1/me`);
	const books = await fetchJson(page, `${apiBaseUrl}/api/v1/books`);
	await expect(me.status).toBe(401);
	await expect(books.status).toBe(401);
});

test('AUTH-05 未認証でReader直アクセス時loginへ遷移する', async ({ page }) => {
	await page.goto(`/reader/${metadata.bookId}`);
	await expect(page).toHaveURL('/login');
});

test('AUTH-06 一般ユーザーがadmin画面へアクセスできない', async ({ page }) => {
	await login(page, fixture.active.email, fixture.password);
	await page.goto('/admin');
	await expect(page).toHaveURL('/');
});

test('AUTH-07 管理者がadmin画面へアクセスできる', async ({ page }) => {
	await login(page, process.env.E2E_ADMIN_EMAIL ?? '', process.env.E2E_ADMIN_PASSWORD ?? '');
	await page.goto('/admin');
	await expect(page).toHaveURL('/admin');
	await expect(page.getByRole('heading', { name: '書籍管理' })).toBeVisible();
});

test('AUTH-08 一般ユーザー自身のpassword changeが利用可能', async ({ page }) => {
	await login(page, fixture.passwordChange.email, fixture.password);
	const result = await fetchJson(page, `${apiBaseUrl}/api/auth/change-password`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ currentPassword: fixture.password, newPassword: `${fixture.password}-changed` }),
	});
	await expect(result.status).toBe(200);
});

test('AUTH-09 password change後、新passwordでloginできる', async ({ page }) => {
	const newPassword = `${fixture.password}-changed`;
	await login(page, fixture.passwordLogin.email, fixture.password);
	const changeResult = await fetchJson(page, `${apiBaseUrl}/api/auth/change-password`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ currentPassword: fixture.password, newPassword }),
	});
	await expect(changeResult.status).toBe(200);

	await page.getByRole('button', { name: 'ログアウト' }).click();
	await login(page, fixture.passwordLogin.email, newPassword);
	await expect(page.getByText('ログイン状態： user')).toBeVisible();
});

test('AUTH-10 利用停止ユーザーの新規loginを拒否する', async ({ page }) => {
	await page.goto('/login');
	const signInResponsePromise = page.waitForResponse(
		(response) => response.url().endsWith('/api/auth/sign-in/email') && response.request().method() === 'POST',
	);
	await page.locator('input[name="userInput"]').fill(fixture.suspended.email);
	await page.locator('input[name="passwordInput"]').fill(fixture.password);
	await page.getByRole('button', { name: 'Submit' }).click();
	const signInResponse = await signInResponsePromise;
	await expect(signInResponse.status()).toBe(401);
	await expect(page).toHaveURL('/login');
});
