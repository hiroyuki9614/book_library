import { describe, expect, test } from 'vitest';
import { PortfolioSafetyError, portfolioFrontendOrigin } from '../src/lib/portfolioRuntime.js';

describe('portfolioFrontendOrigin', () => {
	test('returns the configured production origin', () => {
		expect(portfolioFrontendOrigin({ FRONTEND_URL: 'https://belib.example' })).toBe('https://belib.example');
	});

	test.each([
		undefined,
		'not a URL',
		'ftp://belib.example/',
		'https://user:password@belib.example/',
		'https://belib.example/path',
		'https://belib.example/?query=1',
		'https://belib.example/',
		'https://belib.example:443',
		' https://belib.example',
	])('rejects an invalid or non-origin FRONTEND_URL: %s', (value) => {
		expect(() => portfolioFrontendOrigin({ FRONTEND_URL: value })).toThrow(PortfolioSafetyError);
	});
});
