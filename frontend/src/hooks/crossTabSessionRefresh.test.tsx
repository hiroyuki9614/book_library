import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { renderHook } from 'vitest-browser-react';
import { AuthProvider } from '@/contexts/AuthProvider';
import type { AuthenticatedUser } from '@/contexts/authContext';
import useBook from './useBook';
import useBooks from './useBooks';

const mocks = vi.hoisted(() => ({
	getCurrentUser: vi.fn(),
	fetchBooks: vi.fn(),
	fetchBook: vi.fn(),
	signOut: vi.fn(),
}));

vi.mock('@/api/me', () => ({ getCurrentUser: mocks.getCurrentUser }));
vi.mock('@/api/books', () => ({ fetchBooks: mocks.fetchBooks, fetchBook: mocks.fetchBook }));
vi.mock('@/lib/auth-client', () => ({ authClient: { signOut: mocks.signOut } }));

const userA: AuthenticatedUser = { id: 7, name: 'User A', email: 'a@example.com', role: 'user' };
const userB: AuthenticatedUser = { id: 8, name: 'User B', email: 'b@example.com', role: 'user' };

function createWrapper() {
	const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
	return function Wrapper({ children }: { children: ReactNode }) {
		return (
			<QueryClientProvider client={queryClient}>
				<AuthProvider>{children}</AuthProvider>
			</QueryClientProvider>
		);
	};
}

function prepareSessionRefresh() {
	let resolveRefresh!: (user: AuthenticatedUser) => void;
	const refresh = new Promise<AuthenticatedUser>((resolve) => {
		resolveRefresh = resolve;
	});
	mocks.getCurrentUser.mockResolvedValueOnce(userA).mockReturnValueOnce(refresh);
	return resolveRefresh;
}

afterEach(() => {
	vi.resetAllMocks();
});

describe('cross-tab session refresh', () => {
	test('shelf hides the previous account books while refreshing session on focus', async () => {
		const resolveRefresh = prepareSessionRefresh();
		const accountABooks = [{ id: 1, title: 'Account A restricted book' }];
		const accountBBooks = [{ id: 1, title: 'Account B permitted book' }];
		mocks.fetchBooks.mockImplementation(async (userId: number) => userId === userA.id ? accountABooks : accountBBooks);

		const { result, act } = await renderHook(() => useBooks(), { wrapper: createWrapper() });
		await expect.poll(() => result.current.books).toEqual(accountABooks);

		await act(async () => {
			window.dispatchEvent(new Event('focus'));
		});
		await expect.poll(() => result.current.books).toEqual([]);
		expect(result.current.isLoading).toBe(true);

		await act(async () => resolveRefresh(userB));
		await expect.poll(() => result.current.books).toEqual(accountBBooks);
		expect(mocks.fetchBooks).toHaveBeenCalledWith(userB.id, expect.any(AbortSignal));
	});

	test('reader hides the previous account book while refreshing session on focus', async () => {
		const resolveRefresh = prepareSessionRefresh();
		const accountABook = { id: 44, title: 'Account A restricted book' };
		const accountBBook = { id: 44, title: 'Account B permitted book' };
		mocks.fetchBook.mockResolvedValueOnce(accountABook).mockResolvedValueOnce(accountBBook);

		const { result, act } = await renderHook(() => useBook(44), { wrapper: createWrapper() });
		await expect.poll(() => result.current.book).toEqual(accountABook);

		await act(async () => {
			window.dispatchEvent(new Event('focus'));
		});
		await expect.poll(() => result.current.book).toBeNull();
		expect(result.current.isLoading).toBe(true);

		await act(async () => resolveRefresh(userB));
		await expect.poll(() => result.current.book).toEqual(accountBBook);
		expect(mocks.fetchBook).toHaveBeenCalledTimes(2);
	});
});
