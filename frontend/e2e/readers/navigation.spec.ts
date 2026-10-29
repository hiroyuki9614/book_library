import { test, expect, type Page } from '@playwright/test';
import { epubFixture } from './fixtures';
import { readFileSync } from 'node:fs';

async function fileApi(page: Page, file: Buffer, kind = 'epub', initial: string | null = null) {
	let saved = initial;
	let readStatus = 'reading';
	const writes: string[] = [];
	await page.route('**/api/v1/books/1/file', (route) =>
		route.fulfill({ body: file, contentType: kind === 'epub' ? 'application/epub+zip' : 'application/pdf' }),
	);
	await page.route('**/api/v1/books/1/reading-info', async (route) => {
		if (route.request().method() === 'PATCH') {
			const body = route.request().postDataJSON();
			await new Promise((resolve) => setTimeout(resolve, writes.length === 0 ? 120 : 5));
			saved = String(body.currentPosition ?? body.currentPage);
			readStatus = body.readStatus ?? 'reading';
			writes.push(saved);
		}
		await route.fulfill({
			json: { bookId: 1, currentPosition: saved, currentPage: kind === 'pdf' ? Number(saved ?? 1) : 1, readStatus },
		});
	});
	return { writes, saved: () => saved, status: () => readStatus };
}

async function visibleText(page: Page) {
	return page.evaluate(() => {
		const viewport = document.querySelector('main')!.getBoundingClientRect();
		let text = '';
		for (const iframe of document.querySelectorAll('iframe')) {
			const doc = iframe.contentDocument;
			if (!doc?.body) continue;
			const f = iframe.getBoundingClientRect();
			if (!f.width || !f.height || getComputedStyle(iframe).visibility === 'hidden') continue;
			const pane = iframe.closest('[data-publication-pane]')?.getBoundingClientRect() ?? viewport;
			const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
			while (walker.nextNode()) {
				const node = walker.currentNode;
				if (node.parentElement?.closest('rt,rp,style,script')) continue;
				const range = doc.createRange();
				range.selectNodeContents(node);
				if (
					![...range.getClientRects()].some(
							(r) =>
								r.right + f.left > pane.left &&
								r.left + f.left < pane.right &&
								r.bottom + f.top > viewport.top &&
								r.top + f.top < viewport.bottom,
					)
				)
					continue;
				for (let i = 0; i < (node.textContent?.length ?? 0); i++) {
					range.setStart(node, i);
					range.setEnd(node, i + 1);
					const r = range.getBoundingClientRect();
					if (
						r.right + f.left >= pane.left &&
						r.left + f.left < pane.right &&
						r.bottom + f.top >= viewport.top &&
						r.top + f.top < viewport.bottom
					)
						text += node.textContent![i];
				}
			}
		}
		return text.replace(/\s/g, '');
	});
}
async function cfi(page: Page) {
	return page.evaluate(() => localStorage.getItem('reader-location:1'));
}
async function ready(page: Page) {
	await expect(page.getByRole('combobox')).toBeEnabled({ timeout: 30000 });
	await expect.poll(() => page.evaluate(() => {
		const viewer=document.querySelector<HTMLElement>('[data-reader-viewport]')!;
		const pane=viewer.querySelector<HTMLElement>('[data-publication-pane=primary]');
		const preview=viewer.querySelector<HTMLElement>('[data-publication-pane=following]');
		const stage=pane?.querySelector<HTMLElement>('.epub-container');
		// Horizontal chapters use one full-width native epub.js spread. Vertical
		// chapters use two real half-width renditions because native vertical
		// spread arithmetic advances on the wrong axis.
		const customDual=preview?.style.display==='block';
		return Math.max(Math.abs((pane?.getBoundingClientRect().width ?? 0)-viewer.clientWidth/(customDual?2:1)), Math.abs((stage?.clientHeight ?? 0)-viewer.clientHeight));
	}), {timeout:30000}).toBeLessThan(1);
	await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy', 'false', { timeout: 30000 });
	await expect.poll(() => visibleText(page)).not.toBe('');
}
async function progress(page: Page) {
	const label = await page.locator('label[for="reading-progress"]').innerText();
	const screen = label.match(/章内ページ:\s*(\d+)\s*\/\s*(\d+)（第(\d+)節）/);
	const location = label.match(/本文位置:\s*(\d+)\s*\/\s*(\d+)/);
	expect(screen, 'screen page must be readable').not.toBeNull();
	expect(location, 'character location must be readable').not.toBeNull();
	return { page: Number(screen![1]), section: Number(screen![3]), location: Number(location![1]) };
}
async function move(page: Page, name = '次のページへ', timeout = 5000) {
	const before = await cfi(page);
	const beforeProgress = await progress(page);
	const text = await visibleText(page);
	await page.getByRole('button', { name, exact: true }).click();
	await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy', 'false', {timeout});
	await expect.poll(() => cfi(page)).not.toBe(before);
	await expect.poll(() => visibleText(page)).not.toBe(text);
	const afterProgress = await progress(page);
	if (afterProgress.section === beforeProgress.section) {
		if (name === '次のページへ') expect(afterProgress.page).toBeGreaterThan(beforeProgress.page);
		else expect(afterProgress.page).toBeLessThan(beforeProgress.page);
	}
}

