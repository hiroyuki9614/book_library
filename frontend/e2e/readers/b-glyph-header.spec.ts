import { expect, test, type Page } from '@playwright/test';
import { epubFixture } from './fixtures';

type ReaderKind = 'epub' | 'pdf';
// Position baselines are measured from a direct-text button in the same
// production-built browser/viewport; hard-coded host-font coordinates are brittle.

async function useProtectedBook(page: Page, file: Buffer, kind: ReaderKind, options: { failRead?: boolean } = {}) {
	const attempts: Array<{ currentPosition?: string; currentPage?: number; readStatus: string }> = [];
	let saved = { bookId: 1, currentPosition: null as string | null, currentPage: 1, readStatus: 'unread' };
	const api = { attempts, fail: false };
	await page.route('**/api/v1/books/1/file', (route) => route.fulfill({
		body: file,
		contentType: kind === 'epub' ? 'application/epub+zip' : 'application/pdf',
	}));
	await page.route('**/api/v1/books/1/reading-info', async (route) => {
		if (route.request().method() === 'GET' && options.failRead) return route.fulfill({ status: 503, json: { message: 'Synthetic reading-position GET failure' } });
		if (route.request().method() === 'PATCH') {
			const body = route.request().postDataJSON();
			attempts.push(body);
			if (api.fail) return route.fulfill({ status: 503, json: { message: 'Synthetic save failure' } });
			saved = { ...saved, ...body };
		}
		await route.fulfill({ json: saved });
	});
	return api;
}

async function ready(page: Page, kind: ReaderKind) {
	if (kind === 'epub') {
		await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy', 'false', { timeout: 30_000 });
	} else {
		await expect(page.locator('[data-pdf-page="1"] img').first()).toBeVisible({ timeout: 30_000 });
		await expect(page.getByText('PDF本文を描画しています…')).toHaveCount(0, { timeout: 30_000 });
	}
	await page.evaluate(() => document.fonts.ready);
}

async function geometry(page: Page, kind: ReaderKind) {
	return page.evaluate((readerKind) => {
		const rect = (element: Element | Range) => {
			const { x, y, width, height } = element.getBoundingClientRect();
			return { x, y, width, height };
		};
		const header = document.querySelector('header')!;
		const footer = document.querySelector(`[data-testid="${readerKind}-reader-footer"]`)!;
		// Clone the exact footer at the same width and with inherited production CSS.
		// Render the pre-span direct-text glyph in the cloned buttons, so the outer
		// X/Y/width/height can be compared without KAGOYA-specific font metrics.
		const reference = footer.cloneNode(true) as HTMLElement;
		Object.assign(reference.style, {
			position: 'fixed', left: '0px', top: '0px',
			width: `${footer.getBoundingClientRect().width}px`,
			visibility: 'hidden', pointerEvents: 'none',
		});
		const names = ['前のページへ', '次のページへ'];
		for (const name of names) {
			const cloneButton = reference.querySelector(`[aria-label="${name}"]`);
			const path = cloneButton?.querySelector('svg path');
			let text = cloneButton?.textContent ?? '';
			if (path) {
				const pointsRight = path.getAttribute('d') === 'M9 18 15 12 9 6';
				text = readerKind === 'pdf' ? pointsRight ? '>' : '<' : pointsRight ? '›' : '‹';
			}
			cloneButton?.replaceChildren(document.createTextNode(text));
		}
		document.body.appendChild(reference);
		const baseline = names.map((name) => rect(reference.querySelector(`[aria-label="${name}"]`)!));
		reference.remove();
		const items = [...header.querySelectorAll('a, button, span.truncate, [role="status"], [role="alert"]')]
			.filter((element) => element.getBoundingClientRect().width > 0);
		const overlaps = items.flatMap((item, index) => items.slice(index + 1).filter((other) => {
			if (item.contains(other) || other.contains(item)) return false;
			const a = item.getBoundingClientRect();
			const b = other.getBoundingClientRect();
			return Math.min(a.right, b.right) > Math.max(a.left, b.left) && Math.min(a.bottom, b.bottom) > Math.max(a.top, b.top);
		}).map((other) => `${item.textContent} / ${other.textContent}`));
		return {
			header: rect(header),
			overlaps,
			overflow: document.documentElement.scrollWidth > innerWidth,
			saveStatus: rect(header.querySelector('[role="status"], [role="alert"]')!),
			controls: [...header.querySelectorAll('a, button')].filter((element) => element.getBoundingClientRect().width > 0).map((element) => ({
				name: element.getAttribute('aria-label') ?? element.textContent,
				...rect(element),
			})),
			footer: rect(footer),
			buttons: names.map((name, index) => {
				const button = footer.querySelector(`[aria-label="${name}"]`)!;
				const glyph = button.querySelector('span');
				const svg = button.querySelector('svg');
				const path = svg?.querySelector('path');
				const style = getComputedStyle(button);
				return {
					name, text: button.textContent, ...rect(button), baseline: baseline[index], radius: style.borderRadius,
					translate: style.translate, transform: style.transform, marginTop: style.marginTop,
					glyph: glyph ? rect(glyph) : null,
					viewBox: svg?.getAttribute('viewBox') ?? null,
					svg: svg ? rect(svg) : null,
					path: path?.getAttribute('d') ?? null,
					pathBounds: path ? rect(path) : null,
				};
			}),
		};
	}, kind);
}

