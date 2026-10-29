import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { cleanup, render } from 'vitest-browser-react';

const mocks = vi.hoisted(() => {
	const scrollToPage = vi.fn();
	const bookmarkResult = vi.fn();
	const clearSelection = vi.fn();
	const copyToClipboard = vi.fn();
	return {
		fetchBookFile: vi.fn(),
		fetchReadingInfo: vi.fn(),
		saveReadingInfo: vi.fn(),
		scrollToPage,
		bookmarkResult,
		clearSelection,
		copyToClipboard,
		selectionProvides: { forDocument: () => ({ clear: clearSelection, copyToClipboard, getSelectedText: () => ({ toPromise: async () => ['選択したPDF本文'] }) }) },
		scrollState: { currentPage: 2, totalPages: 5 },
		viewportScrollState: { currentPage: 2, totalPages: 5 },
		useScroll: vi.fn(),
		scrollProvides: {
			scrollToPage,
			getCurrentPage: vi.fn(() => 2),
			getTotalPages: () => 5,
		},
		bookmarkProvides: {
			forDocument: () => ({ getBookmarks: () => ({ toPromise: bookmarkResult }) }),
		},
	};
});

vi.mock('@/api/books', () => ({ fetchBookFile: mocks.fetchBookFile }));
vi.mock('@/api/readingInfo', () => ({ fetchReadingInfo: mocks.fetchReadingInfo, saveReadingInfo: mocks.saveReadingInfo, registerReadingFlush: () => () => {} }));
vi.mock('react-router-dom', () => ({ Link: ({ children }: { children: React.ReactNode }) => <a href='/'>{children}</a> }));
vi.mock('@embedpdf/core', () => ({
	createPluginRegistration: (plugin: unknown, config?: unknown) => ({ plugin, config }),
}));
vi.mock('@embedpdf/core/react', () => ({
	EmbedPDF: ({ children }: { children: (value: { activeDocumentId: string }) => React.ReactNode }) => <>{children({ activeDocumentId: 'doc-1' })}</>,
}));
vi.mock('@embedpdf/engines/react', () => ({ usePdfiumEngine: () => ({ engine: {}, isLoading: false }) }));
vi.mock('@embedpdf/plugin-document-manager/react', () => ({
	DocumentManagerPluginPackage: {},
	DocumentContent: ({ children }: { children: (value: { isLoaded: boolean }) => React.ReactNode }) => <>{children({ isLoaded: true })}</>,
}));
vi.mock('@embedpdf/plugin-viewport/react', () => ({
	ViewportPluginPackage: {},
	Viewport: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('@embedpdf/plugin-scroll/react', () => ({
	ScrollPluginPackage: {},
	useScrollCapability: () => ({ provides: { onLayoutReady: (callback: (event: {documentId:string;totalPages:number}) => void) => { queueMicrotask(() => callback({documentId:'doc-1',totalPages:5})); return () => {}; }, forDocument: () => mocks.scrollProvides } }),
	ScrollStrategy: { Vertical: 'vertical' },
	useScroll: () => mocks.useScroll(),
	Scroller: ({ renderPage }: { renderPage: (value: { width: number; height: number; pageIndex: number }) => React.ReactNode }) => (
		<div>{renderPage({ width: 200, height: 300, pageIndex: 0 })}</div>
	),
}));
vi.mock('@embedpdf/plugin-scroll', () => ({ SCROLL_PLUGIN_ID: 'scroll' }));
vi.mock('@embedpdf/plugin-render/react', () => ({ RenderPluginPackage: {}, RenderLayer: () => <img alt='rendered page' src='data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==' /> }));
vi.mock('@embedpdf/plugin-zoom/react', () => ({ ZoomPluginPackage: {}, ZoomMode: { FitPage: 'fit-page' } }));
vi.mock('@embedpdf/plugin-interaction-manager/react', () => ({
	InteractionManagerPluginPackage: {},
	PagePointerProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock('@embedpdf/plugin-selection/react', () => ({
	SelectionPluginPackage: {},
	SelectionLayer: () => <div>selection</div>,
	useSelectionCapability: () => ({ provides: mocks.selectionProvides }),
}));
vi.mock('@embedpdf/plugin-commands/react', () => ({
	CommandsPluginPackage: {},
	useCommand: (id: string) => ({ visible: true, disabled: false, execute: vi.fn(), label: id === 'nav.previous' ? '<' : '>' }),
}));
vi.mock('@embedpdf/plugin-bookmark', () => ({ BookmarkPluginPackage: {} }));
vi.mock('@embedpdf/plugin-bookmark/react', () => ({
	useBookmarkCapability: () => ({
		provides: mocks.bookmarkProvides,
	}),
}));
vi.mock('./ZoomToolbar', () => ({ ZoomToolbar: () => <button type='button'>ズーム</button> }));

import PdfReader from './index';

describe('PdfReader navigation, outline, and progress', () => {
	beforeEach(() => {
		mocks.scrollState.currentPage = 2;
		mocks.scrollState.totalPages = 5;
		mocks.viewportScrollState.currentPage = 2;
		mocks.viewportScrollState.totalPages = 5;
		mocks.useScroll.mockReset();
		mocks.useScroll.mockImplementation(() => {
			const callIndex = mocks.useScroll.mock.calls.length;
			const state = callIndex % 3 === 0 ? mocks.viewportScrollState : mocks.scrollState;
			return { provides: mocks.scrollProvides, state };
		});
		mocks.fetchBookFile.mockResolvedValue(new Blob(['PDF'], { type: 'application/pdf' }));
		mocks.fetchReadingInfo.mockResolvedValue({ bookId: 7, currentPosition: '2', currentPage: 2, readStatus: 'reading' });
		mocks.saveReadingInfo.mockResolvedValue({});
		mocks.bookmarkResult.mockResolvedValue({
			bookmarks: [{
				title: '第一章',
				target: { type: 'destination', destination: { pageIndex: 0, zoom: { mode: 0 }, view: [] } },
				children: [{ title: '第一節', target: { type: 'destination', destination: { pageIndex: 3, zoom: { mode: 0 }, view: [] } } }],
			}],
		});
		mocks.scrollToPage.mockReset();
		mocks.scrollProvides.getCurrentPage.mockReturnValue(2);
		vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:pdf');
		vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
	});

	afterEach(async () => {
		await cleanup();
		vi.restoreAllMocks();
		mocks.fetchBookFile.mockReset();
		mocks.fetchReadingInfo.mockReset();
		mocks.saveReadingInfo.mockReset();
		mocks.bookmarkResult.mockReset();
		mocks.clearSelection.mockReset();
		mocks.copyToClipboard.mockReset();
	});

	test('保存ページを復元し、現在ページ・総ページ・進捗率を表示する', async () => {
		const { getByText } = await render(<PdfReader bookId={7} pageTurnDirection='ltr' />);
		await vi.waitFor(() => expect(mocks.scrollToPage).toHaveBeenCalledWith({ pageNumber: 2, behavior: 'auto' }));
		await expect.element(getByText('2 / 5 ページ 40%')).toBeInTheDocument();
		await new Promise((resolve) => setTimeout(resolve, 850));
		expect(mocks.saveReadingInfo).not.toHaveBeenCalled();
	});

	test('階層アウトラインを表示して対応ページへ移動し、目次を閉じる', async () => {
		const { getByRole } = await render(<PdfReader bookId={7} pageTurnDirection='ltr' />);
		await getByRole('button', { name: '目次' }).click();
		await expect.element(getByRole('button', { name: '第一章' })).toBeInTheDocument();
		await getByRole('button', { name: '第一節' }).click();
		expect(mocks.scrollToPage).toHaveBeenCalledWith({ pageNumber: 4, behavior: 'auto' });
		await expect.element(getByRole('button', { name: '第一節' })).not.toBeInTheDocument();
	});

	test('アウトラインがないPDFでは目次なしと案内する', async () => {
		mocks.bookmarkResult.mockResolvedValue({ bookmarks: [] });
		const { getByRole, getByText } = await render(<PdfReader bookId={7} pageTurnDirection='ltr' />);
		await getByRole('button', { name: '目次' }).click();
		await expect.element(getByText('このPDFには目次がありません。')).toBeInTheDocument();
	});

	test('LTRの右端タップは次ページへ進み、ドラッグはページ移動しない', async () => {
		const { getByTestId } = await render(<PdfReader bookId={7} pageTurnDirection='ltr' />);
		const viewport = getByTestId('pdf-viewport');
		await expect.element(viewport).toBeInTheDocument();
		const element = viewport.element() as HTMLElement;
		vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, right: 1000, bottom: 600, width: 1000, height: 600, x: 0, y: 0, toJSON: () => ({}) });
		mocks.scrollToPage.mockClear();

		element.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 1, isPrimary: true, clientX: 990, clientY: 20 }));
		element.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1, isPrimary: true, clientX: 990, clientY: 20 }));
		await vi.waitFor(() => expect(mocks.scrollToPage).toHaveBeenCalledWith({ pageNumber: 3, behavior: 'auto' }));

		mocks.scrollToPage.mockClear();
		element.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 2, isPrimary: true, clientX: 10, clientY: 20 }));
		element.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 2, isPrimary: true, clientX: 40, clientY: 20 }));
		expect(mocks.scrollToPage).not.toHaveBeenCalled();
	});

	test('端タップはツールバーと共有するページ状態から次ページを計算する', async () => {
		mocks.scrollState.currentPage = 4;
		mocks.viewportScrollState.currentPage = 2;
		const { getByTestId } = await render(<PdfReader bookId={7} pageTurnDirection='ltr' />);
		const element = getByTestId('pdf-viewport').element() as HTMLElement;
		vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, right: 1000, bottom: 600, width: 1000, height: 600, x: 0, y: 0, toJSON: () => ({}) });
		mocks.scrollToPage.mockClear();

		element.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 11, isPrimary: true, clientX: 990, clientY: 20 }));
		element.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 11, isPrimary: true, clientX: 990, clientY: 20 }));
		await vi.waitFor(() => expect(mocks.scrollToPage).toHaveBeenCalledWith({ pageNumber: 5, behavior: 'auto' }));
	});

	test('端タップ後に長押しタイマーを残さない', async () => {
		const { getByRole, getByTestId } = await render(<PdfReader bookId={7} pageTurnDirection='ltr' />);
		const element = getByTestId('pdf-viewport').element() as HTMLElement;
		vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, right: 1000, bottom: 600, width: 1000, height: 600, x: 0, y: 0, toJSON: () => ({}) });

		element.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 12, isPrimary: true, clientX: 990, clientY: 20 }));
		element.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 12, isPrimary: true, clientX: 990, clientY: 20 }));
		await new Promise((resolve) => setTimeout(resolve, 500));

		await expect.element(getByRole('button', { name: 'コピー', exact: true })).not.toBeInTheDocument();
	});

	test('RTLの左端タップは次ページへ進む', async () => {
		const { getByTestId } = await render(<PdfReader bookId={7} pageTurnDirection='rtl' />);
		await expect.element(getByTestId('pdf-viewport')).toBeInTheDocument();
		const element = getByTestId('pdf-viewport').element() as HTMLElement;
		vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, right: 1000, bottom: 600, width: 1000, height: 600, x: 0, y: 0, toJSON: () => ({}) });
		mocks.scrollToPage.mockClear();

		element.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 1, isPrimary: true, clientX: 5, clientY: 20 }));
		element.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1, isPrimary: true, clientX: 5, clientY: 20 }));
		await vi.waitFor(() => expect(mocks.scrollToPage).toHaveBeenCalledWith({ pageNumber: 3, behavior: 'auto' }));
	});

	test('長押しではコピーせず、メニューのコピー選択後にコピーモードへ入る', async () => {
		const { getByTestId, getByRole } = await render(<PdfReader bookId={7} pageTurnDirection='ltr' />);
		const element = getByTestId('pdf-viewport').element() as HTMLElement;
		vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, right: 1000, bottom: 600, width: 1000, height: 600, x: 0, y: 0, toJSON: () => ({}) });
		mocks.scrollToPage.mockClear();
		element.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 9, isPrimary: true, clientX: 500, clientY: 250 }));
		await new Promise((resolve) => setTimeout(resolve, 500));
		expect(mocks.scrollToPage).not.toHaveBeenCalled();
		await getByRole('button', { name: 'コピー', exact: true }).click();
		await expect.element(getByTestId('pdf-copy-mode-banner')).toBeInTheDocument();
		expect(mocks.clearSelection).toHaveBeenCalledTimes(1);
	});
});
