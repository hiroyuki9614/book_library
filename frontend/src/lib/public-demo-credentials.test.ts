import { afterEach, describe, expect, test, vi } from 'vitest';
import { getPublicDemoCredentials } from './public-demo-credentials';

afterEach(() => vi.unstubAllEnvs());

describe('public demo credentials', () => {
	test('admin Vite values are ignored while the public general demo remains available', () => {
		vi.stubEnv('VITE_PUBLIC_DEMO_EMAIL', 'user@example.test');
		vi.stubEnv('VITE_PUBLIC_DEMO_PASSWORD', 'user-demo-password');
		vi.stubEnv('VITE_PUBLIC_DEMO_ADMIN_EMAIL', 'synthetic-admin@example.test');
		vi.stubEnv('VITE_PUBLIC_DEMO_ADMIN_PASSWORD', 'synthetic-admin-password');
		expect(getPublicDemoCredentials()).toEqual({ email: 'user@example.test', password: 'user-demo-password' });
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
