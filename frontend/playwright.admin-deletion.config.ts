import { defineConfig } from '@playwright/test';
import base from './playwright.config';
export default defineConfig({
 ...base,
 testMatch: 'admin-deletion.spec.ts',
 testIgnore: ['**/readers/**'],
 workers: 1,
 timeout: 90_000,
 use: { ...base.use, viewport: { width: 1000, height: 950 },
  headless: process.env.E2E_HEADED !== 'true',
  launchOptions: process.env.E2E_HEADED === 'true' ? { executablePath: '/usr/bin/google-chrome' } : {},
  trace: 'off', screenshot: 'off',
 },
});
