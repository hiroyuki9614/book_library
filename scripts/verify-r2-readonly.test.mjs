import assert from 'node:assert/strict';
import test from 'node:test';
import { runR2ReadOnlyCli, runR2ReadOnlyObjectPreflight, runR2ReadOnlyPreflight } from './verify-r2-readonly.mjs';

const env = {
	BELIB_R2_READ_ONLY_PREFLIGHT: '1',
	R2_ACCOUNT_ID: '0123456789abcdef0123456789abcdef',
	R2_BUCKET_NAME: 'private-fixture-bucket',
	R2_ACCESS_KEY_ID: 'offline-fixture-key-id',
	R2_SECRET_ACCESS_KEY: 'offline-fixture-secret',
};
const objectEnv = {
	...env,
	BELIB_R2_OBJECT_READ_ONLY_PREFLIGHT: '1',
	R2_PREFLIGHT_OBJECT_KEY: 'books/123e4567-e89b-42d3-a456-426614174000.pdf',
};

test('requires explicit opt-in before calling the bucket client', async () => {
	let called = false;
	await assert.rejects(
		runR2ReadOnlyPreflight({
			env: { ...env, BELIB_R2_READ_ONLY_PREFLIGHT: undefined },
			headBucket: async () => { called = true; },
		}),
	);
	assert.equal(called, false);
});

test('calls only the injected named-bucket HEAD operation with the derived endpoint', async () => {
	const calls = [];
	const result = await runR2ReadOnlyPreflight({
		env,
		headBucket: async (...args) => calls.push(args),
	});

	assert.equal(result, 'R2_READ_ONLY_PREFLIGHT_OK');
	assert.deepEqual(calls, [[
		'private-fixture-bucket',
		{
			region: 'auto',
			endpoint: 'https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com',
			credentials: {
				accessKeyId: 'offline-fixture-key-id',
				secretAccessKey: 'offline-fixture-secret',
			},
		},
	]]);
});

test('rejects custom endpoints and malformed required settings before any request', async () => {
	for (const invalidEnv of [
		{ ...env, R2_ENDPOINT: 'https://untrusted.invalid' },
		{ ...env, R2_ACCOUNT_ID: 'not-an-account-id' },
		{ ...env, R2_BUCKET_NAME: '' },
		{ ...env, R2_SECRET_ACCESS_KEY: '' },
	]) {
		let called = false;
		await assert.rejects(runR2ReadOnlyPreflight({
			env: invalidEnv,
			headBucket: async () => { called = true; },
		}));
		assert.equal(called, false);
	}
});

test('CLI wires an offline SDK client to exactly one named-bucket HEAD command', async () => {
	const constructedClients = [];
	const sentCommands = [];
	let destroyed = false;
	const output = { stdout: [], stderr: [] };
	class FakeHeadBucketCommand {
		constructor(input) {
			this.input = input;
		}
	}
	class FakeS3Client {
		constructor(config) {
			constructedClients.push(config);
		}
		async send(command) {
			sentCommands.push(command);
		}
		destroy() {
			destroyed = true;
		}
	}

	const exitCode = await runR2ReadOnlyCli({
		argv: [],
		env,
		loadS3Sdk: () => ({ HeadBucketCommand: FakeHeadBucketCommand, S3Client: FakeS3Client }),
		stdout: (message) => output.stdout.push(message),
		stderr: (message) => output.stderr.push(message),
	});

	assert.equal(exitCode, 0);
	assert.deepEqual(constructedClients, [{
		region: 'auto',
		endpoint: 'https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com',
		credentials: {
			accessKeyId: 'offline-fixture-key-id',
			secretAccessKey: 'offline-fixture-secret',
		},
	}]);
	assert.equal(sentCommands.length, 1);
	assert.equal(sentCommands[0] instanceof FakeHeadBucketCommand, true);
	assert.deepEqual(sentCommands[0].input, { Bucket: 'private-fixture-bucket' });
	assert.equal(destroyed, true);
	assert.deepEqual(output, {
		stdout: ['R2_READ_ONLY_PREFLIGHT_OK (bucket access only; object integrity not checked)'],
		stderr: [],
	});
	const operationNames = sentCommands.map((command) => command.constructor.name.replace(/^Fake/, ''));
	assert.deepEqual(operationNames, ['HeadBucketCommand']);
	assert.equal(operationNames.some((name) => [
		'PutObjectCommand', 'GetObjectCommand', 'DeleteObjectCommand', 'ListObjectsCommand',
	].includes(name)), false);
});

