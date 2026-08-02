export type InitialAdminConfig = {
	email: string;
	name: string;
	password: string;
};

const KNOWN_DEFAULT_PASSWORDS = new Set(['AdminPass123!', 'DummyPass123!', 'E2eAdminPass123!', 'E2ePass123!']);

export function readInitialAdminConfig(environment: NodeJS.ProcessEnv): InitialAdminConfig {
	const email = environment.INITIAL_ADMIN_EMAIL;
	const name = environment.INITIAL_ADMIN_NAME;
	const password = environment.INITIAL_ADMIN_PASSWORD;

	if (!email?.trim() || !name?.trim() || !password?.trim()) {
		throw new Error('INITIAL_ADMIN_EMAIL, INITIAL_ADMIN_NAME, and INITIAL_ADMIN_PASSWORD are required');
	}

	const normalizedEmail = email.trim();
	const normalizedName = name.trim();

	if (normalizedEmail.length > 254) {
		throw new Error('INITIAL_ADMIN_EMAIL must be at most 254 characters');
	}
	if (normalizedName.length > 255) {
		throw new Error('INITIAL_ADMIN_NAME must be at most 255 characters');
	}
	if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(normalizedEmail)) {
		throw new Error('INITIAL_ADMIN_EMAIL must be a valid email address');
	}
	if (password.length < 8) {
		throw new Error('INITIAL_ADMIN_PASSWORD must be at least 8 characters');
	}
	if (KNOWN_DEFAULT_PASSWORDS.has(password)) {
		throw new Error('INITIAL_ADMIN_PASSWORD must not be a known default password');
	}

	return {
		email: normalizedEmail,
		name: normalizedName,
		password,
	};
}
