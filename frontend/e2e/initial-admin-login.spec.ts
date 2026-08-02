<<<<<<< HEAD
import { expect, test } from '@playwright/test';
import { getE2EConfig } from '../src/e2e/e2eEnv.js';

test('initial admin can log in with supplied E2E credentials', async ({ page }) => {
	const config = getE2EConfig(process.env);

	await page.goto('/login');
	await page.getByLabel('ユーザーID').fill(config.email);
	await page.getByLabel('パスワード').fill(config.password);
	await page.getByRole('button', { name: 'Submit' }).click();

	await expect(page).not.toHaveURL(/\/login$/);
=======
import { test, expect } from '@playwright/test';

// The initial admin is created exactly once, in `frontend/e2e/start-servers.sh`,
// before the Playwright webServer is considered ready. This test only performs
// the login flow using the credentials that were used to create that admin; it
// must not create the admin itself, to avoid running the creation step twice.

test.describe('Initial admin end-to-end', () => {
  test('can login as the initial admin created for E2E', async ({ page }) => {
    const email = process.env.E2E_ADMIN_EMAIL;
    const name = process.env.E2E_ADMIN_NAME;
    const password = process.env.E2E_ADMIN_PASSWORD;

    if (!email || !name || !password) {
      throw new Error(
        'E2E tests require E2E_ADMIN_EMAIL, E2E_ADMIN_NAME, and E2E_ADMIN_PASSWORD environment variables to be set',
      );
    }

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
>>>>>>> f73ab65 (e2e: require env vars and avoid passing passwords on command line (fix PR #10 comments))
});
