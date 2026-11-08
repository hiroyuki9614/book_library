import { afterEach, describe, expect, test, vi } from 'vitest';
import { page } from 'vitest/browser';
import { render } from 'vitest-browser-react';
import { BookTable } from './index';
import { booksData, type Book } from '@/data/booksData';

const routerMocks = vi.hoisted(() => ({
	navigate: vi.fn(),
}));

vi.mock('react-router-dom', () => ({
	useNavigate: () => routerMocks.navigate,
}));

const pickBook = (id: number) => {
	const book = booksData.find((bookData) => bookData.id === id);

	if (!book) {
		throw new Error(`Book fixture was not found: ${id}`);
	}

	return book;
};

const books: Book[] = [pickBook(1), pickBook(2), pickBook(3), pickBook(9)];

type RenderBookTableOptions = {
	searchQuery?: string;
	sort?: string;
	isLoading?: boolean;
	status?: 'all' | 'unread' | 'reading' | 'completed';
	itemsPerPage?: number;
};

const renderBookTable = ({
	searchQuery = '',
	sort = 'newest',
	isLoading = false,
	status = 'all',
	itemsPerPage = 10,
}: RenderBookTableOptions = {}) =>
	render(<BookTable books={books} itemsPerPage={itemsPerPage} searchQuery={searchQuery} sort={sort} isLoading={isLoading} status={status} />);

const getBookRows = (container: HTMLElement) => Array.from(container.querySelectorAll('tbody tr'));

const getBookRow = (container: HTMLElement, title: string) => {
	const row = getBookRows(container).find((bookRow) => bookRow.textContent?.includes(title));

	if (!row) {
		throw new Error(`Book row was not found: ${title}`);
	}

	return row;
};

afterEach(() => {
	vi.clearAllMocks();
});

