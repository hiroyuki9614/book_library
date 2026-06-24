import { afterEach, describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';
import { BookTable } from './index';
import { booksData, type Book } from '@/data/booksData';
import { readingProgressData } from '@/data/readingProgress';

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
const readingProgresses = readingProgressData.filter((progress) => [1, 2, 9].includes(progress.id));

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
	render(<BookTable books={books} readingProgresses={readingProgresses} itemsPerPage={itemsPerPage} searchQuery={searchQuery} sort={sort} isLoading={isLoading} status={status} />);

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

	test('shows only completed books when status is completed', async () => {
		const { getByText } = await renderBookTable({ status: 'completed' });

		await expect.element(getByText('Learning TypeScript')).toBeInTheDocument();
		await expect.element(getByText('React Design Patterns')).not.toBeInTheDocument();
		await expect.element(getByText('System Design Basics')).not.toBeInTheDocument();
	});

	test('shows books with unread progress or no progress when status is unread', async () => {
		const { getByText } = await renderBookTable({ status: 'unread' });

		await expect.element(getByText('System Design Basics')).toBeInTheDocument();
		await expect.element(getByText('Node.js Architecture')).toBeInTheDocument();
		await expect.element(getByText('React Design Patterns')).not.toBeInTheDocument();
		await expect.element(getByText('Learning TypeScript')).not.toBeInTheDocument();
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
		await expect.element(getByText('React Design Patterns')).not.toBeInTheDocument();
	});

	test('shows empty state when there are no current books', async () => {
		const { getByText } = await renderBookTable({ searchQuery: 'not found' });

		await expect.element(getByText('データがありません。')).toBeInTheDocument();
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

		await getBookRow(container, 'React Design Patterns').click();

		expect(routerMocks.navigate).toHaveBeenCalledWith('/reader/1');
	});

	test('does not navigate when row action buttons are clicked', async () => {
		const { getByRole } = await renderBookTable({ sort: 'oldest' });

		await getByRole('button', { name: 'React Design Patterns のメニューを開く' }).click();
		await getByRole('button', { name: 'React Design Patterns をお気に入りにする' }).click();

		expect(routerMocks.navigate).not.toHaveBeenCalled();
	});
});
