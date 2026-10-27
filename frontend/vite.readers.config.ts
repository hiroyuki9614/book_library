import { defineConfig, mergeConfig } from 'vite';
import config from './vite.config';
export default mergeConfig(
	config,
	defineConfig({
		build: { outDir: '../.tmp/readers-dist', emptyOutDir: true, rollupOptions: { input: 'e2e/readers/harness.html' } },
	}),
);
