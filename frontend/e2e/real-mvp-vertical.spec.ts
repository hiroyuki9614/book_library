import { expect, test, type Frame, type Page, type Request, type Response } from '@playwright/test';
import { mkdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createHomeProgressResponseCapture } from '../src/e2e/homeProgressResponses';

const password = process.env.E2E_MVP_PASSWORD;
const adminEmail = process.env.E2E_ADMIN_EMAIL;
const adminPassword = process.env.E2E_ADMIN_PASSWORD;
const metadataPath = process.env.E2E_MVP_METADATA_PATH;
const capturePortfolioScreenshots = process.env.E2E_CAPTURE_PORTFOLIO_SCREENSHOTS === 'true';

async function captureScreenshot(page: Page, name: string) {
	if (!capturePortfolioScreenshots) return;
	const directory = resolve(process.cwd(), '../docs/images');
	mkdirSync(directory, { recursive: true });
	await page.screenshot({ path: resolve(directory, name), type: 'jpeg', quality: 82, fullPage: true });
}

if (!metadataPath || !password || !adminEmail || !adminPassword) {
	throw new Error('E2E_MVP_METADATA_PATH, E2E_MVP_PASSWORD, and admin credentials are required');
}

const metadata = JSON.parse(readFileSync(metadataPath, 'utf8')) as {
	demoEmail: string;
	pdfBookId: number;
	pdfTitle: string;
	epubBookId: number;
	epubTitle: string;
	privateBookId: number;
	privateTitle: string;
};

async function login(page: Page, email: string, loginPassword = password) {
	await page.goto('/login');
	await page.locator('input[name="userInput"]').fill(email);
	await page.locator('input[name="passwordInput"]').fill(loginPassword);
	await page.getByRole('button', { name: 'Submit' }).click();
	await expect(page).toHaveURL('/');
}

type ReadingInfoBody = {
	bookId: number;
	currentPosition: string | null;
	currentPage: number;
	progressPercentage: number | null;
	readStatus: string;
};

async function patchReadingInfo(page: Page, bookId: number, body: { currentPage: number; progressPercentage?: number | null; readStatus?: 'reading' | 'completed' }) {
	return page.evaluate(async ({ bookId, body }) => {
		const response = await fetch(`http://localhost:3000/api/v1/books/${bookId}/reading-info`, {
			credentials: 'include',
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body),
		});
		return { status: response.status, body: await response.json() as ReadingInfoBody };
	}, { bookId, body });
}

async function readReadingInfo(page: Page, bookId: number) {
	return page.evaluate(async ({ bookId }) => {
		const response = await fetch(`http://localhost:3000/api/v1/books/${bookId}/reading-info`, { credentials: 'include' });
		return { status: response.status, body: await response.json() as ReadingInfoBody };
	}, { bookId });
}

