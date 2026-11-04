/**
 * Vite publishes VITE_* values in the browser bundle. Configure these only
 * with newly provisioned demo accounts explicitly approved for public use.
 * The admin demo has full admin privileges. Never reuse operator credentials.
 */
export type PublicDemoCredentials = { email: string; password: string };

export function getPublicDemoCredentials(): PublicDemoCredentials | null {
	const email = import.meta.env.VITE_PUBLIC_DEMO_EMAIL?.trim();
	const password = import.meta.env.VITE_PUBLIC_DEMO_PASSWORD;

	return email && password ? { email, password } : null;
}

export function getPublicAdminDemoCredentials(): PublicDemoCredentials | null {
	const email = import.meta.env.VITE_PUBLIC_DEMO_ADMIN_EMAIL?.trim();
	const password = import.meta.env.VITE_PUBLIC_DEMO_ADMIN_PASSWORD;

	return email && password ? { email, password } : null;
}
