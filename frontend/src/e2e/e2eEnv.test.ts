import { describe, expect, test } from 'vitest';
import { getE2EConfig, getDatabaseName } from './e2eEnv';

describe('E2E environment', () => {
	test('accepts only an explicitly named database ending in _e2e', () => {
		expect(getDatabaseName('postgresql://user:pass@localhost/booklib_e2e')).toBe('booklib_e2e');
		expect(getDatabaseName('postgresql://user:pass@localhost/booklib_test')).toBe('booklib_test');
		expect(getDatabaseName('not-a-database-url')).toBeNull();
		expect(getDatabaseName('postgresql://user:pass@localhost/')).toBeNull();
		expect(() => getE2EConfig({
			E2E_ADMIN_EMAIL: 'admin@example.test',
			E2E_ADMIN_NAME: 'E2E Admin',
			E2E_ADMIN_PASSWORD: 'a-secure-password',
			E2E_DATABASE_URL: 'postgresql://user:pass@localhost/booklib',
		})).toThrow('E2E_DATABASE_URL must reference an _e2e database');
	});

	test('rejects empty and non-PostgreSQL database URLs', () => {
		const baseConfig = {
			E2E_ADMIN_EMAIL: 'admin@example.test',
			E2E_ADMIN_NAME: 'E2E Admin',
			E2E_ADMIN_PASSWORD: 'a-secure-password',
		};

		expect(() => getE2EConfig({ ...baseConfig, E2E_DATABASE_URL: '' })).toThrow();
		expect(() => getE2EConfig({ ...baseConfig, E2E_DATABASE_URL: 'mysql://localhost/booklib_e2e' })).toThrow();
	});

	test('rejects a missing password before the E2E starts', () => {
		expect(() => getE2EConfig({
			E2E_ADMIN_EMAIL: 'admin@example.test',
			E2E_ADMIN_NAME: 'E2E Admin',
			E2E_DATABASE_URL: 'postgresql://user:pass@localhost/booklib_e2e',
		})).toThrow('E2E_ADMIN_EMAIL, E2E_ADMIN_NAME, E2E_ADMIN_PASSWORD, and E2E_DATABASE_URL are required');
	});

	test('rejects production environment without exposing the database URL', () => {
		const databaseUrl = 'postgresql://secret:password@prod.example/booklib_e2e';
		let errorMessage = '';
		try {
			getE2EConfig({
				E2E_ADMIN_EMAIL: 'admin@example.test',
				E2E_ADMIN_NAME: 'E2E Admin',
				E2E_ADMIN_PASSWORD: 'a-secure-password',
				E2E_DATABASE_URL: databaseUrl,
				NODE_ENV: 'production',
			});
		} catch (error) {
			errorMessage = error instanceof Error ? error.message : String(error);
		}
		expect(errorMessage).toBe('E2E cannot run in production environment');
		expect(errorMessage).not.toContain(databaseUrl);
	});
});
