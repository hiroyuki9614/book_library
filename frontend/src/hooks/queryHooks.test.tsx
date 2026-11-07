import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { renderHook } from 'vitest-browser-react';
import useBook from './useBook';
import useBooks from './useBooks';
import useReadingProgresses from './useReadingProgresses';
import useUser from './useUser';

const mocks = vi.hoisted(() => ({
	fetchBooks: vi.fn(),
	fetchBook: vi.fn(),
	readingProgressesApiMock: vi.fn(),
	userApiMock: vi.fn(),
	authUserId: 7 as number | null,
	authIsPending: false,
}));

vi.mock('@/api/books', () => ({
	fetchBooks: mocks.fetchBooks,
	fetchBook: mocks.fetchBook,
}));

vi.mock('@/mocks/readingProgressesApiMock', () => ({
	default: mocks.readingProgressesApiMock,
}));

vi.mock('@/mocks/userApiMock', () => ({
	default: mocks.userApiMock,
}));

vi.mock('@/contexts/useAuth', () => ({
	useAuth: () => ({
		user: mocks.authUserId === null ? null : { id: mocks.authUserId },
		isPending: mocks.authIsPending,
	}),
}));

const createWrapper = () => {
	const queryClient = new QueryClient({
		defaultOptions: {
			queries: {
				retry: false,
				gcTime: 0,
			},
			mutations: {
				retry: false,
				gcTime: 0,
			},
		},
	});

	return function Wrapper({ children }: { children: ReactNode }) {
		return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
	};
};

afterEach(() => {
	vi.clearAllMocks();
	vi.restoreAllMocks();
	mocks.authUserId = 7;
	mocks.authIsPending = false;
});

describe('React Query hooks', () => {
	test('useBooks returns fetched books', async () => {
		const books = [{ id: 1, title: 'React Design Patterns' }];
		mocks.fetchBooks.mockResolvedValueOnce(books);

		const { result } = await renderHook(() => useBooks(), {
			wrapper: createWrapper(),
		});

		await expect.poll(() => result.current.isLoading).toBe(false);
		expect(result.current.books).toEqual(books);
		expect(result.current.isError).toBe(false);
		expect(mocks.fetchBooks).toHaveBeenCalledWith(expect.any(AbortSignal));
	});

	test('useBooks does not expose one account books while another account is loading', async () => {
		const account7Books = [{ id: 1, title: 'Account 7 book', progressPercentage: 88 }];
		const account8Books = [{ id: 1, title: 'Account 8 book', progressPercentage: 12 }];
		let resolveAccount8!: (books: typeof account8Books) => void;
		const account8Request = new Promise<typeof account8Books>((resolve) => {
			resolveAccount8 = resolve;
		});
		const account8Snapshots: Array<ReturnType<typeof useBooks>['books']> = [];
		mocks.fetchBooks.mockResolvedValueOnce(account7Books).mockReturnValueOnce(account8Request);

		const { result, rerender, act } = await renderHook(() => {
			const current = useBooks();
			if (mocks.authUserId === 8 && !mocks.authIsPending) {
				account8Snapshots.push(current.books);
			}
			return current;
		}, {
			wrapper: createWrapper(),
		});

		await expect.poll(() => result.current.books).toEqual(account7Books);

		act(() => {
			mocks.authIsPending = true;
			rerender();
		});
		expect(result.current.books).toEqual([]);
		expect(result.current.isLoading).toBe(true);

		act(() => {
			mocks.authUserId = 8;
			mocks.authIsPending = false;
			rerender();
		});
		expect(result.current.books).toEqual([]);
		expect(result.current.isLoading).toBe(true);
		expect(mocks.fetchBooks).toHaveBeenCalledTimes(2);

		await act(async () => {
			resolveAccount8(account8Books);
		});
		await expect.poll(() => result.current.books).toEqual(account8Books);
		expect(account8Snapshots.flat().some((book) => (
			book.title === account7Books[0].title && book.progressPercentage === account7Books[0].progressPercentage
		))).toBe(false);
	});

	test('useBooks does not fetch without an authenticated user', async () => {
		mocks.authUserId = null;

		const { result } = await renderHook(() => useBooks(), {
			wrapper: createWrapper(),
		});

		expect(result.current.books).toEqual([]);
		expect(result.current.isLoading).toBe(false);
		expect(mocks.fetchBooks).not.toHaveBeenCalled();
	});

	test('useBook clears the prior account book while the next account loads', async () => {
		const account7Book = { id: 44, title: 'Account 7 restricted book', fileType: 'pdf' };
		const account8Book = { id: 44, title: 'Account 8 permitted book', fileType: 'pdf' };
		let resolveAccount8!: (book: typeof account8Book) => void;
		const account8Request = new Promise<typeof account8Book>((resolve) => {
			resolveAccount8 = resolve;
		});
		const account8Snapshots: Array<ReturnType<typeof useBook>['book']> = [];
		mocks.fetchBook.mockResolvedValueOnce(account7Book).mockReturnValueOnce(account8Request);

		const { result, rerender, act } = await renderHook(() => {
			const current = useBook(44);
			if (mocks.authUserId === 8 && !mocks.authIsPending) {
				account8Snapshots.push(current.book);
			}
			return current;
		}, {
			wrapper: createWrapper(),
		});

		await expect.poll(() => result.current.book).toEqual(account7Book);

		act(() => {
			mocks.authIsPending = true;
			rerender();
		});
		expect(result.current.book).toBeNull();
		expect(result.current.isLoading).toBe(true);

		act(() => {
			mocks.authUserId = 8;
			mocks.authIsPending = false;
			rerender();
		});
		expect(result.current.book).toBeNull();
		expect(result.current.isLoading).toBe(true);
		expect(mocks.fetchBook).toHaveBeenCalledTimes(2);

		await act(async () => {
			resolveAccount8(account8Book);
		});
		await expect.poll(() => result.current.book).toEqual(account8Book);
		expect(account8Snapshots).not.toContain(account7Book);
	});

	test('useBook passes the selected book id to the API', async () => {
		const book = { id: 2, title: 'Learning TypeScript', fileType: 'pdf' };
		mocks.fetchBook.mockResolvedValueOnce(book);

		const { result } = await renderHook(() => useBook(2), {
			wrapper: createWrapper(),
		});

		await expect.poll(() => result.current.isLoading).toBe(false);
		expect(result.current.book).toEqual(book);
		expect(result.current.error).toBeNull();
		expect(mocks.fetchBook).toHaveBeenCalledWith(2);
	});

	test('useReadingProgresses returns fetched reading progress records', async () => {
		const readingProgresses = [{ id: 1, progress: 50, status: 'reading' }];
		mocks.readingProgressesApiMock.mockResolvedValueOnce(readingProgresses);

		const { result } = await renderHook(() => useReadingProgresses(), {
			wrapper: createWrapper(),
		});

		await expect.poll(() => result.current.isLoading).toBe(false);
		expect(result.current.readingProgresses).toEqual(readingProgresses);
		expect(result.current.error).toBeNull();
		expect(mocks.readingProgressesApiMock).toHaveBeenCalledWith();
	});

	test('useUser fetches a user through mutation', async () => {
		const user = { id: 1, name: 'ユーザー1', role: 'admin' };
		mocks.userApiMock.mockResolvedValueOnce(user);

		const { result, act } = await renderHook(() => useUser(), {
			wrapper: createWrapper(),
		});

		await act(async () => {
			await expect(result.current.mutateAsync(1)).resolves.toEqual(user);
		});

		expect(mocks.userApiMock).toHaveBeenCalledWith(1);
	});
});
