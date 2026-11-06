import assert from 'node:assert/strict';
import test from 'node:test';
import { runR2ReadOnlyCli, runR2ReadOnlyPreflight } from './verify-r2-readonly.mjs';

const env = {
	BELIB_R2_READ_ONLY_PREFLIGHT: '1',
	R2_ACCOUNT_ID: '0123456789abcdef0123456789abcdef',
	R2_BUCKET_NAME: 'private-fixture-bucket',
	R2_ACCESS_KEY_ID: 'offline-fixture-key-id',
	R2_SECRET_ACCESS_KEY: 'offline-fixture-secret',
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