for (const config of [
	{ rtl: false, vertical: false },
	{ rtl: true, vertical: false },
	{ rtl: true, vertical: true },
]) {
	test(`real EPUB ${JSON.stringify(config)}: modes, 10 next/3 previous, CFI, body, hierarchical TOC and resume`, async ({
		page,
	}, testInfo) => {
		test.setTimeout(180000);
		const api = await fileApi(page, epubFixture(config));
		await page.goto('/e2e/readers/harness.html');
		await ready(page);
		for (const mode of ['常に単ページ', '常に見開き', '自動']) {
			await page.getByRole('combobox').click();
			await page.getByRole('option', { name: mode, exact: true }).click();
			await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy','false');
			if (!config.vertical && mode === '常に見開き') {
				const geometry = await page.locator('[data-publication-pane="primary"] iframe').first().evaluate((frame) => {
					const viewer = frame.closest('[data-reader-viewport]') as HTMLElement;
					const body = frame.contentDocument!.body;
					return {
						viewerWidth: viewer.clientWidth,
						paneWidth: (frame.closest('[data-publication-pane]') as HTMLElement).getBoundingClientRect().width,
						columnWidth: Number.parseFloat(frame.contentWindow!.getComputedStyle(body).columnWidth),
					};
				});
				expect(geometry.paneWidth).toBeCloseTo(geometry.viewerWidth, 0);
				expect(geometry.columnWidth, 'native horizontal spread must render two columns').toBeLessThan(geometry.viewerWidth * 0.75);
			}
			const position = await progress(page);
			for (let i = 0; i < 10; i++) await move(page);
			expect((await progress(page)).location, 'character location cannot stay fixed across ten actual page turns').toBeGreaterThan(position.location);
			for (let i = 0; i < 3; i++) await move(page, '前のページへ');
			expect(await progress(page)).not.toEqual(position);
		}
		await page.getByRole('button', { name: '目次', exact: true }).click();
		await expect(page.getByRole('button', { name: '第四章', exact: true })).toBeVisible();
		await page.getByRole('button', { name: '第四章', exact: true }).click();
		await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy','false');
		if (config.vertical) await testInfo.attach('vertical-layout', { body: await page.screenshot(), contentType: 'image/png' });
		if (config.vertical) await expect.poll(async () => (await progress(page)).section).toBe(4);
		else await expect.poll(() => visibleText(page)).toContain('第4章');
		await expect(page.getByText('読書位置を保存済み', { exact: true })).toBeVisible();
		const saved = api.saved();
		expect(saved).toBe(await cfi(page));
		await page.evaluate(() => localStorage.clear());
		await page.reload();
		await ready(page);
		expect(await cfi(page)).toBe(saved);
	});
}

test('near-start vertical RTL restores exact saved CFI and returns from hierarchical TOC', async ({ page }) => {
	test.setTimeout(120000);
	const api = await fileApi(page, epubFixture({ rtl: true, vertical: true }));
	await page.goto('/e2e/readers/harness.html?direction=rtl');
	await ready(page);
	const firstCfi = await cfi(page);
	expect((await progress(page)).location).toBe(1);
	await page.waitForTimeout(850);
	expect(api.writes).toEqual([]);

	const viewport = (await page.locator('[data-reader-viewport]').boundingBox())!;
	await page.mouse.click(viewport.x + 5, viewport.y + viewport.height / 2);
	await expect.poll(() => cfi(page)).not.toBe(firstCfi);
	await expect.poll(() => api.writes.length).toBe(1);
	const savedCfi = await cfi(page);
	const savedProgress = await progress(page);
	expect(savedProgress.location).toBeGreaterThan(1);
	expect(api.status()).toBe('reading');
	expect(api.saved()).toBe(savedCfi);

	await page.evaluate(() => localStorage.clear());
	await page.reload();
	await ready(page);
	expect(await cfi(page)).toBe(savedCfi);
	expect(await progress(page)).toEqual(savedProgress);

	await page.mouse.click(viewport.x + viewport.width - 5, viewport.y + viewport.height / 2);
	await expect.poll(() => cfi(page)).toBe(firstCfi);
	await page.mouse.click(viewport.x + 5, viewport.y + viewport.height / 2);
	await expect.poll(() => cfi(page)).toBe(savedCfi);

	await page.getByRole('button', { name: '目次', exact: true }).click();
	await page.getByRole('button', { name: '第二章', exact: true }).click();
	await expect.poll(async () => (await progress(page)).section).toBe(2);
	await page.getByRole('button', { name: '目次移動前へ戻る', exact: true }).click();
	await expect.poll(() => cfi(page)).toBe(savedCfi);
});

