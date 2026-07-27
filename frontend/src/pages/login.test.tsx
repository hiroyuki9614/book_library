import { describe, test, expect, vi } from 'vitest';
import { render, fireEvent } from 'vitest-browser-react';
import { MemoryRouter } from 'react-router-dom';
import Login from './Login';
import { authContext } from '@/contexts/authContext';

vi.mock('@/lib/auth-client', () => ({
  authClient: {
    signInEmail: vi.fn(),
    getSession: vi.fn(),
    signOut: vi.fn(),
  },
}));

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

import { authClient } from '@/lib/auth-client';
const mockedAuth = vi.mocked(authClient, { shallow: true });
import { toast } from 'sonner';

describe('Login flow', () => {
  test('calls authClient.signInEmail on submit (success)', async () => {
    mockedAuth.signInEmail.mockResolvedValue({ ok: true });

    const { getByLabelText, getByText } = render(
      <authContext.Provider value={{ session: null, user: null, isPending: false, isAuthenticated: false, role: null, signOut: vi.fn(), logout: vi.fn() }}>
        <MemoryRouter>
          <Login />
        </MemoryRouter>
      </authContext.Provider>,
    );

    const email = getByLabelText('ユーザーID');
    const password = getByLabelText('パスワード');
    const submit = getByText('Submit');

    await fireEvent.input(email, { target: { value: 'alice@example.com' } });
    await fireEvent.input(password, { target: { value: 'password' } });
    await fireEvent.click(submit);

    expect(authClient.signInEmail).toHaveBeenCalledWith({ email: 'alice@example.com', password: 'password' });
    expect(toast.success).toHaveBeenCalled();
  });

  test('shows error toast on sign-in failure', async () => {
    mockedAuth.signInEmail.mockResolvedValue({ ok: false, error: 'invalid' });

    const { getByLabelText, getByText } = render(
      <authContext.Provider value={{ session: null, user: null, isPending: false, isAuthenticated: false, role: null, signOut: vi.fn(), logout: vi.fn() }}>
        <MemoryRouter>
          <Login />
        </MemoryRouter>
      </authContext.Provider>,
    );

    const email = getByLabelText('ユーザーID');
    const password = getByLabelText('パスワード');
    const submit = getByText('Submit');

    await fireEvent.input(email, { target: { value: 'alice@example.com' } });
    await fireEvent.input(password, { target: { value: 'wrongpass' } });
    await fireEvent.click(submit);

    expect(authClient.signInEmail).toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalled();
  });
});
