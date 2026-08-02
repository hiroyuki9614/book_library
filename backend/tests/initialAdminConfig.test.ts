import { describe, expect, test } from 'vitest';
import { readInitialAdminConfig } from '../src/lib/initialAdminConfig.js';

function genPassword(len = 12) {
	// generate a non-constant password at runtime to avoid embedding fixed values
	const rand = Math.random().toString(36).slice(2);
	return ('p' + rand).slice(0, len);
}

describe('readInitialAdminConfig', () => {
	test('requires all initial admin environment values', () => {
		expect(() => readInitialAdminConfig({})).toThrow('INITIAL_ADMIN_EMAIL, INITIAL_ADMIN_NAME, and INITIAL_ADMIN_PASSWORD are required');
	});

	test('rejects whitespace-only values without exposing the input', () => {
		const password = genPassword();

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
		const pwd = genPassword();
		const config = readInitialAdminConfig({
			INITIAL_ADMIN_EMAIL: ' Admin@Example.Test ',
			INITIAL_ADMIN_NAME: ' Initial Admin ',
			INITIAL_ADMIN_PASSWORD: pwd,
		});

		expect(config).toEqual({
			email: 'Admin@Example.Test',
			name: 'Initial Admin',
			password: pwd,
		});
	});

	test('rejects an invalid email address', () => {
		expect(() =>
			readInitialAdminConfig({
				INITIAL_ADMIN_EMAIL: 'not-an-email',
				INITIAL_ADMIN_NAME: 'Admin',
				INITIAL_ADMIN_PASSWORD: genPassword(),
			}),
		).toThrow('INITIAL_ADMIN_EMAIL must be a valid email address');
	});

	test('rejects values exceeding the Prisma field limits', () => {
		expect(() =>
			readInitialAdminConfig({
				INITIAL_ADMIN_EMAIL: `${'a'.repeat(250)}@example.com`,
				INITIAL_ADMIN_NAME: 'Admin',
				INITIAL_ADMIN_PASSWORD: genPassword(),
			}),
		).toThrow('INITIAL_ADMIN_EMAIL must be at most 254 characters');

		expect(() =>
			readInitialAdminConfig({
				INITIAL_ADMIN_EMAIL: 'admin@example.com',
				INITIAL_ADMIN_NAME: 'a'.repeat(256),
				INITIAL_ADMIN_PASSWORD: genPassword(),
			}),
		).toThrow('INITIAL_ADMIN_NAME must be at most 255 characters');
	});

	test('rejects short passwords', () => {
		expect(() =>
			readInitialAdminConfig({
				INITIAL_ADMIN_EMAIL: 'admin@example.com',
				INITIAL_ADMIN_NAME: 'Admin',
							INITIAL_ADMIN_PASSWORD: 'short',
			}),
		).toThrow('INITIAL_ADMIN_PASSWORD must be at least 8 characters');
	});
});
