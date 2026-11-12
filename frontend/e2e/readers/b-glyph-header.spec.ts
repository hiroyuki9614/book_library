import { expect, test, type Page } from '@playwright/test';
import { epubFixture } from './fixtures';
import { createStoredZip } from '../../scripts/aozoraAssets';

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

const SHEET_CHAPTERS = Array.from({ length: 24 }, (_, index) => `Chapter ${String(index + 1).padStart(2, '0')}`);

function sheetEpubFixture() {
	const toc = SHEET_CHAPTERS.map((title, index) => `<li><a href="body.xhtml#chapter-${index}">${title}</a></li>`).join('');
	const body = SHEET_CHAPTERS.map((title, index) => `<h2 id="chapter-${index}">${title}</h2><p>Synthetic reader sheet content.</p>`).join('');
	return createStoredZip([
		{ name: 'mimetype', data: Buffer.from('application/epub+zip') },
		{
			name: 'META-INF/container.xml',
			data: Buffer.from('<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'),
		},
		{
			name: 'OPS/book.opf',
			data: Buffer.from('<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">belib-sheet-regression</dc:identifier><dc:title>Reader sheet fixture</dc:title><dc:language>en</dc:language><meta property="dcterms:modified">2026-11-12T00:00:00Z</meta></metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="body" href="body.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="body"/></spine></package>'),
		},
		{
			name: 'OPS/nav.xhtml',
			data: Buffer.from(`<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Contents</title></head><body><nav epub:type="toc"><ol>${toc}</ol></nav></body></html>`),
		},
		{
			name: 'OPS/body.xhtml',
			data: Buffer.from(`<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Reader sheet fixture</title></head><body>${body}</body></html>`),
		},
	]);
}