test('CLI rejects arguments before loading the SDK and emits only generic failure output', async () => {
	const output = { stdout: [], stderr: [] };
	let sdkLoaded = false;
	const exitCode = await runR2ReadOnlyCli({
		argv: ['unexpected'],
		env,
		loadS3Sdk: () => { sdkLoaded = true; throw new Error('should not load'); },
		stdout: (message) => output.stdout.push(message),
		stderr: (message) => output.stderr.push(message),
	});

	assert.equal(exitCode, 1);
	assert.equal(sdkLoaded, false);
	assert.deepEqual(output, {
		stdout: [],
		stderr: ['R2_READ_ONLY_PREFLIGHT_FAILED; details suppressed'],
	});
});

test('CLI suppresses SDK/network errors and never prints secret configuration or endpoint/bucket', async () => {
	const output = { stdout: [], stderr: [] };
	const forbiddenValues = [
		env.R2_ACCESS_KEY_ID,
		env.R2_SECRET_ACCESS_KEY,
		'https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com',
		env.R2_BUCKET_NAME,
	];
	class FakeHeadBucketCommand {
		constructor(input) { this.input = input; }
	}
	class FailingFakeS3Client {
		constructor() {}
		async send() {
			throw new Error(`request failed with ${forbiddenValues.join(' ')}`);
		}
		destroy() {}
	}

	const exitCode = await runR2ReadOnlyCli({
		argv: [],
		env,
		loadS3Sdk: () => ({ HeadBucketCommand: FakeHeadBucketCommand, S3Client: FailingFakeS3Client }),
		stdout: (message) => output.stdout.push(message),
		stderr: (message) => output.stderr.push(message),
	});
	const emitted = [...output.stdout, ...output.stderr].join('\n');

	assert.equal(exitCode, 1);
	assert.deepEqual(output, {
		stdout: [],
		stderr: ['R2_READ_ONLY_PREFLIGHT_FAILED; details suppressed'],
	});
	for (const forbiddenValue of forbiddenValues) {
		assert.equal(emitted.includes(forbiddenValue), false);
	}
});

test('object preflight validates inputs and calls only the injected object HEAD operation', async () => {
	const calls = [];
	const result = await runR2ReadOnlyObjectPreflight({
		env: { ...objectEnv, R2_PREFLIGHT_EXPECTED_CONTENT_LENGTH: '123', R2_PREFLIGHT_EXPECTED_ETAG: '"opaque-etag"' },
		headObject: async (...args) => {
			calls.push(args);
			return { ContentLength: 123, ETag: 'opaque-etag' };
		},
	});

	assert.equal(result, 'R2_READ_ONLY_OBJECT_PREFLIGHT_OK');
	assert.deepEqual(calls, [[
		'private-fixture-bucket',
		'books/123e4567-e89b-42d3-a456-426614174000.pdf',
		{
			region: 'auto',
			endpoint: 'https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com',
			credentials: {
				accessKeyId: 'offline-fixture-key-id',
				secretAccessKey: 'offline-fixture-secret',
			},
		},
	]]);
});

test('object preflight requires its separate opt-in and rejects malformed keys or expectations before calling HEAD', async () => {
	for (const invalidEnv of [
		{ ...objectEnv, BELIB_R2_OBJECT_READ_ONLY_PREFLIGHT: undefined },
		{ ...objectEnv, R2_PREFLIGHT_OBJECT_KEY: 'books/private key.pdf' },
		{ ...objectEnv, R2_PREFLIGHT_OBJECT_KEY: 'books/../secret.pdf' },
		{ ...objectEnv, R2_PREFLIGHT_EXPECTED_CONTENT_LENGTH: '-1' },
		{ ...objectEnv, R2_PREFLIGHT_EXPECTED_CONTENT_LENGTH: '9007199254740992' },
		{ ...objectEnv, R2_PREFLIGHT_EXPECTED_ETAG: 'bad\nvalue' },
	]) {
		let called = false;
		await assert.rejects(runR2ReadOnlyObjectPreflight({
			env: invalidEnv,
			headObject: async () => { called = true; },
		}));
		assert.equal(called, false);
	}
});

