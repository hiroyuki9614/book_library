import { expect, test, type Page } from '@playwright/test';

const MEASURED_BOOK = 'Measured shelf book';
const UNMEASURED_BOOK = 'Unmeasured shelf book';
const SHELF_URL = '/e2e/readers/harness.html?kind=shelf';

async function openShelf(page: Page, width: number, height = 844) {
	await page.setViewportSize({ width, height });
	await page.goto(SHELF_URL);
}

async function expectNoInertRowActions(page: Page) {
	const table = page.locator('table');
	await expect(table.locator('thead button, tbody tr[role="link"] button')).toHaveCount(0);
	await expect(table.locator('thead [aria-label], tbody tr[role="link"] [aria-label*="メニュー"], tbody tr[role="link"] [aria-label*="お気に入り"]')).toHaveCount(0);
	const headerText = await table.locator('thead').innerText();
	expect(headerText).not.toMatch(/[⋯…★☆]/);
	for (const row of await page.locator('tbody tr[role="link"]').all()) {
		expect(await row.innerText()).not.toMatch(/[⋯…★☆]/);
	}
}

test('uses generated Tailwind mobile book-card layout with accessible progress', async ({ page }) => {
	await openShelf(page, 390);
	const measuredRow = page.getByRole('link', { name: `${MEASURED_BOOK}を読む`, exact: true });
	const unknownRow = page.getByRole('link', { name: `${UNMEASURED_BOOK}を読む`, exact: true });
	const measuredCell = measuredRow.locator('td').last();
	const unknownCell = unknownRow.locator('td').last();
	const progressBar = measuredRow.getByRole('progressbar', { name: `${MEASURED_BOOK} の読書進捗` });

	await expect(measuredRow).toHaveAttribute('role', 'link');
	await expect(measuredRow).toHaveAttribute('aria-label', `${MEASURED_BOOK}を読む`);
	await expect(measuredRow).toHaveAttribute('tabindex', '0');
	await expect(measuredRow).toHaveCSS('display', 'grid');
	await expect(measuredRow.locator('td')).toHaveCount(6);
	await expect(measuredCell).toHaveCSS('display', 'block');
	await expect(measuredCell).toHaveCSS('grid-column', 'span 2 / span 2');
	await expect(unknownRow).toHaveAttribute('role', 'link');
	await expect(unknownRow).toHaveAttribute('aria-label', `${UNMEASURED_BOOK}を読む`);
	await expect(unknownRow.locator('td')).toHaveCount(6);
	await expect(unknownCell).toHaveCSS('display', 'block');
	await expect(unknownCell).toHaveCSS('grid-column', 'span 2 / span 2');

	await expect(page.getByText('34%', { exact: true })).toBeVisible();
	await expect(page.getByText('未計測', { exact: true })).toBeVisible();
	await expect(progressBar).toBeVisible();
	await expect(progressBar).toHaveAttribute('aria-valuenow', '34');
	await expect(progressBar).toHaveAttribute('aria-label', `${MEASURED_BOOK} の読書進捗`);
	await expect(unknownRow.getByRole('progressbar')).toHaveCount(0);
	await expectNoInertRowActions(page);
});

test('uses generated Tailwind table layout with six aligned desktop columns', async ({ page }) => {
	await openShelf(page, 1024, 768);
	const table = page.locator('table');
	const measuredRow = page.getByRole('link', { name: `${MEASURED_BOOK}を読む`, exact: true });
	const unknownRow = page.getByRole('link', { name: `${UNMEASURED_BOOK}を読む`, exact: true });
	const measuredProgress = measuredRow.getByRole('progressbar', { name: `${MEASURED_BOOK} の読書進捗` });
	const measuredContent = measuredProgress.evaluate((element) => getComputedStyle(element.parentElement!).marginLeft);
	const unknownContent = unknownRow.locator('td:last-child > span');

	await expect(table.locator('thead th')).toHaveCount(6);
	await expect(measuredRow).toHaveCSS('display', 'table-row');
	await expect(measuredRow.locator('td')).toHaveCount(6);
	await expect(unknownRow).toHaveCSS('display', 'table-row');
	await expect(unknownRow.locator('td')).toHaveCount(6);
	for (const cell of await measuredRow.locator('td').all()) {
		await expect(cell).toHaveCSS('display', 'table-cell');
	}
	for (const cell of await unknownRow.locator('td').all()) {
		await expect(cell).toHaveCSS('display', 'table-cell');
	}
	expect(await measuredContent).toBe('0px');
	await expect(unknownContent).toHaveCSS('margin-left', '0px');

	const alignment = await page.evaluate(() => {
		const headerCells = Array.from(document.querySelectorAll('thead th'));
		const firstDataRow = document.querySelector('tbody tr[role="link"]');
		if (!firstDataRow) return null;
		const dataCells = Array.from(firstDataRow.querySelectorAll('td'));
		if (headerCells.length !== 6 || dataCells.length !== 6) return null;
		return headerCells.map((header, index) => {
			const headerRect = header.getBoundingClientRect();
			const dataRect = dataCells[index].getBoundingClientRect();
			return Math.max(Math.abs(headerRect.left - dataRect.left), Math.abs(headerRect.right - dataRect.right));
		});
	});
	if (!alignment) throw new Error('Expected six desktop header and data cells');
	expect(alignment).toHaveLength(6);
	for (const edgeDifference of alignment) expect(edgeDifference).toBeLessThanOrEqual(1);
	await expectNoInertRowActions(page);
});

test('routes shelf row click and keyboard activation to a synthetic destination', async ({ page }) => {
	await openShelf(page, 390);
	await page.getByRole('link', { name: `${MEASURED_BOOK}を読む`, exact: true }).click();
	await expect(page).toHaveURL(/\/reader\/1$/);
	await expect(page.getByTestId('shelf-navigation-target')).toHaveAttribute('data-book-id', '1');

	await openShelf(page, 390);
	const unknownRow = page.getByRole('link', { name: `${UNMEASURED_BOOK}を読む`, exact: true });
	await unknownRow.focus();
	await page.keyboard.press('Space');
	await expect(page).toHaveURL(/\/reader\/9$/);
	await expect(page.getByTestId('shelf-navigation-target')).toHaveAttribute('data-book-id', '9');
});
