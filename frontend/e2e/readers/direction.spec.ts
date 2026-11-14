import { test, expect, type Page, type TestInfo } from '@playwright/test';
import { directionEpubFixture } from './fixtures';
import { writeFileSync } from 'node:fs';

type Direction = 'ltr' | 'rtl';

async function fileApi(page: Page, file: Buffer) {
	let saved: string | null = null;
	const writes: string[] = [];
	await page.route('**/api/v1/books/1/file', route => route.fulfill({ body: file, contentType: 'application/epub+zip' }));
	await page.route('**/api/v1/books/1/reading-info', async route => {
		if (route.request().method() === 'PATCH') {
			saved = route.request().postDataJSON().currentPosition;
			writes.push(saved!);
		}
		await route.fulfill({ json: { bookId: 1, currentPosition: saved, readStatus: 'reading' } });
	});
	return { writes, saved: () => saved };
}

async function idle(page: Page) {
	await expect(page.getByRole('combobox', { name: 'ページ表示方法' })).toBeEnabled({ timeout: 30000 });
	await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy', 'false');
}

// Observe actual clipped glyph rectangles, not DOM presence or a next() call.
async function observe(page: Page) {
	return page.evaluate(() => {
		const viewer = document.querySelector<HTMLElement>('[data-reader-viewport]')!;
		const viewport = viewer.getBoundingClientRect();
		const visible: Array<{ number: number; text: string; x: number; direction: string }> = [];
		const layout: Array<{ stage: string; root: string; rootPriority: string; body: string; scroll: number }> = [];
		for (const frame of viewer.querySelectorAll('iframe')) {
			const doc = frame.contentDocument;
			if (!doc?.body || !frame.clientWidth || getComputedStyle(frame).visibility === 'hidden') continue;
			const offset = frame.getBoundingClientRect();
			const style = frame.contentWindow!.getComputedStyle.bind(frame.contentWindow);
			const stage = frame.closest<HTMLElement>('.epub-container')!;
			layout.push({ stage: getComputedStyle(stage).direction, root: style(doc.documentElement).direction, rootPriority: doc.documentElement.style.getPropertyPriority('direction'), body: style(doc.body).direction, scroll: stage.scrollLeft });
			for (const paragraph of doc.querySelectorAll<HTMLElement>('[data-source-page]')) {
				const range = doc.createRange();
				range.selectNodeContents(paragraph);
				const rects = [...range.getClientRects()].filter(r => r.right + offset.left > viewport.left && r.left + offset.left < viewport.right && r.bottom + offset.top > viewport.top && r.top + offset.top < viewport.bottom);
				if (rects.length) visible.push({ number: Number(paragraph.dataset.sourcePage), text: paragraph.textContent!, x: Math.min(...rects.map(r => r.left + offset.left)), direction: style(paragraph).direction });
			}
		}
		return { cfi: localStorage.getItem('reader-location:1'), visible: visible.sort((a, b) => a.number - b.number), layout };
	});
}

// Synthetic CFIs have ordinary element/text paths. Ignore ID assertions when
// comparing their numeric spine/path/offset order (no ranges in this fixture).
function compareCfi(a: string, b: string) {
	const path = (cfi: string) => cfi.replace(/\[[^\]]*\]/g, '').match(/\d+/g)!.map(Number);
	const left = path(a), right = path(b);
	for (let i = 0; i < Math.max(left.length, right.length); i++) {
		const difference = (left[i] ?? 0) - (right[i] ?? 0);
		if (difference) return Math.sign(difference);
	}
	return 0;
}

const cases: Array<{ width: number; direction: Direction; opfDirection: Direction; bodyDirection: Direction; htmlDirection?: Direction }> = [
	{ width: 1100, direction: 'ltr', opfDirection: 'rtl', bodyDirection: 'ltr' },
	{ width: 1100, direction: 'ltr', opfDirection: 'ltr', bodyDirection: 'rtl' },
	{ width: 1100, direction: 'rtl', opfDirection: 'ltr', bodyDirection: 'ltr' },
	...[1100, 393, 320].map(width => ({ width, direction: 'ltr' as const, opfDirection: 'rtl' as const, bodyDirection: 'rtl' as const, htmlDirection: 'rtl' as const })),
	...[1100, 393, 320].map(width => ({ width, direction: 'rtl' as const, opfDirection: 'ltr' as const, bodyDirection: 'rtl' as const, htmlDirection: 'ltr' as const })),
];

