import { defineConfig } from 'vitest/config';
import tsconfigPaths from 'vite-tsconfig-paths';
export default defineConfig({ plugins: [tsconfigPaths()], test: { environment: 'node', include: ['src/api/books.test.ts','src/api/books.epub.test.ts'] } });
