export type InitialAdminConfig = {
	email: string;
	name: string;
	password: string;
};

export function readInitialAdminConfig(environment: NodeJS.ProcessEnv): InitialAdminConfig {
	const email = environment.INITIAL_ADMIN_EMAIL;
	const name = environment.INITIAL_ADMIN_NAME;
	const password = environment.INITIAL_ADMIN_PASSWORD;

	// Require presence; use trim only for email/name existence check and normalization.
	if (!email?.trim() || !name?.trim() || password === undefined || password === null || password.length === 0 || password.trim().length === 0) {
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

	// Password rules: required, reject empty or whitespace-only, must be at least 8 characters.
	// Do NOT trim the password or compare against a blacklist of known defaults here.
	if (password.length < 8) {
		throw new Error('INITIAL_ADMIN_PASSWORD must be at least 8 characters');
	}

	return {
		email: normalizedEmail,
		name: normalizedName,
		password,
	};
}
