import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render } from 'vitest-browser-react';
import { MemoryRouter } from 'react-router-dom';
import CreateUserForm from '@/components/forms/CreateUser';
import { toast } from 'sonner';

vi.mock('@/api/admin-users', () => ({ createUser: vi.fn() }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { createUser } from '@/api/admin-users';

beforeEach(() => {
  vi.resetAllMocks();
});

describe('CreateUserForm', () => {
  test('shows confirmation when admin selected and aborts on cancel', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (createUser as any).mockResolvedValue({ ok: true });

    const rendered = await render(
      <MemoryRouter>
        <CreateUserForm />
      </MemoryRouter>,
    );

    const { getByLabelText, getByText } = rendered;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const queryByText = (rendered as any).queryByText as (text: string) => HTMLElement | null;

    const nameInput = getByLabelText('表示名') as unknown as HTMLInputElement;
    nameInput.value = 'A';
    nameInput.dispatchEvent(new Event('input', { bubbles: true }));

    const emailInput = getByLabelText('メールアドレス') as unknown as HTMLInputElement;
    emailInput.value = 'a@b.com';
    emailInput.dispatchEvent(new Event('input', { bubbles: true }));

    const passwordInput = getByLabelText('初期パスワード') as unknown as HTMLInputElement;
    passwordInput.value = 'password';
    passwordInput.dispatchEvent(new Event('input', { bubbles: true }));

    const roleSelect = getByLabelText('権限') as unknown as HTMLSelectElement;
    roleSelect.value = 'admin';
    roleSelect.dispatchEvent(new Event('change', { bubbles: true }));

    const submitBtn = getByText('作成') as unknown as HTMLElement;
    submitBtn.click();

    // Dialog should be visible
    await expect.element(getByText('管理者権限の付与')).toBeInTheDocument();
    // API should not have been called yet
    expect(createUser).not.toHaveBeenCalled();

    // Cancel
    const cancelBtn = getByText('キャンセル') as unknown as HTMLElement;
    cancelBtn.click();
    await expect.element(queryByText('管理者権限の付与')).not.toBeInTheDocument();
    expect(createUser).not.toHaveBeenCalled();
  });

  test('calls createUser and shows error when API fails for normal user', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (createUser as any).mockResolvedValue({ ok: false, error: 'bad' });

    const rendered = await render(
      <MemoryRouter>
        <CreateUserForm />
      </MemoryRouter>,
    );

    const { getByLabelText, getByText } = rendered;

    const nameInput2 = getByLabelText('表示名') as unknown as HTMLInputElement;
    nameInput2.value = 'A';
    nameInput2.dispatchEvent(new Event('input', { bubbles: true }));

    const emailInput2 = getByLabelText('メールアドレス') as unknown as HTMLInputElement;
    emailInput2.value = 'a@b.com';
    emailInput2.dispatchEvent(new Event('input', { bubbles: true }));

    const passwordInput2 = getByLabelText('初期パスワード') as unknown as HTMLInputElement;
    passwordInput2.value = 'password';
    passwordInput2.dispatchEvent(new Event('input', { bubbles: true }));

    const roleSelect2 = getByLabelText('権限') as unknown as HTMLSelectElement;
    roleSelect2.value = 'user';
    roleSelect2.dispatchEvent(new Event('change', { bubbles: true }));

    const submitBtn2 = getByText('作成') as unknown as HTMLElement;
    submitBtn2.click();

    expect(createUser).toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalled();
  });
});
