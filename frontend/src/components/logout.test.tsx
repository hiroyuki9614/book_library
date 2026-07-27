import { describe, test, expect, vi } from 'vitest';
import { render } from 'vitest-browser-react';
import { AuthProvider } from '@/contexts/AuthProvider';
import { authClient } from '@/lib/auth-client';
import { getCurrentUser } from '@/api/me';
import { useAuth } from '@/contexts/useAuth';

vi.mock('@/lib/auth-client', () => ({
  authClient: {
    signOut: vi.fn(),
  },
}));

vi.mock('@/api/me', () => ({
  getCurrentUser: vi.fn(),
}));

function TestLogoutButton() {
  const { logout } = useAuth();

  return (
    <button type='button' onClick={() => logout && logout()}>
      Logout
    </button>
  );
}

describe('logout', () => {
  test('calls authClient.signOut when logout invoked', async () => {
    vi.mocked(getCurrentUser).mockResolvedValue({
      id: 1,
      name: 'User',
      email: 'a@example.com',
      role: 'user',
    });

    const { getByText } = await render(
      <AuthProvider>
        <TestLogoutButton />
      </AuthProvider>,
    );

    const btn = getByText('Logout');
    await btn.click();

    expect(authClient.signOut).toHaveBeenCalled();
  });
});
