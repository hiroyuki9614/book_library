import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { renderHook } from 'vitest-browser-react';
import useBook from './useBook';
import useBooks from './useBooks';
import useReadingProgresses from './useReadingProgresses';
import useUser from './useUser';

const mocks = vi.hoisted(() => ({
	booksApiMock: vi.fn(),
	readingProgressesApiMock: vi.fn(),
	userApiMock: vi.fn(),
}));

vi.mock('@/mocks/booksApiMock', () => ({
	default: mocks.booksApiMock,
}));

vi.mock('@/mocks/readingProgressesApiMock', () => ({
	default: mocks.readingProgressesApiMock,
}));

vi.mock('@/mocks/userApiMock', () => ({
	default: mocks.userApiMock,
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
});

describe('React Query hooks', () => {
	test('useBooks returns fetched books', async () => {
		const books = [{ id: 1, title: 'React Design Patterns' }];
		mocks.booksApiMock.mockResolvedValueOnce(books);

		const { result } = await renderHook(() => useBooks(), {
			wrapper: createWrapper(),
		});

		await expect.poll(() => result.current.isLoading).toBe(false);
		expect(result.current.books).toEqual(books);
		expect(result.current.isError).toBe(false);
		expect(mocks.booksApiMock).toHaveBeenCalledWith();
	});

	test('useBook passes the selected book id to the API', async () => {
		const books = [{ id: 2, title: 'Learning TypeScript' }];
		mocks.booksApiMock.mockResolvedValueOnce(books);

		const { result } = await renderHook(() => useBook(2), {
			wrapper: createWrapper(),
		});

		await expect.poll(() => result.current.isLoading).toBe(false);
		expect(result.current.books).toEqual(books);
		expect(result.current.error).toBeNull();
		expect(mocks.booksApiMock).toHaveBeenCalledWith(2);
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
