import { beforeEach, describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';
import { MemoryRouter } from 'react-router-dom';
import { getCurrentUser } from '@/api/me';
import { AuthProvider } from './AuthProvider';
import { useAuth } from './useAuth';

vi.mock('@/api/me', () => ({
	getCurrentUser: vi.fn(),
}));

vi.mock('@/lib/auth-client', () => ({
	authClient: {
		signOut: vi.fn(),
	},
}));

function AuthState() {
	const { isPending, role, user } = useAuth();

	return (
		<div>
			<span>{isPending ? 'loading' : 'ready'}</span>
			<span>{user?.email ?? 'no-user'}</span>
			<span>{role ?? 'no-role'}</span>
		</div>
	);
}

describe('AuthProvider session loading', () => {
	beforeEach(() => {
		vi.resetAllMocks();
	});

	test('認証済みユーザーAPI成功時にuserとroleを設定する', async () => {
		vi.mocked(getCurrentUser).mockResolvedValue({
			id: 1,
			name: 'Admin',
			email: 'admin@example.com',
			role: 'admin',
		});

		const { getByText } = await render(
			<MemoryRouter>
				<AuthProvider>
					<AuthState />
				</AuthProvider>
			</MemoryRouter>,
		);

		await expect.element(getByText('admin@example.com')).toBeInTheDocument();
		await expect.element(getByText('admin', { exact: true })).toBeInTheDocument();
		await expect.element(getByText('ready')).toBeInTheDocument();
	});

	test('認証済みユーザーAPI失敗時にもloadingを解除して未認証状態にする', async () => {
		vi.mocked(getCurrentUser).mockRejectedValue(new Error('network failure'));

		const { getByText } = await render(
			<MemoryRouter>
				<AuthProvider>
					<AuthState />
				</AuthProvider>
			</MemoryRouter>,
		);

		await expect.element(getByText('ready')).toBeInTheDocument();
		await expect.element(getByText('no-user')).toBeInTheDocument();
		await expect.element(getByText('no-role')).toBeInTheDocument();
	});
});
