import { describe, expect, test } from 'vitest';
import { readOptionalSeedPassword } from '../src/lib/seedCredentials.js';

describe('readOptionalSeedPassword', () => {
	test('does not create a credential when the environment variable is absent', () => {
		expect(readOptionalSeedPassword(undefined)).toBeUndefined();
	});

	test('does not treat whitespace as a password', () => {
		expect(readOptionalSeedPassword('   ')).toBeUndefined();
	});

	test('returns the explicitly supplied password', () => {
		expect(readOptionalSeedPassword('temporary-development-secret')).toBe('temporary-development-secret');
	});
});
