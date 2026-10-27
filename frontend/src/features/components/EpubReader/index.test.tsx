import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';

const mocks = vi.hoisted(() => ({
	fetchBookFile: vi.fn(),
	fetchReadingInfo: vi.fn(),
	saveReadingInfo: vi.fn(),
	ePub: vi.fn(),
}));

vi.mock('@/api/books', () => ({ fetchBookFile: mocks.fetchBookFile }));
vi.mock('@/api/readingInfo', () => ({
	fetchReadingInfo: mocks.fetchReadingInfo,
	saveReadingInfo: mocks.saveReadingInfo,
}));
vi.mock('epubjs', () => ({ default: mocks.ePub }));
vi.mock('react-router-dom', () => ({
	Link: ({ children }: { children: React.ReactNode }) => <a href='/'>{children}</a>,
}));

import EpubReader from './index';

function createEpubRuntime() {
	const rendition = {
		on: vi.fn(),
		off: vi.fn(),
		display: vi.fn().mockResolvedValue(undefined),
		destroy: vi.fn(),
		prev: vi.fn(),
		next: vi.fn(),
		book: { package: { metadata: { direction: 'ltr' } } },
	};
	const book = {
		renderTo: vi.fn(() => rendition),
		ready: Promise.resolve(),
		locations: {
			generate: vi.fn().mockResolvedValue(undefined),
			locationFromCfi: vi.fn((cfi: string) => cfi.includes('final') ? 3 : cfi.includes('spread-start') ? 2 : 0),
			total: 3,
		},
		navigation: { toc: [] as Array<{ href: string; label: string; subitems?: Array<{ href: string; label: string }> }> },
		package: { metadata: { direction: 'ltr' } },
		destroy: vi.fn(),
	};
	return { book, rendition };
}

function renditionHandler<T>(runtime: ReturnType<typeof createEpubRuntime>, event: string) {
	return runtime.rendition.on.mock.calls.find(([registeredEvent]) => registeredEvent === event)?.[1] as T | undefined;
}

