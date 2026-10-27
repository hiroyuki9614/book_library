import { expect, test, type Page } from '@playwright/test';
import { epubFixture } from './fixtures';

async function installFileApi(page: Page, file: Buffer, kind: 'epub' | 'pdf') {
	let saved: string | null = null;
	const writes: string[] = [];
	await page.route('**/api/v1/books/1/file', (route) => route.fulfill({
		body: file,
		contentType: kind === 'epub' ? 'application/epub+zip' : 'application/pdf',
	}));
	await page.route('**/api/v1/books/1/reading-info', async (route) => {
		if (route.request().method() === 'PATCH') {
			const body = route.request().postDataJSON();
			saved = String(body.currentPosition ?? body.currentPage);
			writes.push(saved);
		}
		await route.fulfill({ json: {
			bookId: 1,
			currentPosition: saved,
			currentPage: kind === 'pdf' ? Number(saved ?? 1) : 1,
			readStatus: 'reading',
		} });
	});
	return { saved: () => saved, writes };
}

async function currentCfi(page: Page) {
	return page.evaluate(() => localStorage.getItem('reader-location:1'));
}

async function waitForEpub(page: Page) {
	await expect(page.getByRole('combobox', { name: 'ページ表示方法' })).toBeEnabled({ timeout: 30_000 });
	await expect(page.getByText(/位置 \d+ \/ \d+/)).toBeVisible();
	await expect(page.frameLocator('iframe').locator('body')).toBeVisible();
	await expect(page.getByTestId('epub-loading')).toHaveCount(0);
}

test('real vertical RTL EPUB starts at 1, turns, saves and restores the exact non-final CFI', async ({ page }) => {
	const api = await installFileApi(page, epubFixture(), 'epub');
	await page.goto('/e2e/readers/harness.html?direction=rtl');
	await waitForEpub(page);
	await expect(page.getByText(/位置 1 \/ \d+/)).toBeVisible();

	const before = await currentCfi(page);
	await page.getByRole('button', { name: '次のページへ' }).click();
	await expect.poll(() => currentCfi(page)).not.toBe(before);
	await expect.poll(api.saved, { timeout: 3_000 }).not.toBeNull();
	const saved = api.saved();
	const progress = await page.locator('label[for="reading-progress"]').innerText();
	expect(progress).not.toContain('100%');

	await page.reload();
	await waitForEpub(page);
	await expect.poll(() => currentCfi(page)).toBe(saved);
	expect((await page.locator('label[for="reading-progress"]').innerText()).replace(/\s/g, '')).toBe(progress.replace(/\s/g, ''));

	const restored = await currentCfi(page);
	const body = page.frameLocator('iframe').locator('body');
	const bodyBounds = await body.boundingBox();
	expect(bodyBounds).not.toBeNull();
	await body.click({ position: { x: Math.min(30, bodyBounds!.width * 0.08), y: Math.min(200, bodyBounds!.height / 2) } });
	await expect.poll(() => currentCfi(page)).not.toBe(restored);
	await page.getByRole('button', { name: '前のページへ' }).click();
	await expect.poll(() => currentCfi(page)).toBe(restored);
	await expect.poll(api.saved, { timeout: 3_000 }).toBe(restored);
	await page.reload();
	await waitForEpub(page);
	const beforeToc = await currentCfi(page);

	await page.getByRole('button', { name: '目次', exact: true }).click();
	await expect(page.getByRole('button', { name: '第一章', exact: true })).toBeVisible();
	await expect(page.getByRole('button', { name: '第二章', exact: true })).toBeVisible();
	await expect(page.getByRole('button', { name: '第三章', exact: true })).toBeVisible();
	await page.getByRole('button', { name: '第二章', exact: true }).click();
	await expect(page.getByRole('button', { name: '目次移動前へ戻る' })).toBeVisible();
	await expect.poll(() => currentCfi(page), { timeout: 15_000 }).toContain('/6/4!');
	await expect(page.frameLocator('iframe').locator('body')).toContainText('第2章', { timeout: 15_000 });
	await page.getByRole('button', { name: '目次移動前へ戻る' }).click();
	await expect.poll(() => currentCfi(page)).toBe(beforeToc);

	const cfiBeforeSpread = await currentCfi(page);
	await page.getByRole('combobox', { name: 'ページ表示方法' }).click();
	await page.getByRole('option', { name: '常に見開き' }).click();
	await expect.poll(() => currentCfi(page)).not.toBeNull();
	expect(await currentCfi(page)).toContain('epubcfi(');
	expect(cfiBeforeSpread).not.toBeNull();
});

test('real PDF renders pages, follows LTR/RTL edge taps, and restores the visible page', async ({ page, browser }) => {
	const source = await browser.newPage();
	await source.setContent(`<style>@page{size:A4;margin:20mm}.page{break-after:page;font:42px serif}</style>${Array.from({ length: 4 }, (_, index) => `<div class="page">REAL PDF PAGE ${index + 1}</div>`).join('')}`);
	const pdf = await source.pdf();
	await source.close();
	const api = await installFileApi(page, pdf, 'pdf');

	await page.goto('/e2e/readers/harness.html?kind=pdf&direction=ltr');
	const input = page.getByRole('spinbutton');
	await expect(input).toHaveValue('1', { timeout: 45_000 });
	await expect(page.getByText('1 / 4 ページ 25%')).toBeVisible();
	await expect(page.getByText('PDF本文を描画しています…')).toHaveCount(0, { timeout: 45_000 });
	await page.getByRole('button', { name: '目次' }).click();
	await expect(page.getByText('このPDFには目次がありません。')).toBeVisible();
	await page.keyboard.press('Escape');

	const viewport = page.getByTestId('pdf-viewport');
	const bounds = await viewport.boundingBox();
	expect(bounds).not.toBeNull();
	await page.mouse.click(bounds!.x + bounds!.width - 5, bounds!.y + bounds!.height / 2);
	await expect(input).toHaveValue('2');
	await expect(page.getByText('2 / 4 ページ 50%')).toBeVisible();
	await expect.poll(api.saved, { timeout: 3_000 }).toBe('2');

	await page.reload();
	await expect(page.getByRole('spinbutton')).toHaveValue('2', { timeout: 45_000 });
	await expect(page.locator('[data-pdf-page="2"]')).toBeAttached();

	await page.goto('/e2e/readers/harness.html?kind=pdf&direction=rtl');
	await expect(page.getByRole('spinbutton')).toHaveValue('2', { timeout: 45_000 });
	const rtlViewport = page.getByTestId('pdf-viewport');
	const rtlBounds = await rtlViewport.boundingBox();
	expect(rtlBounds).not.toBeNull();
	await page.mouse.click(rtlBounds!.x + 5, rtlBounds!.y + rtlBounds!.height / 2);
	await expect(page.getByRole('spinbutton')).toHaveValue('3');
});