test('Book RTL overrides opposing LTR EPUB metadata for native spread order and edge turns', async ({ page }) => {
	await fileApi(page, epubFixture({ rtl: false, vertical: false }));
	await page.goto('/e2e/readers/harness.html?direction=rtl');
	await ready(page);
	await page.getByRole('combobox').click();
	await page.getByRole('option', { name: '常に見開き', exact: true }).click();
	await ready(page);
	const layout = await page.locator('[data-publication-pane="primary"]').evaluate((pane) => {
		const viewer = pane.closest('[data-reader-viewport]') as HTMLElement;
		const stage = pane.querySelector<HTMLElement>('.epub-container')!;
		const frame = pane.querySelector<HTMLIFrameElement>('iframe')!;
		return {
			stageDirection: getComputedStyle(stage).direction,
			rootDirection: frame.contentWindow!.getComputedStyle(frame.contentDocument!.documentElement).direction,
			columnWidth: Number.parseFloat(frame.contentWindow!.getComputedStyle(frame.contentDocument!.body).columnWidth),
			viewerWidth: viewer.clientWidth,
		};
	});
	expect(layout.stageDirection).toBe('rtl');
	expect(layout.rootDirection).toBe('ltr');
	expect(layout.columnWidth).toBeLessThan(layout.viewerWidth * 0.75);
	const first = await cfi(page);
	const firstProgress = await progress(page);
	const viewport = (await page.locator('[data-reader-viewport]').boundingBox())!;
	await page.mouse.click(viewport.x + 5, viewport.y + viewport.height / 2);
	await expect.poll(() => cfi(page)).not.toBe(first);
	const second = await cfi(page);
	const secondProgress = await progress(page);
	expect(secondProgress.page).toBeGreaterThan(firstProgress.page);
	// The reader intentionally suppresses another turn inside the 250 ms
	// de-duplication window. Exercise the opposite edge as a distinct tap.
	await page.waitForTimeout(300);
	await page.mouse.click(viewport.x + viewport.width - 5, viewport.y + viewport.height / 2);
	await expect.poll(() => cfi(page)).not.toBe(second);
	const returnedProgress = await progress(page);
	expect(returnedProgress.page).toBeLessThan(secondProgress.page);
	expect(returnedProgress.page).toBe(firstProgress.page);
});

test('title-only source has real supplemental headings; no headings invents no chapters', async ({ page }) => {
	await fileApi(page, epubFixture({ titleOnly: true }));
	await page.goto('/e2e/readers/harness.html');
	await ready(page);
	await page.getByRole('button', { name: '目次', exact: true }).click();
	await expect(page.getByText('本文見出しから作成した補助目次')).toBeVisible();
	await page.getByRole('button', { name: '第4章', exact: true }).click();
	await expect.poll(() => visibleText(page)).toContain('第4章');
	await page.getByRole('button', { name: '目次', exact: true }).click();
	await page.getByRole('button', { name: '第1章', exact: true }).click();
	await expect.poll(() => visibleText(page)).toContain('第1章');
	await page.unroute('**/api/v1/books/1/file');
	await page.route('**/api/v1/books/1/file', (r) =>
		r.fulfill({ body: epubFixture({ titleOnly: true, headings: false }), contentType: 'application/epub+zip' }),
	);
	await page.reload();
	await ready(page);
	await page.getByRole('button', { name: '目次', exact: true }).click();
	await expect(page.getByText('本文見出しから作成した補助目次')).toHaveCount(0);
});

