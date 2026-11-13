import assert from 'node:assert/strict';
import { test } from 'node:test';
import { runReadOnlyPreflight } from './preflight-e2e-database.mjs';

const cwd = '/home/runner/work/book_library/book_library/backend';

function makeEnvironment(overrides = {}) {
  return {
    E2E_DATABASE_URL: 'postgresql://ci:ci-only-password@127.0.0.1:5432/reader_e2e',
    GITHUB_ACTIONS: 'true',
    RUNNER_ENVIRONMENT: 'github-hosted',
    RUNNER_OS: 'Linux',
    RUNNER_ARCH: 'X64',
    GITHUB_SERVER_URL: 'https://github.com',
    GITHUB_REPOSITORY: 'hiroyuki9614/book_library',
    GITHUB_WORKFLOW: 'Release candidate gates',
    GITHUB_JOB: 'reader-e2e',
    GITHUB_WORKSPACE: '/home/runner/work/book_library/book_library',
    RUNNER_TEMP: '/home/runner/work/_temp',
    GITHUB_EVENT_NAME: 'pull_request',
    GITHUB_RUN_ID: '123456789',
    GITHUB_RUN_ATTEMPT: '1',
    ...overrides,
  };
}

function makeClient({ identity: identityOverrides = {}, schema: schemaOverrides = {}, publicObjectCount = 0 } = {}) {
  const identity = {
    database_name: 'reader_e2e',
    current_user: 'ci',
    session_user: 'ci',
    server_address: '172.17.0.2',
    server_port: 5432,
    client_address: '172.17.0.1',
    current_schema: 'public',
    server_version_num: 160003,
    is_replica: false,
    ...identityOverrides,
  };
  const schema = {
    user_schema_count: 0,
    public_schema_count: 1,
    ...schemaOverrides,
  };
  const statements = [];
  const client = {
    async connect() {
      statements.push('CONNECT');
    },
    async query(statement) {
      statements.push(statement.trim());
      if (statement.includes('current_database()')) return { rows: [identity] };
      if (statement.includes('pg_catalog.pg_depend')) return { rows: [{ public_object_count: publicObjectCount }] };
      if (statement.includes('pg_catalog.pg_namespace')) return { rows: [schema] };
      return { rows: [] };
    },
    async end() {
      statements.push('END');
    },
  };

  return { client, statements };
}

function mockFactory(options) {
  const mock = makeClient(options);
  return { ...mock, createClient: () => mock.client };
}

test('accepts the expected GitHub-hosted reader-e2e service shape using read-only queries', async () => {
  const mock = mockFactory();
  const result = await runReadOnlyPreflight({
    env: makeEnvironment(),
    cwd,
    createClient: mock.createClient,
  });

  assert.deepEqual(result, { ok: true });
  assert.equal(mock.statements[0], 'CONNECT');
  assert.equal(mock.statements[1], 'BEGIN TRANSACTION READ ONLY');
  assert.equal(mock.statements.at(-2), 'ROLLBACK');
  assert.equal(mock.statements.at(-1), 'END');
  assert.ok(mock.statements.filter((statement) => statement.startsWith('SELECT')).length === 3);
  assert.ok(!mock.statements.some((statement) => /\b(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE)\b/i.test(statement)));
});

test('rejects the prior PV3 GitHub runner shape without connecting', async () => {
  const mock = mockFactory();
  const result = await runReadOnlyPreflight({
    env: makeEnvironment({
      GITHUB_REPOSITORY: 'hiroyuki9614/personal-vault-v3',
      GITHUB_WORKSPACE: '/home/runner/work/personal-vault-v3/personal-vault-v3',
    }),
    cwd: '/home/runner/work/personal-vault-v3/personal-vault-v3/backend',
    createClient: mock.createClient,
  });

  assert.equal(result.ok, false);
  assert.match(result.error, /trusted GitHub-hosted runner attestation is missing or incomplete/);
  assert.match(result.error, /no database connection was attempted/);
  assert.deepEqual(mock.statements, []);
});

