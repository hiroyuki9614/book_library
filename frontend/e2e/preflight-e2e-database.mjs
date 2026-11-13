#!/usr/bin/env node

import { isIP } from 'node:net';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const EXPECTED_WORKSPACE = '/home/runner/work/book_library/book_library';
const EXPECTED_BACKEND_DIRECTORY = `${EXPECTED_WORKSPACE}/backend`;
const EXPECTED_RUNNER_TEMP = '/home/runner/work/_temp';
const EXPECTED_DATABASE = {
  host: '127.0.0.1',
  port: '5432',
  username: 'ci',
  database: 'reader_e2e',
};

const IDENTITY_QUERY = `
  SELECT
    current_database() AS database_name,
    current_user AS current_user,
    session_user AS session_user,
    inet_server_addr()::text AS server_address,
    inet_server_port() AS server_port,
    inet_client_addr()::text AS client_address,
    current_schema() AS current_schema,
    current_setting('server_version_num')::integer AS server_version_num,
    pg_is_in_recovery() AS is_replica
`;

const SCHEMA_QUERY = `
  SELECT
    (COUNT(*) FILTER (
      WHERE nspname NOT IN ('pg_catalog', 'information_schema', 'public', 'pg_toast')
        AND nspname !~ '^pg_(temp_[0-9]+|toast_[0-9]+|toast_temp_[0-9]+)$'
    ))::integer AS user_schema_count,
    (COUNT(*) FILTER (WHERE nspname = 'public'))::integer AS public_schema_count
  FROM pg_catalog.pg_namespace
`;

const PUBLIC_OBJECT_QUERY = `
  SELECT COUNT(*)::integer AS public_object_count
  FROM pg_catalog.pg_depend AS dependency
  JOIN pg_catalog.pg_namespace AS nsp
    ON dependency.refclassid = 'pg_catalog.pg_namespace'::regclass
   AND dependency.refobjid = nsp.oid
  WHERE nsp.nspname = 'public'
`;

function refusal(reason, details = '') {
  return {
    ok: false,
    error: `E2E database safety preflight refused: ${reason}${details ? ` ${details}` : ''}`,
  };
}

function hasTrustedGithubHostedContext(env, cwd) {
  const expected = {
    GITHUB_ACTIONS: 'true',
    RUNNER_ENVIRONMENT: 'github-hosted',
    RUNNER_OS: 'Linux',
    RUNNER_ARCH: 'X64',
    GITHUB_SERVER_URL: 'https://github.com',
    GITHUB_REPOSITORY: 'hiroyuki9614/book_library',
    GITHUB_WORKFLOW: 'Release candidate gates',
    GITHUB_JOB: 'reader-e2e',
    GITHUB_WORKSPACE: EXPECTED_WORKSPACE,
    RUNNER_TEMP: EXPECTED_RUNNER_TEMP,
  };

  if (Object.entries(expected).some(([name, value]) => env[name] !== value)) {
    return false;
  }

  if (!['pull_request', 'push'].includes(env.GITHUB_EVENT_NAME)) {
    return false;
  }

  if (!/^\d+$/.test(env.GITHUB_RUN_ID ?? '') || !/^[1-9]\d*$/.test(env.GITHUB_RUN_ATTEMPT ?? '')) {
    return false;
  }

  return resolve(cwd) === EXPECTED_BACKEND_DIRECTORY;
}

function validateConnectionConfiguration(env) {
  const rawUrl = env.E2E_DATABASE_URL;
  if (!rawUrl) {
    return 'E2E_DATABASE_URL is missing (value omitted).';
  }

  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    return 'E2E_DATABASE_URL is invalid (value omitted).';
  }

  if (url.protocol !== 'postgres:' && url.protocol !== 'postgresql:') {
    return 'E2E_DATABASE_URL must use a PostgreSQL protocol.';
  }

  if (
    url.hostname !== EXPECTED_DATABASE.host ||
    url.port !== EXPECTED_DATABASE.port ||
    url.username !== EXPECTED_DATABASE.username ||
    !url.password ||
    url.pathname !== `/${EXPECTED_DATABASE.database}` ||
    url.search !== '' ||
    url.hash !== '' ||
    rawUrl.includes('?') ||
    rawUrl.includes('#')
  ) {
    return 'E2E_DATABASE_URL does not match the exact reader-e2e service endpoint or contains connection parameter overrides (value omitted).';
  }

  if (Object.keys(env).some((name) => /^PG[A-Z0-9_]*$/.test(name) && env[name])) {
    return 'PostgreSQL connection override environment variables must be unset (values omitted).';
  }

  return null;
}

