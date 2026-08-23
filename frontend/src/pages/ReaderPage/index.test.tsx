import { describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';

const mocks = vi.hoisted(() => ({
	useBook: vi.fn(),
}));

vi.mock('@/hooks/useBook', () => ({ default: mocks.useBook }));
vi.mock('react-router-dom', () => ({ useParams: () => ({ id: '7' }) }));
vi.mock('@/features/components/PdfReader', () => ({
	default: ({ bookId }: { bookId: number }) => <div>PDF reader {bookId}</div>,
}));
vi.mock('@/features/components/EpubReader', () => ({
	default: ({ bookId }: { bookId: number }) => <div>EPUB reader {bookId}</div>,
}));

import ReaderPage from './index';

describe('ReaderPage EPUB routing', () => {
	test('EPUB書籍はEpubReaderへ渡す', async () => {
		mocks.useBook.mockReturnValue({
			book: { id: 7, fileType: 'epub' },
			isLoading: false,
			error: null,
		});

		const { getByText } = await render(<ReaderPage />);

		await expect.element(getByText('EPUB reader 7')).toBeInTheDocument();
	});
});