test('real PDF moves images and page numbers, restores only once, zoom, scroll, boundaries and reload', async ({ page, browser }, testInfo) => {
	await page.setViewportSize({ width: 393, height: 850 });
	const pdfPage = await browser.newPage();
	await pdfPage.setContent(
		`<style>@page{size:A4;margin:20mm}.page{break-after:page;font:42px serif}</style>${Array.from({ length: 12 }, (_, i) => `<div class="page">REGRESSION PAGE ${i + 1}<p>${'Distinct text '.repeat(i + 1)}</p></div>`).join('')}`,
	);
	const pdf = await pdfPage.pdf();
	await pdfPage.close();
	const api = await fileApi(page, pdf, 'pdf');
	await page.goto('/e2e/readers/harness.html?kind=pdf');
	const mobileControls = (await page.evaluate(() => innerWidth)) < 768;
	let controlsOpen = false;
	const openControls = async () => {
		if (!mobileControls || controlsOpen) return;
		await page.getByRole('button', { name: '操作メニューを開く', exact: true }).click();
		await expect(page.getByRole('heading', { name: '操作メニュー', exact: true })).toBeVisible();
		controlsOpen = true;
	};
	const closeControls = async () => {
		if (!mobileControls || !controlsOpen) return;
		await page.getByRole('button', { name: '閉じる', exact: true }).click();
		controlsOpen = false;
	};
	const clickControl = async (name: string) => {
		await openControls();
		await page.getByRole('button', { name, exact: true }).click();
		await closeControls();
	};
	const goToPage = async (pageNumber: string) => {
		await openControls();
		const control = page.getByRole('spinbutton');
		await control.fill(pageNumber);
		await control.press('Enter');
		await closeControls();
	};
	const expectPage = async (pageNumber: string) => {
		await openControls();
		await expect(page.getByRole('spinbutton')).toHaveValue(pageNumber);
		await closeControls();
	};
	await openControls();
	await testInfo.attach('pdf-mobile-menu', { body: await page.screenshot(), contentType: 'image/png' });
	const input = page.getByRole('spinbutton');
	await expect(input).toHaveValue('1', { timeout: 45000 });
	const shown = () =>
		page.evaluate(() => {
			const viewport = document.querySelector('main')!.getBoundingClientRect();
			return [...document.querySelectorAll<HTMLElement>('[data-pdf-page]')]
				.map((el) => {
					const r = el.getBoundingClientRect();
					return {
						page: Number(el.dataset.pdfPage),
						area:
							Math.max(0, Math.min(r.bottom, viewport.bottom) - Math.max(r.top, viewport.top)) *
							Math.max(0, Math.min(r.right, viewport.right) - Math.max(r.left, viewport.left)),
					};
				})
				.sort((a, b) => b.area - a.area)[0]?.page;
		});
	const image = async () => page.locator(`[data-pdf-page="${await shown()}"] img`).screenshot();
	await expect(page.getByText('PDF本文を描画しています…')).toHaveCount(0, { timeout: 45000 });
	await closeControls();
	const pdfViewportBox = (await page.getByTestId('pdf-viewport').boundingBox())!;
	const firstPageBox = (await page.locator('[data-pdf-page="1"]').boundingBox())!;
	const selectionStartX = firstPageBox.x + firstPageBox.width * 0.35;
	const selectionEndX = firstPageBox.x + firstPageBox.width * 0.89;
	expect((selectionEndX - pdfViewportBox.x) / pdfViewportBox.width, 'selection pointer-up must finish inside the reader edge zone').toBeGreaterThanOrEqual(0.85);
	await page.mouse.move(selectionStartX, firstPageBox.y + Math.min(110, firstPageBox.height * 0.15));
	await page.mouse.down();
	await page.mouse.move(selectionEndX, firstPageBox.y + Math.min(110, firstPageBox.height * 0.15), { steps: 12 });
	await page.mouse.up();
	await expect.poll(() => page.locator('[data-pdf-page="1"] div').evaluateAll((nodes) => nodes.some((node) => getComputedStyle(node).backgroundColor.includes('33, 150, 243')))).toBe(true);
	await page.waitForTimeout(300);
	await expectPage('1');
	const first = await image();
	await testInfo.attach('pdf-normal', { body: await page.screenshot(), contentType: 'image/png' });
	const longPressPage = (await page.locator('[data-pdf-page="1"]').boundingBox())!;
	await page.mouse.move(longPressPage.x + longPressPage.width * 0.5, longPressPage.y + longPressPage.height * 0.5);
	await page.mouse.down();
	await page.waitForTimeout(500);
	await page.mouse.up();
	await expect(page.getByRole('button', { name: 'コピー', exact: true })).toBeVisible();
	await testInfo.attach('pdf-long-press-menu', { body: await page.screenshot(), contentType: 'image/png' });
	await page.getByRole('button', { name: 'コピー', exact: true }).click();
	await expect(page.getByTestId('pdf-copy-mode-banner')).toBeVisible();
	await testInfo.attach('pdf-copy-mode', { body: await page.screenshot(), contentType: 'image/png' });
	await page.mouse.move(selectionStartX, firstPageBox.y + Math.min(170, firstPageBox.height * 0.2));
	await page.mouse.down();
	await page.mouse.move(selectionEndX, firstPageBox.y + Math.min(170, firstPageBox.height * 0.2), { steps: 12 });
	await page.mouse.up();
	await expect(page.getByRole('button', { name: '選択範囲をコピー', exact: true })).toBeVisible();
	await page.getByRole('button', { name: '選択範囲をコピー', exact: true }).click();
	await expect(page.locator('[data-testid="pdf-viewport"] > p[role="status"]')).toContainText(/コピーしました|コピーに失敗|選択できるテキストがありません/);
	await page.getByRole('button', { name: '完了', exact: true }).click();
	for (let i = 0; i < 5; i++) {
		const old = await image();
		await clickControl('次のページへ');
		await expectPage(String(i + 2));
		await expect.poll(shown).toBe(i + 2);
		expect((await image()).equals(old)).toBe(false);
	}
	for (let i = 0; i < 2; i++) {
		await clickControl('前のページへ');
		await expectPage(String(5 - i));
		await expect.poll(shown).toBe(5 - i);
	}
	expect((await image()).equals(first)).toBe(false);
	await goToPage('10');
	await expectPage('10');
	await expect.poll(shown).toBe(10);
	await clickControl('拡大');
	await expectPage('10');
	await page.waitForTimeout(500);
	await expectPage('10');
	await page.getByText('読書位置を保存済み', { exact: true }).waitFor();
	expect(api.saved()).toBe('10');
	await page.reload();
	controlsOpen = false;
	await openControls();
	await expect(page.getByRole('spinbutton')).toHaveValue('10', { timeout: 45000 });
	await expect.poll(shown).toBe(10);
	await closeControls();
	await image();
	await page.waitForTimeout(300);
	const area = (await page.locator('main').boundingBox())!;
	await page.mouse.move(area.x + area.width / 2, area.y + area.height / 2);
	await page.mouse.wheel(0, 1400);
	await openControls();
	await expect.poll(async () => Number(await page.getByRole('spinbutton').inputValue())).toBeGreaterThan(10);
	await closeControls();
	await goToPage('12');
	await expect.poll(shown).toBe(12);
	await openControls();
	await expect(page.getByRole('button', { name: '次のページへ', exact: true })).toBeDisabled();
	await closeControls();
	await goToPage('1');
	await expect.poll(shown).toBe(1);
	await openControls();
	await expect(page.getByRole('button', { name: '前のページへ', exact: true })).toBeDisabled();
	await closeControls();
});

