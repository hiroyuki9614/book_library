import { afterEach, describe, expect, test, vi } from 'vitest';
import { getPublicDemoCredentials, getPublicAdminDemoCredentials } from './public-demo-credentials';

afterEach(() => vi.unstubAllEnvs());

describe('public demo credentials', () => {
	test('管理者デモは明示的な専用設定が両方ある場合のみ公開する', () => {
		vi.stubEnv('VITE_PUBLIC_DEMO_EMAIL', 'user@example.test');
		vi.stubEnv('VITE_PUBLIC_DEMO_PASSWORD', 'user-demo-password');
		vi.stubEnv('VITE_PUBLIC_DEMO_ADMIN_EMAIL', '');
		vi.stubEnv('VITE_PUBLIC_DEMO_ADMIN_PASSWORD', '');
		expect(getPublicAdminDemoCredentials()).toBeNull();
		vi.stubEnv('VITE_PUBLIC_DEMO_ADMIN_EMAIL', ' admin-demo@example.test ');
		expect(getPublicAdminDemoCredentials()).toBeNull();
		vi.stubEnv('VITE_PUBLIC_DEMO_ADMIN_EMAIL', '');
		vi.stubEnv('VITE_PUBLIC_DEMO_ADMIN_PASSWORD', 'admin-demo-password');
		expect(getPublicAdminDemoCredentials()).toBeNull();
		vi.stubEnv('VITE_PUBLIC_DEMO_ADMIN_EMAIL', ' admin-demo@example.test ');
		expect(getPublicAdminDemoCredentials()).toEqual({ email: 'admin-demo@example.test', password: 'admin-demo-password' });
		expect(getPublicDemoCredentials()?.email).toBe('user@example.test');
	});
	test('両方未設定または片方のみ設定された場合は公開しない', () => {
		vi.stubEnv('VITE_PUBLIC_DEMO_EMAIL', '');
		vi.stubEnv('VITE_PUBLIC_DEMO_PASSWORD', '');
		expect(getPublicDemoCredentials()).toBeNull();

		vi.stubEnv('VITE_PUBLIC_DEMO_EMAIL', 'demo@example.test');
		expect(getPublicDemoCredentials()).toBeNull();

		vi.stubEnv('VITE_PUBLIC_DEMO_EMAIL', '');
		vi.stubEnv('VITE_PUBLIC_DEMO_PASSWORD', 'sample-demo-password');
		expect(getPublicDemoCredentials()).toBeNull();
	});

	test('公開用のIDとパスワードが両方設定された場合のみ返す', () => {
		vi.stubEnv('VITE_PUBLIC_DEMO_EMAIL', ' demo@example.test ');
		vi.stubEnv('VITE_PUBLIC_DEMO_PASSWORD', 'sample-demo-password');
		expect(getPublicDemoCredentials()).toEqual({
			email: 'demo@example.test',
			password: 'sample-demo-password',
		});
	});
});
