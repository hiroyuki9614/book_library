import { beforeEach, describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { authContext, type AuthenticatedUser } from '@/contexts/authContext';
import { authClient } from '@/lib/auth-client';
import Login from './Login';

vi.mock('@/lib/auth-client', () => ({
	authClient: {
		signIn: {
			email: vi.fn(),
		},
	},
}));

vi.mock('sonner', () => ({
	toast: {
		success: vi.fn(),
		error: vi.fn(),
	},
}));

const adminUser: AuthenticatedUser = {
	id: 1,
	name: 'Admin',
	email: 'admin@example.com',
	role: 'admin',
};

describe('Login flow', () => {
	beforeEach(() => {
		vi.resetAllMocks();
		vi.unstubAllEnvs();
	});

	test('ログイン成功後にセッションを再取得して認証済みページへ遷移する', async () => {
		vi.mocked(authClient.signIn.email).mockResolvedValue({
			data: {
				token: 'session-token',
				user: {
					id: '1',
					name: 'Admin',
					email: 'admin@example.com',
					emailVerified: true,
					image: null,
					createdAt: new Date(),
					updatedAt: new Date(),
				},
			},
			error: null,
		});
		const refreshAuth = vi.fn().mockResolvedValue(adminUser);

		const { getByLabelText, getByText } = await render(
			<authContext.Provider
				value={{
					user: null,
					isPending: false,
					isAuthenticated: false,
					role: null,
					refreshAuth,
					signOut: vi.fn(),
					logout: vi.fn(),
				}}
			>
				<MemoryRouter initialEntries={['/login']}>
					<Routes>
						<Route path='/login' element={<Login />} />
						<Route path='/' element={<div>Authenticated page</div>} />
					</Routes>
				</MemoryRouter>
			</authContext.Provider>,
		);

		await getByLabelText('ユーザーID').fill('admin@example.com');
		await getByLabelText('パスワード').fill('password');
		await getByText('Submit').click();

		await expect.element(getByText('Authenticated page')).toBeInTheDocument();
		expect(authClient.signIn.email).toHaveBeenCalledWith({
			email: 'admin@example.com',
			password: 'password',
		});
		expect(refreshAuth).toHaveBeenCalledOnce();
	});

	test('デモ入口に公開範囲と共有読書位置の注意を表示する', async () => {
		const { getByText } = await render(
			<authContext.Provider
				value={{
					user: null,
					isPending: false,
					isAuthenticated: false,
					role: null,
					refreshAuth: vi.fn(),
					signOut: vi.fn(),
					logout: vi.fn(),
				}}
			>
				<MemoryRouter><Login /></MemoryRouter>
			</authContext.Provider>,
		);

		await expect.element(getByText('採用面接向けデモ')).toBeInTheDocument();
		await expect.element(getByText(/公開デモ作品だけ/)).toBeInTheDocument();
		await expect.element(getByText(/読書位置も共有/)).toBeInTheDocument();
	});
	test('公開デモID/PWは両方設定時のみ表示し、ログイン権限のデバッグ表示を出さない', async () => {
		vi.stubEnv('VITE_PUBLIC_DEMO_EMAIL', ' demo@example.test ');
		vi.stubEnv('VITE_PUBLIC_DEMO_PASSWORD', 'sample-demo-password');
		const { getByText, queryByText } = await render(
			<authContext.Provider
				value={{
					user: null,
					isPending: false,
					isAuthenticated: false,
					role: null,
					refreshAuth: vi.fn(),
					signOut: vi.fn(),
					logout: vi.fn(),
				}}
			>
				<MemoryRouter><Login /></MemoryRouter>
			</authContext.Provider>,
		);

		await expect.element(getByText('demo@example.test')).toBeInTheDocument();
		await expect.element(getByText('sample-demo-password')).toBeInTheDocument();
		expect(queryByText(/現在の権限/)).toBeNull();
	});

	test('公開デモID/PWが揃わなければ資格情報を表示しない', async () => {
		vi.stubEnv('VITE_PUBLIC_DEMO_EMAIL', 'demo@example.test');
		vi.stubEnv('VITE_PUBLIC_DEMO_PASSWORD', '');
		const { getByText, queryByText } = await render(
			<authContext.Provider
				value={{
					user: null,
					isPending: false,
					isAuthenticated: false,
					role: null,
					refreshAuth: vi.fn(),
					signOut: vi.fn(),
					logout: vi.fn(),
				}}
			>
				<MemoryRouter><Login /></MemoryRouter>
			</authContext.Provider>,
		);

		await expect.element(getByText(/現在公開準備中/)).toBeInTheDocument();
		expect(queryByText('demo@example.test')).toBeNull();
	});

});
