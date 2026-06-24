import type { ReactNode } from 'react';
import { describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import GuestRoute from './GuestRoute';
import RequireAdmin from './RequireAdmin';
import RequireAuth from './RequireAuth';
import { authContext, type Role } from '@/contexts/authContext';

const renderWithAuth = ({ children, initialEntries, role }: { children: ReactNode; initialEntries: string[]; role: Role }) =>
	render(
		<authContext.Provider value={{ role, login: vi.fn(), logout: vi.fn() }}>
			<MemoryRouter initialEntries={initialEntries}>{children}</MemoryRouter>
		</authContext.Provider>,
	);

function LocationDisplay() {
	const location = useLocation();

	return <div>Current path: {location.pathname}</div>;
}

function TestPage({ label }: { label: string }) {
	return (
		<>
			<div>{label}</div>
			<LocationDisplay />
		</>
	);
}

describe('route guard components', () => {
	test('RequireAuth redirects unauthenticated users to the login page', async () => {
		const { getByText } = await renderWithAuth({
			role: null,
			initialEntries: ['/private'],
			children: (
				<Routes>
					<Route element={<RequireAuth />}>
						<Route path='/private' element={<div>Private page</div>} />
					</Route>
					<Route path='/login' element={<TestPage label='Login page' />} />
				</Routes>
			),
		});

		await expect.element(getByText('Login page')).toBeInTheDocument();
		await expect.element(getByText('Current path: /login')).toBeInTheDocument();
		await expect.element(getByText('Private page')).not.toBeInTheDocument();
	});

	test('RequireAuth renders children for authenticated users', async () => {
		const { getByText } = await renderWithAuth({
			role: 'user',
			initialEntries: ['/private'],
			children: (
				<Routes>
					<Route element={<RequireAuth />}>
						<Route path='/private' element={<div>Private page</div>} />
					</Route>
					<Route path='/login' element={<div>Login page</div>} />
				</Routes>
			),
		});

		await expect.element(getByText('Private page')).toBeInTheDocument();
		await expect.element(getByText('Login page')).not.toBeInTheDocument();
	});

	test('GuestRoute renders children for unauthenticated users', async () => {
		const { getByText } = await renderWithAuth({
			role: null,
			initialEntries: ['/login'],
			children: (
				<Routes>
					<Route element={<GuestRoute />}>
						<Route path='/login' element={<div>Login page</div>} />
					</Route>
					<Route path='/' element={<div>Home page</div>} />
				</Routes>
			),
		});

		await expect.element(getByText('Login page')).toBeInTheDocument();
		await expect.element(getByText('Home page')).not.toBeInTheDocument();
	});

	test('GuestRoute redirects authenticated users to the home page', async () => {
		const { getByText } = await renderWithAuth({
			role: 'user',
			initialEntries: ['/login'],
			children: (
				<Routes>
					<Route element={<GuestRoute />}>
						<Route path='/login' element={<div>Login page</div>} />
					</Route>
					<Route path='/' element={<TestPage label='Home page' />} />
				</Routes>
			),
		});

		await expect.element(getByText('Home page')).toBeInTheDocument();
		await expect.element(getByText('Current path: /')).toBeInTheDocument();
		await expect.element(getByText('Login page')).not.toBeInTheDocument();
	});

	test('RequireAdmin renders children for admin users', async () => {
		const { getByText } = await renderWithAuth({
			role: 'admin',
			initialEntries: ['/admin'],
			children: (
				<Routes>
					<Route path='/admin' element={<RequireAdmin />}>
						<Route index element={<div>Admin page</div>} />
					</Route>
					<Route path='/' element={<div>Home page</div>} />
				</Routes>
			),
		});

		await expect.element(getByText('Admin page')).toBeInTheDocument();
		await expect.element(getByText('Home page')).not.toBeInTheDocument();
	});

	test('RequireAdmin redirects non-admin users to the home page', async () => {
		const { getByText } = await renderWithAuth({
			role: 'user',
			initialEntries: ['/admin'],
			children: (
				<Routes>
					<Route path='/admin' element={<RequireAdmin />}>
						<Route index element={<div>Admin page</div>} />
					</Route>
					<Route path='/' element={<TestPage label='Home page' />} />
				</Routes>
			),
		});

		await expect.element(getByText('Home page')).toBeInTheDocument();
		await expect.element(getByText('Current path: /')).toBeInTheDocument();
		await expect.element(getByText('Admin page')).not.toBeInTheDocument();
	});

	test('RequireAdmin redirects unauthenticated users to the home page', async () => {
		const { getByText } = await renderWithAuth({
			role: null,
			initialEntries: ['/admin'],
			children: (
				<Routes>
					<Route path='/admin' element={<RequireAdmin />}>
						<Route index element={<div>Admin page</div>} />
					</Route>
					<Route path='/' element={<TestPage label='Home page' />} />
				</Routes>
			),
		});

		await expect.element(getByText('Home page')).toBeInTheDocument();
		await expect.element(getByText('Current path: /')).toBeInTheDocument();
		await expect.element(getByText('Admin page')).not.toBeInTheDocument();
	});
});
