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
});
