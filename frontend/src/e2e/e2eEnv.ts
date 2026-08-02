const REQUIRED_E2E_DATABASE_SUFFIX = '_e2e';

export type E2EConfig = {
	email: string;
	name: string;
	password: string;
	databaseUrl: string;
};

export function getDatabaseName(databaseUrl: string) {
	try {
		const parsedUrl = new URL(databaseUrl);
		if (!['postgres:', 'postgresql:'].includes(parsedUrl.protocol)) {
			return null;
		}

		const databaseName = decodeURIComponent(parsedUrl.pathname.replace(/^\//, ''));
		return databaseName || null;
	} catch {
		return null;
	}
}

export function getE2EConfig(environment: NodeJS.ProcessEnv): E2EConfig {
	const email = environment.E2E_ADMIN_EMAIL;
	const name = environment.E2E_ADMIN_NAME;
	const password = environment.E2E_ADMIN_PASSWORD;
	const databaseUrl = environment.E2E_DATABASE_URL;

	if (!email?.trim() || !name?.trim() || !password?.trim() || !databaseUrl?.trim()) {
		throw new Error('E2E_ADMIN_EMAIL, E2E_ADMIN_NAME, E2E_ADMIN_PASSWORD, and E2E_DATABASE_URL are required');
	}

	if (environment.NODE_ENV === 'production' || environment.ENVIRONMENT === 'production') {
		throw new Error('E2E cannot run in production environment');
	}

	const databaseName = getDatabaseName(databaseUrl);
	if (!databaseName?.endsWith(REQUIRED_E2E_DATABASE_SUFFIX)) {
		throw new Error('E2E_DATABASE_URL must reference an _e2e database');
	}

	if (password.length < 8) {
		throw new Error('E2E_ADMIN_PASSWORD must be at least 8 characters');
	}

	return {
		email: email.trim(),
		name: name.trim(),
		password,
		databaseUrl,
	};
}
