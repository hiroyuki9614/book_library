import { spawnSync } from 'node:child_process';

type E2EAdminConfig = {
	email: string;
	name: string;
	password: string;
	databaseUrl: string;
};

type Spawn = typeof spawnSync;

export function runInitialAdminSetup(config: E2EAdminConfig, backendDirectory: string, spawn: Spawn = spawnSync) {
	const npmCommand = process.platform === 'win32' ? 'npm.cmd' : 'npm';
	const result = spawn(npmCommand, ['run', 'create:initial-admin'], {
		cwd: backendDirectory,
		env: {
			...process.env,
			DATABASE_URL: config.databaseUrl,
			INITIAL_ADMIN_EMAIL: config.email,
			INITIAL_ADMIN_NAME: config.name,
			INITIAL_ADMIN_PASSWORD: config.password,
		},
		stdio: 'pipe',
		shell: false,
		encoding: 'utf8',
	});

	if (result.status !== 0) {
		throw new Error('E2E initial admin setup failed');
	}
}
