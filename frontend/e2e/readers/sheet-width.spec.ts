import { expect, test, type Locator, type Page } from '@playwright/test';
import { epubFixture, outlinedPdfFixture } from './fixtures';

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

async function checkZoomControls(sheet: Locator, label: string) {
	const zoomOut = sheet.getByRole('button', { name: '縮小', exact: true });
	const zoomIn = sheet.getByRole('button', { name: '拡大', exact: true });
	const fitWidth = sheet.getByRole('button', { name: '倍率をリセット', exact: true });
	const toolbar = zoomOut.locator('..');
	const percentage = toolbar.getByText(/^\d+%$/);
	await expect(fitWidth).toHaveText('ページ幅に合わせる');
	const sheetBounds = await sheet.boundingBox();
	// The mobile section excludes the scroll container's padding; desktop uses its settings content div.
	const contentBounds = await toolbar.locator('..').boundingBox();
	expect(sheetBounds).not.toBeNull();
	expect(contentBounds).not.toBeNull();
	for (const [name, control] of [['toolbar', toolbar], ['zoom out', zoomOut], ['percentage', percentage], ['zoom in', zoomIn], ['fit width', fitWidth]] as const) {
		await expect(control).toBeVisible();
		const bounds = await control.boundingBox();
		expect(bounds, `${label}: ${name} must have measurable bounds`).not.toBeNull();
		for (const [region, container] of [['Sheet', sheetBounds!], ['active content', contentBounds!]] as const) {
			expect(bounds!.x, `${label}: ${name} left inside ${region}`).toBeGreaterThanOrEqual(container.x - 1);
			expect(bounds!.y, `${label}: ${name} top inside ${region}`).toBeGreaterThanOrEqual(container.y - 1);
			expect(bounds!.x + bounds!.width, `${label}: ${name} right inside ${region}`).toBeLessThanOrEqual(container.x + container.width + 1);
			expect(bounds!.y + bounds!.height, `${label}: ${name} bottom inside ${region}`).toBeLessThanOrEqual(container.y + container.height + 1);
		}
		if (name !== 'toolbar' && name !== 'percentage') {
			expect(bounds!.width, `${label}: ${name} hit target width`).toBeGreaterThanOrEqual(44);
			expect(bounds!.height, `${label}: ${name} hit target height`).toBeGreaterThanOrEqual(44);
		}
	}
	const readZoom = async () => Number((await percentage.innerText()).replace('%', ''));
	const initialZoom = await readZoom();
	await zoomOut.click();
	await expect.poll(readZoom).toBeLessThan(initialZoom);
	const reducedZoom = await readZoom();
	await fitWidth.click();
	await expect.poll(readZoom).toBeGreaterThan(reducedZoom);
	const fittedZoom = await percentage.innerText();
	await zoomIn.click();
	await expect.poll(readZoom).toBeGreaterThan(Number(fittedZoom.replace('%', '')));
	await fitWidth.click();
	await expect(percentage).toHaveText(fittedZoom);
}

