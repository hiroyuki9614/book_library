import { describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';

const mocks = vi.hoisted(() => ({
	useBook: vi.fn(),
}));

vi.mock('@/hooks/useBook', () => ({ default: mocks.useBook }));
vi.mock('@/contexts/useAuth', () => ({ useAuth: () => ({ user: { id: 7 } }) }));
vi.mock('react-router-dom', () => ({ useParams: () => ({ id: '7' }) }));
vi.mock('@/features/components/PdfReader', () => ({
	default: ({ bookId, pageTurnDirection }: { bookId: number; pageTurnDirection: string }) => <div>PDF reader {bookId} {pageTurnDirection}</div>,
}));
vi.mock('@/features/components/EpubReader', () => ({
	default: ({ bookId, pageTurnDirection, userId }: { bookId: number; pageTurnDirection: string; userId?: number }) => <div>EPUB reader {bookId} {pageTurnDirection} user {userId}</div>,
}));

import ReaderPage from './index';

describe('ReaderPage EPUB routing', () => {
	test('EPUB書籍はEpubReaderへ渡す', async () => {
		mocks.useBook.mockReturnValue({
			book: { id: 7, fileType: 'epub', pageTurnDirection: 'rtl' },
			isLoading: false,
			error: null,
		});

		const { getByText } = await render(<ReaderPage />);

		await expect.element(getByText('EPUB reader 7 rtl user 7')).toBeInTheDocument();
	});
});