test('real PDF RTL left edge advances exactly one rendered page', async ({ page, browser }) => {
	const pdfPage = await browser.newPage();
	await pdfPage.setContent(
		`<style>@page{size:A4;margin:20mm}.page{break-after:page;font:42px serif}</style>${Array.from({ length: 4 }, (_, i) => `<div class="page">RTL PAGE ${i + 1}</div>`).join('')}`,
	);
	const pdf = await pdfPage.pdf();
	await pdfPage.close();
	const api = await fileApi(page, pdf, 'pdf');
	await page.goto('/e2e/readers/harness.html?kind=pdf&direction=rtl');
	const input = page.getByRole('spinbutton');
	await expect(input).toHaveValue('1', { timeout: 45000 });
	await expect(page.getByText('PDF本文を描画しています…')).toHaveCount(0, { timeout: 45000 });
	await page.waitForTimeout(850);
	expect(api.writes).toEqual([]);
	const viewport = (await page.getByTestId('pdf-viewport').boundingBox())!;
	await page.mouse.click(viewport.x + 5, viewport.y + viewport.height / 2);
	await expect(input).toHaveValue('2');
	await expect.poll(() => api.saved()).toBe('2');
});

for (const width of [1100, 393, 320])
	test(`EPUB UI keyboard, iframe edges, links, selection and rapid input at ${width}px`, async ({ page }) => {
		await page.setViewportSize({ width, height: 850 });
		const api = await fileApi(page, epubFixture({ rtl: true, titleOnly: true }));
		await page.goto('/e2e/readers/harness.html');
		await ready(page);
		const iframePadding = await page.locator('[data-publication-pane="primary"] iframe').first().evaluate((frame) => {
			const style = frame.contentWindow!.getComputedStyle(frame.contentDocument!.body);
			return { left: style.paddingLeft, right: style.paddingRight };
		});
		if (width < 768) {
			expect(iframePadding).toEqual({ left: '16px', right: '16px' });
		} else {
			expect(iframePadding).toEqual({ left: '0px', right: '0px' });
		}
		const next = page.getByRole('button', { name: '次のページへ', exact: true });
		const prev = page.getByRole('button', { name: '前のページへ', exact: true });
		expect((await next.boundingBox())!.x).toBeLessThan((await prev.boundingBox())!.x);
		await expect(prev).toBeDisabled();
		await move(page);
		const before = await cfi(page);
		const box = (await page.locator('[data-reader-viewport]').boundingBox())!;
		await page.mouse.click(box.x + 5, box.y + box.height / 2);
		await expect.poll(() => cfi(page)).not.toBe(before);
		const afterEdge = await cfi(page);
		await page.keyboard.press('ArrowLeft');
		await expect.poll(() => cfi(page)).not.toBe(afterEdge);
		const right = await cfi(page);
		// Distinct edge taps must be outside the shared 250ms duplicate guard.
		await page.waitForTimeout(300);
		await page.mouse.click(box.x + box.width - 5, box.y + box.height / 2);
		await expect.poll(() => cfi(page)).not.toBe(right);
		const text = await visibleText(page);
		for (let i = 0; i < 5; i++) await next.click({ noWaitAfter: true });
		await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy', 'false');
		await expect.poll(() => visibleText(page)).not.toBe(text);
		await expect(page.getByText('読書位置を保存済み', { exact: true })).toBeVisible();
		expect(api.saved()).toBe(await cfi(page));
		await page.getByRole('button', { name: '目次', exact: true }).click();
		await page.getByRole('button', { name: '第1章', exact: true }).click();
		await expect.poll(() => visibleText(page)).toContain('第1章');
		await page.frameLocator('[data-publication-pane="primary"] iframe').first().getByRole('link', { name: '本文リンク', exact: true }).first().click();
		await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy','false');
		await expect.poll(() => visibleText(page)).toContain('第2章');
		const beforeSelect = await cfi(page);
		// Native word selection plus keyboard expansion must not turn a page.
		const selectionBox = (await page.locator('[data-reader-viewport]').boundingBox())!;
		const selectionX = selectionBox.x + selectionBox.width * 0.65;
		const selectionY = selectionBox.y + Math.min(160, selectionBox.height * 0.4);
		await page.mouse.click(selectionX, selectionY, { clickCount: 2 });
		await page.keyboard.press('Shift+ArrowRight');
		expect(
			await page
				.frameLocator('[data-publication-pane="primary"] iframe').first()
				.locator('body')
				.evaluate((el) => el.ownerDocument.getSelection()?.toString().length ?? 0),
		).toBeGreaterThan(0);

		expect(await cfi(page)).toBe(beforeSelect);
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
	});