function checkHeader(observed: Awaited<ReturnType<typeof geometry>>, width: number) {
	// Assert presence before geometry loops: an empty controls array must not pass vacuously.
	expect(observed.controls.some((control) => control.name === '本棚に戻る'), 'reader back link must exist').toBe(true);
	expect(observed.controls.some((control) => control.name === '表示設定' || control.name === '操作メニューを開く'), 'reader settings/menu must exist').toBe(true);
	// Saving state must remain visible and inside the header even on narrow screens.
	expect(observed.saveStatus.width, 'save status must be rendered').toBeGreaterThan(0);
	expect(observed.saveStatus.height, 'save status must be visible').toBeGreaterThan(0);
	expect(observed.saveStatus.x).toBeGreaterThanOrEqual(0);
	expect(observed.saveStatus.x + observed.saveStatus.width).toBeLessThanOrEqual(width);
	expect(observed.saveStatus.y).toBeGreaterThanOrEqual(observed.header.y);
	expect(observed.saveStatus.y + observed.saveStatus.height).toBeLessThanOrEqual(observed.header.y + observed.header.height);
	expect(observed.overlaps, 'header title, save status and controls must not overlap').toEqual([]);
	expect(observed.overflow, 'reader must fit the viewport').toBe(false);
	for (const control of observed.controls) {
		expect(control.width, `${control.name}: touch width`).toBeGreaterThanOrEqual(44);
		expect(control.height, `${control.name}: touch height`).toBeGreaterThanOrEqual(44);
		expect(control.x).toBeGreaterThanOrEqual(0);
		expect(control.x + control.width).toBeLessThanOrEqual(width);
		expect(control.y).toBeGreaterThanOrEqual(observed.header.y);
		expect(control.y + control.height).toBeLessThanOrEqual(observed.header.y + observed.header.height);
		if (width >= 1280) expect(control.y + control.height / 2).toBe(25.5);
	}
	if (width >= 1280) {
		expect(observed.header.height, 'one desktop row including its border').toBe(52);
		expect(observed.saveStatus.y + observed.saveStatus.height / 2).toBe(25.5);
	}
}

