#!/usr/bin/env bash
set -Eeuo pipefail

# This verifier owns its disposable database. It never targets a caller-supplied
# database, and deliberately rejects production-like connection targets.
if [[ -n "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL must not be supplied to the disposable migration verifier" >&2
  exit 1
fi

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
run_id="${GITHUB_RUN_ID:-local}-$$-${RANDOM}"
container="belib-ci-postgres-${run_id}"
db_name="ci_${run_id//[^A-Za-z0-9_]/_}"
password="ci-only-password-${RANDOM}"
port_file="$(mktemp)"
cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  rm -f "$port_file"
}
trap cleanup EXIT

docker run --detach --rm \
  --name "$container" \
  --publish 127.0.0.1::5432 \
  --env POSTGRES_USER=ci \
  --env "POSTGRES_PASSWORD=$password" \
  --env "POSTGRES_DB=$db_name" \
  postgres:16.14-bookworm >/dev/null

for attempt in {1..60}; do
  if docker exec "$container" pg_isready -U ci -d "$db_name" >/dev/null 2>&1; then
    break
  fi
  if [[ "$attempt" == 60 ]]; then
    echo "Disposable PostgreSQL did not become ready" >&2
    exit 1
  fi
  sleep 1
done

host_port="$(docker port "$container" 5432/tcp | sed -n 's/.*127\.0\.0\.1:\([0-9][0-9]*\).*/\1/p' | head -n1)"
if [[ -z "$host_port" || "$host_port" == "5432" ]]; then
  echo "Could not determine the disposable PostgreSQL loopback port" >&2
  exit 1
fi

database_url="postgresql://ci:${password}@127.0.0.1:${host_port}/${db_name}"
if [[ "$database_url" =~ (production|prod|staging|stage|shared|rds|cloud) ]]; then
  echo "Refusing a production-like migration target" >&2
  exit 1
fi

cd "$root_dir/backend"
export DATABASE_URL="$database_url"
npx prisma validate --schema prisma/schema.prisma
npm run verify:file-hash-migration
first_output="$(npx prisma migrate deploy 2>&1)" || { printf '%s\n' "$first_output"; exit 1; }
printf '%s\n' "$first_output"
second_output="$(npx prisma migrate deploy 2>&1)" || { printf '%s\n' "$second_output"; exit 1; }
printf '%s\n' "$second_output"
if ! grep -Eiq 'no pending migrations to apply|database is already up to date' <<<"$second_output"; then
  echo "Second migrate deploy did not prove that no migrations remain" >&2
  exit 1
fi