export function validatePreflightConfiguration(env, cwd) {
  if (!hasTrustedGithubHostedContext(env, cwd)) {
    return refusal(
      'trusted GitHub-hosted runner attestation is missing or incomplete; the existing release-ci.yml reader-e2e job context is required, and no database connection was attempted.',
    );
  }

  const connectionError = validateConnectionConfiguration(env);
  if (connectionError) {
    return refusal(connectionError);
  }

  return { ok: true };
}

function hasExpectedIdentity(identity) {
  return (
    identity?.database_name === EXPECTED_DATABASE.database &&
    identity?.current_user === EXPECTED_DATABASE.username &&
    identity?.session_user === EXPECTED_DATABASE.username &&
    isIP(identity?.server_address ?? '') !== 0 &&
    Number(identity?.server_port) === Number(EXPECTED_DATABASE.port) &&
    isIP(identity?.client_address ?? '') !== 0 &&
    identity?.current_schema === 'public' &&
    Number(identity?.server_version_num) >= 160000 &&
    Number(identity?.server_version_num) < 170000 &&
    identity?.is_replica === false
  );
}

function readNonNegativeCount(row, name) {
  const value = Number(row?.[name]);
  return Number.isInteger(value) && value >= 0 ? value : null;
}

export async function runReadOnlyPreflight({ env, cwd, createClient }) {
  const configuration = validatePreflightConfiguration(env, cwd);
  if (!configuration.ok) {
    return configuration;
  }

  if (typeof createClient !== 'function') {
    return refusal('the PostgreSQL client is unavailable; database identity and emptiness could not be verified.');
  }

  let client;
  let transactionStarted = false;
  let result = refusal('database identity or emptiness could not be verified.');

  try {
    client = createClient({
      connectionString: env.E2E_DATABASE_URL,
      connectionTimeoutMillis: 5000,
      query_timeout: 5000,
      application_name: 'belib-e2e-readonly-preflight',
    });

    await client.connect();
    await client.query('BEGIN TRANSACTION READ ONLY');
    transactionStarted = true;

    const identityResult = await client.query(IDENTITY_QUERY);
    const identity = identityResult?.rows?.[0];
    if (!hasExpectedIdentity(identity)) {
      return refusal('connected database identity or endpoint did not match the expected PostgreSQL 16 reader-e2e service.');
    }

    const schemaResult = await client.query(SCHEMA_QUERY);
    const schema = schemaResult?.rows?.[0];
    const userSchemaCount = readNonNegativeCount(schema, 'user_schema_count');
    const publicSchemaCount = readNonNegativeCount(schema, 'public_schema_count');

    const publicObjectResult = await client.query(PUBLIC_OBJECT_QUERY);
    const publicObjectCount = readNonNegativeCount(publicObjectResult?.rows?.[0], 'public_object_count');

    if (userSchemaCount === null || publicSchemaCount !== 1 || publicObjectCount === null) {
      return refusal('PostgreSQL catalog state was incomplete; database safety could not be verified.');
    }

    if (userSchemaCount !== 0 || publicObjectCount !== 0) {
      return refusal('the database contains existing user schemas or public-schema objects.');
    }

    result = { ok: true };
  } catch {
    result = refusal('the read-only connection or catalog checks failed; database safety could not be verified (details omitted).');
  } finally {
    if (client && transactionStarted) {
      try {
        await client.query('ROLLBACK');
      } catch {
        result = refusal('the read-only transaction could not be closed cleanly; database safety could not be verified.');
      }
    }

    if (client) {
      try {
        await client.end();
      } catch {
        result = refusal('the read-only database connection could not be closed cleanly; database safety could not be verified.');
      }
    }
  }

  return result;
}

async function main() {
  const configuration = validatePreflightConfiguration(process.env, process.cwd());
  if (!configuration.ok) {
    console.error(`ERROR: ${configuration.error}`);
    process.exitCode = 1;
    return;
  }

  let Client;
  try {
    const requireFromBackend = createRequire(resolve(process.cwd(), 'package.json'));
    ({ Client } = requireFromBackend('pg'));
  } catch {
    console.error('ERROR: E2E database safety preflight refused: the existing PostgreSQL client dependency is unavailable.');
    process.exitCode = 1;
    return;
  }

  const result = await runReadOnlyPreflight({
    env: process.env,
    cwd: process.cwd(),
    createClient: (configuration) => new Client(configuration),
  });

  if (!result.ok) {
    console.error(`ERROR: ${result.error}`);
    process.exitCode = 1;
    return;
  }

  console.log('[e2e] database safety preflight passed: trusted runner, service identity, and empty schema verified.');
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