test('object CLI sends exactly one HeadObject command, destroys the client, and keeps output generic', async () => {
	const constructedClients = [];
	const sentCommands = [];
	let destroyed = false;
	const output = { stdout: [], stderr: [] };
	class FakeHeadObjectCommand {
		constructor(input) { this.input = input; }
	}
	class ForbiddenCommand {
		constructor(input) { this.input = input; }
	}
	class FakeS3Client {
		constructor(config) { constructedClients.push(config); }
		async send(command) {
			sentCommands.push(command);
			return { ContentLength: 12, ETag: 'opaque-value' };
		}
		destroy() { destroyed = true; }
	}

	const exitCode = await runR2ReadOnlyCli({
		argv: [],
		env: { ...objectEnv, R2_PREFLIGHT_EXPECTED_CONTENT_LENGTH: '12', R2_PREFLIGHT_EXPECTED_ETAG: 'opaque-value' },
		loadS3Sdk: () => ({
			HeadBucketCommand: ForbiddenCommand,
			HeadObjectCommand: FakeHeadObjectCommand,
			GetObjectCommand: ForbiddenCommand,
			PutObjectCommand: ForbiddenCommand,
			DeleteObjectCommand: ForbiddenCommand,
			ListObjectsV2Command: ForbiddenCommand,
			S3Client: FakeS3Client,
		}),
		stdout: (message) => output.stdout.push(message),
		stderr: (message) => output.stderr.push(message),
	});

	assert.equal(exitCode, 0);
	assert.equal(constructedClients.length, 1);
	assert.deepEqual(sentCommands.map((command) => command.constructor.name), ['FakeHeadObjectCommand']);
	assert.deepEqual(sentCommands[0].input, {
		Bucket: 'private-fixture-bucket',
		Key: 'books/123e4567-e89b-42d3-a456-426614174000.pdf',
	});
	assert.equal(destroyed, true);
	assert.deepEqual(output, {
		stdout: ['R2_READ_ONLY_OBJECT_PREFLIGHT_OK (object metadata only; bytes and checksum not verified)'],
		stderr: [],
	});
	const emitted = [...output.stdout, ...output.stderr].join('\n');
	for (const forbiddenValue of [objectEnv.R2_PREFLIGHT_OBJECT_KEY, env.R2_BUCKET_NAME, env.R2_ACCESS_KEY_ID, env.R2_SECRET_ACCESS_KEY]) {
		assert.equal(emitted.includes(forbiddenValue), false);
	}
});

test('object CLI fails closed on missing/mismatched expected metadata and suppresses object identifiers and errors', async () => {
	const output = { stdout: [], stderr: [] };
	let destroyed = false;
	class FakeHeadObjectCommand {
		constructor(input) { this.input = input; }
	}
	class FailingFakeS3Client {
		async send() { throw new Error(`missing ${objectEnv.R2_PREFLIGHT_OBJECT_KEY} ${env.R2_BUCKET_NAME} ${env.R2_SECRET_ACCESS_KEY}`); }
		destroy() { destroyed = true; }
	}
	const exitCode = await runR2ReadOnlyCli({
		argv: [],
		env: { ...objectEnv, R2_PREFLIGHT_EXPECTED_CONTENT_LENGTH: '99' },
		loadS3Sdk: () => ({ HeadObjectCommand: FakeHeadObjectCommand, S3Client: FailingFakeS3Client }),
		stdout: (message) => output.stdout.push(message),
		stderr: (message) => output.stderr.push(message),
	});
	const emitted = [...output.stdout, ...output.stderr].join('\n');
	assert.equal(exitCode, 1);
	assert.equal(destroyed, true);
	assert.deepEqual(output, { stdout: [], stderr: ['R2_READ_ONLY_PREFLIGHT_FAILED; details suppressed'] });
	for (const forbiddenValue of [objectEnv.R2_PREFLIGHT_OBJECT_KEY, env.R2_BUCKET_NAME, env.R2_SECRET_ACCESS_KEY]) {
		assert.equal(emitted.includes(forbiddenValue), false);
	}
});

test('object CLI rejects failed metadata comparisons and validates opt-in before loading the SDK', async () => {
	let sdkLoaded = false;
	const output = { stdout: [], stderr: [] };
	const invalidExpectationExit = await runR2ReadOnlyCli({
		argv: [],
		env: { ...objectEnv, R2_PREFLIGHT_EXPECTED_CONTENT_LENGTH: '10' },
		loadS3Sdk: () => ({
			HeadObjectCommand: class { constructor(input) { this.input = input; } },
			S3Client: class {
				async send() { return { ContentLength: 11 }; }
				destroy() {}
			},
		}),
		stdout: (message) => output.stdout.push(message),
		stderr: (message) => output.stderr.push(message),
	});
	const invalidOptInExit = await runR2ReadOnlyCli({
		argv: [],
		env: { ...env, BELIB_R2_OBJECT_READ_ONLY_PREFLIGHT: 'yes' },
		loadS3Sdk: () => { sdkLoaded = true; throw new Error('must not load'); },
		stdout: (message) => output.stdout.push(message),
		stderr: (message) => output.stderr.push(message),
	});
	assert.equal(invalidExpectationExit, 1);
	assert.equal(invalidOptInExit, 1);
	assert.equal(sdkLoaded, false);
	assert.deepEqual(output, {
		stdout: [],
		stderr: [
			'R2_READ_ONLY_PREFLIGHT_FAILED; details suppressed',
			'R2_READ_ONLY_PREFLIGHT_FAILED; details suppressed',
		],
	});
});
