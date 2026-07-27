import { describe, test, expect, vi } from 'vitest';
import { render, fireEvent } from 'vitest-browser-react';
import { MemoryRouter } from 'react-router-dom';
import { AuthProvider } from '@/contexts/AuthProvider';
import { authClient } from '@/lib/auth-client';
import { useAuth } from '@/contexts/useAuth';

vi.mock('@/lib/auth-client', () => ({
  authClient: {
    signInEmail: vi.fn(),
    getSession: vi.fn(),
    signOut: vi.fn(),
  },
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
    const mocked = vi.mocked(authClient, { shallow: true });
    mocked.getSession.mockResolvedValue({ user: { id: 1, email: 'a', role: 'user' } });

    const { getByText } = render(
      <MemoryRouter>
        <AuthProvider>
          <TestLogoutButton />
        </AuthProvider>
      </MemoryRouter>,
    );

    const btn = getByText('Logout');
    await fireEvent.click(btn);

    expect(authClient.signOut).toHaveBeenCalled();
  });
});