for (const config of cases) {
	test(`owner direction ${JSON.stringify(config)}: visible columns, CFI, input, modes and restore`, async ({ page }, testInfo: TestInfo) => {
		test.setTimeout(180000);
		await page.setViewportSize({ width: config.width, height: 850 });
		const file = directionEpubFixture(config);
		if (config.width === 1100 && config.direction === 'ltr' && config.opfDirection === 'rtl' && config.bodyDirection === 'rtl') {
			writeFileSync(testInfo.outputPath('owner-ltr-direction.epub'), file);
		}
		const api = await fileApi(page, file);
		const observations: Array<{ action: string; mode: string; observation: Awaited<ReturnType<typeof observe>> }> = [];
		try {
			await page.goto(`/e2e/readers/harness.html?direction=${config.direction}`);
			await idle(page);
			for (const mode of ['常に単ページ', '常に見開き', '自動']) {
				await page.getByRole('combobox').click();
				await page.getByRole('option', { name: mode, exact: true }).click();
				await idle(page);
				await page.getByRole('button', { name: '目次', exact: true }).click();
				await page.getByRole('button', { name: 'Chapter 1', exact: true }).click();
				await idle(page);
				const columns = mode === '常に見開き' || (mode === '自動' && config.width >= 800) ? 2 : 1;
				const assertObservation = (observation: Awaited<ReturnType<typeof observe>>, expected: number[]) => {
					expect(observation.visible.map(p => p.number), JSON.stringify(observation)).toEqual(expected);
					expect(observation.cfi).toMatch(/^epubcfi\(/);
					for (const layout of observation.layout) {
						expect(layout.stage).toBe(config.direction);
						expect(layout.root).toBe(config.direction);
						expect(layout.rootPriority).toBe('important');
						expect(layout.body).toBe(config.direction);
					}
					for (const p of observation.visible) {
						expect(p.text).toContain(`SOURCE PAGE ${String(p.number).padStart(2, '0')}`);
						expect(p.direction, 'publisher text bidi direction must survive').toBe(config.bodyDirection);
					}
					if (expected.length === 2) {
						const [earlier, later] = observation.visible;
						if (config.direction === 'ltr') expect(earlier.x).toBeLessThan(later.x);
						else expect(earlier.x).toBeGreaterThan(later.x);
					}
				};
				const record = async (action: string, expected: number[]) => {
					const observation = await observe(page);
					observations.push({ action, mode, observation });
					assertObservation(observation, expected);
					if (action === 'start' && columns === 2) await page.screenshot({ path: testInfo.outputPath(`spread-${mode}.png`) });
					return observation;
				};
				const expectedAt = (turn: number) => Array.from({ length: columns }, (_, i) => turn * columns + i + 1);
				let before = await record('start', expectedAt(0));
				await expect(page.getByRole('button', { name: '前のページへ' })).toBeDisabled();
				const previousButton = page.getByRole('button', { name: '前のページへ' });
				const nextButton = page.getByRole('button', { name: '次のページへ' });
				const buttonRects = [await previousButton.boundingBox(), await nextButton.boundingBox()];
				if (config.direction === 'ltr') expect(buttonRects[0]!.x).toBeLessThan(buttonRects[1]!.x);
				else expect(buttonRects[0]!.x).toBeGreaterThan(buttonRects[1]!.x);
				await expect(nextButton).toHaveText(config.direction === 'ltr' ? '›' : '‹');
				const turn = async (next: boolean, input: number, changes = true) => {
					const side = next === (config.direction === 'ltr') ? 'right' : 'left';
					if (input % 3 === 0) await (next ? nextButton : previousButton).click();
					else {
						const viewport = (await page.locator('[data-reader-viewport]').boundingBox())!;
						if (input % 3 === 1) {
							await page.mouse.click(viewport.x + viewport.width / 2, viewport.y + viewport.height / 2);
							await page.keyboard.press(side === 'right' ? 'ArrowRight' : 'ArrowLeft');
						} else {
							// Separate physical taps beyond the reader's intentional debounce.
							await page.waitForTimeout(300);
							await page.mouse.click(viewport.x + (side === 'right' ? viewport.width - 5 : 5), viewport.y + viewport.height / 2);
						}
					}
					if (changes) await expect.poll(async () => (await observe(page)).cfi).not.toBe(before.cfi);
					else await page.waitForTimeout(350);
					await idle(page);
				};
				await turn(false, 1, false);
				await turn(false, 2, false);
				expect((await record('previous at start (key/edge)', expectedAt(0))).cfi).toBe(before.cfi);
				const history = [before];
				for (let i = 1; i <= 10; i++) {
					await turn(true, i - 1);
					const after = await record(`next ${i} (${['button', 'key', 'edge'][(i - 1) % 3]})`, expectedAt(i));
					expect(compareCfi(after.cfi!, before.cfi!)).toBe(1);
					history.push(after); before = after;
				}
				for (let i = 1; i <= 3; i++) {
					await turn(false, i - 1);
					const after = await record(`previous ${i}`, expectedAt(10 - i));
					expect(compareCfi(after.cfi!, before.cfi!)).toBe(-1);
					expect(after.cfi).toBe(history[10 - i].cfi);
					before = after;
				}
				await expect.poll(() => api.saved()).toBe(before.cfi);
				const savedButtonRects = [await previousButton.boundingBox(), await nextButton.boundingBox()];
				const writes = api.writes.length;
				await page.evaluate(() => localStorage.clear());
				await page.reload();
				await idle(page);
				const restoredBeforeModeChange = await observe(page);
				const initialMode = await page.getByRole('combobox').innerText();
				const initialColumns = initialMode === '常に見開き' || (initialMode === '自動' && config.width >= 800) ? 2 : 1;
				const savedFirstPage = before.visible[0].number;
				const initialFirstPage = initialColumns === 2 ? savedFirstPage - ((savedFirstPage - 1) % 2) : savedFirstPage;
				const expectedInitialPages = Array.from({ length: initialColumns }, (_, i) => initialFirstPage + i);
				observations.push({ action: 'reloaded before mode change', mode, observation: restoredBeforeModeChange });
				assertObservation(restoredBeforeModeChange, expectedInitialPages);
				expect(restoredBeforeModeChange.cfi, 'saved CFI must be restored before changing spread mode').toBe(before.cfi);
				await page.waitForTimeout(850);
				const restoredAfterSettle = await observe(page);
				observations.push({ action: 'reloaded after settle before mode change', mode, observation: restoredAfterSettle });
				assertObservation(restoredAfterSettle, expectedInitialPages);
				expect(restoredAfterSettle.cfi, 'saved CFI must remain restored after settling before changing spread mode').toBe(before.cfi);
				expect(api.writes.length, 'opening must not overwrite the saved CFI').toBe(writes);
				// Restore defaults to the viewport mode; reselect the mode through UI.
				await page.getByRole('combobox').click();
				await page.getByRole('option', { name: mode, exact: true }).click();
				await idle(page);
				const restored = await record('reload restore', expectedAt(7));
				expect(restored.cfi).toBe(before.cfi);
				expect([await previousButton.boundingBox(), await nextButton.boundingBox()]).toEqual(savedButtonRects);
				await expect.poll(() => api.saved()).toBe(restored.cfi);
				before = restored;
				for (let i = 8; i < 32 / columns; i++) {
					await turn(true, 0);
					before = await record(`to end ${i}`, expectedAt(i));
				}
				await expect(nextButton).toBeDisabled();
				await expect(previousButton).toBeEnabled();
				await turn(true, 1, false);
				await turn(true, 2, false);
				expect((await record('next at end (key/edge)', expectedAt(32 / columns - 1))).cfi).toBe(before.cfi);
				const endRects = [await previousButton.boundingBox(), await nextButton.boundingBox()];
				for (let i = 0; i < 2; i++) {
					expect(endRects[i]!.width).toBe(buttonRects[i]!.width);
					expect(endRects[i]!.height).toBe(buttonRects[i]!.height);
					expect(endRects[i]!.y).toBe(buttonRects[i]!.y);
				}
			}
		} finally {
			const path = testInfo.outputPath('direction-observations.json');
			writeFileSync(path, JSON.stringify({ config, observations }, null, 2));
			await testInfo.attach('direction-observations', { path, contentType: 'application/json' });
		}
	});
}

test('TOC back records the position after a queued next turn', async ({ page }) => {
	await page.setViewportSize({ width: 393, height: 850 });
	await fileApi(page, directionEpubFixture({ opfDirection: 'ltr', bodyDirection: 'ltr' }));
	await page.goto('/e2e/readers/harness.html?direction=ltr');
	await idle(page);

	const viewport = page.locator('[data-reader-viewport]');
	const spreadSelect = page.getByRole('combobox', { name: 'ページ表示方法' });
	const nextButton = page.getByRole('button', { name: '次のページへ', exact: true });
	const previousButton = page.getByRole('button', { name: '前のページへ', exact: true });
	const initial = await observe(page);
	expect(initial.visible.map((item) => item.number)).toEqual([1]);

	const selectSpread = async (name: string) => {
		await spreadSelect.click();
		await page.getByRole('option', { name, exact: true }).click({ animations: 'disabled' });
		await idle(page);
	};
	await selectSpread('常に見開き');
	const spreadStart = await observe(page);
	expect(spreadStart.visible.map((item) => item.number)).toEqual([1, 2]);

	// Capture the exact visible position reached by one real next operation, then
	// return to the start so the queued run has an independent expected result.
	await nextButton.click();
	await idle(page);
	const afterNext = await observe(page);
	expect(afterNext.visible.map((item) => item.number)).toEqual([3, 4]);
	expect(afterNext.cfi).not.toBe(spreadStart.cfi);
	await previousButton.click();
	await idle(page);
	const returnedToStart = await observe(page);
	expect(returnedToStart.cfi).toBe(spreadStart.cfi);
	expect(returnedToStart.visible.map((item) => item.number)).toEqual([1, 2]);
	await selectSpread('常に単ページ');
	const singlePageStart = await observe(page);
	expect(singlePageStart.visible.map((item) => item.number)).toEqual([1]);

	// A real spread reflow holds the serial queue while one next turn and the
	// distant TOC destination are added behind it through visible UI controls.
	await spreadSelect.click();
	await page.getByRole('option', { name: '常に見開き', exact: true }).click({ animations: 'disabled' });
	await nextButton.click();
	await expect(viewport).toHaveAttribute('data-reader-queue', '2');
	await page.getByRole('button', { name: '目次', exact: true }).click({ animations: 'disabled' });
	await page.getByRole('button', { name: 'Chapter 4', exact: true }).click({ animations: 'disabled' });
	await expect(viewport).toHaveAttribute('data-reader-queue', '3');
	await idle(page);
	const jumped = await observe(page);
	expect(jumped.visible.map((item) => item.number)).toEqual([25]);
	expect(jumped.visible[0].text).toContain('Chapter 4, column 1.');

	const backButton = page.getByRole('button', { name: '目次移動前へ戻る', exact: true });
	await expect(backButton).toBeVisible();
	await backButton.click();
	await idle(page);
	const returned = await observe(page);
	expect(returned.cfi).toBe(afterNext.cfi);
	expect(returned.visible.map((item) => item.number)).toEqual(afterNext.visible.map((item) => item.number));
	expect(returned.visible[0].text).toContain('Chapter 1, column 3.');
});
