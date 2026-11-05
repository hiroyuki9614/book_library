import { expect, test, type Page } from '@playwright/test';
import { epubFixture } from './fixtures';

type ReaderKind = 'epub' | 'pdf';
type Direction = 'ltr' | 'rtl';
type Position = readonly [number, number];

// Actual Chromium rectangles from the production-built 3740a3a harness at 851px
// high. Keep these separate from the header: its height must not move the footer.
const FOOTER_BUTTONS: Record<ReaderKind, Record<Direction, Record<number, readonly [Position, Position]>>> = {
	epub: {
		ltr: {
			1280: [[353.09375, 801], [409.09375, 801]],
			1440: [[433.09375, 801], [489.09375, 801]],
			1663: [[544.59375, 801], [600.59375, 801]],
			1024: [[225.09375, 801], [281.09375, 801]],
			393: [[148.5, 711], [200.5, 711]],
			320: [[112, 711], [164, 711]],
		},
		rtl: {
			1280: [[882.90625, 801], [826.90625, 801]],
			1440: [[962.90625, 801], [906.90625, 801]],
			1663: [[1074.40625, 801], [1018.40625, 801]],
			1024: [[754.90625, 801], [698.90625, 801]],
			393: [[200.5, 711], [148.5, 711]],
			320: [[164, 711], [112, 711]],
		},
	},
	pdf: {
		ltr: {
			1280: [[574.515625, 803], [898.578125, 803]],
			1440: [[654.515625, 803], [978.578125, 803]],
			1663: [[766.015625, 803], [1090.078125, 803]],
			1024: [[446.515625, 803], [770.578125, 803]],
			393: [[12.46875, 803], [336.53125, 803]],
			320: [[69.96875, 751], [206, 803]],
		},
		rtl: {
			1280: [[898.578125, 803], [574.515625, 803]],
			1440: [[978.578125, 803], [654.515625, 803]],
			1663: [[1090.078125, 803], [766.015625, 803]],
			1024: [[770.578125, 803], [446.515625, 803]],
			393: [[336.53125, 803], [12.46875, 803]],
			320: [[206.03125, 751], [70, 803]],
		},
	},
};

async function useProtectedBook(page: Page, file: Buffer, kind: ReaderKind) {
	const attempts: Array<{ currentPosition?: string; currentPage?: number; readStatus: string }> = [];
	let saved = { bookId: 1, currentPosition: null as string | null, currentPage: 1, readStatus: 'unread' };
	const api = { attempts, fail: false };
	await page.route('**/api/v1/books/1/file', (route) => route.fulfill({
		body: file,
		contentType: kind === 'epub' ? 'application/epub+zip' : 'application/pdf',
	}));
	await page.route('**/api/v1/books/1/reading-info', async (route) => {
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
			buttons: ['前のページへ', '次のページへ'].map((name) => {
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
					name, text: button.textContent, ...rect(button), radius: style.borderRadius,
					translate: style.translate, transform: style.transform, marginTop: style.marginTop,
					glyph: glyph ? rect(glyph) : null,
					inkCenterY: range.getBoundingClientRect().y + metrics.fontBoundingBoxAscent + (metrics.actualBoundingBoxDescent - metrics.actualBoundingBoxAscent) / 2,
				};
			}),
		};
	}, kind);
}

function checkHeader(observed: Awaited<ReturnType<typeof geometry>>, width: number) {
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
				console.log(JSON.stringify({ kind, direction, width, ...observed }));
				await testInfo.attach(`${kind}-${direction}-${width}-geometry`, { body: JSON.stringify(observed, null, 2), contentType: 'application/json' });
				if (width === 1280) await page.screenshot({ path: testInfo.outputPath(`${kind}-${direction}-layout.png`) });
				checkHeader(observed, width);
				const expectedHeader = width >= 1280 ? 52 : kind === 'epub' ? width < 640 ? 97 : 105 : width < 640 ? 53 : 61;
				expect(observed.header.height).toBe(expectedHeader);
				const footerHeight = kind === 'epub' ? width < 640 ? 145 : 57 : width === 320 ? 105 : 53;
				expect(observed.footer).toEqual({ x: 0, y: 851 - footerHeight, width, height: footerHeight });
				for (const [index, button] of observed.buttons.entries()) {
					const [x, y] = FOOTER_BUTTONS[kind][direction][width][index];
					expect({ x: button.x, y: button.y, width: button.width, height: button.height }, `${button.name}: unchanged outer rectangle`).toEqual({ x, y, width: 44, height: 44 });
					expect(button.translate).toBe('none');
					expect(button.transform).toBe('none');
					expect(button.marginTop).toBe('0px');
					const previous = index === 0;
					const pointsRight = previous === (direction === 'rtl');
					expect(button.text).toBe(kind === 'epub' ? pointsRight ? '›' : '‹' : pointsRight ? '>' : '<');
					if (kind === 'epub') {
						expect(Number.parseFloat(button.radius)).toBeGreaterThanOrEqual(22);
						expect(button.glyph, 'only the glyph has an optical correction').not.toBeNull();
						expect(button.glyph!.x + button.glyph!.width / 2).toBeCloseTo(x + 22, 1);
						expect(Math.abs(button.glyph!.y + button.glyph!.height / 2 - (y + 22))).toBeLessThanOrEqual(1);
						expect(button.inkCenterY, 'visible glyph ink is vertically centered').toBeCloseTo(y + 22, 1);
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
