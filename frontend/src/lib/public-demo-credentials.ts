/** Vite publishes these public demo values in the browser bundle. */
export type PublicDemoCredentials = { email: string; password: string };

export function getPublicDemoCredentials(): PublicDemoCredentials | null {
	const email = import.meta.env.VITE_PUBLIC_DEMO_EMAIL?.trim();
	const password = import.meta.env.VITE_PUBLIC_DEMO_PASSWORD;

	return email && password ? { email, password } : null;
}
