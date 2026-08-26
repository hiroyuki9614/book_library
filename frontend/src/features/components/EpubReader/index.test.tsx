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
			locationFromCfi: vi.fn((cfi: string) => (cfi.includes('final') ? 2 : 0)),
			total: 3,
		},
		navigation: { toc: [] },
		package: { metadata: { direction: 'ltr' } },
		destroy: vi.fn(),
	};
	return { book, rendition };
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
		vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:protected-epub');
		vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
	});

	afterEach(() => {
		vi.restoreAllMocks();
		mocks.fetchBookFile.mockReset();
		mocks.fetchReadingInfo.mockReset();
		mocks.saveReadingInfo.mockReset();
		mocks.ePub.mockReset();
	});

	test('DBのCFIを復元してprotected EPUBを表示する', async () => {
		await render(<EpubReader bookId={7} />);

		await vi.waitFor(() => expect(mocks.fetchBookFile).toHaveBeenCalledWith(7));
		await vi.waitFor(() => expect(mocks.fetchReadingInfo).toHaveBeenCalledWith(7));
		await vi.waitFor(() => expect(mocks.ePub).toHaveBeenCalledWith('blob:protected-epub'));
		await vi.waitFor(() => expect(runtime.rendition.display).toHaveBeenCalledWith('epubcfi(/6/2!/4/1:0)'));
	});

	test('relocated時にCFIとcompleted状態をReadingInfoへ保存する', async () => {
		await render(<EpubReader bookId={7} />);
		await vi.waitFor(() => expect(runtime.rendition.on).toHaveBeenCalledWith('relocated', expect.any(Function)));

		const relocatedHandler = runtime.rendition.on.mock.calls.find(([event]) => event === 'relocated')?.[1] as ((value: { start: { cfi: string } }) => void) | undefined;
		expect(relocatedHandler).toBeTypeOf('function');
		relocatedHandler?.({ start: { cfi: 'epubcfi(final)' } });

		expect(localStorage.getItem('reader-location:7')).toBe('epubcfi(final)');
		await vi.waitFor(() => expect(mocks.saveReadingInfo).toHaveBeenCalledWith(7, 'epubcfi(final)', 'completed'));
	});
});