for (const kind of ['epub', 'pdf'] as const) {
	for (const direction of ['ltr', 'rtl'] as const) {
		test(`${kind} ${direction}: production-built header and footer geometry`, async ({ page, browser }, testInfo) => {
			test.setTimeout(90_000);
			let file: Buffer;
			if (kind === 'epub') file = epubFixture({ rtl: direction === 'rtl', vertical: direction === 'rtl' });
			else {
				const source = await browser.newPage();
				await source.setContent('<h1>Reader geometry fixture</h1><p>Synthetic PDF content.</p>');
				file = await source.pdf();
				await source.close();
			}
			const api = await useProtectedBook(page, file, kind);
			for (const width of [1280, 1440, 1663, 1024, 393, 320]) {
				await page.setViewportSize({ width, height: 851 });
				await page.goto(`/e2e/readers/harness.html?kind=${kind}&direction=${direction}`);
				await ready(page, kind);
				const observed = await geometry(page, kind);
				// Both navigation buttons must be present and remain inside the footer.
				expect(observed.buttons.map((button) => button.name)).toEqual(['前のページへ', '次のページへ']);
				for (const button of observed.buttons) {
					expect(button.x).toBeGreaterThanOrEqual(0);
					expect(button.x + button.width).toBeLessThanOrEqual(width);
					expect(button.y).toBeGreaterThanOrEqual(observed.footer.y);
					expect(button.y + button.height).toBeLessThanOrEqual(observed.footer.y + observed.footer.height);
				}
				// The 320px PDF footer intentionally wraps navigation across two
				// rows; keep that measured 52px separation instead of assuming
				// all reader controls always share one row.
				if (kind === 'pdf' && width === 320) {
					expect(observed.buttons[1].y - observed.buttons[0].y,
						'PDF 320px navigation must preserve its two-row layout').toBeCloseTo(52, 1);
				} else {
					expect(observed.buttons[0].y).toBeCloseTo(observed.buttons[1].y, 1);
				}
				console.log(JSON.stringify({ kind, direction, width, ...observed }));
				await testInfo.attach(`${kind}-${direction}-${width}-geometry`, { body: JSON.stringify(observed, null, 2), contentType: 'application/json' });
				if (width === 1280) await page.screenshot({ path: testInfo.outputPath(`${kind}-${direction}-layout.png`) });
				checkHeader(observed, width);
				const expectedHeader = width >= 1280 ? 52 : kind === 'epub' ? width < 640 ? 97 : 105 : width < 640 ? 53 : 61;
				expect(observed.header.height).toBe(expectedHeader);
				const footerHeight = kind === 'epub' ? width < 640 ? 145 : 57 : width === 320 ? 105 : 53;
				expect(observed.footer).toEqual({ x: 0, y: 851 - footerHeight, width, height: footerHeight });
				for (const [index, button] of observed.buttons.entries()) {
					const { x, y, width: baseWidth, height: baseHeight } = button.baseline;
					// The reference footer is anchored at y=0; the live footer remains fixed
					// at the viewport bottom. Both are measured in the SAME font environment.
					expect({ x: button.x, y: button.y - observed.footer.y, width: button.width, height: button.height },
						`${button.name}: unchanged outer rectangle vs direct-text baseline`).toEqual({ x, y, width: baseWidth, height: baseHeight });
					expect({ width: button.width, height: button.height }).toEqual({ width: 44, height: 44 });
					expect(button.translate).toBe('none');
					expect(button.transform).toBe('none');
					expect(button.marginTop).toBe('0px');
					const previous = index === 0;
					const pointsRight = previous === (direction === 'rtl');
					expect(Number.parseFloat(button.radius), 'EPUB/PDF page-turn targets must be circular').toBeGreaterThanOrEqual(22);
					expect(button.glyph, 'the SVG wrapper is present').not.toBeNull();
					expect(button.glyph!.x + button.glyph!.width / 2).toBeCloseTo(button.x + 22, 1);
					expect(Math.abs(button.glyph!.y + button.glyph!.height / 2 - (button.y + 22))).toBeLessThanOrEqual(1);
					expect(button.text).toBe('');
					expect(button.viewBox).toBe('0 0 24 24');
					expect(button.svg).not.toBeNull();
					expect({ width: button.svg!.width, height: button.svg!.height }).toEqual({ width: 24, height: 24 });
					expect(button.path).toBe(pointsRight ? 'M9 18 15 12 9 6' : 'M15 18 9 12 15 6');
					expect(button.pathBounds, 'the SVG path must exist').not.toBeNull();
					await expect(page.getByRole('button', { name: button.name }).locator('svg path')).toBeVisible();
					expect(button.pathBounds!.width).toBeGreaterThan(0);
					expect(button.pathBounds!.height).toBeGreaterThan(0);
					expect(button.pathBounds!.x + button.pathBounds!.width / 2).toBeCloseTo(button.x + 22, 1);
					expect(button.pathBounds!.y + button.pathBounds!.height / 2).toBeCloseTo(button.y + 22, 1);
				}
				if (width >= 640) {
					expect(observed.controls.find((control) => control.name === '本棚に戻る')!.x).toBe(12);
					expect(observed.controls.find((control) => control.name === '目次')!.x).toBe(width - 156);
					expect(observed.controls.find((control) => control.name === '表示設定')!.x).toBe(width - 94);
				}
				await page.keyboard.press('Tab');
				const back = page.getByRole('link', { name: '本棚に戻る' });
				await expect(back).toBeFocused();
				expect(await back.evaluate((element) => getComputedStyle(element).boxShadow)).not.toBe('none');
				expect(api.attempts, 'opening/reflow must not save a position').toEqual([]);
			}
		});
	}
}