async function assertHomeProgress(page: Page, bookId: number, title: string, expected: 'measured' | 'unmeasured' = 'measured', navigation: 'home' | 'reload' = 'home') {
	const capture = createHomeProgressResponseCapture((request) =>
		request.isNavigationRequest() && request.frame() === page.mainFrame());
	const onRequest = (request: Request) => capture.onRequest(request);
	const onResponse = (response: Response) => capture.onResponse(response);
	const onFrameNavigated = (frame: Frame) => {
		if (frame === page.mainFrame()) capture.onMainFrameNavigated();
	};
	page.on('request', onRequest);
	page.on('response', onResponse);
	page.on('framenavigated', onFrameNavigated);
	try {
		if (navigation === 'reload') await page.reload();
		else await page.goto('/');
		await expect.poll(() => capture.pages.some(({ requestedPage }) => requestedPage === 1), { timeout: 30_000 }).toBe(true);
		const firstPage = capture.pages.find(({ requestedPage }) => requestedPage === 1)!;
		const firstUrl = new URL(firstPage.url);
		expect(firstUrl.origin, 'Home shelf list must come from the configured local backend').toBe('http://localhost:3000');
		expect(firstPage.status, 'Books page 1 must return HTTP 200').toBe(200);
		expect(firstPage.body.page, 'Books response page must match its request').toBe(1);
		const total = firstPage.body.total;
		expect(total).toBeGreaterThan(0);
		const expectedPages = Math.ceil(total / 100);
		await expect.poll(() => new Set(capture.pages.map(({ requestedPage }) => requestedPage)).size, { timeout: 30_000 }).toBe(expectedPages);
		const capturedPages = capture.pages;
		expect(capturedPages).toHaveLength(expectedPages);
		const listPages = capturedPages.map(({ requestedPage, url, status, body }) => {
			expect(new URL(url).origin, `Books page ${requestedPage} must come from the configured local backend`).toBe('http://localhost:3000');
			expect(status, `Books page ${requestedPage} must return HTTP 200`).toBe(200);
			expect(body.page, 'Books response page must match its request').toBe(requestedPage);
			return body;
		});

		listPages.sort((a, b) => a.page - b.page);
		expect(listPages.map((result) => result.page)).toEqual(Array.from({ length: expectedPages }, (_, index) => index + 1));
		expect(listPages.every((result) => result.total === total)).toBe(true);
		const books = listPages.flatMap((result) => result.books);
		expect(books).toHaveLength(total);
		expect(new Set(books.map((book) => book.id)).size, 'Books list must not contain duplicate IDs across API pages').toBe(total);
		const savedBook = books.find((book) => book.id === bookId);
		expect(savedBook, `${title} must be present in the authenticated Home list`).toBeDefined();
		expect(savedBook!.title).toBe(title);
		if (expected === 'unmeasured') {
			expect(savedBook!.progressPercentage, `${title} must remain server-reported as unmeasured`).toBeNull();
		} else {
			expect(typeof savedBook!.progressPercentage).toBe('number');
			expect(Number.isFinite(savedBook!.progressPercentage)).toBe(true);
		}
		const measured = books
			.map((book) => book.progressPercentage)
			.filter((progress): progress is number => typeof progress === 'number' && Number.isFinite(progress));
		const averageLabel = measured.length === 0
			? `未計測（計測済み0/${total}冊）`
			: `${Math.round(measured.reduce((sum, progress) => sum + progress, 0) / measured.length)}%（計測済み${measured.length}/${total}冊）`;

		await expect(page.getByText(averageLabel, { exact: true }).first()).toBeVisible();
		await page.getByRole('textbox', { name: '書籍を検索' }).fill(title);
		const row = page.getByRole('link', { name: `${title}を読む` });
		if (expected === 'unmeasured') {
			await expect(row.getByText('未計測', { exact: true })).toBeVisible();
			await expect(row.getByRole('progressbar')).toHaveCount(0);
		} else {
			const savedProgress = savedBook!.progressPercentage as number;
			await expect(row.getByText(`${Math.round(savedProgress)}%`, { exact: true })).toBeVisible();
			await expect(row.getByRole('progressbar', { name: `${title} の読書進捗` }))
				.toHaveAttribute('aria-valuenow', String(Math.round(savedProgress)));
		}
	} finally {
		page.off('request', onRequest);
		page.off('response', onResponse);
		page.off('framenavigated', onFrameNavigated);
		capture.dispose();
	}
}

async function assertHomeProgressAcrossSession(page: Page, bookId: number, title: string) {
	await page.setViewportSize({ width: 393, height: 851 });
	await assertHomeProgress(page, bookId, title);
	await assertHomeProgress(page, bookId, title, 'measured', 'reload');
	await page.getByRole('button', { name: 'Toggle Sidebar' }).click();
	await page.getByRole('button', { name: 'ログアウト', exact: true }).click();
	await expect(page).toHaveURL('/login');
	await login(page, metadata.demoEmail);
	await assertHomeProgress(page, bookId, title);
}

test('demo user sees only public EPUB/PDF and cannot access a private book or admin API', async ({ page }) => {
	await login(page, metadata.demoEmail);
	await expect(page.getByText(metadata.pdfTitle, { exact: true })).toBeVisible();
	await expect(page.getByText(metadata.epubTitle, { exact: true })).toBeVisible();
	await expect(page.getByText(metadata.privateTitle, { exact: true })).toHaveCount(0);
	await captureScreenshot(page, 'portfolio-demo-shelf.jpg');

	const statuses = await page.evaluate(async ({ privateBookId }) => {
		const options = { credentials: 'include' as const };
		const detail = await fetch(`http://localhost:3000/api/v1/books/${privateBookId}`, options);
		const file = await fetch(`http://localhost:3000/api/v1/books/${privateBookId}/file`, options);
		const readingGet = await fetch(`http://localhost:3000/api/v1/books/${privateBookId}/reading-info`, options);
		const readingPatch = await fetch(`http://localhost:3000/api/v1/books/${privateBookId}/reading-info`, {
			...options,
			method: 'PATCH',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ currentPage: 1 }),
		});
		const admin = await fetch('http://localhost:3000/api/v1/admin/books', options);
		const search = await fetch(`http://localhost:3000/api/v1/books?q=${encodeURIComponent('Private Existing Book')}`, options);
		return { detail: detail.status, file: file.status, readingGet: readingGet.status, readingPatch: readingPatch.status, admin: admin.status, search: await search.json() };
	}, { privateBookId: metadata.privateBookId });

	expect(statuses).toMatchObject({ detail: 403, file: 403, readingGet: 403, readingPatch: 403, admin: 403 });
	expect(statuses.search).toMatchObject({ total: 0, books: [] });
});

