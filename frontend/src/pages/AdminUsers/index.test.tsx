import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { render } from 'vitest-browser-react';

const mocks = vi.hoisted(() => ({
	createAdminUser: vi.fn(),
	disableAdminUser: vi.fn(),
	fetchAdminUsers: vi.fn(),
	resetAdminUserPassword: vi.fn(),
	restoreAdminUser: vi.fn(),
}));

vi.mock('@/api/admin', () => ({
	createAdminUser: mocks.createAdminUser,
	disableAdminUser: mocks.disableAdminUser,
	fetchAdminUsers: mocks.fetchAdminUsers,
	resetAdminUserPassword: mocks.resetAdminUserPassword,
	restoreAdminUser: mocks.restoreAdminUser,
}));

import AdminUsers from './index';

const activeUser = {
	id: 7,
	email: 'reader@example.com',
	name: 'Reader',
	deletedAt: null,
	createdAt: '2026-08-01T00:00:00.000Z',
};

const disabledUser = {
	id: 8,
	email: 'stopped@example.com',
	name: 'Stopped',
	deletedAt: '2026-08-20T00:00:00.000Z',
	createdAt: '2026-08-02T00:00:00.000Z',
};

describe('AdminUsers', () => {
	beforeEach(() => {
		mocks.fetchAdminUsers.mockResolvedValue([activeUser, disabledUser]);
		mocks.createAdminUser.mockResolvedValue({
			id: 9,
			email: 'new@example.com',
			name: 'New User',
			deletedAt: null,
			createdAt: '2026-08-24T00:00:00.000Z',
		});
		mocks.disableAdminUser.mockResolvedValue({ ...activeUser, deletedAt: '2026-08-24T01:00:00.000Z' });
		mocks.restoreAdminUser.mockResolvedValue({ ...disabledUser, deletedAt: null });
		mocks.resetAdminUserPassword.mockResolvedValue({ id: 7, passwordReset: true });
	});

	afterEach(() => {
		vi.restoreAllMocks();
		for (const mock of Object.values(mocks)) mock.mockReset();
	});

	test('active・利用停止ユーザーをbackendから表示する', async () => {
		const { getByText } = await render(<AdminUsers />);
		await vi.waitFor(() => expect(mocks.fetchAdminUsers).toHaveBeenCalledTimes(1));
		await expect.element(getByText('reader@example.com')).toBeInTheDocument();
		await expect.element(getByText('stopped@example.com')).toBeInTheDocument();
	});

	test('一般ユーザーを登録して一覧へ反映する', async () => {
		const { getByRole, getByText } = await render(<AdminUsers />);
		await getByRole('textbox', { name: 'メールアドレス' }).fill('new@example.com');
		await getByRole('textbox', { name: '表示名' }).fill('New User');
		await getByRole('textbox', { name: '初期パスワード' }).fill('password123');
		await getByRole('button', { name: '登録', exact: true }).click();

		await vi.waitFor(() => expect(mocks.createAdminUser).toHaveBeenCalledWith({
			email: 'new@example.com',
			name: 'New User',
			password: 'password123',
		}));
		await expect.element(getByText('new@example.com')).toBeInTheDocument();
	});

	test('利用停止と利用再開を反映する', async () => {
		const { getByRole } = await render(<AdminUsers />);
		await vi.waitFor(() => expect(mocks.fetchAdminUsers).toHaveBeenCalledTimes(1));

		await getByRole('button', { name: '利用停止', exact: true }).click();
		await vi.waitFor(() => expect(mocks.disableAdminUser).toHaveBeenCalledWith(7));

		await getByRole('button', { name: '利用再開', exact: true }).click();
		await vi.waitFor(() => expect(mocks.restoreAdminUser).toHaveBeenCalledWith(8));
	});

	test('仮パスワードを再設定する', async () => {
		const { getByRole } = await render(<AdminUsers />);
		await vi.waitFor(() => expect(mocks.fetchAdminUsers).toHaveBeenCalledTimes(1));

		const resetButtons = getByRole('button', { name: '仮パスワード', exact: true });
		await resetButtons.first().click();
		await getByRole('textbox', { name: 'Reader の仮パスワード' }).fill('temporary123');
		await getByRole('button', { name: '設定', exact: true }).click();

		await vi.waitFor(() => expect(mocks.resetAdminUserPassword).toHaveBeenCalledWith(7, 'temporary123'));
	});
});
