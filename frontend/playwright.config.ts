import path from 'node:path';
import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { getE2EConfig } from './src/e2e/e2eEnv.js';

const e2eConfig = getE2EConfig(process.env);
const configDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryDirectory = path.resolve(configDirectory, '..');

process.env.DATABASE_URL = e2eConfig.databaseUrl;
process.env.VITE_API_BASE_URL = 'http://localhost:3000';
process.env.FRONTEND_URL = 'http://localhost:5173';

export default defineConfig({
	testDir: './e2e',
	globalSetup: './e2e/global-setup.ts',
	timeout: 120_000,
	reporter: [['list'], ['html', { outputFolder: 'playwright-report', open: 'never' }]],
	use: {
		baseURL: 'http://localhost:5173',
		trace: 'on-first-retry',
		screenshot: 'only-on-failure',
		video: 'retain-on-failure',
	},
	webServer: [
		{
			command: 'npm run dev',
			cwd: path.join(repositoryDirectory, 'backend'),
			url: 'http://localhost:3000/health',
			timeout: 120_000,
			reuseExistingServer: false,
		},
		{
			command: 'npm run dev',
			cwd: path.join(repositoryDirectory, 'frontend'),
			url: 'http://localhost:5173',
			timeout: 120_000,
			reuseExistingServer: false,
		},
	],
});