test('demo user reads protected PDF and restores its saved page', async ({ page }) => {
	await page.setViewportSize({ width: 393, height: 851 });
	await login(page, metadata.demoEmail);
	const readingInfoPath = `/api/v1/books/${metadata.pdfBookId}/reading-info`;
	const prepared = await patchReadingInfo(page, metadata.pdfBookId, { currentPage: 2, readStatus: 'completed' });
	expect(prepared.status).toBe(200);
	expect(prepared.body).toMatchObject({
		bookId: metadata.pdfBookId,
		currentPosition: '2',
		currentPage: 2,
		progressPercentage: 100,
		readStatus: 'completed',
	});
	const openReadingInfoResponse = page.waitForResponse((response) => response.url().endsWith(readingInfoPath)
		&& response.request().method() === 'GET');
	const fileResponse = page.waitForResponse((response) => response.url().endsWith(`/api/v1/books/${metadata.pdfBookId}/file`));
	await page.getByText(metadata.pdfTitle, { exact: true }).click();
	await expect(page).toHaveURL(`/reader/${metadata.pdfBookId}`);
	expect((await fileResponse).headers()['content-type']).toContain('application/pdf');
	const openReadingInfo = await openReadingInfoResponse;
	const openReadingInfoBody = await openReadingInfo.json() as ReadingInfoBody;
	expect(openReadingInfo.status()).toBe(200);
	expect(openReadingInfoBody).toMatchObject({
		bookId: metadata.pdfBookId,
		currentPosition: '2',
		currentPage: 2,
		progressPercentage: 100,
		readStatus: 'completed',
	});

	const mobileControls = (await page.evaluate(() => innerWidth)) < 768;
	const pdfFooter = page.getByTestId('pdf-reader-footer');
	const footerPageInput = pdfFooter.getByRole('spinbutton');
	const pdfFooterProgress = pdfFooter.locator('output[aria-live="polite"]');
	const operationMenu = page.getByRole('dialog', { name: '操作メニュー' });
	const menuPageInput = operationMenu.getByRole('spinbutton');
	const menuProgress = operationMenu.locator('output[aria-live="polite"]');
	await expect(page.getByText('PDF本文を描画しています…')).toHaveCount(0, { timeout: 30_000 });
	await expect(page.getByRole('alert')).toHaveCount(0);
	const viewport = page.getByTestId('pdf-viewport');
	await expect(viewport).toBeVisible({ timeout: 30_000 });
	const secondPdfPage = page.locator('[data-pdf-page="2"]');
	await expect(secondPdfPage).toBeVisible({ timeout: 30_000 });
	const shown = () => page.evaluate(() => {
		const viewportElement = document.querySelector<HTMLElement>('[data-testid="pdf-viewport"]');
		if (!viewportElement) return undefined;
		const viewport = viewportElement.getBoundingClientRect();
		return [...document.querySelectorAll<HTMLElement>('[data-pdf-page]')].map(el => {
			const r = el.getBoundingClientRect();
			return { page: Number(el.dataset.pdfPage), area: Math.max(0, Math.min(r.bottom, viewport.bottom) - Math.max(r.top, viewport.top)) * Math.max(0, Math.min(r.right, viewport.right) - Math.max(r.left, viewport.left)) };
		}).sort((a, b) => b.area - a.area)[0]?.page;
	});
	await expect.poll(shown).toBe(2);
	await expect(page.locator('[data-pdf-page="2"] img')).toBeVisible();
	if (mobileControls) {
		await page.getByRole('button', { name: '操作メニューを開く', exact: true }).click();
		await expect(operationMenu.getByRole('heading', { name: '操作メニュー', exact: true })).toBeVisible();
		await expect(menuPageInput).toHaveValue('2', { timeout: 30_000 });
		await expect(menuProgress).toHaveText('2 / 2 ページ 100%');
		await operationMenu.getByRole('button', { name: '前のページへ', exact: true }).click();
		await expect(menuPageInput).toHaveValue('1');
		await page.getByRole('button', { name: '閉じる', exact: true }).click();
	} else {
		await expect(footerPageInput).toHaveValue('2', { timeout: 30_000 });
		await pdfFooter.getByRole('button', { name: '前のページへ', exact: true }).click();
		await expect(footerPageInput).toHaveValue('1');
	}
	if (mobileControls) {
		for (const width of [320, 393]) {
			await page.setViewportSize({ width, height: 851 });
			await page.getByRole('button', { name: '操作メニューを開く', exact: true }).click();
			await expect(operationMenu.getByRole('heading', { name: '操作メニュー', exact: true })).toBeVisible();
			await expect(menuPageInput).toHaveValue('1', { timeout: 30_000 });
			await expect(menuProgress).toHaveText('1 / 2 ページ 50%');
			await expect(pdfFooter).toHaveAttribute('aria-hidden', 'true');
			await expect(pdfFooter).toHaveAttribute('inert');
			await expect(menuPageInput).toBeEnabled();
			await expect(operationMenu.getByRole('button', { name: '前のページへ', exact: true })).toBeDisabled();
			await expect(operationMenu.getByRole('button', { name: '次のページへ', exact: true })).toBeEnabled();
			await page.getByRole('button', { name: '閉じる', exact: true }).click();
			await expect(pdfFooter).not.toHaveAttribute('aria-hidden');
			await expect(pdfFooter).not.toHaveAttribute('inert');
			await expect(footerPageInput).toHaveValue('1');
			await expect(pdfFooterProgress).toHaveText('1 / 2 ページ 50%');
			await expect(pdfFooter.getByRole('button', { name: '前のページへ', exact: true })).toBeDisabled();
			await expect(pdfFooter.getByRole('button', { name: '次のページへ', exact: true })).toBeEnabled();
		}
		await page.getByRole('button', { name: '操作メニューを開く', exact: true }).click();
		await expect(operationMenu.getByRole('heading', { name: '操作メニュー', exact: true })).toBeVisible();
	}
	if (!mobileControls) {
		await expect(footerPageInput).toHaveValue('1', { timeout: 30_000 });
		await expect(pdfFooterProgress).toBeVisible();
		await expect(pdfFooterProgress).toHaveText('1 / 2 ページ 50%');
	}
	await page.getByRole('button', { name: '目次' }).click();
	await expect(page.getByRole('button', { name: 'First chapter' })).toBeVisible();
	await page.getByRole('button', { name: 'First chapter' }).click();
	if (mobileControls) {
		await page.getByRole('button', { name: '閉じる', exact: true }).click();
	}
	await expect(page.getByRole('dialog')).toBeHidden();

	const saveResponse = page.waitForResponse((response) => response.url().endsWith(readingInfoPath)
		&& response.request().method() === 'PATCH'
		&& response.request().postDataJSON()?.currentPage === 2
		&& response.request().postDataJSON()?.readStatus === 'completed');
	await expect.poll(shown).toBe(1);
	const firstImage = await page.locator('[data-pdf-page="1"] img').screenshot();
	const viewportBounds = await viewport.boundingBox();
	expect(viewportBounds).not.toBeNull();
	if (mobileControls) {
		await page.getByRole('button', { name: '操作メニューを開く', exact: true }).click();
		await expect(operationMenu.getByRole('heading', { name: '操作メニュー', exact: true })).toBeVisible();
		await operationMenu.getByRole('button', { name: '次のページへ', exact: true }).click();
	} else {
		// Locator hit testing waits until the closing TOC overlay releases pointer input.
		// Stay inside the page content rather than the vertical scrollbar gutter;
		// this is still within the rightmost 15% edge-tap zone.
		await viewport.click({ position: { x: viewportBounds!.width * 0.9, y: viewportBounds!.height / 2 } });
	}
	if (mobileControls) await page.getByRole('button', { name: '閉じる', exact: true }).click();
	if (mobileControls) {
		await page.getByRole('button', { name: '操作メニューを開く', exact: true }).click();
		await expect(operationMenu.getByRole('heading', { name: '操作メニュー', exact: true })).toBeVisible();
		await expect(menuPageInput).toHaveValue('2');
		await expect(menuProgress).toHaveText('2 / 2 ページ 100%');
		await expect(pdfFooter).toHaveAttribute('aria-hidden', 'true');
		await expect(pdfFooter).toHaveAttribute('inert');
	} else {
		await expect(footerPageInput).toHaveValue('2');
	}
	await expect.poll(shown).toBe(2);
	expect((await page.locator('[data-pdf-page="2"] img').screenshot()).equals(firstImage)).toBe(false);
	if (mobileControls) await page.getByRole('button', { name: '閉じる', exact: true }).click();
	await expect(pdfFooter).not.toHaveAttribute('aria-hidden');
	await expect(pdfFooter).not.toHaveAttribute('inert');
	await expect(footerPageInput).toHaveValue('2');
	await expect(pdfFooterProgress).toBeVisible();
	await expect(pdfFooterProgress).toHaveText('2 / 2 ページ 100%');
	await expect(pdfFooter.getByRole('button', { name: '前のページへ', exact: true })).toBeEnabled();
	await expect(pdfFooter.getByRole('button', { name: '次のページへ', exact: true })).toBeDisabled();
	const savedResponse = await saveResponse;
	expect(savedResponse.request().postDataJSON()).toEqual({
		currentPage: 2,
		readStatus: 'completed',
		progressPercentage: 100,
		expectedSaveSequence: expect.stringMatching(/^\\d+$/),
		saveSequence: expect.stringMatching(/^[1-9]\d*$/),
	});
	expect(savedResponse.status()).toBe(200);
	expect(await savedResponse.json()).toMatchObject({
		bookId: metadata.pdfBookId,
		currentPosition: '2',
		currentPage: 2,
		progressPercentage: 100,
		readStatus: 'completed',
	});

	const latePatchRequests: Array<{
		request: Request;
		currentPage: unknown;
		progressPercentage: unknown;
		readStatus: unknown;
	}> = [];
	let monitorResizePatches = false;
	const monitorLatePatches = (request: Request) => {
		if (!monitorResizePatches || request.method() !== 'PATCH' || !request.url().endsWith(readingInfoPath)) return;
		const body = request.postDataJSON() as { currentPage?: unknown; progressPercentage?: unknown; readStatus?: unknown };
		latePatchRequests.push({ request, currentPage: body.currentPage, progressPercentage: body.progressPercentage, readStatus: body.readStatus });
	};
	page.on('request', monitorLatePatches);
	monitorResizePatches = true;
	await page.setViewportSize({ width: 851, height: 393 });
	await expect(viewport).toBeVisible();
	await expect.poll(shown).toBe(2);
	// Unmounting the reader flushes any pending debounced save. Await every
	// observed PATCH and then perform an authenticated server read before any
	// reopen, so a failed or late save cannot be hidden by a reload.
	await page.getByRole('link', { name: '本棚に戻る', exact: true }).click();
	await expect(page).toHaveURL('/');
	const latePatchResponses = await Promise.all(latePatchRequests.map(async (patch) => {
		const response = await patch.request.response();
		let responseBodyDecoded = false;
		const body = response
			? await response.json().then((value: unknown) => {
				responseBodyDecoded = true;
				return value as { currentPage?: unknown; progressPercentage?: unknown; readStatus?: unknown } | null;
			}).catch(() => null)
			: null;
		return {
			requestCurrentPage: patch.currentPage,
			requestProgressPercentage: patch.progressPercentage,
			requestReadStatus: patch.readStatus,
			status: response?.status() ?? null,
			responseBodyDecoded,
			responseCurrentPage: body?.currentPage ?? null,
			responseProgressPercentage: body?.progressPercentage ?? null,
			responseReadStatus: body?.readStatus ?? null,
		};
	}));
	page.off('request', monitorLatePatches);
	monitorResizePatches = false;
	const finalServerRead = await readReadingInfo(page, metadata.pdfBookId);
	expect({
		status: finalServerRead.status,
		currentPosition: finalServerRead.body.currentPosition,
		currentPage: finalServerRead.body.currentPage,
		progressPercentage: finalServerRead.body.progressPercentage,
		readStatus: finalServerRead.body.readStatus,
		latePatches: latePatchResponses,
	}, 'Final server read must confirm persisted page 2 before evaluating the reopened PDF restore.')
		.toMatchObject({ status: 200, currentPosition: '2', currentPage: 2, progressPercentage: 100, readStatus: 'completed' });
	expect(latePatchResponses.every((patch) => patch.requestCurrentPage === 2
		&& patch.requestProgressPercentage === 100
		&& patch.status === 200
		&& (!patch.responseBodyDecoded || (patch.responseCurrentPage === 2 && patch.responseProgressPercentage === 100 && patch.responseReadStatus === 'completed'))),
	`ReadingInfo PATCH calls after resize must not overwrite page 2; observed ${JSON.stringify(latePatchResponses)}`)
		.toBe(true);
	const reopenReadingInfoResponse = page.waitForResponse((response) => response.url().endsWith(readingInfoPath)
		&& response.request().method() === 'GET');
	await page.goto(`/reader/${metadata.pdfBookId}`);
	const reopenReadingInfo = await reopenReadingInfoResponse;
	const reopenReadingInfoBody = await reopenReadingInfo.json() as ReadingInfoBody;
	expect(reopenReadingInfo.status()).toBe(200);
	expect(reopenReadingInfoBody).toMatchObject({
		bookId: metadata.pdfBookId,
		currentPosition: '2',
		currentPage: 2,
		progressPercentage: 100,
		readStatus: 'completed',
	});
	await expect(page.getByText('PDF本文を描画しています…')).toHaveCount(0, { timeout: 30_000 });
	await expect(page.getByRole('alert')).toHaveCount(0);
	await expect(footerPageInput).toHaveValue('2', { timeout: 30_000 });
	await expect.poll(shown).toBe(2);
	await expect(page.locator('[data-pdf-page="2"] img')).toBeVisible();
	const reloadReadingInfoResponse = page.waitForResponse((response) => response.url().endsWith(readingInfoPath)
		&& response.request().method() === 'GET');
	await page.reload();
	const reloadResponse = await reloadReadingInfoResponse;
	const reloadBody = await reloadResponse.json() as ReadingInfoBody;
	expect(reloadResponse.status()).toBe(200);
	expect(reloadBody).toMatchObject({
		bookId: metadata.pdfBookId,
		currentPosition: '2',
		currentPage: 2,
		progressPercentage: 100,
		readStatus: 'completed',
	});
	await expect(page.getByText('PDF本文を描画しています…')).toHaveCount(0, { timeout: 30_000 });
	await expect(page.getByRole('alert')).toHaveCount(0);
	await expect(footerPageInput).toHaveValue('2', { timeout: 30_000 });
	await expect.poll(shown).toBe(2);
	await expect(page.locator('[data-pdf-page="2"] img')).toBeVisible();
	await assertHomeProgressAcrossSession(page, metadata.pdfBookId, metadata.pdfTitle);
});