describe('EpubReader protected source and ReadingInfo', () => {
	let runtime: ReturnType<typeof createEpubRuntime>;

	beforeEach(() => {
		localStorage.clear();
		runtime = createEpubRuntime();
		mocks.fetchBookFile.mockResolvedValue(new Blob(['PK EPUB DATA'], { type: 'application/epub+zip' }));
		mocks.fetchReadingInfo.mockResolvedValue({
			bookId: 7,
			currentPosition: 'epubcfi(/6/2!/4/1:0)',
			currentPage: 1,
			readStatus: 'reading',
		});
		mocks.saveReadingInfo.mockResolvedValue({
			bookId: 7,
			currentPosition: 'epubcfi(final)',
			currentPage: 1,
			readStatus: 'completed',
		});
		mocks.ePub.mockReturnValue(runtime.book);
	});

	afterEach(() => {
		vi.restoreAllMocks();
		mocks.fetchBookFile.mockReset();
		mocks.fetchReadingInfo.mockReset();
		mocks.saveReadingInfo.mockReset();
		mocks.ePub.mockReset();
	});

	test('DBのCFIを復元してprotected EPUBを表示する', async () => {
		await render(<EpubReader bookId={7} pageTurnDirection='ltr' />);

		await vi.waitFor(() => expect(mocks.fetchBookFile).toHaveBeenCalledWith(7));
		await vi.waitFor(() => expect(mocks.fetchReadingInfo).toHaveBeenCalledWith(7));
		await vi.waitFor(() => expect(mocks.ePub).toHaveBeenCalledWith(expect.any(ArrayBuffer)));
		expect(runtime.book.locations.generate).toHaveBeenCalledWith(1600);
		await vi.waitFor(() => expect(runtime.rendition.display).toHaveBeenCalledWith('epubcfi(/6/2!/4/1:0)'));
	});

	test('スマホ幅では単ページ表示を初期値にする', async () => {
		const originalWidth = window.innerWidth;
		Object.defineProperty(window, 'innerWidth', { configurable: true, value: 393 });

		try {
			await render(<EpubReader bookId={7} pageTurnDirection='ltr' />);

			await vi.waitFor(() => expect(runtime.book.renderTo).toHaveBeenCalledWith(
				expect.any(HTMLElement),
				expect.objectContaining({ spread: 'none' }),
			));
		} finally {
			Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth });
		}
	});

	test('relocated時にCFIとcompleted状態をReadingInfoへ保存する', async () => {
		await render(<EpubReader bookId={7} pageTurnDirection='ltr' />);
		await vi.waitFor(() => expect(runtime.rendition.on).toHaveBeenCalledWith('relocated', expect.any(Function)));

		const relocatedHandler = renditionHandler<(value: { start: { cfi: string; href?: string }; atEnd?: boolean }) => void>(runtime, 'relocated');
		expect(relocatedHandler).toBeTypeOf('function');
		relocatedHandler?.({ start: { cfi: 'epubcfi(final)', href: 'chapter-2.xhtml' }, atEnd: true });

		expect(localStorage.getItem('reader-location:7')).toBe('epubcfi(final)');
		await vi.waitFor(() => expect(mocks.saveReadingInfo).toHaveBeenCalledWith(7, 'epubcfi(final)', 'completed'));
		await expect.element(document.body).toHaveTextContent('位置 4 / 4');
		await expect.element(document.body).toHaveTextContent('100%');
	});

	test('見開きの開始位置が最終location未満でもatEndなら読了にする', async () => {
		await render(<EpubReader bookId={7} pageTurnDirection='ltr' />);
		await vi.waitFor(() => expect(runtime.rendition.on).toHaveBeenCalledWith('relocated', expect.any(Function)));
		const relocatedHandler = renditionHandler<(value: {
			start: { cfi: string; href?: string };
			end?: { cfi?: string };
			atEnd?: boolean;
		}) => void>(runtime, 'relocated');

		relocatedHandler?.({
			start: { cfi: 'epubcfi(spread-start)', href: 'chapter-2.xhtml' },
			end: { cfi: 'epubcfi(final)' },
			atEnd: true,
		});

		await vi.waitFor(() => expect(mocks.saveReadingInfo).toHaveBeenCalledWith(7, 'epubcfi(spread-start)', 'completed'));
		await expect.element(document.body).toHaveTextContent('位置 4 / 4');
		await expect.element(document.body).toHaveTextContent('100%');
	});

	test('最終locationを含む見開きでもatEndでなければ早期読了にしない', async () => {
		await render(<EpubReader bookId={7} pageTurnDirection='ltr' />);
		await vi.waitFor(() => expect(runtime.rendition.on).toHaveBeenCalledWith('relocated', expect.any(Function)));
		const relocatedHandler = renditionHandler<(value: {
			start: { cfi: string; href?: string };
			end?: { cfi?: string };
			atEnd?: boolean;
		}) => void>(runtime, 'relocated');

		relocatedHandler?.({
			start: { cfi: 'epubcfi(spread-start)', href: 'chapter-2.xhtml' },
			end: { cfi: 'epubcfi(final)' },
			atEnd: false,
		});

		await vi.waitFor(() => expect(mocks.saveReadingInfo).toHaveBeenCalledWith(7, 'epubcfi(spread-start)', 'reading'), { timeout: 1_500 });
		await expect.element(document.body).toHaveTextContent('位置 3 / 4');
		await expect.element(document.body).toHaveTextContent('67%');
	});

	test('locationが1個だけのEPUBは終端到達前に不正な100%を表示しない', async () => {
		runtime.book.locations.total = 0;
		await render(<EpubReader bookId={7} pageTurnDirection='ltr' />);
		await vi.waitFor(() => expect(runtime.rendition.on).toHaveBeenCalledWith('relocated', expect.any(Function)));
		const relocatedHandler = renditionHandler<(value: {
			start: { cfi: string; href?: string };
			atEnd?: boolean;
		}) => void>(runtime, 'relocated');

		relocatedHandler?.({ start: { cfi: 'epubcfi(first)', href: 'chapter-1.xhtml' }, atEnd: false });
		await expect.element(document.body).not.toHaveTextContent('位置 1 / 1');
		await expect.element(document.body).not.toHaveTextContent('100%');

		relocatedHandler?.({ start: { cfi: 'epubcfi(final)', href: 'chapter-1.xhtml' }, atEnd: true });
		await vi.waitFor(() => expect(mocks.saveReadingInfo).toHaveBeenCalledWith(7, 'epubcfi(final)', 'completed'));
		await expect.element(document.body).toHaveTextContent('位置 1 / 1');
		await expect.element(document.body).toHaveTextContent('100%');
	});

	test('通常移動のReadingInfo保存をまとめ、最後のCFIだけを送る', async () => {
		await render(<EpubReader bookId={7} pageTurnDirection='ltr' />);
		await vi.waitFor(() => expect(runtime.rendition.on).toHaveBeenCalledWith('relocated', expect.any(Function)));
		const relocatedHandler = renditionHandler<(value: { start: { cfi: string; href?: string } }) => void>(runtime, 'relocated');

		relocatedHandler?.({ start: { cfi: 'epubcfi(first)', href: 'chapter-1.xhtml' } });
		relocatedHandler?.({ start: { cfi: 'epubcfi(second)', href: 'chapter-1.xhtml' } });
		expect(mocks.saveReadingInfo).not.toHaveBeenCalled();
		await vi.waitFor(() => expect(mocks.saveReadingInfo).toHaveBeenCalledWith(7, 'epubcfi(second)', 'reading'), { timeout: 1_500 });
		expect(mocks.saveReadingInfo).toHaveBeenCalledTimes(1);
	});

	test('階層目次から移動し、移動前のCFIへ戻る', async () => {
		runtime.book.navigation.toc = [{
			href: 'chapter-1.xhtml',
			label: '第一章',
			subitems: [{ href: 'chapter-1.xhtml#section-1', label: '第一節' }],
		}];
		const { getByRole } = await render(<EpubReader bookId={7} pageTurnDirection='ltr' />);
		await vi.waitFor(() => expect(runtime.rendition.display).toHaveBeenCalled());

		const relocatedHandler = renditionHandler<(value: { start: { cfi: string; href?: string } }) => void>(runtime, 'relocated');
		relocatedHandler?.({ start: { cfi: 'epubcfi(before-toc)', href: 'chapter-1.xhtml' } });
		await getByRole('button', { name: '目次' }).click();
		await expect.element(getByRole('button', { name: '第一章' })).toHaveAttribute('aria-current', 'location');
		await expect.element(getByRole('button', { name: '第一節' })).not.toHaveAttribute('aria-current');
		await getByRole('button', { name: '第一節' }).click();

		await vi.waitFor(() => expect(runtime.rendition.display).toHaveBeenCalledWith('chapter-1.xhtml#section-1'));
		await expect.element(getByRole('button', { name: '目次移動前へ戻る' })).toBeInTheDocument();
		await getByRole('button', { name: '目次移動前へ戻る' }).click();
		expect(runtime.rendition.display).toHaveBeenCalledWith('epubcfi(before-toc)');
	});

	test('目次がない場合に案内する', async () => {
		const { getByRole, getByText } = await render(<EpubReader bookId={7} pageTurnDirection='ltr' />);
		await vi.waitFor(() => expect(runtime.rendition.display).toHaveBeenCalled());
		await getByRole('button', { name: '目次' }).click();
		await expect.element(getByText('このEPUBには目次がありません。')).toBeInTheDocument();
	});

	test('iframe本文の左右端タップをLTRで送り、リンクとドラッグは妨害しない', async () => {
		await render(<EpubReader bookId={7} pageTurnDirection='ltr' />);
		await vi.waitFor(() => expect(runtime.rendition.on).toHaveBeenCalledWith('rendered', expect.any(Function)));
		const renderedHandler = renditionHandler<(section: unknown, view: { document: Document }) => void>(runtime, 'rendered');
		const frame = document.createElement('iframe');
		const viewer = document.querySelector<HTMLElement>('[tabindex="1"]')!;
		viewer.append(frame);
		vi.spyOn(viewer, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, right: 300, bottom: 600, width: 300, height: 600, x: 0, y: 0, toJSON: () => ({}) });
		vi.spyOn(frame, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, right: 300, bottom: 600, width: 300, height: 600, x: 0, y: 0, toJSON: () => ({}) });
		const contentDocument = frame.contentDocument!;
		renderedHandler?.({}, { document: contentDocument });

		const rightEdge = (contentDocument.defaultView?.innerWidth ?? 300) - 1;
		contentDocument.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 1, isPrimary: true, clientX: rightEdge, clientY: 20 }));
		contentDocument.body.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1, isPrimary: true, clientX: rightEdge, clientY: 20 }));
		expect(runtime.rendition.next).toHaveBeenCalledTimes(1);

		const link = contentDocument.createElement('a');
		link.href = '#target';
		contentDocument.body.append(link);
		link.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 2, isPrimary: true, clientX: rightEdge, clientY: 20 }));
		link.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 2, isPrimary: true, clientX: rightEdge, clientY: 20 }));
		contentDocument.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 3, isPrimary: true, clientX: 10, clientY: 20 }));
		contentDocument.body.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 3, isPrimary: true, clientX: 40, clientY: 20 }));
		expect(runtime.rendition.next).toHaveBeenCalledTimes(1);
		expect(runtime.rendition.prev).not.toHaveBeenCalled();
		frame.remove();
	});

	test('RTLでは左端タップが次ページになり、再描画時にリスナーを重複登録しない', async () => {
		runtime.rendition.book.package.metadata.direction = 'rtl';
		runtime.book.package.metadata.direction = 'rtl';
		await render(<EpubReader bookId={7} pageTurnDirection='rtl' />);
		await vi.waitFor(() => expect(runtime.rendition.on).toHaveBeenCalledWith('rendered', expect.any(Function)));
		const renderedHandler = renditionHandler<(section: unknown, view: { document: Document }) => void>(runtime, 'rendered');
		const frame = document.createElement('iframe');
		const viewer = document.querySelector<HTMLElement>('[tabindex="1"]')!;
		viewer.append(frame);
		vi.spyOn(viewer, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, right: 300, bottom: 600, width: 300, height: 600, x: 0, y: 0, toJSON: () => ({}) });
		vi.spyOn(frame, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, right: 300, bottom: 600, width: 300, height: 600, x: 0, y: 0, toJSON: () => ({}) });
		const contentDocument = frame.contentDocument!;
		renderedHandler?.({}, { document: contentDocument });
		renderedHandler?.({}, { document: contentDocument });

		contentDocument.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 1, isPrimary: true, clientX: 5, clientY: 20 }));
		contentDocument.body.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1, isPrimary: true, clientX: 5, clientY: 20 }));
		expect(runtime.rendition.next).toHaveBeenCalledTimes(1);
		expect(runtime.rendition.prev).not.toHaveBeenCalled();
		frame.remove();
	});
});
