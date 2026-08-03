#!/usr/bin/env node

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

if (parsed.protocol !== 'postgres:' && parsed.protocol !== 'postgresql:') {
  console.error('ERROR: E2E_DATABASE_URL must use the postgres or postgresql protocol.');
  process.exit(3);
}

let dbName;
try {
  dbName = decodeURIComponent(parsed.pathname.replace(/^\//, ''));
} catch {
  console.error('ERROR: E2E_DATABASE_URL contains an invalid encoded database name.');
  process.exit(4);
}

if (!dbName) {
  console.error('ERROR: E2E_DATABASE_URL does not contain a database name.');
  process.exit(5);
}

if (!dbName.endsWith('_e2e')) {
  console.error('ERROR: the E2E database name must end with "_e2e".');
  process.exit(6);
}

console.log(`[e2e] validated database name: ${dbName}`);