test('rejects a database containing existing public-schema objects', async () => {
  const mock = mockFactory({ publicObjectCount: 1 });
  const result = await runReadOnlyPreflight({
    env: makeEnvironment(),
    cwd,
    createClient: mock.createClient,
  });

  assert.equal(result.ok, false);
  assert.match(result.error, /existing user schemas or public-schema objects/);
  assert.ok(mock.statements.includes('ROLLBACK'));
});

test('rejects an existing user schema even when public is empty', async () => {
  const mock = mockFactory({ schema: { user_schema_count: 1 } });
  const result = await runReadOnlyPreflight({
    env: makeEnvironment(),
    cwd,
    createClient: mock.createClient,
  });

  assert.equal(result.ok, false);
  assert.match(result.error, /existing user schemas or public-schema objects/);
  assert.ok(mock.statements.includes('ROLLBACK'));
});

test('rejects an unexpected database identity', async () => {
  const mock = mockFactory({ identity: { database_name: 'retained_prod' } });
  const result = await runReadOnlyPreflight({
    env: makeEnvironment(),
    cwd,
    createClient: mock.createClient,
  });

  assert.equal(result.ok, false);
  assert.match(result.error, /identity or endpoint/);
  assert.match(result.error, /failed checks: database_name/);
  assert.match(result.error, /\"database_name\":\"retained_prod\"/);
  assert.ok(!result.error.includes('ci-only-password'));
  assert.ok(mock.statements.includes('ROLLBACK'));
});

test('rejects a connection URL with a parameter override before connecting', async () => {
  const mock = mockFactory();
  const result = await runReadOnlyPreflight({
    env: makeEnvironment({
      E2E_DATABASE_URL: 'postgresql://ci:ci-only-password@127.0.0.1:5432/reader_e2e?options=-csearch_path=public',
    }),
    cwd,
    createClient: mock.createClient,
  });

  assert.equal(result.ok, false);
  assert.match(result.error, /connection parameter overrides/);
  assert.deepEqual(mock.statements, []);
  assert.ok(!result.error.includes('ci-only-password'));
});

test('rejects PostgreSQL environment overrides before connecting', async () => {
  const mock = mockFactory();
  const result = await runReadOnlyPreflight({
    env: makeEnvironment({ PGHOST: 'other-host' }),
    cwd,
    createClient: mock.createClient,
  });

  assert.equal(result.ok, false);
  assert.match(result.error, /PostgreSQL connection override environment variables must be unset/);
  assert.deepEqual(mock.statements, []);
});

test('rejects an unexpected database role', async () => {
  const mock = mockFactory({ identity: { current_user: 'postgres' } });
  const result = await runReadOnlyPreflight({
    env: makeEnvironment(),
    cwd,
    createClient: mock.createClient,
  });

  assert.equal(result.ok, false);
  assert.match(result.error, /identity or endpoint/);
  assert.ok(mock.statements.includes('ROLLBACK'));
});

test('rejects a self-hosted or missing GitHub runner origin without connecting', async () => {
  const mock = mockFactory();
  const result = await runReadOnlyPreflight({
    env: makeEnvironment({ RUNNER_ENVIRONMENT: 'self-hosted' }),
    cwd,
    createClient: mock.createClient,
  });

  assert.equal(result.ok, false);
  assert.match(result.error, /trusted GitHub-hosted runner attestation is missing or incomplete/);
  assert.match(result.error, /no database connection was attempted/);
  assert.deepEqual(mock.statements, []);
});

test('rejects caller-only GitHub boolean without complete runner provenance', async () => {
  const mock = mockFactory();
  const result = await runReadOnlyPreflight({
    env: { E2E_DATABASE_URL: makeEnvironment().E2E_DATABASE_URL, GITHUB_ACTIONS: 'true' },
    cwd,
    createClient: mock.createClient,
  });

  assert.equal(result.ok, false);
  assert.match(result.error, /trusted GitHub-hosted runner attestation is missing or incomplete/);
  assert.deepEqual(mock.statements, []);
});
