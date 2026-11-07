import { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { AuthProvider } from '@/contexts/AuthProvider';
import type { AuthenticatedUser } from '@/contexts/authContext';
import RequireAuth from '@/components/RequireAuth';
import Home from '@/pages/Home';
import ReaderPage from '@/pages/ReaderPage';

const mocks = vi.hoisted(() => ({
	getCurrentUser: vi.fn(),
	fetchBooks: vi.fn(),
	fetchBook: vi.fn(),
	signOut: vi.fn(),
	readerMounts: 0,
	readerUnmounts: 0,
}));

vi.mock('@/api/me', () => ({ getCurrentUser: mocks.getCurrentUser }));
vi.mock('@/api/books', () => ({ fetchBooks: mocks.fetchBooks, fetchBook: mocks.fetchBook }));
vi.mock('@/lib/auth-client', () => ({ authClient: { signOut: mocks.signOut } }));
vi.mock('@/features/components/PdfReader', async () => {
	const React = await import('react');
	return {
		default: function PdfReaderStub({ bookId, bookTitle }: { bookId: number; bookTitle: string }) {
			const [instance] = React.useState(() => ++mocks.readerMounts);
			React.useEffect(() => () => { mocks.readerUnmounts += 1; }, []);
			return React.createElement('div', { 'data-testid': 'reader-content', 'data-instance': String(instance) }, `PDF reader ${bookId}: ${bookTitle}`);
		},
	};
});
vi.mock('@/features/components/EpubReader', () => ({ default: () => null }));

const adminUser: AuthenticatedUser = { id: 7, name: 'Admin A', email: 'a@example.com', role: 'admin' };
const generalUser: AuthenticatedUser = { id: 7, name: 'Admin A', email: 'a@example.com', role: 'user' };
const userB: AuthenticatedUser = { id: 8, name: 'User B', email: 'b@example.com', role: 'user' };

const restrictedBook = {
	id: 11,
	title: 'Account A restricted book',
	author: 'A Author',
	status: 'Reading' as const,
	fileType: 'pdf' as const,
	category: 'Technology',
	currentPage: 5,
	progressPercentage: 83,
	createdAt: '2026-01-01T00:00:00.000Z',
	updatedAt: '2026-01-01T00:00:00.000Z',
};
const generalBook = {
	...restrictedBook,
	id: 12,
	title: 'General user permitted book',
	progressPercentage: 15,
};
const userBBook = {
	...restrictedBook,
	id: 13,
	title: 'Account B permitted book',
	progressPercentage: 42,
};
const readerBook = {
	id: 11,
	title: restrictedBook.title,
	authorName: 'A Author',
	category: { id: 2, name: 'Technology' },
	readStatus: 'reading' as const,
	progressPercentage: 83,
	pageTurnDirection: 'ltr' as const,
	hasFile: true,
	fileType: 'pdf' as const,
	createdAt: '2026-01-01T00:00:00.000Z',
	updatedAt: '2026-01-01T00:00:00.000Z',
};

function NavigationControls() {
	const navigate = useNavigate();
	return (
		<nav>
			<button type='button' onClick={() => navigate('/')}>Go home</button>
			<button type='button' onClick={() => navigate('/reader/11')}>Go reader</button>
		</nav>
	);
}

function RoutedShelf() {
	return (
		<MemoryRouter initialEntries={['/']}>
			<NavigationControls />
			<Routes>
				<Route element={<RequireAuth />}>
					<Route path='/' element={<Home />} />
					<Route path='/reader/:id' element={<ReaderPage />} />
				</Route>
				<Route path='/login' element={<div>Login page</div>} />
			</Routes>
		</MemoryRouter>
	);
}

async function setup() {
	const queryClient = new QueryClient({
		defaultOptions: {
			queries: { retry: false, gcTime: 5 * 60 * 1000 },
		},
	});
	const view = await render(
		<QueryClientProvider client={queryClient}>
			<AuthProvider><RoutedShelf /></AuthProvider>
		</QueryClientProvider>,
	);
	return { ...view, queryClient };
}

function setVisibilityState(state: DocumentVisibilityState) {
	Object.defineProperty(document, 'visibilityState', { configurable: true, value: state });
}

let originalVisibilityDescriptor: PropertyDescriptor | undefined;

beforeEach(() => {
	vi.resetAllMocks();
	mocks.readerMounts = 0;
	mocks.readerUnmounts = 0;
	originalVisibilityDescriptor = Object.getOwnPropertyDescriptor(document, 'visibilityState');
	setVisibilityState('visible');
	mocks.getCurrentUser.mockResolvedValue(adminUser);
	mocks.fetchBooks.mockImplementation(async (userId: number) => {
		if (userId === 7) return [restrictedBook];
		return [userBBook];
	});
	mocks.fetchBook.mockResolvedValue(readerBook);
});

afterEach(() => {
	if (originalVisibilityDescriptor) Object.defineProperty(document, 'visibilityState', originalVisibilityDescriptor);
	else Reflect.deleteProperty(document, 'visibilityState');
	vi.restoreAllMocks();
});

describe('cross-tab session refresh', () => {
	test('serializes a stale /me response and revalidates after visibility returns without exposing account A books', async () => {
		const { getByRole, getByText } = await setup();
		await expect.element(getByText(restrictedBook.title)).toBeInTheDocument();
		await expect.poll(() => mocks.getCurrentUser.mock.calls.length).toBe(2);

		let resolveStaleA!: (user: AuthenticatedUser) => void;
		const staleAResponse = new Promise<AuthenticatedUser>((resolve) => { resolveStaleA = resolve; });
		let resolveCurrentB!: (user: AuthenticatedUser) => void;
		const currentBResponse = new Promise<AuthenticatedUser>((resolve) => { resolveCurrentB = resolve; });
		mocks.getCurrentUser.mockImplementationOnce(() => staleAResponse);
		mocks.getCurrentUser.mockImplementationOnce(() => currentBResponse);

		await act(async () => window.dispatchEvent(new Event('focus')));
		await expect.poll(() => mocks.getCurrentUser.mock.calls.length).toBe(3);
		await expect.element(getByRole('status')).toBeInTheDocument();

		// A visibility return is the recovery signal even when no focus event is
		// delivered. Its /me request queues behind the old response.
		setVisibilityState('hidden');
		await act(async () => document.dispatchEvent(new Event('visibilitychange')));
		setVisibilityState('visible');
		await act(async () => document.dispatchEvent(new Event('visibilitychange')));
		expect(mocks.getCurrentUser).toHaveBeenCalledTimes(3);
		expect(mocks.fetchBooks).toHaveBeenCalledTimes(1);

		await act(async () => resolveStaleA(adminUser));
		await expect.poll(() => mocks.getCurrentUser.mock.calls.length).toBe(4);
		await expect.element(getByRole('status')).toBeInTheDocument();
		await expect.element(getByRole('link', { name: `${restrictedBook.title}を読む` })).not.toBeInTheDocument();

		await act(async () => resolveCurrentB(userB));
		await expect.element(getByText(userBBook.title)).toBeInTheDocument();
		await expect.element(getByText(restrictedBook.title)).not.toBeInTheDocument();
		expect(mocks.fetchBooks).toHaveBeenLastCalledWith(userB.id, expect.any(AbortSignal));
	});

	test('a same-id role downgrade cannot reuse the retained administrator shelf cache', async () => {
		const { getByText, getByRole, queryClient } = await setup();
		await expect.element(getByText(restrictedBook.title)).toBeInTheDocument();
		await expect.poll(() => mocks.getCurrentUser.mock.calls.length).toBe(2);
		const retainedAdminResults = queryClient.getQueriesData({ queryKey: ['books', 'list', adminUser.id, 'admin'] });
		expect(retainedAdminResults.some(([, data]) => Array.isArray(data) && data.some((book) => book.title === restrictedBook.title))).toBe(true);

		let resolveGeneralBooks!: (books: typeof generalBook[]) => void;
		const generalBooksResponse = new Promise<typeof generalBook[]>((resolve) => { resolveGeneralBooks = resolve; });
		mocks.fetchBooks.mockImplementationOnce(() => generalBooksResponse);
		let resolveGeneralUser!: (user: AuthenticatedUser) => void;
		const generalUserResponse = new Promise<AuthenticatedUser>((resolve) => { resolveGeneralUser = resolve; });
		mocks.getCurrentUser.mockImplementationOnce(() => generalUserResponse);

		await act(async () => window.dispatchEvent(new Event('focus')));
		await expect.poll(() => mocks.getCurrentUser.mock.calls.length).toBe(3);
		await expect.element(getByRole('status')).toBeInTheDocument();
		await act(async () => resolveGeneralUser(generalUser));

		await expect.poll(() => mocks.fetchBooks.mock.calls.length).toBe(2);
		await expect.element(getByText(restrictedBook.title)).not.toBeInTheDocument();
		expect(mocks.fetchBooks).toHaveBeenLastCalledWith(adminUser.id, expect.any(AbortSignal));

		await act(async () => resolveGeneralBooks([generalBook]));
		await expect.element(getByText(generalBook.title)).toBeInTheDocument();
		await expect.element(getByText(restrictedBook.title)).not.toBeInTheDocument();
	});

	test('same-account focus revalidation keeps the already-open reader mounted and Home entry revalidates on a visible window', async () => {
		const { getByText, getByRole, getByTestId } = await setup();
		await expect.element(getByText(restrictedBook.title)).toBeInTheDocument();
		await expect.poll(() => mocks.getCurrentUser.mock.calls.length).toBe(2);

		// Navigate directly to the reader so this test isolates route retention
		// from BookTable's separate row click/keyboard interaction tests.
		await getByRole('button', { name: 'Go reader' }).click();
		await expect.element(getByTestId('reader-content')).toBeInTheDocument();
		await expect.poll(() => mocks.fetchBook).toHaveBeenCalled();
		// A route transition may issue more than one authorized /me refresh.
		// Measure the settled baseline instead of treating a fetch count as API contract.
		const readerEntryRefreshCount = mocks.getCurrentUser.mock.calls.length;
		expect(readerEntryRefreshCount).toBeGreaterThanOrEqual(3);
		const readerInstance = await getByTestId('reader-content').getAttribute('data-instance');
		expect(mocks.readerUnmounts).toBe(0);

		let resolveSameAccount!: (user: AuthenticatedUser) => void;
		const sameAccountResponse = new Promise<AuthenticatedUser>((resolve) => { resolveSameAccount = resolve; });
		mocks.getCurrentUser.mockImplementationOnce(() => sameAccountResponse);
		await act(async () => window.dispatchEvent(new Event('focus')));
		await expect.poll(() => mocks.getCurrentUser.mock.calls.length).toBeGreaterThan(readerEntryRefreshCount);
		await expect.element(getByRole('status')).toBeInTheDocument();
		expect(mocks.readerUnmounts).toBe(0);
		expect(await getByTestId('reader-content').getAttribute('data-instance')).toBe(readerInstance);

		await act(async () => resolveSameAccount(adminUser));
		await expect.element(getByRole('status')).not.toBeInTheDocument();
		await expect.element(getByTestId('reader-content')).toBeInTheDocument();
		expect(mocks.readerUnmounts).toBe(0);
		expect(await getByTestId('reader-content').getAttribute('data-instance')).toBe(readerInstance);

		let resolveHomeEntry!: (user: AuthenticatedUser) => void;
		const homeEntryResponse = new Promise<AuthenticatedUser>((resolve) => { resolveHomeEntry = resolve; });
		mocks.getCurrentUser.mockImplementationOnce(() => homeEntryResponse);
		const beforeHomeEntry = mocks.getCurrentUser.mock.calls.length;
		await getByRole('button', { name: 'Go home' }).click();
		await expect.poll(() => mocks.getCurrentUser.mock.calls.length).toBeGreaterThan(beforeHomeEntry);
		await expect.element(getByRole('status')).toBeInTheDocument();
		await act(async () => resolveHomeEntry(adminUser));
		await expect.element(getByText(restrictedBook.title)).toBeInTheDocument();
	});
});