test('save errors are visible and retry writes the current EPUB CFI', async ({ page }) => {
	await fileApi(page, epubFixture());
	let fail = true;
	let saved: string | null = null;
	await page.route('**/api/v1/books/1/reading-info', async (route) => {
		if (route.request().method() === 'PATCH') {
			if (fail) {
				await route.fulfill({ status: 503, json: { error: 'unavailable' } });
				return;
			}
			saved = route.request().postDataJSON().currentPosition;
		}
		await route.fulfill({ json: { bookId: 1, currentPosition: saved, currentPage: 1, readStatus: 'reading' } });
	});
	await page.goto('/e2e/readers/harness.html');
	await ready(page);
	await move(page);
	await expect(page.getByRole('alert')).toContainText('保存できませんでした');
	fail = false;
	await page.getByRole('button', { name: '再試行', exact: true }).click();
	await expect(page.getByText('読書位置を保存済み', { exact: true })).toBeVisible();
	expect(saved).toBe(await cfi(page));
});

for (const vertical of [false, true])
	test(`adjacent EPUB pages preserve every source character (vertical=${vertical})`, async ({ page }, testInfo) => {
		await fileApi(page, epubFixture({ rtl: true, vertical }));
		await page.goto('/e2e/readers/harness.html');
		await ready(page);
		await page.getByRole('combobox').click();
		await page.getByRole('option', { name: '常に単ページ', exact: true }).click();
		const offsets = () =>
			page.evaluate(() => {
				const viewport = document.querySelector('main')!.getBoundingClientRect();
				const iframe = document.querySelector('iframe')!;
				const doc = iframe.contentDocument!;
				const f = iframe.getBoundingClientRect();
				const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
				let index = 0;
				const visible: number[] = [];
				while (walker.nextNode()) {
					const node = walker.currentNode;
					if (node.parentElement?.closest('rt,rp,style,script')) continue;
					for (let i = 0; i < (node.textContent?.length ?? 0); i++) {
						if (/\s/.test(node.textContent![i])) continue;
						const range = doc.createRange();
						range.setStart(node, i);
						range.setEnd(node, i + 1);
						const r = range.getBoundingClientRect();
						if (
							r.right + f.left >= viewport.left &&
							r.left + f.left < viewport.right &&
							r.bottom + f.top >= viewport.top &&
							r.top + f.top < viewport.bottom
						)
							visible.push(index);
						index++;
					}
				}
				return visible;
			});
		await page.waitForTimeout(300);
		for (let i = 0; i < 3; i++) {
			const before = await offsets();
			await move(page);
			const after = await offsets();
			// Adjacent screen pages must touch in original text order, including punctuation.
			expect(after[0]).toBe(before.at(-1)! + 1);
			expect(after.every((value, index) => index === 0 || value === after[index - 1] + 1)).toBe(true);
			if (i < 2) await testInfo.attach(`adjacent-${i + 1}`, { body: await page.screenshot(), contentType: 'image/png' });
		}
	});

test('in-flight EPUB movement tolerates resize and immediate shelf reentry without stale saves', async ({ page }) => {
	const errors: string[] = [];
	page.on('pageerror', (e) => errors.push(String(e)));
	await fileApi(page, epubFixture({ rtl: true }));
	await page.goto('/e2e/readers/harness.html');
	await ready(page);
	for (const width of [393, 320, 1100]) {
		await page.getByRole('button', { name: '次のページへ', exact: true }).click();
		await page.setViewportSize({ width, height: 850 });
		await ready(page);
		await move(page);
	}
	for (let i = 0; i < 3; i++) await page.getByRole('button', { name: '次のページへ', exact: true }).click({ noWaitAfter: true });
	await page.getByRole('link', { name: '本棚に戻る' }).click();
	const last = await cfi(page);
	await page.getByRole('link', { name: 'テスト書籍を読む' }).click();
	await ready(page);
	expect(await cfi(page)).toBe(last);
	expect(errors).toEqual([]);
});

