import { expect, test } from '@playwright/test';

test('logs in with the E2E initial admin', async ({ page }) => {
	const email = process.env.E2E_ADMIN_EMAIL;
	const password = process.env.E2E_ADMIN_PASSWORD;

	if (!email || !password) {
		throw new Error('E2E_ADMIN_EMAIL and E2E_ADMIN_PASSWORD are required');
	}

	await page.goto('/login');
	await page.locator('input[name="userInput"]').fill(email);
	await page.locator('input[name="passwordInput"]').fill(password);
	await page.locator('button[type="submit"]').click();

	await expect(page).toHaveURL((url) => url.pathname === '/');
	await expect(page.getByPlaceholder('Search books or authors...')).toBeVisible();
});
