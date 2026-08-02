export type InitialAdminConfig = {
	email: string;
	name: string;
	password: string;
};

export function readInitialAdminConfig(_environment: NodeJS.ProcessEnv): InitialAdminConfig {
	const email = _environment.INITIAL_ADMIN_EMAIL;
	const name = _environment.INITIAL_ADMIN_NAME;
	const password = _environment.INITIAL_ADMIN_PASSWORD;

	if (!email?.trim() || !name?.trim() || !password?.trim()) {
		throw new Error('INITIAL_ADMIN_EMAIL, INITIAL_ADMIN_NAME, and INITIAL_ADMIN_PASSWORD are required');
	}

	return {
		email: email.trim(),
		name: name.trim(),
		password,
	};
}