test('demo user opens protected EPUB and persists/restores a CFI', async ({ page }) => {
	await page.setViewportSize({ width: 393, height: 851 });
	await login(page, metadata.demoEmail);
	const unmeasured = await patchReadingInfo(page, metadata.epubBookId, { currentPage: 1, progressPercentage: null, readStatus: 'reading' });
	expect(unmeasured.status).toBe(200);
	expect(unmeasured.body).toMatchObject({
		bookId: metadata.epubBookId,
		currentPosition: '1',
		currentPage: 1,
		progressPercentage: null,
		readStatus: 'reading',
	});
	await assertHomeProgress(page, metadata.epubBookId, metadata.epubTitle, 'unmeasured');
	const readingInfoPath = `/api/v1/books/${metadata.epubBookId}/reading-info`;
	const openReadingInfoResponse = page.waitForResponse((response) => response.url().endsWith(readingInfoPath)
		&& response.request().method() === 'GET');
	const fileResponse = page.waitForResponse((response) => response.url().endsWith(`/api/v1/books/${metadata.epubBookId}/file`));
	await page.getByText(metadata.epubTitle, { exact: true }).click();
	await expect(page).toHaveURL(`/reader/${metadata.epubBookId}`);
	expect((await fileResponse).headers()['content-type']).toContain('application/epub+zip');
	const openReadingInfo = await openReadingInfoResponse;
	const openReadingInfoBody = await openReadingInfo.json() as ReadingInfoBody;
	expect(openReadingInfo.status()).toBe(200);
	expect(openReadingInfoBody).toMatchObject({
		bookId: metadata.epubBookId,
		currentPosition: '1',
		currentPage: 1,
		progressPercentage: null,
		readStatus: 'reading',
	});
	await expect(page.getByRole('button', { name: '目次' })).toBeVisible({ timeout: 30_000 });
	await expect(page.getByTestId('epub-reader-footer').getByText(/本文位置: \d+ \/ \d+/)).toBeVisible();
	await expect(page.getByRole('alert')).toHaveCount(0);
	await page.getByRole('button', { name: '目次' }).click();
	await expect(page.getByRole('button', { name: '第一節' })).toBeVisible();
	await page.getByRole('button', { name: '第一章' }).click();
	await expect(page.getByRole('button', { name: '目次移動前へ戻る' })).toBeVisible();
	await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy','false');
	await expect(page.frameLocator('[data-publication-pane="primary"] iframe').first().getByRole('heading', { name: '第一章' })).toBeVisible();

	const epubBody = page.frameLocator('[data-publication-pane="primary"] iframe').first().locator('body');
	const epubBounds = await epubBody.boundingBox();
	expect(epubBounds).not.toBeNull();
	const saveResponse = page.waitForResponse((response) => response.url().endsWith(`/api/v1/books/${metadata.epubBookId}/reading-info`)
		&& response.request().method() === 'PATCH'
		&& response.request().postDataJSON()?.readStatus === 'completed');
	await epubBody.click({ position: { x: epubBounds!.width - 5, y: epubBounds!.height / 2 } });
	await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy','false');
	await expect(page.frameLocator('[data-publication-pane="primary"] iframe').first().getByRole('heading', { name: '第二章' })).toBeVisible();
	expect((await saveResponse).status()).toBe(200);

	const result = await readReadingInfo(page, metadata.epubBookId);
	expect(result.status).toBe(200);
	expect(result.body).toMatchObject({ bookId: metadata.epubBookId, currentPage: 1, progressPercentage: 100, readStatus: 'completed' });
	expect(result.body.currentPosition).toMatch(/^epubcfi\(/);
	await expect(page.getByText(new RegExp(`${result.body.progressPercentage}%`))).toBeVisible();
	await page.setViewportSize({ width: 851, height: 393 });
	const reloadReadingInfoResponse = page.waitForResponse((response) => response.url().endsWith(readingInfoPath)
		&& response.request().method() === 'GET');
	await page.evaluate(() => {
		for (let index = localStorage.length - 1; index >= 0; index -= 1) {
			const key = localStorage.key(index);
			if (key?.startsWith('reader-location:')) localStorage.removeItem(key);
		}
	});
	await page.reload();
	const reloadReadingInfo = await reloadReadingInfoResponse;
	const reloadReadingInfoBody = await reloadReadingInfo.json() as ReadingInfoBody;
	expect(reloadReadingInfo.status()).toBe(200);
	expect(reloadReadingInfoBody).toMatchObject({
		bookId: metadata.epubBookId,
		currentPosition: result.body.currentPosition,
		currentPage: result.body.currentPage,
		progressPercentage: result.body.progressPercentage,
		readStatus: result.body.readStatus,
	});
	await expect(page.getByRole('button', { name: '目次' })).toBeVisible({ timeout: 30_000 });
	await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy','false');
	await expect(page.getByRole('alert')).toHaveCount(0);
	await expect(page.frameLocator('[data-publication-pane="primary"] iframe').first().getByRole('heading', { name: '第二章' })).toBeVisible();
	await assertHomeProgressAcrossSession(page, metadata.epubBookId, metadata.epubTitle);
});


test('B reader desktop chrome stays inside the viewport without overlapping controls', async ({ page }) => {
	await page.setViewportSize({ width: 1280, height: 900 });
	await login(page, metadata.demoEmail);

	for (const reader of [
		{ kind: 'EPUB', bookId: metadata.epubBookId, footerTestId: 'epub-reader-footer' },
		{ kind: 'PDF', bookId: metadata.pdfBookId, footerTestId: 'pdf-reader-footer' },
	] as const) {
		await page.goto(`/reader/${reader.bookId}`);
		const footer = page.getByTestId(reader.footerTestId);
		await expect(footer).toBeVisible({ timeout: 45_000 });
		if (reader.kind === 'EPUB') {
			await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy', 'false', { timeout: 45_000 });
		} else {
			await expect(page.getByTestId('pdf-viewport')).toBeVisible();
			await expect(page.getByText('PDF本文を描画しています…')).toHaveCount(0, { timeout: 45_000 });
		}

		for (const width of [1280, 1440, 1663]) {
			await page.setViewportSize({ width, height: 900 });
			await expect(page.getByRole('button', { name: '目次', exact: true })).toBeVisible();
			await expect(page.getByRole('button', { name: '表示設定', exact: true })).toBeVisible();
			// Wait for responsive layout/resize observers, without reading internal reader state.
			await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
			const layout = await footer.evaluate((footerElement) => {
				const root = footerElement.parentElement!;
				const header = root.querySelector('header')!;
				const main = root.querySelector('main')!;
				const box = (element: Element) => {
					const r = element.getBoundingClientRect();
					return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height };
				};
				const headerBox = box(header);
				const mainBox = box(main);
				const footerBox = box(footerElement);
				const findOverlaps = (area: Element) => {
					const controls = [...area.querySelectorAll<HTMLElement>('a, button, input')].map((element) => ({
						name: element.getAttribute('aria-label') || element.textContent?.trim().slice(0, 40) || element.tagName,
						box: box(element),
					})).filter(({ box: r }) => r.width > 0 && r.height > 0);
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
					return collisions;
				};
				return {
					viewportWidth: window.innerWidth,
					viewportHeight: window.innerHeight,
					pageScrollWidth: document.documentElement.scrollWidth,
					headerBox,
					mainBox,
					footerBox,
					footerScrollWidth: footerElement.scrollWidth,
					footerClientWidth: footerElement.clientWidth,
					headerOverlaps: findOverlaps(header),
					footerOverlaps: findOverlaps(footerElement),
				};
			});
			expect(layout.pageScrollWidth, `${reader.kind} ${width}px document horizontally overflowed`).toBeLessThanOrEqual(width + 2);
			expect(layout.footerScrollWidth, `${reader.kind} ${width}px footer horizontally overflowed`).toBeLessThanOrEqual(layout.footerClientWidth + 2);
			expect(layout.headerOverlaps, `${reader.kind} ${width}px header controls collided`).toEqual([]);
			expect(layout.footerOverlaps, `${reader.kind} ${width}px footer controls collided`).toEqual([]);
			expect(layout.mainBox.top, `${reader.kind} ${width}px main overlaps header`).toBeGreaterThanOrEqual(layout.headerBox.bottom - 2);
			expect(layout.footerBox.top, `${reader.kind} ${width}px footer overlaps main`).toBeGreaterThanOrEqual(layout.mainBox.bottom - 2);
			expect(layout.mainBox.height, `${reader.kind} ${width}px reading surface too small`).toBeGreaterThan(400);
			expect(layout.footerBox.bottom, `${reader.kind} ${width}px footer outside screen`).toBeLessThanOrEqual(layout.viewportHeight + 2);
		}
	}
});

