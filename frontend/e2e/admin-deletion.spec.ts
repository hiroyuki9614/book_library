import { expect, test, type Page } from '@playwright/test';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
const metaPath = process.env.E2E_MVP_METADATA_PATH!;
const api = 'http://localhost:3000';
const title = '【管理者専用】BeLib 操作確認ガイド';
async function login(page: Page, email: string, password: string) {
 await page.goto('/login');
 await page.locator('input[name="userInput"]').fill(email);
 await page.locator('input[name="passwordInput"]').fill(password);
 await page.getByRole('button', { name: 'Submit', exact: true }).click();
 await expect(page).toHaveURL('/');
}
test('admin sample registration, deletion denies new reading, restore, and permanent deletion', async ({ page, browser }) => {
 const meta = JSON.parse(readFileSync(metaPath, 'utf8'));
 const evidence = resolve(process.cwd(), '../.tmp/admin-deletion-evidence');
 mkdirSync(evidence, { recursive: true });
 await login(page, process.env.E2E_ADMIN_EMAIL!, process.env.E2E_ADMIN_PASSWORD!);
 // Prime the real shelf and reader caches before deleting from the same SPA.
 await page.getByText(meta.pdfTitle, { exact: true }).click();
 await expect(page.getByRole('spinbutton')).toHaveValue('1', { timeout: 30_000 });
 await page.getByRole('link', { name: '書籍管理', exact: true }).click();
 // Seed reading state through the real API; reader navigation is tested separately.
 expect((await page.request.patch(api + '/api/v1/books/' + meta.pdfBookId + '/reading-info', {
  data: { currentPage: 2, readStatus: 'completed' },
 })).status()).toBe(200);
 const publicRow = page.getByRole('row').filter({ hasText: meta.pdfTitle });
 await publicRow.getByRole('button', { name: '削除', exact: true }).click();
 await expect(publicRow).toHaveCount(0);
 for (const suffix of ['', '/file', '/reading-info']) {
  const r = await page.request.get(api + '/api/v1/books/' + meta.pdfBookId + suffix);
  expect(r.status()).toBe(404);
 }
 await page.getByRole('link', { name: 'Dashboard', exact: true }).click();
 await expect(page.getByText(meta.pdfTitle, { exact: true })).toHaveCount(0);
 await page.goto('/reader/' + meta.pdfBookId);
 await expect(page.getByText('書籍が見つかりません。', { exact: true })).toBeVisible();
 await expect(page.getByRole('spinbutton')).toHaveCount(0);
 await page.getByRole('link', { name: '書籍管理', exact: true }).click();
 await page.getByRole('button', { name: '削除済み', exact: true }).click();
 const deleted = page.getByRole('row').filter({ hasText: meta.pdfTitle });
 await deleted.getByRole('button', { name: '復元', exact: true }).click();
 await page.getByRole('button', { name: '通常書籍', exact: true }).click();
 await expect(publicRow).toBeVisible();
 const restoredPosition = await page.request.get(api + '/api/v1/books/' + meta.pdfBookId + '/reading-info');
 expect(await restoredPosition.json()).toMatchObject({ currentPage: 2, readStatus: 'completed' });
 await publicRow.getByRole('button', { name: '削除', exact: true }).click();
 await page.getByRole('button', { name: '削除済み', exact: true }).click();
 page.once('dialog', (dialog) => dialog.accept());
 await deleted.getByRole('button', { name: '完全削除', exact: true }).click();
 await expect(deleted).toHaveCount(0);
 expect((await page.request.get(api + '/api/v1/books/' + meta.pdfBookId + '/file')).status()).toBe(404);
 // Create a task-owned original PDF; uploaded files remain out of Git.
 const pdfPage = await page.context().newPage();
 await pdfPage.setContent('<h1>BeLib Admin Guide</h1><p>Original sample. Administrator access only.</p><p>Delete moves a book to trash. Restore keeps reading progress. Permanent deletion removes the file and related records.</p>');
 const pdf = await pdfPage.pdf({ format: 'A4' });
 await pdfPage.close();
 const samplePath = resolve(evidence, 'belib-admin-guide.pdf');
 writeFileSync(samplePath, pdf);
 await page.getByRole('button', { name: '新しい書籍を登録', exact: true }).click();
 await page.getByLabel('タイトル *', { exact: true }).fill(title);
 await page.getByRole('combobox', { name: '公開範囲', exact: true }).click();
 await page.getByRole('option', { name: '管理者のみ', exact: true }).click();
 await page.getByLabel('書籍ファイル *', { exact: true }).setInputFiles(samplePath);
 const registered = page.waitForResponse((r) => r.url().endsWith('/api/v1/admin/book-registrations') && r.status() === 201);
 await page.getByRole('button', { name: '登録する', exact: true }).click();
 const sample = await (await registered).json();
 await expect(page.getByRole('row').filter({ hasText: title })).toBeVisible();
 await page.screenshot({ path: resolve(evidence, 'admin-only-book.png') });
 const readerContext = await browser.newContext();
 const reader = await readerContext.newPage();
 await login(reader, meta.demoEmail, process.env.E2E_MVP_PASSWORD!);
 await expect(reader.getByText(title, { exact: true })).toHaveCount(0);
 for (const suffix of ['', '/file', '/reading-info']) {
  expect((await reader.request.get(api + '/api/v1/books/' + sample.id + suffix)).status()).toBe(403);
 }
 expect((await reader.request.delete(api + '/api/v1/admin/books/' + sample.id)).status()).toBe(403);
 await readerContext.close();
 writeFileSync(resolve(evidence, 'result.json'), JSON.stringify({
  host: 'ubuntu-worker', principal: 'hiroyuki', headed: process.env.E2E_HEADED === 'true',
  browser: browser.version(), deletedBookId: meta.pdfBookId, adminSampleId: sample.id,
  deletionDenial: 404, privateBookDenial: 403, production: false,
  checks: ['real-ui-registration', 'soft-delete', 'cached-shelf-cleared', 'new-reader-denied', 'restore', 'permanent-delete', 'general-user-denied'],
 }, null, 2));
});
