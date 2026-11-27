import { expect, test } from '@playwright/test';
import { epubFixture } from './fixtures';

const conflictMessage = '最新の保存位置を読み込みました。ページを移動すると保存できます。';

for (const kind of ['pdf', 'epub'] as const) {
	for (const width of [320, 393, 1024, 1280, 1440, 1663]) {
		test(`${kind} ${width}px keeps the conflict label clear of header controls`, async ({ page, browser }) => {
			let file = epubFixture();
			if (kind === 'pdf') {
				const source = await browser.newPage();
				try {
					await source.setContent('<div>FIRST PAGE</div>');
					file = await source.pdf();
				} finally {
					await source.close();
				}
			}

			await page.route('**/api/v1/books/1/file', (route) => route.fulfill({
				body: file,
				contentType: kind === 'epub' ? 'application/epub+zip' : 'application/pdf',
			}));
			const initialPatches: string[] = [];
			await page.route(/\/api\/v1\/books\/1\/reading-info(?:\/cas-v1)?$/, (route) => {
				if (route.request().method() === 'PATCH') {
					initialPatches.push(route.request().url());
					return route.fulfill({ status: 409, json: { message: 'Unexpected initial save' } });
				}
				return route.fulfill({
					json: {
						bookId: 1,
						currentPosition: null,
						currentPage: 1,
						readStatus: 'unread',
						progressPercentage: null,
						saveConflict: true,
					},
				});
			});

			await page.setViewportSize({ width, height: 851 });
			await page.goto(`/e2e/readers/harness.html?kind=${kind}&direction=ltr`);
			if (kind === 'pdf') {
				await expect(page.locator('[data-pdf-page="1"] img').first()).toBeVisible({ timeout: 30_000 });
			} else {
				await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy', 'false', { timeout: 30_000 });
			}
			await page.evaluate(() => document.fonts.ready);

			const header = page.locator('header').filter({ has: page.getByTitle(conflictMessage, { exact: true }) });
			const label = header.getByTitle(conflictMessage, { exact: true });
			await expect(label).toBeVisible();
			const { text, headerBounds, overlaps } = await label.evaluate((element) => {
				const headerElement = element.closest('header')!;
				const headerBounds = headerElement.getBoundingClientRect().toJSON();
				const range = document.createRange();
				range.selectNodeContents(element);
				const text = range.getBoundingClientRect().toJSON();
				const overlaps = Array.from(headerElement.querySelectorAll('a, button')).flatMap((control) => {
					const bounds = control.getBoundingClientRect();
					const overlapsText = bounds.width > 0 && bounds.height > 0
						&& bounds.left < text.right - 1 && bounds.right > text.left + 1
						&& bounds.top < text.bottom - 1 && bounds.bottom > text.top + 1;
					return overlapsText ? [control.getAttribute('aria-label') ?? control.textContent] : [];
				});
				return { text, headerBounds, overlaps };
			});

			expect(overlaps).toEqual([]);
			expect(text.left).toBeGreaterThanOrEqual(headerBounds.left - 1);
			expect(text.right).toBeLessThanOrEqual(headerBounds.right + 1);
			await expect(label).toHaveText('最新位置を読込');
			expect(initialPatches).toEqual([]);
		});
	}
}
