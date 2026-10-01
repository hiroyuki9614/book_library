import { beforeEach, describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';
import type { Book } from '@/data/booksData';
import Home from './index';

const mocks = vi.hoisted(() => ({
	useBooks: vi.fn(),
	useReadingProgresses: vi.fn(),
	navigate: vi.fn(),
}));

vi.mock('@/hooks/useBooks', () => ({ default: mocks.useBooks }));
vi.mock('@/hooks/useReadingProgresses', () => ({ default: mocks.useReadingProgresses }));
vi.mock('react-router-dom', () => ({ useNavigate: () => mocks.navigate }));

const books: Book[] = [
	{ id: 34, title: '保存済みの読書中書籍', author: '著者A', category: 'Frontend', status: 'Reading', fileType: 'epub', currentPage: 0 },
	{ id: 35, title: '保存済みの読了書籍', author: '著者B', category: 'Backend', status: 'Completed', fileType: 'pdf', currentPage: 0 },
];

beforeEach(() => {
	vi.clearAllMocks();
	mocks.useBooks.mockReturnValue({ books, isLoading: false, isError: false });
	// Deliberately contradict the saved book state. Production must not consume this prototype.
	mocks.useReadingProgresses.mockReturnValue({
		readingProgresses: [
			{ id: 34, status: 'unread', progress: 93 },
			{ id: 35, status: 'reading', progress: 93 },
		],
	});
});

describe('Home reading evidence', () => {
	test('uses saved book states without calling the prototype progress hook', async () => {
		const { container } = await render(<Home />);
		expect(mocks.useReadingProgresses).not.toHaveBeenCalled();
		const rows = Array.from(container.querySelectorAll('tbody tr'));
		expect(rows.find((row) => row.textContent?.includes(books[0].title))?.textContent).toContain('reading');
		expect(rows.find((row) => row.textContent?.includes(books[1].title))?.textContent).toContain('completed');
		expect(container.textContent).toContain('2冊');
	});

	test('shows unmeasured percentages instead of mock averages or zero progress bars', async () => {
		const { container } = await render(<Home />);
		expect(container.textContent).toContain('未計測');
		expect(container.textContent).not.toContain('93%');
		expect(container.textContent).not.toContain('0%');
		expect(container.querySelector('[role="progressbar"]')).toBeNull();
	});

	test('does not represent initial loading as an empty library', async () => {
		mocks.useBooks.mockReturnValue({ books: [], isLoading: true, isError: false });
		const { container } = await render(<Home />);
		expect(container.textContent).toContain('読み込み中');
		expect(container.textContent).not.toContain('0冊');
		expect(container.textContent).not.toContain('データがありません。');
		expect(container.querySelector('[data-slot="skeleton"]')).not.toBeNull();
	});

	test('shows API failure instead of successful zero counts', async () => {
		mocks.useBooks.mockReturnValue({ books: [], isLoading: false, isError: true });
		const { container } = await render(<Home />);
		expect(container.textContent).toContain('書籍一覧の取得に失敗しました。');
		expect(container.textContent).toContain('取得失敗');
		expect(container.textContent).not.toContain('0冊');
		expect(container.textContent).not.toContain('データがありません。');
	});

	test('keeps a successful empty library distinct from unmeasured progress', async () => {
		mocks.useBooks.mockReturnValue({ books: [], isLoading: false, isError: false });
		const { container } = await render(<Home />);
		expect(container.textContent).toContain('0冊');
		expect(container.textContent).toContain('データがありません。');
		expect(container.textContent).toContain('未計測');
		expect(container.textContent).not.toContain('取得失敗');
		expect(mocks.useReadingProgresses).not.toHaveBeenCalled();
	});
});