for (const kind of ['epub', 'pdf'] as const) {
	for (const width of [1280, 320]) {
		test(`${kind} ${width}: save errors and keyboard retry remain visible in built CSS`, async ({ page, browser }) => {
			test.setTimeout(90_000);
			let file: Buffer;
			if (kind === 'epub') file = epubFixture();
			else {
				const source = await browser.newPage();
				await source.setContent('<style>.page{break-after:page}</style><div class="page">FIRST PAGE</div><div>SECOND PAGE</div>');
				file = await source.pdf();
				await source.close();
			}
			const api = await useProtectedBook(page, file, kind);
			api.fail = true;
			await page.setViewportSize({ width, height: 851 });
			await page.goto(`/e2e/readers/harness.html?kind=${kind}&direction=ltr`);
			await ready(page, kind);
			await page.waitForTimeout(850);
			expect(api.attempts).toEqual([]);
			const beforeCfi = await page.evaluate(() => localStorage.getItem('reader-location:1'));
			await page.getByTestId(`${kind}-reader-footer`).getByRole('button', { name: '次のページへ', exact: true }).click();
			const header = page.locator('header');
			await expect(header.getByRole('alert')).toContainText('保存失敗');
			await expect(header.getByRole('alert')).toBeVisible();
			await expect(header.getByText('✓ 保存済み', { exact: true })).toHaveCount(0);
			const retry = header.getByRole('button', { name: '再試行', exact: true });
			await expect(retry).toBeVisible();
			checkHeader(await geometry(page, kind), width);
			if (kind === 'epub') {
				const current = await page.evaluate(() => localStorage.getItem('reader-location:1'));
				expect(current).not.toBe(beforeCfi);
				expect(api.attempts[0].currentPosition).toBe(current);
			} else {
				await expect(page.getByRole('spinbutton')).toHaveValue('2');
				await expect(page.locator('[data-pdf-page="2"] img').first()).toBeVisible();
				expect(api.attempts[0].currentPage).toBe(2);
			}
			api.fail = false;
			for (let index = 0; index < 20; index++) {
				await page.keyboard.press('Tab');
				if (await retry.evaluate((element) => element === document.activeElement)) break;
			}
			await expect(retry).toBeFocused();
			await page.keyboard.press('Enter');
			await expect(header.getByRole('status')).toContainText('保存済み');
			expect(api.attempts).toHaveLength(2);
			expect(api.attempts[1]).toEqual(api.attempts[0]);
			checkHeader(await geometry(page, kind), width);
		});
	}
}

