import { describe, expect, test, vi } from 'vitest';
import { runInitialAdminSetup } from './runInitialAdminSetup';

describe('runInitialAdminSetup', () => {
	test('passes credentials through env and never through command arguments or a shell', () => {
		const spawn = vi.fn().mockReturnValue({ status: 0, stdout: '', stderr: '' });
		const config = {
			email: 'admin@example.test',
			name: 'E2E Admin',
			password: 'secret-that-must-not-appear',
			databaseUrl: 'postgresql://user:pass@localhost/booklib_e2e',
		};

		runInitialAdminSetup(config, '/backend', spawn);

		const [command, args, options] = spawn.mock.calls[0];
		expect(command).toMatch(/npm(\.cmd)?$/);
		expect(args).toEqual(['run', 'create:initial-admin']);
		expect(options.shell).toBe(false);
		expect(options.env.INITIAL_ADMIN_PASSWORD).toBe(config.password);
		expect(options.env.DATABASE_URL).toBe(config.databaseUrl);
		expect(JSON.stringify(args)).not.toContain(config.password);
	});

	test('does not expose child process output when setup fails', () => {
		const spawn = vi.fn().mockReturnValue({
			status: 1,
			stdout: configSecretOutput,
			stderr: configSecretOutput,
		});
		const config = {
			email: 'admin@example.test',
			name: 'E2E Admin',
			password: 'secret-that-must-not-appear',
			databaseUrl: 'postgresql://user:pass@localhost/booklib_e2e',
		};

		expect(() => runInitialAdminSetup(config, '/backend', spawn)).toThrow('E2E initial admin setup failed');
	});
});

const configSecretOutput = 'child output is intentionally not surfaced';
