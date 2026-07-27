import { beforeEach, describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';
import GuestRoute from '@/components/GuestRoute';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuthProvider } from '@/contexts/AuthProvider';
import RequireAuth from '@/components/RequireAuth';

vi.mock('@/api/me', () => ({
  getCurrentUser: vi.fn(),
}));

import { getCurrentUser } from '@/api/me';

beforeEach(() => {
  localStorage.clear();
  vi.resetAllMocks();
});

describe('localStorage should not be trusted for auth state', () => {
  test('RequireAuth redirects to login even when localStorage contains auth flags', async () => {
    // Simulate stale localStorage that says user is authenticated
    localStorage.setItem('role', 'admin');
    localStorage.setItem('isAuthenticated', 'true');

    vi.mocked(getCurrentUser).mockRejectedValue(new Error('Unauthorized'));

    const rendered = await render(
      <MemoryRouter initialEntries={["/private"]}>
        <AuthProvider>
          <Routes>
            <Route element={<RequireAuth />}>
              <Route path='/private' element={<div>Private page</div>} />
            </Route>
            <Route path='/login' element={<div>Login page</div>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );

    const { getByText } = rendered;
    await expect.element(getByText('Login page')).toBeInTheDocument();
  });

  test('GuestRoute does not redirect to home when localStorage indicates authenticated but session is empty', async () => {
    localStorage.setItem('role', 'admin');
    localStorage.setItem('isAuthenticated', 'true');

    vi.mocked(getCurrentUser).mockRejectedValue(new Error('Unauthorized'));

    const rendered2 = await render(
      <MemoryRouter initialEntries={["/login"]}>
        <AuthProvider>
          <Routes>
            <Route element={<RequireAuth />}> {/* keep page guard to ensure provider finishes */}
              <Route path='/private' element={<div>Private page</div>} />
            </Route>
            <Route element={<GuestRoute />}>
              <Route path='/login' element={<div>Login page</div>} />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );

    const { getByText: getByText2 } = rendered2;
    await expect.element(getByText2('Login page')).toBeInTheDocument();
  });
});