// Mixed writing modes must not reinterpret a spread or skip the next chapter.
test('mixed vertical/horizontal chapters: real two-page geometry, complete source order, boundaries and completed save', async ({ page }, testInfo) => {
	test.setTimeout(180000);
	const api = await fileApi(page, epubFixture({ rtl: true, mixed: true, paragraphs: 2 }));
	await page.goto('/e2e/readers/harness.html');
	await ready(page);
	const source = () => page.evaluate(() => {
		const viewport = document.querySelector('main')!.getBoundingClientRect();
		const all: number[] = [];
		const panes: Array<{ x: number; width: number; writing: string }> = [];
		let chapterLength = 0;
		for (const iframe of document.querySelectorAll('iframe')) {
			const f = iframe.getBoundingClientRect(), doc = iframe.contentDocument;
			if (!doc?.body || !f.width || getComputedStyle(iframe).visibility === 'hidden') continue;
			const pane=iframe.closest('[data-publication-pane]')?.getBoundingClientRect() ?? viewport;
			const section = Number(doc.title.match(/第(\d+)章/)![1]);
			const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
			let index = 0; const visible: number[] = [];
			while (walker.nextNode()) {
				const node = walker.currentNode;
				if (node.parentElement?.closest('rt,rp,style,script')) continue;
				for (let i=0;i<(node.textContent?.length ?? 0);i++) {
					if (/\s/.test(node.textContent![i])) continue;
					const range=doc.createRange();range.setStart(node,i);range.setEnd(node,i+1);
					const r=range.getBoundingClientRect();
					if (r.right+f.left>=pane.left && r.left+f.left<pane.right && r.bottom+f.top>=viewport.top && r.top+f.top<viewport.bottom) visible.push(index);
					index++;
				}
			}
			chapterLength=index;
			all.push(...visible.map(n=>(section-1)*index+n));
			if (visible.length) panes.push({x:f.left,width:f.width,writing:doc.defaultView!.getComputedStyle(doc.body).writingMode});
		}
		return { all, panes, chapterLength };
	});
	for (const mode of ['常に単ページ', '常に見開き', '自動']) {
		await page.getByRole('combobox').click();await page.getByRole('option',{name:mode,exact:true}).click();
		await page.getByRole('button',{name:'目次',exact:true}).click();await page.getByRole('button',{name:'第一章',exact:true}).click();
		await expect.poll(async()=> (await source()).all[0]).toBe(0);
		await expect(page.getByRole('button',{name:'前のページへ'})).toBeDisabled();
		let before=await source(); let turns=0;
		const seen = new Set<number>();
		const record = (values: number[]) => {
			expect(values.length).toBeGreaterThan(0);
			expect(values.every((value,index)=>index===0||value>values[index-1]), 'visible source order must be strictly increasing').toBe(true);
			for (const value of values) seen.add(value);
		};
		record(before.all);
		if(mode!=='常に単ページ') {
			expect(before.panes).toHaveLength(2);
			expect(before.panes[0].x).toBeGreaterThan(before.panes[1].x);
			expect(before.panes[0].width).toBeLessThan(600);
			expect(before.panes[0].writing).toBe('vertical-rl');
			await page.screenshot({path:testInfo.outputPath(`mixed-${mode}.png`)});
		}
		while(await page.getByRole('button',{name:'次のページへ'}).isEnabled()) {
			expect(turns++).toBeLessThan(60);
			await move(page);
			const after=await source();
			record(after.all);
			before=after;
		}
		const completeSource = [...seen].sort((a,b)=>a-b);
		expect(completeSource[0]).toBe(0);
		expect(completeSource.every((value,index)=>index===0||value===completeSource[index-1]+1), 'all source characters must appear across screens').toBe(true);
		expect(completeSource.at(-1)).toBe(before.chapterLength*4-1);
		await expect(page.getByText('読書位置を保存済み',{exact:true})).toBeVisible();
		expect(api.writes.length).toBeGreaterThan(0);
		expect(api.status()).toBe('completed');
		expect(api.saved()).toBe(await cfi(page));
		await page.getByRole('button',{name:'前のページへ'}).click();
		await expect(page.getByRole('button',{name:'次のページへ'})).toBeEnabled();
	}
});

