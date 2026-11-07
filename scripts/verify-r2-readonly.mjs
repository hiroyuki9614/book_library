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

function readObjectConfiguration(env) {
	if (env.BELIB_R2_OBJECT_READ_ONLY_PREFLIGHT !== '1') {
		throw new Error('object opt-in required');
	}
	const objectKey = env.R2_PREFLIGHT_OBJECT_KEY ?? '';
	if (!/^books\/[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.(?:pdf|epub)$/i.test(objectKey)) {
		throw new Error('invalid object key configuration');
	}

	let expectedContentLength;
	if (env.R2_PREFLIGHT_EXPECTED_CONTENT_LENGTH !== undefined) {
		const value = env.R2_PREFLIGHT_EXPECTED_CONTENT_LENGTH;
		if (!/^(0|[1-9][0-9]*)$/.test(value) || !Number.isSafeInteger(Number(value))) {
			throw new Error('invalid expected length configuration');
		}
		expectedContentLength = Number(value);
	}

	let expectedETag;
	if (env.R2_PREFLIGHT_EXPECTED_ETAG !== undefined) {
		const value = env.R2_PREFLIGHT_EXPECTED_ETAG;
		if (typeof value !== 'string' || !value || /[\r\n\0]/.test(value)) {
			throw new Error('invalid expected ETag configuration');
		}
		expectedETag = value.replace(/^"(.*)"$/, '$1');
		if (!expectedETag || expectedETag.includes('"')) {
			throw new Error('invalid expected ETag configuration');
		}
	}

	return { objectKey, expectedContentLength, expectedETag };
}

function assertExpectedObjectMetadata(head, expected) {
	if (expected.expectedContentLength !== undefined && head?.ContentLength !== expected.expectedContentLength) {
		throw new Error('object length mismatch');
	}
	if (expected.expectedETag !== undefined) {
		const actualETag = typeof head?.ETag === 'string' ? head.ETag.replace(/^"(.*)"$/, '$1') : undefined;
		if (actualETag !== expected.expectedETag) {
			throw new Error('object ETag mismatch');
		}
	}
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

/** Run a single named-object HEAD check using an injected metadata-only operation. */
export async function runR2ReadOnlyObjectPreflight({ env, headObject }) {
	const config = readConfiguration(env);
	const expected = readObjectConfiguration(env);
	if (typeof headObject !== 'function') {
		throw new Error('head operation is unavailable');
	}
	const { bucketName, ...clientConfig } = config;
	const head = await headObject(bucketName, expected.objectKey, clientConfig);
	assertExpectedObjectMetadata(head, expected);
	return 'R2_READ_ONLY_OBJECT_PREFLIGHT_OK';
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
		// Validate both opt-ins and all supplied inputs before loading the SDK.
		readConfiguration(env);
		const objectMode = env.BELIB_R2_OBJECT_READ_ONLY_PREFLIGHT === '1';
		if (objectMode) readObjectConfiguration(env);
		else if (env.BELIB_R2_OBJECT_READ_ONLY_PREFLIGHT !== undefined) {
			throw new Error('invalid object opt-in');
		}

		const sdk = loadS3Sdk();
		const { S3Client } = sdk;
		if (objectMode) {
			const { HeadObjectCommand } = sdk;
			await runR2ReadOnlyObjectPreflight({
				env,
				headObject: async (bucketName, objectKey, config) => {
					const client = new S3Client(config);
					try {
						return await client.send(new HeadObjectCommand({ Bucket: bucketName, Key: objectKey }));
					} finally {
						client.destroy();
					}
				},
			});
			stdout('R2_READ_ONLY_OBJECT_PREFLIGHT_OK (object metadata only; bytes and checksum not verified)');
		} else {
			const { HeadBucketCommand } = sdk;
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
		}
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
