#!/usr/bin/env node
// Validates that E2E_DATABASE_URL points at a database intended for E2E use,
// based on the database name suffix rather than a substring match on the
// full connection string. The full connection string (which may contain
// credentials) is never logged.

const value = process.env.E2E_DATABASE_URL;

if (!value) {
  console.error('ERROR: E2E_DATABASE_URL is not set.');
  process.exit(1);
}

let parsed;
try {
  parsed = new URL(value);
} catch {
  console.error('ERROR: E2E_DATABASE_URL is not a valid URL (value omitted).');
  process.exit(2);
}

const dbName = parsed.pathname.replace(/^\//, '');

if (!dbName) {
  console.error('ERROR: E2E_DATABASE_URL does not contain a database name.');
  process.exit(3);
}

if (!/_e2e$/.test(dbName)) {
  console.error(`ERROR: database name "${dbName}" must end with "_e2e" to be used for E2E tests.`);
  process.exit(4);
}

console.log(`[e2e] database name "${dbName}" is a valid E2E database.`);
