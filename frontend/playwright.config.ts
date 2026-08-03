import { defineConfig } from '@playwright/test';

export default defineConfig({
	testDir: './e2e',
	use: {
		baseURL: 'http://localhost:5173',
		trace: 'on-first-retry',
		screenshot: 'only-on-failure',
	},
	webServer: {
		command: 'sh ./e2e/start-servers.sh',
		url: 'http://localhost:5173',
		reuseExistingServer: false,
	},
});
