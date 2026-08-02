import { describe, expect, test } from 'vitest';
import { readInitialAdminConfig } from '../src/lib/initialAdminConfig.js';

describe('readInitialAdminConfig', () => {
	test('requires all initial admin environment values', () => {
		expect(() => readInitialAdminConfig({})).toThrow('INITIAL_ADMIN_EMAIL, INITIAL_ADMIN_NAME, and INITIAL_ADMIN_PASSWORD are required');
	});

	test('rejects whitespace-only values without exposing the input', () => {
		const password = 'secret-that-must-not-appear';

		expect(() =>
			readInitialAdminConfig({
				INITIAL_ADMIN_EMAIL: 'admin@example.test',
				INITIAL_ADMIN_NAME: 'Admin',
				INITIAL_ADMIN_PASSWORD: '   ',
			}),
		).toThrow('INITIAL_ADMIN_EMAIL, INITIAL_ADMIN_NAME, and INITIAL_ADMIN_PASSWORD are required');
		let errorMessage = '';
		try {
			readInitialAdminConfig({ INITIAL_ADMIN_EMAIL: password });
		} catch (error) {
			errorMessage = error instanceof Error ? error.message : String(error);
		}
		expect(errorMessage).not.toContain(password);
	});

	test('returns credentials without logging or changing them', () => {
		const config = readInitialAdminConfig({
			INITIAL_ADMIN_EMAIL: ' Admin@Example.Test ',
			INITIAL_ADMIN_NAME: ' Initial Admin ',
			INITIAL_ADMIN_PASSWORD: 'secret-that-must-not-appear',
		});

		expect(config).toEqual({
			email: 'Admin@Example.Test',
			name: 'Initial Admin',
			password: 'secret-that-must-not-appear',
		});
	});
});