describe('BookTable', () => {
	test('filters books by title search query', async () => {
		const { getByText } = await renderBookTable({ searchQuery: 'react' });

		await expect.element(getByText('React Design Patterns')).toBeInTheDocument();
		await expect.element(getByText('Learning TypeScript')).not.toBeInTheDocument();
		await expect.element(getByText('Node.js Architecture')).not.toBeInTheDocument();
	});

	test('filters books by author search query', async () => {
		const { getByText } = await renderBookTable({ searchQuery: 'goldberg' });

		await expect.element(getByText('Learning TypeScript')).toBeInTheDocument();
		await expect.element(getByText('React Design Patterns')).not.toBeInTheDocument();
		await expect.element(getByText('Node.js Architecture')).not.toBeInTheDocument();
	});

	test('shows only reading books when status is reading', async () => {
		const { getByText } = await renderBookTable({ status: 'reading' });

		await expect.element(getByText('React Design Patterns')).toBeInTheDocument();
		await expect.element(getByText('Learning TypeScript')).not.toBeInTheDocument();
		await expect.element(getByText('System Design Basics')).not.toBeInTheDocument();
	});

	test('uses the backend book status for the status filter', async () => {
		expect(pickBook(9).status).toBe('Completed');
		const { getByText } = await renderBookTable({ status: 'completed' });

		await expect.element(getByText('Learning TypeScript')).toBeInTheDocument();
		await expect.element(getByText('React Design Patterns')).not.toBeInTheDocument();
		await expect.element(getByText('System Design Basics')).toBeInTheDocument();
	});

	test('shows only saved unread books, not completed books with an unread prototype', async () => {
		const { getByText } = await renderBookTable({ status: 'unread' });

		await expect.element(getByText('System Design Basics')).not.toBeInTheDocument();
		await expect.element(getByText('Node.js Architecture')).toBeInTheDocument();
		await expect.element(getByText('React Design Patterns')).not.toBeInTheDocument();
		await expect.element(getByText('Learning TypeScript')).not.toBeInTheDocument();
	});

	test('rounds measured shelf progress for display and accessibility', async () => {
		const progressByBookId = new Map([[1, 34.4], [2, 34.5], [3, 34.6], [9, 99.6]]);
		const booksWithProgress: Book[] = books.map((book) => ({ ...book, progressPercentage: progressByBookId.get(book.id) ?? null }));
		const { container } = await render(
			<BookTable books={booksWithProgress} itemsPerPage={10} searchQuery='' sort='newest' isLoading={false} status='all' />,
		);

		for (const book of booksWithProgress) {
			const roundedProgress = Math.round(book.progressPercentage ?? 0);
			const row = getBookRow(container, book.title);
			const progressbar = row.querySelector('[role="progressbar"]');
			const indicator = progressbar?.querySelector<HTMLElement>('[data-slot="progress-indicator"]');
			const expectedTransform = roundedProgress === 100 ? 'translateX(0%)' : `translateX(-${100 - roundedProgress}%)`;
			expect(row.textContent).toContain(`${roundedProgress}%`);
			expect(progressbar?.getAttribute('aria-label')).toBe(`${book.title} の読書進捗`);
			expect(progressbar?.getAttribute('aria-valuenow')).toBe(String(roundedProgress));
			expect(indicator?.style.transform).toBe(expectedTransform);
		}
	});

	test('leaves unknown progress unmeasured without a progress bar', async () => {
		const unknownBook = { ...pickBook(9), progressPercentage: null };
		const { container } = await render(
			<BookTable books={[unknownBook]} itemsPerPage={10} searchQuery='' sort='newest' isLoading={false} status='all' />,
		);
		const row = getBookRow(container, unknownBook.title);
		expect(row.textContent).toContain('未計測');
		expect(row.querySelector('[role="progressbar"]')).toBeNull();
	});

	test('shows measured and unknown progress visibly in mobile book cards', async () => {
		const measuredBook = { ...pickBook(1), progressPercentage: 34.4 };
		const unknownBook = { ...pickBook(9), progressPercentage: null };
		const responsiveDisplayStyles = document.createElement('style');
		responsiveDisplayStyles.textContent = '.hidden { display: none; } .block { display: block; } @media (min-width: 48rem) { .md\\:table-cell { display: table-cell; } }';
		document.head.append(responsiveDisplayStyles);

		try {
			await page.viewport(390, 844);
			expect(window.matchMedia('(max-width: 767px)').matches).toBe(true);
			const { container } = await render(
				<BookTable books={[measuredBook, unknownBook]} itemsPerPage={10} searchQuery='' sort='oldest' isLoading={false} status='all' />,
			);
			const measuredRow = getBookRow(container, measuredBook.title);
			const unknownRow = getBookRow(container, unknownBook.title);
			const measuredCell = measuredRow.querySelector('td:last-child') as HTMLElement;
			const unknownCell = unknownRow.querySelector('td:last-child') as HTMLElement;

			expect(measuredRow.querySelectorAll('td')).toHaveLength(6);
			expect(measuredCell.classList.contains('col-span-2')).toBe(true);
			expect(measuredCell.classList.contains('block')).toBe(true);
			expect(getComputedStyle(measuredCell).display).toBe('block');
			expect(measuredCell.textContent).toContain('34%');
			expect(measuredCell.querySelector('[role="progressbar"]')?.getAttribute('aria-label')).toBe(`${measuredBook.title} の読書進捗`);
			expect(measuredCell.querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow')).toBe('34');

			expect(unknownRow.querySelectorAll('td')).toHaveLength(6);
			expect(getComputedStyle(unknownCell).display).toBe('block');
			expect(unknownCell.textContent).toContain('未計測');
			expect(unknownCell.querySelector('[role="progressbar"]')).toBeNull();

			await page.viewport(1024, 768);
			expect(getComputedStyle(measuredCell).display).toBe('table-cell');
			expect(getComputedStyle(unknownCell).display).toBe('table-cell');
		} finally {
			responsiveDisplayStyles.remove();
			await page.viewport(414, 896);
		}
	});

	test('removes inert row actions while keeping one aligned cell per column', async () => {
		const { container } = await renderBookTable();
		expect(container.querySelectorAll('thead th')).toHaveLength(6);
		expect(getBookRow(container, 'React Design Patterns').querySelectorAll('td')).toHaveLength(6);
		expect(container.querySelectorAll('button[aria-label*="メニュー"]')).toHaveLength(0);
		expect(container.querySelectorAll('button[aria-label*="お気に入り"]')).toHaveLength(0);
	});

	test('cover imageがない実API書籍にはファイル形式付きfallbackを表示する', async () => {
		const book = { ...pickBook(1), coverImage: undefined };
		const { getByLabelText, getByText } = await render(
			<BookTable books={[book]} itemsPerPage={10} searchQuery='' sort='newest' isLoading={false} status='all' />,
		);

		await expect.element(getByLabelText(`${book.title} の表紙未設定`)).toBeInTheDocument();
		await expect.element(getByText('pdf', { exact: true })).toBeInTheDocument();
	});

	test('sorts books by id descending when sort is newest', async () => {
		const { container } = await renderBookTable({ sort: 'newest' });

		const rowText = getBookRows(container).map((row) => row.textContent ?? '');

		expect(rowText[0]).toContain('System Design Basics');
		expect(rowText[1]).toContain('Node.js Architecture');
		expect(rowText[2]).toContain('Learning TypeScript');
		expect(rowText[3]).toContain('React Design Patterns');
	});

	test('sorts books by id ascending when sort is not newest', async () => {
		const { container } = await renderBookTable({ sort: 'oldest' });

		const rowText = getBookRows(container).map((row) => row.textContent ?? '');

		expect(rowText[0]).toContain('React Design Patterns');
		expect(rowText[1]).toContain('Learning TypeScript');
		expect(rowText[2]).toContain('Node.js Architecture');
		expect(rowText[3]).toContain('System Design Basics');
	});

	test('shows skeleton rows while loading', async () => {
		const { container, getByText } = await renderBookTable({ isLoading: true, itemsPerPage: 3 });

		expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
		expect(container.querySelector('tbody tr')?.querySelectorAll('td')).toHaveLength(6);
		await expect.element(getByText('React Design Patterns')).not.toBeInTheDocument();
	});

	test('shows empty state when there are no current books', async () => {
		const { getByText } = await renderBookTable({ searchQuery: 'not found' });

		await expect.element(getByText('データがありません。')).toBeInTheDocument();
	});

	test('clamps pagination and disables invalid controls when account-scoped books shrink', async () => {
		const { getByText, rerender } = await renderBookTable({ itemsPerPage: 2 });
		await getByText('次へ').click();
		await expect.element(getByText('2 / 2')).toBeInTheDocument();

		rerender(
			<BookTable books={[pickBook(1)]} itemsPerPage={2} searchQuery='' sort='newest' isLoading={false} status='all' />,
		);
		await expect.element(getByText('1 / 1')).toBeInTheDocument();
		await expect.element(getByText('React Design Patterns')).toBeInTheDocument();
		await expect.element(getByText('前へ')).toBeDisabled();
		await expect.element(getByText('次へ')).toBeDisabled();
	});

	test('paginates books and moves between pages', async () => {
		const { getByText } = await renderBookTable({ itemsPerPage: 2 });

		await expect.element(getByText('1 / 2')).toBeInTheDocument();
		await expect.element(getByText('System Design Basics')).toBeInTheDocument();
		await expect.element(getByText('Node.js Architecture')).toBeInTheDocument();
		await expect.element(getByText('Learning TypeScript')).not.toBeInTheDocument();
		await expect.element(getByText('React Design Patterns')).not.toBeInTheDocument();

		await getByText('次へ').click();

		await expect.element(getByText('2 / 2')).toBeInTheDocument();
		await expect.element(getByText('Learning TypeScript')).toBeInTheDocument();
		await expect.element(getByText('React Design Patterns')).toBeInTheDocument();
		await expect.element(getByText('System Design Basics')).not.toBeInTheDocument();
		await expect.element(getByText('Node.js Architecture')).not.toBeInTheDocument();

		await getByText('前へ').click();

		await expect.element(getByText('1 / 2')).toBeInTheDocument();
		await expect.element(getByText('System Design Basics')).toBeInTheDocument();
		await expect.element(getByText('Node.js Architecture')).toBeInTheDocument();
	});

	test('navigates to the reader page when a book row is clicked', async () => {
		const { container } = await renderBookTable({ sort: 'oldest' });

		(getBookRow(container, 'React Design Patterns') as HTMLElement).click();

		expect(routerMocks.navigate).toHaveBeenCalledWith('/reader/1');
	});

	test('opens a mobile book card from the keyboard', async () => {
		const { container } = await renderBookTable({ sort: 'oldest' });

		getBookRow(container, 'React Design Patterns').dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Enter' }));

		expect(routerMocks.navigate).toHaveBeenCalledWith('/reader/1');
	});

	test('keeps row navigation accessible from the keyboard', async () => {
		const { container } = await renderBookTable({ sort: 'oldest' });
		getBookRow(container, 'React Design Patterns').dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: ' ' }));

		expect(routerMocks.navigate).toHaveBeenCalledWith('/reader/1');
	});
});
