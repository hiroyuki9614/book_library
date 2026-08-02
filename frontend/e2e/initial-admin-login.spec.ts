import { expect, test } from '@playwright/test';
import { getE2EConfig } from '../src/e2e/e2eEnv.js';

test('initial admin can log in with supplied E2E credentials', async ({ page }) => {
	const config = getE2EConfig(process.env);

	await page.goto('/login');
	await page.getByLabel('ユーザーID').fill(config.email);
	await page.getByLabel('パスワード').fill(config.password);
	await page.getByRole('button', { name: 'Submit' }).click();

	await expect(page).not.toHaveURL(/\/login$/);
});
