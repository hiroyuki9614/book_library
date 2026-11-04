/**
 * Vite publishes VITE_* values in the browser bundle. Configure these only
 * with intentionally public demo accounts. An admin demo must belong to an
 * isolated demo environment, never the administrator of a private library.
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