test('B reader EPUB mobile chrome stays inside the viewport without overlapping controls', async ({ page }) => {
	await page.setViewportSize({ width: 393, height: 851 });
	await login(page, metadata.demoEmail);
	await page.goto(`/reader/${metadata.epubBookId}`);

	const footer = page.getByTestId('epub-reader-footer');
	await expect(footer).toBeVisible({ timeout: 45_000 });
	await expect(page.locator('[data-reader-viewport]')).toHaveAttribute('aria-busy', 'false', { timeout: 45_000 });

	for (const [width, height] of [[320, 640], [393, 851]] as const) {
		await page.setViewportSize({ width, height });
		await expect(page.getByRole('button', { name: '目次', exact: true })).toBeVisible();
		await expect(page.getByRole('button', { name: '表示設定', exact: true })).toBeVisible();
		await expect(footer.getByRole('combobox', { name: 'ページ表示方法' })).toBeVisible();
		// Wait for responsive layout/resize observers, without reading internal reader state.
		await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
		const layout = await footer.evaluate((footerElement) => {
			const root = footerElement.parentElement!;
			const box = (element: Element) => {
				const r = element.getBoundingClientRect();
				return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height };
			};
			const headerElement = root.querySelector('header')!;
			const mainElement = root.querySelector('main')!;
			const headerBox = box(headerElement);
			const mainBox = box(mainElement);
			const footerBox = box(footerElement);
			const findOverlaps = (area: Element) => {
				const controls = [...area.querySelectorAll<HTMLElement>('a, button, input')].map((element) => ({
					name: element.getAttribute('aria-label') || element.textContent?.trim().slice(0, 40) || element.tagName,
					box: box(element),
				})).filter(({ box: r }) => r.width > 0 && r.height > 0);
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
				return collisions;
			};
			return {
				viewportWidth: window.innerWidth,
				viewportHeight: window.innerHeight,
				pageScrollWidth: document.documentElement.scrollWidth,
				headerBox,
				mainBox,
				footerBox,
				headerScrollWidth: headerElement.scrollWidth,
				headerClientWidth: headerElement.clientWidth,
				footerScrollWidth: footerElement.scrollWidth,
				footerClientWidth: footerElement.clientWidth,
				headerOverlaps: findOverlaps(headerElement),
				footerOverlaps: findOverlaps(footerElement),
			};
		});
		expect(layout.pageScrollWidth, `EPUB ${width}px document horizontally overflowed`).toBeLessThanOrEqual(width + 2);
		expect(layout.headerScrollWidth, `EPUB ${width}px header horizontally overflowed`).toBeLessThanOrEqual(layout.headerClientWidth + 2);
		expect(layout.footerScrollWidth, `EPUB ${width}px footer horizontally overflowed`).toBeLessThanOrEqual(layout.footerClientWidth + 2);
		expect(layout.headerOverlaps, `EPUB ${width}px header controls collided`).toEqual([]);
		expect(layout.footerOverlaps, `EPUB ${width}px footer controls collided`).toEqual([]);
		expect(layout.mainBox.top, `EPUB ${width}px main overlaps header`).toBeGreaterThanOrEqual(layout.headerBox.bottom - 2);
		expect(layout.footerBox.top, `EPUB ${width}px footer overlaps main`).toBeGreaterThanOrEqual(layout.mainBox.bottom - 2);
		expect(layout.mainBox.left, `EPUB ${width}px main starts outside the viewport`).toBeGreaterThanOrEqual(-2);
		expect(layout.mainBox.right, `EPUB ${width}px main ends outside the viewport`).toBeLessThanOrEqual(width + 2);
		expect(layout.mainBox.height, `EPUB ${width}px reading surface too small`).toBeGreaterThanOrEqual(0.60 * layout.viewportHeight);
		expect(layout.footerBox.bottom, `EPUB ${width}px footer outside screen`).toBeLessThanOrEqual(layout.viewportHeight + 2);

		await page.getByRole('button', { name: '表示設定', exact: true }).click();
		const settings = page.getByRole('heading', { name: '表示設定', exact: true });
		await expect(settings).toBeVisible();
		const settingsSpread = page.getByRole('combobox', { name: 'ページ表示方法' });
		await expect(settingsSpread).toBeVisible();
		await settingsSpread.click();
		await expect(page.getByRole('option', { name: '常に単ページ', exact: true })).toBeVisible();
		await page.keyboard.press('Escape');
		await page.keyboard.press('Escape');
		await expect(settings).toBeHidden();

		await page.getByRole('button', { name: '目次', exact: true }).click();
		await expect(page.getByRole('navigation', { name: 'EPUB目次' })).toBeVisible();
		await page.keyboard.press('Escape');
	}
});