async function checkSheet(page: Page, label: string, viewportWidth: number, hasZoomControls = false) {
	const sheet = page.locator('[data-slot="sheet-content"][data-side="right"]');
	await expect(sheet).toBeVisible();
	await expect(sheet).toBeInViewport({ ratio: 1 });
	const bounds = await sheet.boundingBox();
	expect(bounds, `${label}: reader sheet must have measurable bounds`).not.toBeNull();
	const desiredWidth = viewportWidth < 640 ? Math.min(208, viewportWidth - 16) : 224;
	expect(bounds!.width, `${label}: compact width`).toBeCloseTo(desiredWidth, 0);
	expect(bounds!.x, `${label}: panel must fit within viewport`).toBeGreaterThanOrEqual(-1);
	expect(bounds!.x + bounds!.width, `${label}: no horizontal overflow`).toBeLessThanOrEqual(viewportWidth + 1);
	if (hasZoomControls) await checkZoomControls(sheet, label);
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

for (const width of [320, 393, 1280, 1440, 1663]) {
	test(`PDF reader Sheet content and zoom controls stay contained and operable at ${width}px in actual built CSS`, async ({ page, browser }) => {
		test.setTimeout(150_000);
		const pdfSource = await browser.newPage();
		await pdfSource.setContent('<h1>Reader sheet test</h1><p>Synthetic PDF page.</p>');
		const pdf = await pdfSource.pdf();
		await pdfSource.close();
		await useProtectedBook(page, pdf, 'pdf');
		await page.setViewportSize({ width, height: 851 });
		await page.goto('/e2e/readers/harness.html?kind=pdf');
		await expect(page.getByTestId('pdf-viewport')).toBeVisible({ timeout: 45_000 });
		await expect(page.locator('[data-pdf-page="1"] img')).toBeVisible({ timeout: 45_000 });
		await expect(page.getByText('PDF本文を描画しています…')).toHaveCount(0, { timeout: 45_000 });
		if (width < 768) {
			await page.getByRole('button', { name: '操作メニューを開く' }).click();
			await checkSheet(page, `PDF mobile controls ${width}`, width, true);
			// Unlike the desktop Sheet, mobile PDF has no Radix SheetTrigger;
			// Escape must restore keyboard focus to its actual opener.
			await expect(page.getByRole('button', { name: '操作メニューを開く' })).toBeFocused();
		} else {
			await page.getByRole('button', { name: '目次', exact: true }).click();
			await checkSheet(page, `PDF TOC ${width}`, width);
			await page.getByRole('button', { name: '表示設定', exact: true }).click();
			await checkSheet(page, `PDF settings ${width}`, width, true);
			const footerReset = page.getByTestId('pdf-reader-footer').getByRole('button', { name: '倍率をリセット', exact: true });
			const footerToolbar = footerReset.locator('..');
			const centers = await footerToolbar.locator(':scope > *').evaluateAll((controls) => controls.map((control) => {
				const bounds = control.getBoundingClientRect();
				return bounds.y + bounds.height / 2;
			}));
			expect(centers).toHaveLength(4);
			for (const center of centers) expect(center, 'desktop footer zoom controls stay in one row').toBeCloseTo(centers[0], 0);
			await expect(footerReset).toHaveText('Reset');
			await footerReset.click();
			await expect(footerToolbar.getByText('100%', { exact: true })).toBeVisible();
		}
	});
}

test('PDF mobile menu releases its modal overlay and focus on transition to desktop', async ({ page }) => {
	test.setTimeout(150_000);
	await useProtectedBook(page, outlinedPdfFixture(), 'pdf');
	const positionWrites: string[] = [];
	page.on('request', (request) => {
		if (request.method() === 'PATCH' && request.url().includes('/api/v1/books/1/reading-info')) {
			positionWrites.push(request.postData() ?? '');
		}
	});
	await page.setViewportSize({ width: 393, height: 851 });
	await page.goto('/e2e/readers/harness.html?kind=pdf');
	await expect(page.locator('[data-pdf-page="1"] img')).toBeVisible({ timeout: 45_000 });

	await page.getByRole('button', { name: '操作メニューを開く' }).click();
	const menu = page.getByRole('dialog', { name: '操作メニュー' });
	const footer = page.getByTestId('pdf-reader-footer');
	await expect(menu).toBeVisible();
	await expect(footer).toHaveAttribute('aria-hidden', 'true');
	// Closing on resize must also discard the expanded mobile outline, not just the overlay.
	await menu.getByRole('button', { name: '目次', exact: true }).click();
	await expect(page.getByTestId('pdf-mobile-toc')).toBeVisible();

	// Exactly below Tailwind's md breakpoint the mobile menu must remain usable.
	await page.setViewportSize({ width: 767, height: 851 });
	await expect(menu).toBeVisible();
	await expect(footer).toHaveAttribute('inert');

	// At md=768 the opener is hidden; close the modal without leaving a stale overlay.
	await page.setViewportSize({ width: 768, height: 851 });
	await expect(menu).toBeHidden();

	// Keep the desktop reader functional at a common larger width as well.
	await page.setViewportSize({ width: 1280, height: 851 });
	await expect(menu).toBeHidden();
	await expect(footer).not.toHaveAttribute('aria-hidden', 'true');
	await expect(footer).not.toHaveAttribute('inert', '');
	await expect(page.getByRole('link', { name: '本棚に戻る' })).toBeFocused();

	// Desktop reader controls must be operable after the transition.
	await page.getByRole('button', { name: '目次', exact: true }).click();
	const desktopToc = page.getByRole('dialog', { name: 'このPDFの目次' });
	await expect(desktopToc).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(desktopToc).toBeHidden();

	// Returning to the phone layout must restore a usable opener without a stale overlay.
	await page.setViewportSize({ width: 393, height: 851 });
	const opener = page.getByRole('button', { name: '操作メニューを開く' });
	await expect(opener).toBeVisible();
	await opener.click();
	await expect(menu).toBeVisible();
	await expect(page.getByTestId('pdf-mobile-toc')).toHaveCount(0);
	await expect(menu.getByRole('button', { name: '目次', exact: true })).toHaveAttribute('aria-expanded', 'false');
	await page.keyboard.press('Escape');
	await expect(menu).toBeHidden();
	await expect(opener).toBeFocused();
	await expect(footer).not.toHaveAttribute('inert');
	// A pure layout/menu transition must never persist a new reading position.
	await page.waitForTimeout(850);
	expect(positionWrites, 'responsive menu changes must not issue position PATCH').toEqual([]);
});

for (const [controlName, dialogName] of [['目次', 'このPDFの目次'], ['表示設定', '表示設定']] as const) {
	test(`PDF desktop ${controlName} sheet releases its modal and focus on transition to mobile`, async ({ page }) => {
		test.setTimeout(150_000);
		await useProtectedBook(page, outlinedPdfFixture(), 'pdf');
		const positionWrites: string[] = [];
		page.on('request', (request) => {
			if (request.method() === 'PATCH' && request.url().includes('/api/v1/books/1/reading-info')) {
				positionWrites.push(request.postData() ?? '');
			}
		});
		await page.setViewportSize({ width: 1280, height: 851 });
		await page.goto('/e2e/readers/harness.html?kind=pdf');
		await expect(page.locator('[data-pdf-page="1"] img')).toBeVisible({ timeout: 45_000 });

		await page.getByRole('button', { name: controlName, exact: true }).click();
		const desktopSheet = page.getByRole('dialog', { name: dialogName });
		await expect(desktopSheet).toBeVisible();

		// The desktop trigger remains visible exactly at md=768.
		await page.setViewportSize({ width: 768, height: 851 });
		await expect(desktopSheet).toBeVisible();
		// At 767 the desktop-only trigger disappears; its active modal must close.
		await page.setViewportSize({ width: 767, height: 851 });
		await expect(desktopSheet).toBeHidden();
		const mobileOpener = page.getByRole('button', { name: '操作メニューを開く' });
		await expect(mobileOpener).toBeVisible();
		await expect(mobileOpener).toBeFocused();

		await mobileOpener.click();
		const mobileSheet = page.getByRole('dialog', { name: '操作メニュー' });
		await expect(mobileSheet).toBeVisible();
		await page.keyboard.press('Escape');
		await expect(mobileSheet).toBeHidden();
		await expect(mobileOpener).toBeFocused();
		await page.waitForTimeout(850);
		expect(positionWrites, 'layout-only desktop/mobile transition must not save position').toEqual([]);
	});
}

test('PDF mobile TOC jump closes the operation sheet and the next open starts collapsed', async ({ page }) => {
	test.setTimeout(150_000);
	// Real /Outlines and /Dest bookmarks, not Chromium's experimental PDF outline generator.
	await useProtectedBook(page, outlinedPdfFixture(), 'pdf');

	for (const width of [320, 393]) {
		await page.setViewportSize({ width, height: 851 });
		await page.goto('/e2e/readers/harness.html?kind=pdf');
		await expect(page.locator('[data-pdf-page="1"] img')).toBeVisible({ timeout: 45_000 });
		await page.getByRole('button', { name: '操作メニューを開く' }).click();
		const menu = page.getByRole('dialog', { name: '操作メニュー' });
		await menu.getByRole('button', { name: '目次', exact: true }).click();
		const toc = page.getByTestId('pdf-mobile-toc');
		await expect(toc).toBeVisible();
		await toc.getByRole('button', { name: 'Chapter Two', exact: true }).click();
		await expect(menu).toBeHidden();
		await expect(page.getByRole('spinbutton', { name: 'ページ番号' })).toHaveValue('2');
		await expect(page.locator('[data-pdf-page="2"] img')).toBeVisible();
		const opener = page.getByRole('button', { name: '操作メニューを開く' });
		await expect(opener).toBeFocused();
		await opener.click();
		await expect(page.getByTestId('pdf-mobile-toc')).toHaveCount(0);
		await expect(menu.getByRole('button', { name: '目次', exact: true })).toHaveAttribute('aria-expanded', 'false');
		await page.keyboard.press('Escape');
		await expect(opener).toBeFocused();
	}
});

test('PDF mobile reader chrome keeps its header, reading surface and footer within 320/393px viewports', async ({ page, browser }) => {
	test.setTimeout(150_000);
	const pdfSource = await browser.newPage();
	await pdfSource.setContent('<h1>Mobile reader chrome</h1><p>Synthetic PDF page.</p>');
	const pdf = await pdfSource.pdf();
	await pdfSource.close();
	await useProtectedBook(page, pdf, 'pdf');

	for (const [width, height] of [[320, 640], [393, 851]] as const) {
		await page.setViewportSize({ width, height });
		await page.goto('/e2e/readers/harness.html?kind=pdf');
		await expect(page.getByTestId('pdf-viewport')).toBeVisible({ timeout: 45_000 });
		await expect(page.locator('[data-pdf-page="1"] img')).toBeVisible({ timeout: 45_000 });
		await expect(page.getByText('PDF本文を描画しています…')).toHaveCount(0, { timeout: 45_000 });

		const footer = page.getByTestId('pdf-reader-footer');
		await expect(footer).toBeVisible();
		await expect(page.getByRole('button', { name: '操作メニューを開く' })).toBeVisible();
		await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
		const geometry = await footer.evaluate((footerElement) => {
			const root = footerElement.parentElement!;
			const header = root.querySelector('header');
			const main = root.querySelector('main');
			if (!header || !main) throw new Error('PDF reader chrome is missing its header or main');
			const rect = (element: Element) => {
				const box = element.getBoundingClientRect();
				return { left: box.left, right: box.right, top: box.top, bottom: box.bottom, height: box.height };
			};
			const controls = [...header.querySelectorAll<HTMLElement>('a, button')]
				.map((element) => ({ name: element.getAttribute('aria-label') || element.textContent?.trim() || element.tagName, box: rect(element) }))
				.filter(({ box }) => box.right > box.left && box.bottom > box.top);
			const collisions: string[] = [];
			for (let i = 0; i < controls.length; i++) {
				for (let j = i + 1; j < controls.length; j++) {
					const a = controls[i], b = controls[j];
					if (Math.min(a.box.right, b.box.right) - Math.max(a.box.left, b.box.left) > 1
						&& Math.min(a.box.bottom, b.box.bottom) - Math.max(a.box.top, b.box.top) > 1) {
						collisions.push(`${a.name} / ${b.name}`);
					}
				}
			}
			return {
				viewportHeight: window.innerHeight,
				documentScrollWidth: document.documentElement.scrollWidth,
				headerScrollWidth: header.scrollWidth,
				headerClientWidth: header.clientWidth,
				footerScrollWidth: footerElement.scrollWidth,
				footerClientWidth: footerElement.clientWidth,
				header: rect(header),
				main: rect(main),
				footer: rect(footerElement),
				collisions,
			};
		});
		expect(geometry.documentScrollWidth, `PDF ${width}px horizontal document overflow`).toBeLessThanOrEqual(width + 2);
		expect(geometry.headerScrollWidth, `PDF ${width}px header overflow`).toBeLessThanOrEqual(geometry.headerClientWidth + 2);
		expect(geometry.footerScrollWidth, `PDF ${width}px footer overflow`).toBeLessThanOrEqual(geometry.footerClientWidth + 2);
		expect(geometry.collisions, `PDF ${width}px header controls collide`).toEqual([]);
		expect(geometry.main.top, `PDF ${width}px main overlaps header`).toBeGreaterThanOrEqual(geometry.header.bottom - 2);
		expect(geometry.footer.top, `PDF ${width}px footer overlaps main`).toBeGreaterThanOrEqual(geometry.main.bottom - 2);
		expect(geometry.main.height, `PDF ${width}px body too small`).toBeGreaterThanOrEqual(geometry.viewportHeight * 0.6);
		expect(geometry.footer.bottom, `PDF ${width}px footer outside viewport`).toBeLessThanOrEqual(geometry.viewportHeight + 2);

		await page.getByRole('button', { name: '操作メニューを開く' }).click();
		await expect(footer).toHaveAttribute('aria-hidden', 'true');
		await expect(footer).toHaveAttribute('inert');
		await page.getByRole('button', { name: '閉じる', exact: true }).click();
		await expect(footer).not.toHaveAttribute('aria-hidden');
		await expect(footer).not.toHaveAttribute('inert');
	}
});

/** Full desktop reading-area geometry, complementing the narrow Sheet tests. */
async function checkReaderChrome(page: Page, kind: 'epub' | 'pdf', width: number) {
	const footer = page.getByTestId(kind === 'epub' ? 'epub-reader-footer' : 'pdf-reader-footer');
	await expect(footer).toBeVisible();
	await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
	const data = await footer.evaluate((element) => {
		const header = element.parentElement?.querySelector(':scope > header');
		const main = element.parentElement?.querySelector(':scope > main');
		if (!header || !main) throw new Error('B reader header/main/footer are not siblings');
		const box = (node: Element) => {
			const r = node.getBoundingClientRect();
			return { top: r.top, bottom: r.bottom, height: r.height };
		};
		return {
			header: box(header), main: box(main), footer: box(element),
			viewHeight: window.innerHeight, documentWidth: document.documentElement.scrollWidth,
			headerScroll: header.scrollWidth, headerWidth: header.clientWidth,
			footerScroll: element.scrollWidth, footerWidth: element.clientWidth,
		};
	});
	const label = `${kind} ${width}px`;
	expect(data.documentWidth, `${label} document overflow`).toBeLessThanOrEqual(width + 2);
	expect(data.headerScroll, `${label} header overflow`).toBeLessThanOrEqual(data.headerWidth + 2);
	expect(data.footerScroll, `${label} footer overflow`).toBeLessThanOrEqual(data.footerWidth + 2);
	expect(data.main.top, `${label} header/main overlap`).toBeGreaterThanOrEqual(data.header.bottom - 2);
	expect(data.footer.top, `${label} main/footer overlap`).toBeGreaterThanOrEqual(data.main.bottom - 2);
	expect(data.main.height, `${label} reading area height`).toBeGreaterThanOrEqual(data.viewHeight * 0.6);
	expect(data.footer.bottom, `${label} footer offscreen`).toBeLessThanOrEqual(data.viewHeight + 2);
}


/**
 * The B layout may optically adjust only the chevron glyph, never its round
 * hit target. Assert real Chromium geometry before/after an actual EPUB turn.
 */
async function checkEpubNavigationButtonGeometry(page: Page, width: number) {
	const footer = page.getByTestId('epub-reader-footer');
	const previous = footer.getByRole('button', { name: '前のページへ', exact: true });
	const next = footer.getByRole('button', { name: '次のページへ', exact: true });
	const viewport = page.locator('[data-reader-viewport]');
	await expect(previous).toBeVisible();
	await expect(next).toBeEnabled();
	await expect(viewport).toHaveAttribute('aria-busy', 'false', { timeout: 45_000 });
	const readCfi = () => page.evaluate(() => localStorage.getItem('reader-location:1'));
	await expect.poll(readCfi, { timeout: 45_000 }).toMatch(/^epubcfi\(/);
	const beforeCfi = await readCfi();
	const measure = () => footer.evaluate((element) => {
		return ['前のページへ', '次のページへ'].map((name) => {
			const button = [...element.querySelectorAll<HTMLButtonElement>('button')]
				.find((candidate) => candidate.getAttribute('aria-label') === name);
			if (!button) throw new Error(`Missing EPUB turn button: ${name}`);
			const bounds = button.getBoundingClientRect();
			const glyph = button.querySelector<HTMLElement>('span[aria-hidden="true"]');
			if (!glyph) throw new Error(`Missing EPUB turn glyph: ${name}`);
			const glyphBounds = glyph.getBoundingClientRect();
			return {
				name,
				x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height,
				glyphCenterOffsetY: (glyphBounds.top + glyphBounds.height / 2) - (bounds.top + bounds.height / 2),
				buttonTransform: getComputedStyle(button).transform,
			};
		});
	});
	const before = await measure();
	for (const button of before) {
		expect(button.width, `EPUB ${width}px ${button.name} button width`).toBeCloseTo(44, 0);
		expect(button.height, `EPUB ${width}px ${button.name} button height`).toBeCloseTo(44, 0);
		expect(button.buttonTransform, `EPUB ${width}px ${button.name} outer button is not translated`).toBe('none');
		expect(Math.abs(button.glyphCenterOffsetY), `EPUB ${width}px ${button.name} glyph vertically centered`).toBeLessThanOrEqual(3);
	}
	await next.click();
	await expect.poll(readCfi, { timeout: 45_000 }).not.toBe(beforeCfi);
	await expect(viewport).toHaveAttribute('aria-busy', 'false', { timeout: 45_000 });
	const afterCfi = await readCfi();
	expect(afterCfi, `EPUB ${width}px page turn must leave a changed current CFI after becoming idle`).not.toBe(beforeCfi);
	const after = await measure();
	for (let index = 0; index < before.length; index++) {
		for (const property of ['x', 'y', 'width', 'height'] as const) {
			expect(Math.abs(after[index][property] - before[index][property]), `EPUB ${width}px ${before[index].name} ${property} must not shift after page turn`).toBeLessThanOrEqual(0.75);
		}
	}
}

for (const width of [1280, 1440, 1663]) {
	test(`EPUB desktop B chrome ${width}px`, async ({ page }) => {
		test.setTimeout(90_000);
		await useProtectedBook(page, epubFixture({ vertical: true, rtl: true }), 'epub');
		await page.setViewportSize({ width, height: 851 });
		await page.goto('/e2e/readers/harness.html');
		await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy', 'false', { timeout: 45_000 });
		await checkReaderChrome(page, 'epub', width);
		await checkEpubNavigationButtonGeometry(page, width);
	});
	test(`PDF desktop B chrome ${width}px`, async ({ page, browser }) => {
		test.setTimeout(90_000);
		const pdfSource = await browser.newPage();
		await pdfSource.setContent('<h1>Desktop reader B chrome</h1><p>Synthetic PDF page.</p>');
		const pdf = await pdfSource.pdf();
		await pdfSource.close();
		await useProtectedBook(page, pdf, 'pdf');
		await page.setViewportSize({ width, height: 851 });
		await page.goto('/e2e/readers/harness.html?kind=pdf');
		await expect(page.locator('[data-pdf-page="1"] img')).toBeVisible({ timeout: 45_000 });
		await expect(page.getByText('PDF本文を描画しています…')).toHaveCount(0, { timeout: 45_000 });
		await checkReaderChrome(page, 'pdf', width);
	});
}

for (const [width, height] of [[320, 640], [393, 851]] as const) {
	test(`EPUB mobile B chrome at ${width}px keeps header, text and footer readable`, async ({ page }) => {
		test.setTimeout(90_000);
		await useProtectedBook(page, epubFixture({ vertical: true, rtl: true }), 'epub');
		await page.setViewportSize({ width, height });
		await page.goto('/e2e/readers/harness.html');
		await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy', 'false', { timeout: 45_000 });
		await checkReaderChrome(page, 'epub', width);
	});
}
