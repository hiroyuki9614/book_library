/**
 * Vite publishes VITE_* values in the browser bundle. Configure these only
 * with an intentionally public, restricted-role demo account.
 */
export function getPublicDemoCredentials(): { email: string; password: string } | null {
	const email = import.meta.env.VITE_PUBLIC_DEMO_EMAIL?.trim();
	const password = import.meta.env.VITE_PUBLIC_DEMO_PASSWORD;

	return email && password ? { email, password } : null;
}
