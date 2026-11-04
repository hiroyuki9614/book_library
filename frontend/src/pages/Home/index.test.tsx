import { beforeEach, describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';
import type { Book } from '@/data/booksData';
import Home from './index';

const mocks = vi.hoisted(() => ({
	useBooks: vi.fn(),
	navigate: vi.fn(),
}));

vi.mock('@/hooks/useBooks', () => ({ default: mocks.useBooks }));
vi.mock('react-router-dom', () => ({ useNavigate: () => mocks.navigate }));

const books: Book[] = [
	{ id: 34, title: '保存済みの読書中書籍', author: '著者A', category: 'Frontend', status: 'Reading', fileType: 'epub', currentPage: 0, progressPercentage: 40 },
	{ id: 35, title: '保存済みの読了書籍', author: '著者B', category: 'Backend', status: 'Completed', fileType: 'pdf', currentPage: 0, progressPercentage: 100 },
];

beforeEach(() => {
	vi.clearAllMocks();
	mocks.useBooks.mockReturnValue({ books, isLoading: false, isError: false });
});

describe('Home reading evidence', () => {
	test('uses saved book states without calling the prototype progress hook', async () => {
		const { container } = await render(<Home />);
		const rows = Array.from(container.querySelectorAll('tbody tr'));
		expect(rows.find((row) => row.textContent?.includes(books[0].title))?.textContent).toContain('reading');
		expect(rows.find((row) => row.textContent?.includes(books[1].title))?.textContent).toContain('completed');
		expect(container.textContent).toContain('2冊');
	});

	test('shows the measured average with its denominator', async () => {
		const { container } = await render(<Home />);
		expect(container.textContent).toContain('70%（計測済み2/2冊）');
		expect(container.textContent).not.toContain('93%');
		expect(container.querySelector('[role="progressbar"]')).not.toBeNull();
	});

	test('excludes an unmeasured in-progress book from the average denominator', async () => {
		mocks.useBooks.mockReturnValue({
			books: [...books, { id: 36, title: '未計測の読書中書籍', author: '著者C', category: 'Design', status: 'Reading', fileType: 'epub', currentPage: 0, progressPercentage: null }],
			isLoading: false,
			isError: false,
		});
		const { container } = await render(<Home />);
		expect(container.textContent).toContain('70%（計測済み2/3冊）');
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
	});
});
