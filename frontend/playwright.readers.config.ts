import { defineConfig } from '@playwright/test';
export default defineConfig({
	testDir: './e2e/readers',
	timeout: 90_000,
	workers: 1,
	use: {
		baseURL: 'http://127.0.0.1:5174',
		viewport: { width: 1100, height: 850 },
		screenshot: 'only-on-failure',
		trace: process.env.READER_ACTUAL_EPUB_PATH ? 'off' : 'retain-on-failure',
	},
	webServer: {
		command:
			'npx vite build --config vite.readers.config.ts && npx vite preview --outDir ../.tmp/readers-dist --host 127.0.0.1 --port 5174 --strictPort',
		url: 'http://127.0.0.1:5174/e2e/readers/harness.html',
		timeout: 120_000,
		env: { VITE_API_BASE_URL: 'http://127.0.0.1:5174' },
	},
});