test('epub desktop: failed server restore keeps the 52px header and visible warning', async ({ page }) => {
	test.setTimeout(90_000);
	const api = await useProtectedBook(page, epubFixture(), 'epub', { failRead: true });
	await page.setViewportSize({ width: 1280, height: 851 });
	await page.goto('/e2e/readers/harness.html?kind=epub&direction=ltr');
	await ready(page, 'epub');
	const warning = page.locator('[data-reader-alerts]').getByRole('alert');
	await expect(warning).toContainText('保存位置を確認・復元できませんでした');
	await expect(warning).toBeVisible();
	expect(await warning.evaluate((node) => node.closest('header'))).toBeNull();
	const observed = await geometry(page, 'epub');
	checkHeader(observed, 1280);
	expect(observed.header.height).toBe(52);
	expect(observed.footer).toEqual({ x: 0, y: 794, width: 1280, height: 57 });
	expect(api.attempts, 'failed server GET must not trigger an initial save').toEqual([]);
});

// The sheet's width is an actual viewport measurement, not an assumption based
// on Tailwind class names: EPUB/PDF must share a narrow reading-first sidebar.
for (const kind of ['epub', 'pdf'] as const) {
	test(`${kind}: TOC/settings side sheets stay narrow at desktop and phone widths`, async ({ page, browser }) => {
		test.setTimeout(120_000);
		let file: Buffer;
		if (kind === 'epub') file = epubFixture();
		else {
			const source = await browser.newPage();
			await source.setContent('<h1>PDF sheet-width fixture</h1><p>Synthetic PDF content.</p>');
			file = await source.pdf();
			await source.close();
		}
		await useProtectedBook(page, file, kind);
		for (const width of [1280, 393, 320]) {
			await page.setViewportSize({ width, height: 851 });
			await page.goto(`/e2e/readers/harness.html?kind=${kind}&direction=ltr`);
			await ready(page, kind);
			const expectedSheetWidth = width >= 640 ? 224 : 208;
			const verifySheet = async (dialog: ReturnType<typeof page.getByRole>) => {
				await expect(dialog).toBeVisible();
				const box = await dialog.boundingBox();
				expect(box, `${kind} ${width}px: sheet must have a visible rectangle`).not.toBeNull();
				expect(box!.width, `${kind} ${width}px: reading-side sheet width`).toBe(expectedSheetWidth);
				await expect.poll(async () => {
					const settled = await dialog.boundingBox();
					return settled ? Math.round(settled.x + settled.width) : 0;
				}, `${kind} ${width}px: sheet must settle against right edge`).toBe(width);
			};
			if (kind === 'pdf' && width < 768) {
				await page.getByRole('button', { name: '操作メニューを開く' }).click();
				const menu = page.getByRole('dialog', { name: '操作メニュー' });
				await verifySheet(menu);
				await expect(menu.getByRole('region', { name: '表示設定' })).toBeVisible();
				await expect(menu.getByRole('button', { name: '目次', exact: true })).toBeVisible();
				await menu.getByRole('button', { name: '閉じる' }).click();
				await expect(menu).toBeHidden();
			} else {
				await page.getByRole('button', { name: '目次', exact: true }).click();
				const toc = page.getByRole('dialog', { name: kind === 'epub' ? 'この書籍の目次' : 'このPDFの目次' });
				await verifySheet(toc);
				await toc.getByRole('button', { name: '閉じる' }).click();
				await expect(toc).toBeHidden();
				await page.getByRole('button', { name: '表示設定', exact: true }).click();
				const settings = page.getByRole('dialog', { name: '表示設定' });
				await verifySheet(settings);
				await settings.getByRole('button', { name: '閉じる' }).click();
				await expect(settings).toBeHidden();
			}
			expect(await page.evaluate(() => document.documentElement.scrollWidth),
				`${kind} ${width}px: closed sheet must not leave horizontal overflow`).toBeLessThanOrEqual(width);
		}
	});
}

