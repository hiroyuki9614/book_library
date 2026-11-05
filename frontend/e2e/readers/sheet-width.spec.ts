import { expect, test, type Page } from '@playwright/test';
import { epubFixture } from './fixtures';

// Observe production-built Tailwind CSS, not Vitest's unstyled component DOM.
async function useProtectedBook(page: Page, file: Buffer, kind: 'epub' | 'pdf') {
	await page.route('**/api/v1/books/1/file', (route) => route.fulfill({
		body: file,
		contentType: kind === 'epub' ? 'application/epub+zip' : 'application/pdf',
	}));
	await page.route('**/api/v1/books/1/reading-info', (route) => route.fulfill({
		json: { bookId: 1, currentPosition: null, currentPage: 1, readStatus: 'unread' },
	}));
}

async function checkSheet(page: Page, label: string, viewportWidth: number) {
	const sheet = page.locator('[data-slot="sheet-content"][data-side="right"]');
	await expect(sheet).toBeVisible();
	const bounds = await sheet.boundingBox();
	expect(bounds, `${label}: reader sheet must have measurable bounds`).not.toBeNull();
	const desiredWidth = viewportWidth < 640 ? Math.min(208, viewportWidth - 16) : 224;
	expect(bounds!.width, `${label}: compact width`).toBeCloseTo(desiredWidth, 0);
	expect(bounds!.x, `${label}: panel must fit within viewport`).toBeGreaterThanOrEqual(-1);
	expect(bounds!.x + bounds!.width, `${label}: no horizontal overflow`).toBeLessThanOrEqual(viewportWidth + 1);
	await page.keyboard.press('Escape');
	await expect(sheet).not.toBeVisible();
}

test('EPUB reader TOC and display settings stay compact in actual built CSS at mobile and desktop widths', async ({ page }) => {
	test.setTimeout(150_000);
	await useProtectedBook(page, epubFixture({ vertical: true, rtl: true }), 'epub');
	for (const width of [320, 393, 1280, 1440, 1663]) {
		await page.setViewportSize({ width, height: 851 });
		await page.goto('/e2e/readers/harness.html');
		await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy', 'false', { timeout: 45_000 });
		await page.getByRole('button', { name: '目次', exact: true }).click();
		await checkSheet(page, `EPUB TOC ${width}`, width);
		await page.getByRole('button', { name: '表示設定', exact: true }).click();
		await checkSheet(page, `EPUB settings ${width}`, width);
	}
});

test('PDF reader TOC, settings and mobile actions stay compact in actual built CSS', async ({ page, browser }) => {
	test.setTimeout(150_000);
	const pdfSource = await browser.newPage();
	await pdfSource.setContent('<h1>Reader sheet test</h1><p>Synthetic PDF page.</p>');
	const pdf = await pdfSource.pdf();
	await pdfSource.close();
	await useProtectedBook(page, pdf, 'pdf');
	for (const width of [320, 393, 1280, 1440, 1663]) {
		await page.setViewportSize({ width, height: 851 });
		await page.goto('/e2e/readers/harness.html?kind=pdf');
		await expect(page.getByTestId('pdf-viewport')).toBeVisible({ timeout: 45_000 });
		await expect(page.locator('[data-pdf-page="1"] img')).toBeVisible({ timeout: 45_000 });
		await expect(page.getByText('PDF本文を描画しています…')).toHaveCount(0, { timeout: 45_000 });
		if (width < 768) {
			await page.getByRole('button', { name: '操作メニューを開く' }).click();
			await checkSheet(page, `PDF mobile controls ${width}`, width);
		} else {
			await page.getByRole('button', { name: '目次', exact: true }).click();
			await checkSheet(page, `PDF TOC ${width}`, width);
			await page.getByRole('button', { name: '表示設定', exact: true }).click();
			await checkSheet(page, `PDF settings ${width}`, width);
		}
	}
});
