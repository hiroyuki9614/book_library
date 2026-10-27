import { test, expect, type Page } from '@playwright/test';
import { epubFixture } from './fixtures';

async function fileApi(page: Page, file: Buffer, kind = 'epub', initial: string | null = null) {
	let saved = initial;
	const writes: string[] = [];
	await page.route('**/api/v1/books/1/file', (route) =>
		route.fulfill({ body: file, contentType: kind === 'epub' ? 'application/epub+zip' : 'application/pdf' }),
	);
	await page.route('**/api/v1/books/1/reading-info', async (route) => {
		if (route.request().method() === 'PATCH') {
			const body = route.request().postDataJSON();
			await new Promise((resolve) => setTimeout(resolve, writes.length === 0 ? 120 : 5));
			saved = String(body.currentPosition ?? body.currentPage);
			writes.push(saved);
		}
		await route.fulfill({
			json: { bookId: 1, currentPosition: saved, currentPage: kind === 'pdf' ? Number(saved ?? 1) : 1, readStatus: 'reading' },
		});
	});
	return { writes, saved: () => saved };
}

async function visibleText(page: Page) {
	return page.evaluate(() => {
		const viewport = document.querySelector('main')!.getBoundingClientRect();
		let text = '';
		for (const iframe of document.querySelectorAll('iframe')) {
			const doc = iframe.contentDocument!;
			const f = iframe.getBoundingClientRect();
			const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
			while (walker.nextNode()) {
				const node = walker.currentNode;
				if (node.parentElement?.closest('rt,rp,style,script')) continue;
				const range = doc.createRange();
				range.selectNodeContents(node);
				if (
					![...range.getClientRects()].some(
						(r) =>
							r.right + f.left > viewport.left &&
							r.left + f.left < viewport.right &&
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
						r.width > 0 &&
						r.height > 0 &&
						r.right + f.left > viewport.left + 1 &&
						r.left + f.left < viewport.right - 1 &&
						r.bottom + f.top > viewport.top + 1 &&
						r.top + f.top < viewport.bottom - 1
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
	await expect.poll(() => visibleText(page)).not.toBe('');
}
async function move(page: Page, name = '次のページへ') {
	const before = await cfi(page);
	const text = await visibleText(page);
	await page.getByRole('button', { name, exact: true }).click();
	await expect.poll(() => cfi(page)).not.toBe(before);
	await expect.poll(() => visibleText(page)).not.toBe(text);
}

for (const config of [
	{ rtl: false, vertical: false },
	{ rtl: true, vertical: false },
	{ rtl: true, vertical: true },
]) {
	test(`real EPUB ${JSON.stringify(config)}: modes, 10 next/3 previous, CFI, body, hierarchical TOC and resume`, async ({
		page,
	}, testInfo) => {
		const api = await fileApi(page, epubFixture(config));
		await page.goto('/e2e/readers/harness.html');
		await ready(page);
		for (const mode of ['常に単ページ', '常に見開き', '自動']) {
			await page.getByRole('combobox').click();
			await page.getByRole('option', { name: mode, exact: true }).click();
			await page.waitForTimeout(300);
			const position = await page.locator('label[for="reading-progress"]').innerText();
			for (let i = 0; i < 10; i++) await move(page);
			for (let i = 0; i < 3; i++) await move(page, '前のページへ');
			expect(await page.locator('label[for="reading-progress"]').innerText()).not.toBe(position);
		}
		await page.getByRole('button', { name: '目次', exact: true }).click();
		await expect(page.getByRole('button', { name: '第四章', exact: true })).toBeVisible();
		await page.getByRole('button', { name: '第四章', exact: true }).click();
		if (config.vertical) await testInfo.attach('vertical-layout', { body: await page.screenshot(), contentType: 'image/png' });
		await expect.poll(() => visibleText(page)).toContain('第4章');
		await expect(page.getByText('読書位置を保存済み', { exact: true })).toBeVisible();
		const saved = api.saved();
		expect(saved).toBe(await cfi(page));
		await page.evaluate(() => localStorage.clear());
		await page.reload();
		await ready(page);
		expect(await cfi(page)).toBe(saved);
	});
}

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

test('real PDF moves images and page numbers, restores only once, zoom, scroll, boundaries and reload', async ({ page, browser }) => {
	const pdfPage = await browser.newPage();
	await pdfPage.setContent(
		`<style>@page{size:A4;margin:20mm}.page{break-after:page;font:42px serif}</style>${Array.from({ length: 12 }, (_, i) => `<div class="page">REGRESSION PAGE ${i + 1}<p>${'Distinct text '.repeat(i + 1)}</p></div>`).join('')}`,
	);
	const pdf = await pdfPage.pdf();
	await pdfPage.close();
	const api = await fileApi(page, pdf, 'pdf');
	await page.goto('/e2e/readers/harness.html?kind=pdf');
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
	const first = await image();
	for (let i = 0; i < 5; i++) {
		const old = await image();
		await page.getByRole('button', { name: '次のページへ' }).click();
		await expect(input).toHaveValue(String(i + 2));
		await expect.poll(shown).toBe(i + 2);
		expect((await image()).equals(old)).toBe(false);
	}
	for (let i = 0; i < 2; i++) {
		await page.getByRole('button', { name: '前のページへ' }).click();
		await expect(input).toHaveValue(String(5 - i));
		await expect.poll(shown).toBe(5 - i);
	}
	expect((await image()).equals(first)).toBe(false);
	await input.fill('10');
	await input.press('Enter');
	await expect(input).toHaveValue('10');
	await expect.poll(shown).toBe(10);
	await page.getByRole('button', { name: '拡大', exact: true }).click();
	await expect(input).toHaveValue('10');
	await page.waitForTimeout(500);
	await expect(input).toHaveValue('10');
	await page.getByText('読書位置を保存済み', { exact: true }).waitFor();
	expect(api.saved()).toBe('10');
	await page.reload();
	await expect(page.getByRole('spinbutton')).toHaveValue('10', { timeout: 45000 });
	await expect.poll(shown).toBe(10);
	const area = (await page.locator('main').boundingBox())!;
	await page.mouse.move(area.x + area.width / 2, area.y + area.height / 2);
	await page.mouse.wheel(0, 800);
	await expect.poll(async () => Number(await input.inputValue())).toBeGreaterThan(10);
	await input.fill('12');
	await input.press('Enter');
	await expect.poll(shown).toBe(12);
	await expect(page.getByRole('button', { name: '次のページへ' })).toBeDisabled();
	await input.fill('1');
	await input.press('Enter');
	await expect.poll(shown).toBe(1);
	await expect(page.getByRole('button', { name: '前のページへ' })).toBeDisabled();
});

for (const width of [1100, 393, 320])
	test(`EPUB UI keyboard, iframe edges, links, selection and rapid input at ${width}px`, async ({ page }) => {
		await page.setViewportSize({ width, height: 850 });
		const api = await fileApi(page, epubFixture({ rtl: true, titleOnly: true }));
		await page.goto('/e2e/readers/harness.html');
		await ready(page);
		const next = page.getByRole('button', { name: '次のページへ', exact: true });
		const prev = page.getByRole('button', { name: '前のページへ', exact: true });
		expect((await next.boundingBox())!.x).toBeLessThan((await prev.boundingBox())!.x);
		await expect(prev).toBeDisabled();
		await move(page);
		const before = await cfi(page);
		const box = (await page.locator('.epub-container').boundingBox())!;
		await page.mouse.click(box.x + 5, box.y + box.height / 2);
		await expect.poll(() => cfi(page)).not.toBe(before);
		const afterEdge = await cfi(page);
		await page.keyboard.press('ArrowLeft');
		await expect.poll(() => cfi(page)).not.toBe(afterEdge);
		const right = await cfi(page);
		await page.mouse.click(box.x + box.width - 5, box.y + box.height / 2);
		await expect.poll(() => cfi(page)).not.toBe(right);
		const text = await visibleText(page);
		for (let i = 0; i < 5; i++) await next.click({ noWaitAfter: true });
		await expect.poll(() => visibleText(page)).not.toBe(text);
		await expect(page.getByText('読書位置を保存済み', { exact: true })).toBeVisible();
		expect(api.saved()).toBe(await cfi(page));
		await page.getByRole('button', { name: '目次', exact: true }).click();
		await page.getByRole('button', { name: '第1章', exact: true }).click();
		await expect.poll(() => visibleText(page)).toContain('第1章');
		await page.frameLocator('iframe').getByRole('link', { name: '本文リンク', exact: true }).first().click();
		await expect.poll(() => visibleText(page)).toContain('第2章');
		const beforeSelect = await cfi(page);
		// Native word selection plus keyboard expansion must not turn a page.
		const selectionBox = (await page.locator('.epub-container').boundingBox())!;
		const selectionX = selectionBox.x + selectionBox.width * 0.65;
		const selectionY = selectionBox.y + Math.min(160, selectionBox.height * 0.4);
		await page.mouse.click(selectionX, selectionY, { clickCount: 2 });
		await page.keyboard.press('Shift+ArrowRight');
		expect(
			await page
				.frameLocator('iframe')
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
							r.width > 0 &&
							r.height > 0 &&
							r.right + f.left > viewport.left + 1 &&
							r.left + f.left < viewport.right - 1 &&
							r.bottom + f.top > viewport.top + 1 &&
							r.top + f.top < viewport.bottom - 1
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
