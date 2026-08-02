import { test, expect } from '@playwright/test';
import { spawnSync } from 'node:child_process';

// This test assumes backend at http://localhost:3000 and frontend at http://localhost:5173
// It also expects DATABASE_URL_TEST to be set and accessible to the create-initial-admin script.

test.describe('Initial admin end-to-end', () => {
  test('can create initial admin and login via UI', async ({ page }) => {
    const email = process.env.E2E_ADMIN_EMAIL ?? 'e2e-admin@example.com';
    const name = process.env.E2E_ADMIN_NAME ?? 'E2E Admin';
    const password = process.env.E2E_ADMIN_PASSWORD ?? 'E2ePass123!';

    // Run the create-initial-admin script
    const res = spawnSync('npm', ['run', 'create:initial-admin', '--silent', '--', `--INITIAL_ADMIN_EMAIL=${email}`, `--INITIAL_ADMIN_NAME=${name}`, `--INITIAL_ADMIN_PASSWORD=${password}`], {
      cwd: 'backend',
      env: { ...process.env },
      stdio: 'inherit',
      shell: true,
    });

    expect(res.status).toBe(0);

    // Visit frontend login page
    await page.goto('http://localhost:5173/login');

    await page.fill('input[name="email"]', email);
    await page.fill('input[name="password"]', password);
    await page.click('button[type="submit"]');

    // Wait for redirect to authenticated page
    await page.waitForURL('**/');

    // Check for an element visible only to authenticated users, e.g., logout button
    await expect(page.locator('text=Logout').first()).toBeVisible();
  });
});
