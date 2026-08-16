import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const adminEmail = process.env.E2E_ADMIN_EMAIL;
const adminPassword = process.env.E2E_ADMIN_PASSWORD;
const generalUserEmail = 'belib-mvp-real-e2e-unpermissioned-20260811@example.com';
const password = process.env.E2E_MVP_PASSWORD;
const metadataPath = process.env.E2E_MVP_METADATA_PATH;

if (!adminEmail || !adminPassword || !password || !metadataPath) {
	throw new Error('E2E_ADMIN_EMAIL, E2E_ADMIN_PASSWORD, E2E_MVP_PASSWORD, and E2E_MVP_METADATA_PATH are required');
}

const { bookId } = JSON.parse(readFileSync(metadataPath, 'utf8')) as { bookId: number };

type ApiResult = {
	status: number;
	body: string;
};

async function login(page: Page, email: string, userPassword: string) {
	await page.goto('/login');
	await page.locator('input[name="userInput"]').fill(email);
	await page.locator('input[name="passwordInput"]').fill(userPassword);
	await page.getByRole('button', { name: 'Submit' }).click();
	await expect(page).toHaveURL('/');
}

async function loginAdmin(page: Page) {
	await login(page, adminEmail, adminPassword);
}

async function openAdmin(page: Page) {
	await loginAdmin(page);
	await page.goto('/admin');
	await expect(page.getByRole('heading', { name: '書籍管理' })).toBeVisible();
}

async function postAdminBookFile(page: Page, fileName: string, mimeType: string, content: string): Promise<ApiResult> {
	return page.evaluate(
		async ({ bookId: id, fileName: name, mimeType: type, content: fileContent }) => {
			const formData = new FormData();
			formData.append('file', new File([fileContent], name, { type }));
			const response = await fetch(`http://localhost:3000/api/v1/admin/books/${id}/files`, {
				method: 'POST',
				body: formData,
				credentials: 'include',
			});
			return { status: response.status, body: await response.text() };
		},
		{ bookId, fileName, mimeType, content },
	);
}

