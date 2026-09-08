import { defineConfig } from 'vitest/config';
import { playwright } from '@vitest/browser-playwright';
import react from '@vitejs/plugin-react-swc';
import tsconfigPaths from 'vite-tsconfig-paths';

export default defineConfig({
	plugins: [react(), tsconfigPaths()],
	optimizeDeps: {
		include: ['@tanstack/react-query', 'react', 'react-dom', 'react-dom/client', 'react-router-dom', 'lucide-react', 'next-themes', 'vitest-browser-react'],
	},
	test: {
		exclude: ['**/node_modules/**', 'e2e/**', 'src/e2e/**'],
		browser: {
			enabled: true,
			provider: playwright(),
			// https://vitest.dev/config/browser/playwright
			instances: [{ browser: 'chromium' }],
		},
	},
});
