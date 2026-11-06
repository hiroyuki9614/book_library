import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const requireBackendDependency = createRequire(new URL('../backend/package.json', import.meta.url));

function readConfiguration(env) {
	if (env.BELIB_R2_READ_ONLY_PREFLIGHT !== '1') {
		throw new Error('explicit opt-in required');
	}
	if (env.R2_ENDPOINT) {
		throw new Error('custom endpoint is not supported');
	}

	const accountId = env.R2_ACCOUNT_ID ?? '';
	const bucketName = env.R2_BUCKET_NAME ?? '';
	const accessKeyId = env.R2_ACCESS_KEY_ID ?? '';
	const secretAccessKey = env.R2_SECRET_ACCESS_KEY ?? '';
	if (!/^[a-f0-9]{32}$/i.test(accountId)) {
		throw new Error('invalid account configuration');
	}
	if (!/^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/.test(bucketName)) {
		throw new Error('invalid bucket configuration');
	}
	if (!accessKeyId || !secretAccessKey) {
		throw new Error('missing credential configuration');
	}

	return {
		bucketName,
		region: 'auto',
		endpoint: `https://${accountId}.r2.cloudflarestorage.com`,
		credentials: { accessKeyId, secretAccessKey },
	};
}

/**
 * Run one named-bucket HEAD check. The injected function is deliberately the
 * only storage operation available to this helper; it receives no key or URL.
 */
export async function runR2ReadOnlyPreflight({ env, headBucket }) {
	const config = readConfiguration(env);
	if (typeof headBucket !== 'function') {
		throw new Error('head operation is unavailable');
	}
	const { bucketName, ...clientConfig } = config;
	await headBucket(bucketName, clientConfig);
	return 'R2_READ_ONLY_PREFLIGHT_OK';
}

export async function runR2ReadOnlyCli({
	argv,
	env,
	loadS3Sdk = () => requireBackendDependency('@aws-sdk/client-s3'),
	stdout = (message) => console.log(message),
	stderr = (message) => console.error(message),
}) {
	try {
		if (!Array.isArray(argv) || argv.length !== 0) {
			throw new Error('arguments are not supported');
		}

		const { HeadBucketCommand, S3Client } = loadS3Sdk();
		await runR2ReadOnlyPreflight({
			env,
			headBucket: async (bucketName, config) => {
				const client = new S3Client(config);
				try {
					await client.send(new HeadBucketCommand({ Bucket: bucketName }));
				} finally {
					client.destroy();
				}
			},
		});
		stdout('R2_READ_ONLY_PREFLIGHT_OK (bucket access only; object integrity not checked)');
		return 0;
	} catch {
		stderr('R2_READ_ONLY_PREFLIGHT_FAILED; details suppressed');
		return 1;
	}
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
	runR2ReadOnlyCli({ argv: process.argv.slice(2), env: process.env })
		.then((exitCode) => { process.exitCode = exitCode; });
}