test.describe('BeLib MVP Wave 1C administrator capability audit', () => {
	test('BOOK-01 管理者が書籍登録画面を開ける', async ({ page }) => {
		await openAdmin(page);
		await page.getByRole('button', { name: '新しい書籍を登録' }).click();
		await expect(page.getByRole('heading', { name: '書籍を登録' })).toBeVisible();
	});

	test('BOOK-02 PDF選択はUIで受け付けるがadmin APIへ送信されない', async ({ page }) => {
		await openAdmin(page);
		let adminRequestCount = 0;
		page.on('request', (request) => {
			if (request.url().includes('/api/v1/admin/')) adminRequestCount += 1;
		});

		await page.getByRole('button', { name: '新しい書籍を登録' }).click();
		const title = 'Wave 1C PDF UI-only audit';
		await page.getByLabel('タイトル *').fill(title);
		await page.locator('#book-file').setInputFiles({ name: 'audit.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4') });
		await page.getByRole('button', { name: '登録する' }).click();

		const row = page.getByRole('row').filter({ hasText: title });
		await expect(row).toContainText('audit.pdf');
		expect(adminRequestCount).toBe(0);
	});

	test('BOOK-03 EPUB選択はUIで受け付けるがadmin APIへ送信されない', async ({ page }) => {
		await openAdmin(page);
		let adminRequestCount = 0;
		page.on('request', (request) => {
			if (request.url().includes('/api/v1/admin/')) adminRequestCount += 1;
		});

		await page.getByRole('button', { name: '新しい書籍を登録' }).click();
		const title = 'Wave 1C EPUB UI-only audit';
		await page.getByLabel('タイトル *').fill(title);
		await page.locator('#book-file').setInputFiles({ name: 'audit.epub', mimeType: 'application/epub+zip', buffer: Buffer.from('PK\\x03\\x04') });
		await page.getByRole('button', { name: '登録する' }).click();

		const row = page.getByRole('row').filter({ hasText: title });
		await expect(row).toContainText('audit.epub');
		expect(adminRequestCount).toBe(0);
	});

	test('BOOK-05 backend-only PDF validation rejects invalid MIME and content', async ({ page }) => {
		await loginAdmin(page);
		await expect(await postAdminBookFile(page, 'audit.txt', 'text/plain', 'not a pdf')).toMatchObject({ status: 400 });
		await expect(await postAdminBookFile(page, 'audit.pdf', 'application/pdf', 'not a pdf')).toMatchObject({ status: 400 });
	});

	test('BOOK-06 backend-only duplicate hash rejection is observable', async ({ page }) => {
		await loginAdmin(page);
		const first = await postAdminBookFile(page, 'duplicate-audit.pdf', 'application/pdf', '%PDF-1.4 wave-1c-duplicate');
		const second = await postAdminBookFile(page, 'duplicate-audit-again.pdf', 'application/pdf', '%PDF-1.4 wave-1c-duplicate');

		expect(first.status).toBe(201);
		expect(second.status).toBe(500);
	});

	test('BOOK-07/BOOK-08 タイトルとカテゴリの入力はlocal stateへ反映されるだけで永続化されない', async ({ page }) => {
		await openAdmin(page);
		await page.getByRole('button', { name: '新しい書籍を登録' }).click();
		const title = 'Wave 1C metadata UI-only audit';
		await page.getByLabel('タイトル *').fill(title);
		await page.getByRole('combobox', { name: 'カテゴリ' }).click();
		await page.getByRole('option', { name: '技術書' }).click();
		await page.getByRole('button', { name: '登録する' }).click();

		const row = page.getByRole('row').filter({ hasText: title });
		await expect(row).toContainText('技術書');
		const apiList = await page.evaluate(async () => {
			const response = await fetch('http://localhost:3000/api/v1/books', { credentials: 'include' });
			return (await response.json()) as { books?: Array<{ title: string }> };
		});
		expect(apiList.books?.some((book) => book.title === title)).toBe(false);
	});

	test('BOOK-10/BOOK-11/BOOK-12 公開範囲の選択UIが存在しない', async ({ page }) => {
		await openAdmin(page);
		await page.getByRole('button', { name: '新しい書籍を登録' }).click();
		await expect(page.getByText('公開範囲')).toHaveCount(0);
		await expect(page.getByLabel('公開範囲')).toHaveCount(0);
	});

	test('BOOK-14 メタデータ編集操作が管理画面に存在しない', async ({ page }) => {
		await openAdmin(page);
		await expect(page.getByRole('button', { name: /編集|edit/i })).toHaveCount(0);
	});

	test('CATEGORY-01/CATEGORY-02/CATEGORY-03 カテゴリ管理UIが存在しない', async ({ page }) => {
		await openAdmin(page);
		await expect(page.getByText(/カテゴリ管理|カテゴリを追加|カテゴリ一覧/)).toHaveCount(0);
	});

	test('USER-01/USER-02/USER-03/USER-04 ユーザー管理UIが存在しない', async ({ page }) => {
		await openAdmin(page);
		await expect(page.getByText(/ユーザー管理|ユーザーを登録|仮パスワード/)).toHaveCount(0);
	});

	test('DELETE-01〜DELETE-06 削除・復元UIが存在しない', async ({ page }) => {
		await openAdmin(page);
		await expect(page.getByText(/削除済み|復元|完全削除/)).toHaveCount(0);
	});

	test('REPLACE-01 ファイル差し替えUIが存在しない', async ({ page }) => {
		await openAdmin(page);
		await expect(page.getByText(/差し替え|置き換え/)).toHaveCount(0);
	});

	test('ACCESS-01 一般ユーザーはadmin UI/APIを実行できない', async ({ browser }) => {
		const context = await browser.newContext();
		const page = await context.newPage();
		try {
			await login(page, generalUserEmail, password);
			await page.goto('/admin');
			await expect(page).toHaveURL('/');

			const response = await page.evaluate(async () => {
				const result = await fetch('http://localhost:3000/api/v1/admin/books', {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({}),
					credentials: 'include',
				});
				return result.status;
			});
			expect(response).toBe(403);
		} finally {
			await context.close();
		}
	});
});
