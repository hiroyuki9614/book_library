import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';

const mocks = vi.hoisted(() => ({
	fetchBookFile: vi.fn(),
	ePub: vi.fn(),
}));

vi.mock('@/api/books', () => ({ fetchBookFile: mocks.fetchBookFile }));
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
			locationFromCfi: vi.fn(() => 0),
			total: 1,
		},
		navigation: { toc: [] },
		package: { metadata: { direction: 'ltr' } },
		destroy: vi.fn(),
	};
	return { book, rendition };
}

describe('EpubReader protected source', () => {
	beforeEach(() => {
		mocks.fetchBookFile.mockResolvedValue(new Blob(['PK EPUB DATA'], { type: 'application/epub+zip' }));
		mocks.ePub.mockReturnValue(createEpubRuntime().book);
		vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:protected-epub');
		vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
	});

	afterEach(() => {
		vi.restoreAllMocks();
		mocks.fetchBookFile.mockReset();
		mocks.ePub.mockReset();
	});

	test('bookIdの保護APIからEPUB Blobを取得してepubjsへ渡す', async () => {
		await render(<EpubReader bookId={7} />);

		await vi.waitFor(() => expect(mocks.fetchBookFile).toHaveBeenCalledWith(7));
		await vi.waitFor(() => expect(mocks.ePub).toHaveBeenCalledWith('blob:protected-epub'));
	});
});