test('pdf page turns keep circular chevron buttons fixed at desktop and mobile widths', async ({ page, browser }) => {
	test.setTimeout(150_000);
	const source = await browser.newPage();
	await source.setContent('<style>.page{break-after:page}</style><div class="page">FIRST PAGE</div><div>SECOND PAGE</div>');
	const file = await source.pdf();
	await source.close();
	const api = await useProtectedBook(page, file, 'pdf');

	const buttonRects = async () => (await geometry(page, 'pdf')).buttons.map(({ name, x, y, width, height, radius }) => ({
		name, x, y, width, height, radius,
	}));
	for (const width of [1280, 393, 320]) {
		await page.setViewportSize({ width, height: 851 });
		await page.goto('/e2e/readers/harness.html?kind=pdf&direction=ltr');
		await ready(page, 'pdf');
		// The mocked reading-info survives the preceding viewport. Normalize the
		// starting page after PDF.js reports its real document page count: a
		// previous viewport may have persisted page 2 before its return save.
		const pageNumber = page.getByRole('spinbutton', { name: 'ページ番号' });
		await expect.poll(async () => Number(await pageNumber.getAttribute('max')),
			'PDF fixture must expose at least two real pages').toBeGreaterThanOrEqual(2);
		if (await pageNumber.inputValue() !== '1') {
			await pageNumber.fill('1');
			await pageNumber.press('Enter');
			await expect(pageNumber).toHaveValue('1');
		}
		await expect(page.getByTestId('pdf-reader-footer').getByRole('button', { name: '次のページへ', exact: true })).toBeEnabled();
		const firstPage = await buttonRects();
		for (const button of firstPage) {
			expect({ width: button.width, height: button.height }).toEqual({ width: 44, height: 44 });
			expect(Number.parseFloat(button.radius), 'PDF navigation must remain circular').toBeGreaterThanOrEqual(22);
		}

		const footer = page.getByTestId('pdf-reader-footer');
		const attemptsBeforeTurn = api.attempts.length;
		await footer.getByRole('button', { name: '次のページへ', exact: true }).click();
		await expect(page.getByRole('spinbutton', { name: 'ページ番号' })).toHaveValue('2');
		await expect(page.locator('[data-pdf-page="2"] img').first()).toBeVisible();
		expect(await buttonRects(), 'PDF page 2 must not move the navigation targets').toEqual(firstPage);

		// Persisted page 2 must restore without shifting the circular controls.
		// Wait for the real mocked PATCH before a reload; a local-only page update
		// would otherwise produce a false positive for restoration.
		await expect.poll(() => api.attempts.slice(attemptsBeforeTurn).some((attempt) => attempt.currentPage === 2),
			'PDF turn must persist page 2 before reload').toBe(true);
		const patchesBeforeReload = api.attempts.length;
		await page.reload();
		await expect(page.getByRole('spinbutton', { name: 'ページ番号' })).toHaveValue('2', { timeout: 30_000 });
		await expect(page.locator('[data-pdf-page="2"] img').first()).toBeVisible({ timeout: 30_000 });
		await page.evaluate(() => document.fonts.ready);
		await page.waitForTimeout(850);
		expect(api.attempts, 'restoring PDF page 2 must not issue an unsolicited PATCH').toHaveLength(patchesBeforeReload);
		expect(await buttonRects(), 'restored PDF page 2 must preserve the navigation targets').toEqual(firstPage);

		await footer.getByRole('button', { name: '前のページへ', exact: true }).click();
		await expect(page.getByRole('spinbutton', { name: 'ページ番号' })).toHaveValue('1');
		await expect(page.locator('[data-pdf-page="1"] img').first()).toBeVisible();
		expect(await buttonRects(), 'restoring PDF page 1 must not move the navigation targets').toEqual(firstPage);
	}
});
