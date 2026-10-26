import { createHash } from 'node:crypto';
import { readFile, realpath, stat } from 'node:fs/promises';
import { isAbsolute } from 'node:path';

export class PortfolioSafetyError extends Error {}

export function assertRuntimeRevision(builtRevision: string, expectedRevision?: string, imageId?: string) {
	if (!/^[0-9a-f]{40}$/.test(builtRevision) || builtRevision !== expectedRevision ||
		!imageId || !/^sha256:[0-9a-f]{64}$/.test(imageId)) {
		throw new PortfolioSafetyError('Reviewed revision and immutable running image identity must be verified before use');
	}
	return { gitRevision: builtRevision, imageId };
}

export function portfolioApiBaseUrl(environment: NodeJS.ProcessEnv) {
	const port = environment.APP_PORT || '3000';
	if (!/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535) {
		throw new PortfolioSafetyError('APP_PORT must identify the running API port');
	}
	const url = `http://127.0.0.1:${port}`;
	if (environment.PORTFOLIO_API_BASE_URL && environment.PORTFOLIO_API_BASE_URL !== url) {
		throw new PortfolioSafetyError('Registration must target the API in this running container');
	}
	return url;
}

export async function readPortfolioRuntime(environment = process.env) {
	// Written by the image build, never inferred from a checkout or an environment override.
	const revision = (await readFile(new URL('../../../build-revision.txt', import.meta.url), 'utf8')).trim();
	const identity = assertRuntimeRevision(revision, environment.PORTFOLIO_EXPECTED_REVISION, environment.PORTFOLIO_IMAGE_ID);
	const databaseUrl = environment.DATABASE_URL;
	if (!databaseUrl) throw new PortfolioSafetyError('DATABASE_URL is required');
	if (!environment.DEMO_USER_EMAIL?.trim()) throw new PortfolioSafetyError('DEMO_USER_EMAIL is required to inspect the intended account');
	return { ...identity, databaseUrl, ...await readPortfolioStorageIdentity(environment) };
}

export async function readPortfolioStorageIdentity(environment: NodeJS.ProcessEnv) {
	const storageDriver = environment.BOOK_FILE_STORAGE_DRIVER;
	let storageIdentity: string;
	if (storageDriver === 'local') {
		const root = environment.BOOK_FILE_STORAGE_ROOT;
		if (!root || !isAbsolute(root)) throw new PortfolioSafetyError('An explicit absolute storage root is required');
		const canonicalRoot = await realpath(root);
		const info = await stat(canonicalRoot);
		if (!info.isDirectory()) throw new PortfolioSafetyError('Storage root must be an existing directory');
		storageIdentity = JSON.stringify(['local', canonicalRoot, info.dev, info.ino]);
	} else if (storageDriver === 'r2') {
		const endpoint = environment.R2_ENDPOINT?.trim() ||
			(environment.R2_ACCOUNT_ID?.trim() ? `https://${environment.R2_ACCOUNT_ID.trim()}.r2.cloudflarestorage.com` : '');
		const bucket = environment.R2_BUCKET_NAME?.trim();
		if (!endpoint || !bucket) throw new PortfolioSafetyError('R2 endpoint/account and bucket identity are required');
		const url = new URL(endpoint);
		if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
			throw new PortfolioSafetyError('R2 identity must use a credential-free HTTPS endpoint');
		}
		storageIdentity = JSON.stringify(['r2', url.origin, bucket]);
	} else {
		throw new PortfolioSafetyError('BOOK_FILE_STORAGE_DRIVER must explicitly be local or r2');
	}
	return { storageDriver, storageIdentityHash: createHash('sha256').update(storageIdentity).digest('hex') } as const;
}

export function reportPortfolioFailure(error: unknown) {
	// Raw DB, filesystem, HTTP and SDK errors may contain connection strings, keys or titles.
	console.error(error instanceof PortfolioSafetyError ? error.message : 'Portfolio command failed; stop and inspect through the protected operator procedure');
	process.exitCode = 1;
}