// Chromium can scroll the RTL pagination container while expanding a native
// selection deep in a long Japanese chapter. Chapter-start selection misses it.
for (const variant of [{vertical:false,spread:false,pane:'primary'}, {vertical:false,spread:true,pane:'primary'}, {vertical:true,spread:false,pane:'primary'}, {vertical:true,spread:true,pane:'following'}]) {
test(`native selection deep inside a Japanese chapter preserves the visible page ${JSON.stringify(variant)}`, async ({ page }) => {
	await page.setViewportSize({width:704,height:798});
	await fileApi(page,epubFixture({rtl:true,vertical:variant.vertical,paragraphs:40}));
	await page.goto('/e2e/readers/harness.html');await ready(page);
	await page.getByRole('combobox').click();await page.getByRole('option',{name:variant.spread?'常に見開き':'常に単ページ',exact:true}).click();await ready(page);
	for(let i=0;i<21;i++) {await page.getByRole('button',{name:'次のページへ',exact:true}).click();await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy','false');}
	const before=await cfi(page),text=await visibleText(page);
	const box=(await page.locator(`[data-publication-pane="${variant.pane}"]`).boundingBox())!;
	await page.mouse.click(box.x+box.width*.65,box.y+150,{clickCount:2});
	await page.keyboard.press('Shift+ArrowRight');await page.waitForTimeout(350);
	expect(await page.frameLocator(`[data-publication-pane="${variant.pane}"] iframe`).first().locator('body').evaluate(e=>e.ownerDocument.getSelection()?.toString().length??0)).toBeGreaterThan(0);
	expect(await cfi(page)).toBe(before);expect(await visibleText(page)).toBe(text);
	await move(page);
});
}

// Odd viewports produce half-pixel custom panes; native iframe measurement
// rounds to integer clientWidth. Ordinary same-spine turns must retain the
// active document. Horizontal chapters use epub.js's full-width native spread,
// while vertical chapters use the custom following-page rendition.
for (const vertical of process.env.READER_ACTUAL_EPUB_PATH ? [false] : [false, true]) {
test(`narrow odd-width spreads finish layout and preserve real page movement vertical=${vertical}`, async ({page}) => {
	// The optional full novel covers two widths and many reflows. Keep each
	// readiness/movement budget unchanged; allow time for the complete matrix.
	test.setTimeout(process.env.READER_ACTUAL_EPUB_PATH ? 600000 : 240000);
	await page.setViewportSize({width:736,height:859});
	await fileApi(page,process.env.READER_ACTUAL_EPUB_PATH ? readFileSync(process.env.READER_ACTUAL_EPUB_PATH) : epubFixture({rtl:true,vertical}));
	await page.goto('/e2e/readers/harness.html');await ready(page);
	const initialFrame=await page.locator('[data-publication-pane="primary"] iframe').elementHandle();
	await page.getByRole('combobox').click();await page.getByRole('option',{name:'常に見開き',exact:true}).click();await ready(page);
	expect(await initialFrame!.evaluate(frame=>frame.isConnected),'Spread reflow must retain the loaded chapter document').toBe(true);
	for(let i=0;i<3;i++)await move(page,'次のページへ',30000);
	await page.getByRole('combobox').click();await page.getByRole('option',{name:'常に単ページ',exact:true}).click();await ready(page);
	for(const width of process.env.READER_ACTUAL_EPUB_PATH ? [393,320] : [393,391,320,833]) {
		await page.setViewportSize({width,height:900});await ready(page);
		for(const mode of ['常に単ページ','常に見開き','自動']) {
			if(process.env.READER_ACTUAL_EPUB_PATH)console.log('same-file mode start',new Date().toISOString(),width,mode);
			await page.getByRole('combobox').click();await page.getByRole('option',{name:mode,exact:true}).click();await ready(page);
			if(process.env.READER_ACTUAL_EPUB_PATH)console.log('same-file mode ready',new Date().toISOString(),width,mode);
			for(let i=0;i<3;i++) {
				const spreadActive=mode==='常に見開き'||(mode==='自動'&&width===833);
				const tracked=spreadActive
					? await page.locator(`[data-publication-pane="${vertical?'following':'primary'}"] iframe`).first().elementHandle() : null;
				if(spreadActive&&!vertical)await expect(page.locator('[data-publication-pane="following"] iframe')).toHaveCount(0);
				const section=tracked && await tracked.evaluate(frame=>frame.closest('.epub-view')?.getAttribute('ref'));
				await move(page,'次のページへ',30000);
				if(tracked) {
					const currentSection=await page.locator(`[data-publication-pane="${vertical?'following':'primary'}"] iframe`).first().evaluate(frame=>frame.closest('.epub-view')?.getAttribute('ref'));
					if(currentSection===section)expect(await tracked.evaluate(frame=>frame.isConnected),'Same-spine page turns must retain the active document').toBe(true);
				}
			}
			await move(page,'前のページへ',30000);
		}
	}
});
}

for (const vertical of [false,true]) {
test(`height reflow retains the chapter, visible CFI anchor and native selection vertical=${vertical}`, async ({page}) => {
	await page.setViewportSize({width:736,height:900});
	await fileApi(page,epubFixture({rtl:true,vertical}));
	await page.goto('/e2e/readers/harness.html');await ready(page);
	for(let i=0;i<3;i++)await move(page);
	const before=await visibleText(page);
	const frame=await page.locator('[data-publication-pane="primary"] iframe').elementHandle();
	const box=await page.locator('[data-reader-viewport]').boundingBox();
	await page.mouse.click(box!.x+box!.width*.65,box!.y+150,{clickCount:2});await page.keyboard.press('Shift+ArrowRight');
	const selection=await frame!.evaluate(f=>f.contentDocument!.getSelection()!.toString());expect(selection.length).toBeGreaterThan(0);
	await page.setViewportSize({width:736,height:1050});await ready(page);
	expect(await frame!.evaluate(f=>f.isConnected)).toBe(true);
	expect(await frame!.evaluate(f=>f.contentDocument!.getSelection()!.toString())).toBe(selection);
	expect(await visibleText(page)).toContain(before.slice(0,20));
	const area=await page.locator('[data-reader-viewport]').boundingBox();await page.mouse.click(area!.x+area!.width/2,area!.y+area!.height/2);
	await move(page);
});
}

test('conditional publisher writing mode is rediscovered on resize', async ({page}) => {
	await page.setViewportSize({width:736,height:1000});
	await page.emulateMedia({colorScheme:'light'});
	await fileApi(page,epubFixture({rtl:true,responsive:true}));
	await page.goto('/e2e/readers/harness.html');await ready(page);
	const writing=()=>page.locator('[data-publication-pane="primary"] iframe').evaluate(f=>f.contentWindow!.getComputedStyle(f.contentDocument!.body).writingMode);
	expect(await writing()).toBe('horizontal-tb');await move(page);
	const frame=await page.locator('[data-publication-pane="primary"] iframe').elementHandle();
	await page.emulateMedia({colorScheme:'dark'});
	await page.setViewportSize({width:736,height:650});await ready(page);
	expect(await frame!.evaluate(f=>f.isConnected)).toBe(false);
	expect(await writing()).toBe('vertical-rl');await move(page);
	await page.emulateMedia({colorScheme:'light'});
	await page.setViewportSize({width:736,height:1000});await ready(page);
	expect(await writing()).toBe('horizontal-tb');await move(page);
});
