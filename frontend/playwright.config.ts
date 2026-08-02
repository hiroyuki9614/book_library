import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 120000,
  expect: {
    timeout: 5000,
  },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    actionTimeout: 10000,
  },
  webServer: {
    // start-servers.sh is used to set up the E2E environment (E2E DB, env vars, start backend/frontend)
    command: 'sh ./e2e/start-servers.sh',
    url: 'http://localhost:5173',
    timeout: 120000,
    reuseExistingServer: false,
  },
});
