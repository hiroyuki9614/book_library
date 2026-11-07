import { act, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom';
import { AuthProvider } from '@/contexts/AuthProvider';
import { useAuth } from '@/contexts/useAuth';
import type { AuthenticatedUser } from '@/contexts/authContext';
import RequireAuth from '@/components/RequireAuth';
import RequireAdmin from '@/components/RequireAdmin';
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
const adminUserB: AuthenticatedUser = { id: 9, name: 'Admin B', email: 'admin-b@example.com', role: 'admin' };

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

function AdminRouteContent() {
	const { user } = useAuth();
	const [loadedFor] = useState(() => user?.name ?? 'unknown user');
	return <div data-testid='admin-private-row'>Admin data for {loadedFor}</div>;
}

function RoutedShelf({ initialEntries = ['/'] }: { initialEntries?: string[] }) {
	return (
		<MemoryRouter initialEntries={initialEntries}>
			<NavigationControls />
			<Routes>
				<Route element={<RequireAuth />}>
					<Route path='/' element={<Home />} />
					<Route path='/reader/:id' element={<ReaderPage />} />
					<Route path='/admin' element={<RequireAdmin />}>
						<Route index element={<AdminRouteContent />} />
					</Route>
				</Route>
				<Route path='/login' element={<div>Login page</div>} />
			</Routes>
		</MemoryRouter>
	);
}

async function setup(initialEntries = ['/']) {
	const queryClient = new QueryClient({
		defaultOptions: {
			queries: { retry: false, gcTime: 5 * 60 * 1000 },
		},
	});
	const view = await render(
		<QueryClientProvider client={queryClient}>
			<AuthProvider><RoutedShelf initialEntries={initialEntries} /></AuthProvider>
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
		await expect.element(getByRole('status')).not.toBeInTheDocument();
		const settledInitialRequestCount = mocks.getCurrentUser.mock.calls.length;

		let resolveStaleA!: (user: AuthenticatedUser) => void;
		const staleAResponse = new Promise<AuthenticatedUser>((resolve) => { resolveStaleA = resolve; });
		let resolveCurrentB!: (user: AuthenticatedUser) => void;
		const currentBResponse = new Promise<AuthenticatedUser>((resolve) => { resolveCurrentB = resolve; });
		mocks.getCurrentUser.mockImplementationOnce(() => staleAResponse);
		mocks.getCurrentUser.mockImplementationOnce(() => currentBResponse);

		await act(async () => window.dispatchEvent(new Event('focus')));
		await expect.poll(() => mocks.getCurrentUser.mock.calls.length).toBeGreaterThan(settledInitialRequestCount);
		await expect.element(getByRole('status')).toBeInTheDocument();
		const staleRequestCount = mocks.getCurrentUser.mock.calls.length;

		// A visibility return is the recovery signal even when no focus event is
		// delivered. Its /me request queues behind the old response.
		setVisibilityState('hidden');
		await act(async () => document.dispatchEvent(new Event('visibilitychange')));
		setVisibilityState('visible');
		await act(async () => document.dispatchEvent(new Event('visibilitychange')));
		expect(mocks.getCurrentUser.mock.calls.length).toBe(staleRequestCount);
		expect(mocks.fetchBooks).toHaveBeenCalledTimes(1);

		await act(async () => resolveStaleA(adminUser));
		await expect.poll(() => mocks.getCurrentUser.mock.calls.length).toBeGreaterThan(staleRequestCount);
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
		await expect.element(getByRole('status')).not.toBeInTheDocument();
		const settledInitialRequestCount = mocks.getCurrentUser.mock.calls.length;
		const retainedAdminResults = queryClient.getQueriesData({ queryKey: ['books', 'list', adminUser.id, 'admin'] });
		expect(retainedAdminResults.some(([, data]) => Array.isArray(data) && data.some((book) => book.title === restrictedBook.title))).toBe(true);

		let resolveGeneralBooks!: (books: typeof generalBook[]) => void;
		const generalBooksResponse = new Promise<typeof generalBook[]>((resolve) => { resolveGeneralBooks = resolve; });
		mocks.fetchBooks.mockImplementationOnce(() => generalBooksResponse);
		let resolveGeneralUser!: (user: AuthenticatedUser) => void;
		const generalUserResponse = new Promise<AuthenticatedUser>((resolve) => { resolveGeneralUser = resolve; });
		mocks.getCurrentUser.mockImplementationOnce(() => generalUserResponse);

		await act(async () => window.dispatchEvent(new Event('focus')));
		await expect.poll(() => mocks.getCurrentUser.mock.calls.length).toBeGreaterThan(settledInitialRequestCount);
		await expect.element(getByRole('status')).toBeInTheDocument();
		await act(async () => resolveGeneralUser(generalUser));

		await expect.poll(() => mocks.fetchBooks.mock.calls.length).toBe(2);
		await expect.element(getByText(restrictedBook.title)).not.toBeInTheDocument();
		expect(mocks.fetchBooks).toHaveBeenLastCalledWith(adminUser.id, expect.any(AbortSignal));

		await act(async () => resolveGeneralBooks([generalBook]));
		await expect.element(getByText(generalBook.title)).toBeInTheDocument();
		await expect.element(getByText(restrictedBook.title)).not.toBeInTheDocument();
	});

	test.each([
		{
			name: 'admin A switches to general account B',
			nextUser: userB,
			homeBook: userBBook,
			adminSnapshot: null,
		},
		{
			name: 'the same account loses its admin role',
			nextUser: generalUser,
			homeBook: generalBook,
			adminSnapshot: null,
		},
		{
			name: 'admin A switches to admin B',
			nextUser: adminUserB,
			homeBook: null,
			adminSnapshot: 'Admin data for Admin B',
		},
	])('$name masks then clears the previous admin view', async ({ nextUser, homeBook, adminSnapshot }) => {
		const { getByRole, getByText, getByTestId } = await setup(['/admin']);
		const adminRow = getByTestId('admin-private-row');
		await expect.element(adminRow).toHaveTextContent('Admin data for Admin A');
		await expect.element(getByRole('status')).not.toBeInTheDocument();
		const settledRequestCount = mocks.getCurrentUser.mock.calls.length;

		mocks.getCurrentUser.mockImplementation(async () => nextUser);
		if (nextUser.id === generalUser.id && nextUser.role === 'user') {
			mocks.fetchBooks.mockImplementationOnce(async () => [generalBook]);
		}
		let resolveIdentity!: (user: AuthenticatedUser) => void;
		const nextIdentityResponse = new Promise<AuthenticatedUser>((resolve) => { resolveIdentity = resolve; });
		mocks.getCurrentUser.mockImplementationOnce(() => nextIdentityResponse);

		await act(async () => window.dispatchEvent(new Event('focus')));
		await expect.poll(() => mocks.getCurrentUser.mock.calls.length).toBeGreaterThan(settledRequestCount);
		await expect.element(getByRole('status')).toBeInTheDocument();
		// The router outlet stays mounted under the inert hidden veil until /me resolves.
		await expect.element(adminRow).toBeInTheDocument();
		await expect.element(adminRow).not.toBeVisible();
		const hiddenOutlet = document.querySelector('[aria-hidden="true"][inert]');
		expect(hiddenOutlet?.textContent).toContain('Admin data for Admin A');

		await act(async () => resolveIdentity(nextUser));
		if (adminSnapshot) {
			await expect.element(getByTestId('admin-private-row')).toHaveTextContent(adminSnapshot);
			await expect.element(getByText('Admin data for Admin A')).not.toBeInTheDocument();
		} else {
			await expect.element(getByText(homeBook!.title)).toBeInTheDocument();
			await expect.element(adminRow).not.toBeInTheDocument();
		}
	});

	test('Home and RequireAuth share one focus revalidation while the shelf hook is mounted', async () => {
		const { getByRole, getByText } = await setup();
		await expect.element(getByText(restrictedBook.title)).toBeInTheDocument();
		await expect.element(getByRole('status')).not.toBeInTheDocument();
		const settledRequestCount = mocks.getCurrentUser.mock.calls.length;

		let resolveSameIdentity!: (user: AuthenticatedUser) => void;
		const sameIdentityResponse = new Promise<AuthenticatedUser>((resolve) => { resolveSameIdentity = resolve; });
		mocks.getCurrentUser.mockImplementationOnce(() => sameIdentityResponse);

		await act(async () => window.dispatchEvent(new Event('focus')));
		await expect.poll(() => mocks.getCurrentUser.mock.calls.length).toBeGreaterThan(settledRequestCount);
		await expect.element(getByRole('status')).toBeInTheDocument();
		await act(async () => resolveSameIdentity(adminUser));
		await expect.element(getByRole('status')).not.toBeInTheDocument();
		// A second listener in the child hook would queue another /me after the
		// delayed first response settles.
		expect(mocks.getCurrentUser.mock.calls.length).toBe(settledRequestCount + 1);
	});

	test('same-account focus revalidation keeps the already-open reader mounted and Home entry revalidates on a visible window', async () => {
		const { getByText, getByRole, getByTestId } = await setup();
		await expect.element(getByText(restrictedBook.title)).toBeInTheDocument();
		await expect.element(getByRole('status')).not.toBeInTheDocument();

		// Navigate directly to the reader so this test isolates route retention
		// from BookTable's separate row click/keyboard interaction tests.
		await getByRole('button', { name: 'Go reader' }).click();
		await expect.element(getByTestId('reader-content')).toBeInTheDocument();
		await expect.poll(() => mocks.fetchBook).toHaveBeenCalled();
		// Measure the settled baseline instead of treating an absolute /me count as API contract.
		const readerEntryRefreshCount = mocks.getCurrentUser.mock.calls.length;
		expect(readerEntryRefreshCount).toBeGreaterThan(0);
		const readerMountCount = mocks.readerMounts;
		expect(readerMountCount).toBe(1);
		expect(mocks.readerUnmounts).toBe(0);

		let resolveSameAccount!: (user: AuthenticatedUser) => void;
		const sameAccountResponse = new Promise<AuthenticatedUser>((resolve) => { resolveSameAccount = resolve; });
		mocks.getCurrentUser.mockImplementationOnce(() => sameAccountResponse);
		await act(async () => window.dispatchEvent(new Event('focus')));
		await expect.poll(() => mocks.getCurrentUser.mock.calls.length).toBeGreaterThan(readerEntryRefreshCount);
		await expect.element(getByRole('status')).toBeInTheDocument();
		expect(mocks.readerUnmounts).toBe(0);
		expect(mocks.readerMounts).toBe(readerMountCount);

		await act(async () => resolveSameAccount(adminUser));
		await expect.element(getByRole('status')).not.toBeInTheDocument();
		await expect.element(getByTestId('reader-content')).toBeInTheDocument();
		expect(mocks.readerUnmounts).toBe(0);
		expect(mocks.readerMounts).toBe(readerMountCount);

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