test('admin sees private and demo books', async ({ page }) => {
	await login(page, adminEmail, adminPassword);
	await expect(page.getByText(metadata.pdfTitle, { exact: true })).toBeVisible();
	await expect(page.getByText(metadata.epubTitle, { exact: true })).toBeVisible();
	await expect(page.getByText(metadata.privateTitle, { exact: true })).toBeVisible();
});

test('unauthenticated browser receives 401 and public signup is disabled', async ({ page }) => {
	await page.goto('/login');
	await captureScreenshot(page, 'portfolio-demo-login.jpg');
	const statuses = await page.evaluate(async ({ privateBookId }) => {
		const list = await fetch('http://localhost:3000/api/v1/books', { credentials: 'include' });
		const detail = await fetch(`http://localhost:3000/api/v1/books/${privateBookId}`, { credentials: 'include' });
		const signup = await fetch('http://localhost:3000/api/auth/sign-up/email', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ email: 'blocked@example.com', name: 'Blocked', password: 'blocked-password' }),
		});
		return { list: list.status, detail: detail.status, signup: signup.status };
	}, { privateBookId: metadata.privateBookId });
	expect(statuses).toEqual({ list: 401, detail: 401, signup: 400 });
	await page.goto(`/reader/${metadata.privateBookId}`);
	await expect(page).toHaveURL('/login');
});