async function checkOpenedSheet(page: Page, viewportWidth: number) {
	const sheet = page.locator('[data-slot="sheet-content"][data-side="right"]');
	await expect(sheet).toBeVisible();
	// Wait for the real slide-in animation to finish before measuring its position.
	await expect(sheet).toBeInViewport({ ratio: 1 });
	const bounds = (await sheet.boundingBox())!;
	const expectedWidth = viewportWidth < 640 ? 208 : 224;
	expect(bounds.width, 'rendered sheet width stays within the B design limit').toBeLessThanOrEqual(expectedWidth);
	expect(bounds.width, 'rendered sheet keeps its intended usable width').toBeGreaterThanOrEqual(expectedWidth - 1);
	expect(bounds.x).toBeGreaterThanOrEqual(0);
	expect(bounds.x + bounds.width).toBeCloseTo(viewportWidth, 1);
	expect(bounds.y).toBeGreaterThanOrEqual(0);
	expect(bounds.y + bounds.height).toBeLessThanOrEqual(page.viewportSize()!.height);
	expect(await sheet.evaluate((element) => element.scrollWidth <= element.clientWidth), 'sheet has no horizontal content overflow').toBe(true);
	const close = (await sheet.getByRole('button', { name: '閉じる', exact: true }).boundingBox())!;
	expect(close.width).toBeGreaterThanOrEqual(44);
	expect(close.height).toBeGreaterThanOrEqual(44);
	return { sheet, bounds };
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
			cloneButton?.replaceChildren(document.createTextNode(cloneButton.textContent ?? ''));
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
				const range = document.createRange();
				range.selectNodeContents(glyph ?? button);
				const style = getComputedStyle(button);
				const canvas = document.createElement('canvas');
				const context = canvas.getContext('2d')!;
				context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
				const metrics = context.measureText(button.textContent ?? '');
				return {
					name, text: button.textContent, ...rect(button), baseline: baseline[index], radius: style.borderRadius,
					translate: style.translate, transform: style.transform, marginTop: style.marginTop,
					glyph: glyph ? rect(glyph) : null,
					inkCenterY: range.getBoundingClientRect().y + metrics.fontBoundingBoxAscent + (metrics.actualBoundingBoxDescent - metrics.actualBoundingBoxAscent) / 2,
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
					expect(button.text).toBe(kind === 'epub' ? pointsRight ? '›' : '‹' : pointsRight ? '>' : '<');
					if (kind === 'epub') {
						expect(Number.parseFloat(button.radius)).toBeGreaterThanOrEqual(22);
						expect(button.glyph, 'only the glyph has an optical correction').not.toBeNull();
						expect(button.glyph!.x + button.glyph!.width / 2).toBeCloseTo(button.x + 22, 1);
						expect(Math.abs(button.glyph!.y + button.glyph!.height / 2 - (button.y + 22))).toBeLessThanOrEqual(1);
						// Glyph ink varies by roughly 0.5px across installed system fonts;
						// 1px still catches meaningful optical off-centering.
						expect(Math.abs(button.inkCenterY - (button.y + 22)), 'visible glyph ink is vertically centered within 1px').toBeLessThanOrEqual(1);
					} else expect(button.radius).toBe('6px');
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

for (const kind of ['epub', 'pdf'] as const) {
	for (const width of [1280, 1440, 1663, 393, 320]) {
		test(`${kind} ${width}: production-built sheets scroll internally and preserve reader width on close`, async ({ page, browser }) => {
			let file: Buffer;
			if (kind === 'epub') file = sheetEpubFixture();
			else {
				const source = await browser.newPage();
				try {
					await source.setContent(`<style>h1{break-before:page}</style>${SHEET_CHAPTERS.map((title) => `<h1>${title}</h1><p>Synthetic reader sheet content.</p>`).join('')}`);
					file = await source.pdf({ outline: true, tagged: true });
				} finally {
					await source.close();
				}
			}
			const api = await useProtectedBook(page, file, kind);
			await page.setViewportSize({ width, height: 851 });
			await page.goto(`/e2e/readers/harness.html?kind=${kind}&direction=ltr`);
			await ready(page, kind);
			const reader = kind === 'epub' ? page.locator('[data-reader-viewport]') : page.getByTestId('pdf-viewport');
			await expect(reader).toBeVisible();
			const readerBefore = (await reader.boundingBox())!;
			expect(readerBefore.width, 'closed sheets leave the full viewport available for reading').toBe(width);
			const mobilePdf = kind === 'pdf' && width < 768;
			const header = page.locator('header');
			const tocTrigger = header.getByRole('button', { name: mobilePdf ? '操作メニューを開く' : '目次', exact: true });
			await tocTrigger.click();
			// PDF mobile exposes both settings and an expandable TOC in its operation sheet.
			if (mobilePdf) await page.getByRole('dialog', { name: '操作メニュー', exact: true }).getByRole('button', { name: '目次', exact: true }).click();
			const toc = page.getByRole('navigation', { name: kind === 'epub' ? 'EPUB目次' : 'PDF目次', exact: true });
			await expect(toc).toBeVisible();
			await expect(toc.getByRole('button')).toHaveCount(SHEET_CHAPTERS.length);
			const contents = await checkOpenedSheet(page, width);
			const scroller = mobilePdf ? contents.sheet.locator(':scope > .overflow-y-auto') : toc;
			await expect(scroller).toBeVisible();
			const scrolling = await scroller.evaluate((element) => ({
				clientHeight: element.clientHeight,
				scrollHeight: element.scrollHeight,
				clientWidth: element.clientWidth,
				scrollWidth: element.scrollWidth,
			}));
			expect(scrolling.clientHeight).toBeGreaterThan(0);
			expect(scrolling.scrollHeight, 'real publication contents overflow inside the sheet').toBeGreaterThan(scrolling.clientHeight);
			expect(scrolling.scrollWidth).toBeLessThanOrEqual(scrolling.clientWidth);
			const lastChapter = toc.getByRole('button', { name: SHEET_CHAPTERS[SHEET_CHAPTERS.length - 1], exact: true });
			await expect(lastChapter).not.toBeInViewport();
			await scroller.hover();
			await page.mouse.wheel(0, scrolling.scrollHeight);
			await expect.poll(() => scroller.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
			await expect(lastChapter).toBeInViewport({ ratio: 1 });
			const scrollTop = await scroller.evaluate((element) => element.scrollTop);
			await expect(contents.sheet.locator('[data-slot="sheet-header"]')).toBeInViewport({ ratio: 1 });
			expect(await page.evaluate(() => ({ x: scrollX, y: scrollY })), 'scrolling stays inside the sheet').toEqual({ x: 0, y: 0 });
			expect((await reader.boundingBox())!.width).toBe(readerBefore.width);
			await page.keyboard.press('Escape');
			await expect(contents.sheet).toHaveCount(0);
			await expect(reader).toBeVisible();
			const readerAfterContents = (await reader.boundingBox())!;
			expect(readerAfterContents.width).toBe(readerBefore.width);

			const settingsTrigger = header.getByRole('button', { name: mobilePdf ? '操作メニューを開く' : '表示設定', exact: true });
			await settingsTrigger.focus();
			await settingsTrigger.press('Enter');
			const settings = await checkOpenedSheet(page, width);
			const settingControl = kind === 'epub' ? settings.sheet.getByRole('combobox') : settings.sheet.getByRole('button', { name: '拡大', exact: true });
			await expect(settingControl).toBeInViewport({ ratio: 1 });
			expect((await reader.boundingBox())!.width).toBe(readerBefore.width);
			await settings.sheet.getByRole('button', { name: '閉じる', exact: true }).click();
			await expect(settings.sheet).toHaveCount(0);
			await expect(reader).toBeVisible();
			const readerAfterSettings = (await reader.boundingBox())!;
			expect(readerAfterSettings.width).toBe(readerBefore.width);
			expect(api.attempts, 'sheet interactions must not turn or save a reading page').toEqual([]);
			console.log(JSON.stringify({ kind, width, contents: contents.bounds, settings: settings.bounds, scrolling: { ...scrolling, scrollTop }, readerBefore, readerAfterContents, readerAfterSettings }));
		});
	}
}
