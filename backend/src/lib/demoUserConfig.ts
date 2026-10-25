export type DemoUserConfig = {
	email: string;
	name: string;
	password: string;
};

export function readDemoUserConfig(environment: NodeJS.ProcessEnv): DemoUserConfig {
	const email = environment.DEMO_USER_EMAIL?.trim().toLowerCase();
	const name = environment.DEMO_USER_NAME?.trim();
	const password = environment.DEMO_USER_PASSWORD;

	if (!email || !name || !password || !password.trim()) {
		throw new Error('DEMO_USER_EMAIL, DEMO_USER_NAME, and DEMO_USER_PASSWORD are required');
	}
	if (email.length > 254 || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
		throw new Error('DEMO_USER_EMAIL must be a valid email address');
	}
	if (name.length > 255) {
		throw new Error('DEMO_USER_NAME must be at most 255 characters');
	}
	if (password.length < 12) {
		throw new Error('DEMO_USER_PASSWORD must be at least 12 characters');
	}

	return { email, name, password };
}
