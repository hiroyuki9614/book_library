#!/usr/bin/env bash
# Read-only i5 admission check. Never sources the env file or changes Docker state.
set -euo pipefail

fail() { printf 'BLOCKED: %s\n' "$*" >&2; exit 1; }
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
[[ $# -eq 1 ]] || fail 'Supply exact absolute path of the staging-only env file'
file="$1"
[[ "$file" = /* && -f "$file" && ! -L "$file" ]] || fail 'Staging env must be an existing regular absolute-path file (not symlink)'
[[ "$(id -un)" == hiroyuki ]] || fail 'Wrong principal: require i5 user hiroyuki'
[[ "$(hostname)" == ubuntu-worker ]] || fail 'Wrong host: require i5 ubuntu-worker'
[[ "$(stat -c %u -- "$file")" == "$(id -u)" ]] || fail 'Env file must be owned by current principal'
mode="$(stat -c %a -- "$file")"
[[ "$mode" == 600 || "$mode" == 400 ]] || fail 'Env file must have mode 0600 or 0400'
# Compose gives ambient variables precedence over --env-file; reject all ambient staging,
# Compose, Docker, and database credential inputs before reading the host-only file.
if compgen -e | grep -Eq '^(BELIB_STAGE_|COMPOSE_|DOCKER_|DATABASE_URL$|DIRECT_URL$|POSTGRES_|BETTER_AUTH_SECRET$|R2_|AWS_|S3_|PROD_|PRODUCTION_|VITE_PUBLIC_DEMO_|BELIB_FRONTEND_)'; then
  fail 'Ambient staging, Docker, Compose, or database overrides are forbidden; use the staging env file only'
fi

allowed_keys='BELIB_STAGE_DB_BOOTSTRAP_PASSWORD|BELIB_STAGE_DB_APP_PASSWORD|BELIB_STAGE_DB_MIGRATE_PASSWORD|BELIB_STAGE_AUTH_SECRET|BELIB_STAGE_VCS_REF|BELIB_STAGE_PORT'
if grep -Ev "^(${allowed_keys})=[^[:space:]]+$" "$file" >/dev/null; then
  fail 'Staging env must contain only the six approved staging keys'
fi
for key in BELIB_STAGE_DB_BOOTSTRAP_PASSWORD BELIB_STAGE_DB_APP_PASSWORD BELIB_STAGE_DB_MIGRATE_PASSWORD BELIB_STAGE_AUTH_SECRET BELIB_STAGE_VCS_REF BELIB_STAGE_PORT; do
  [[ "$(grep -Ec "^(${key})=" "$file")" -eq 1 ]] || fail "Missing or duplicate $key (no value displayed)"
done

port="$(sed -n 's/^BELIB_STAGE_PORT=//p' "$file")"
rev="$(sed -n 's/^BELIB_STAGE_VCS_REF=//p' "$file")"
[[ "$port" =~ ^[0-9]{4,5}$ && "$port" -ge 1024 && "$port" -le 65535 ]] || fail 'Invalid loopback port'
[[ "$rev" =~ ^[0-9a-f]{40}$ ]] || fail 'Revision must be full lowercase 40-character Git SHA'
[[ "$(git -C "$root" rev-parse HEAD)" == "$rev" ]] || fail 'Checked-out Git HEAD differs from staging revision'
[[ -z "$(git -C "$root" status --porcelain)" ]] || fail 'Dirty source tree: fixed staging SHA cannot describe local changes'
# Hex-only values are safe for Compose interpolation, libpq URLs, and psql password prompts.
for key in BELIB_STAGE_DB_BOOTSTRAP_PASSWORD BELIB_STAGE_DB_APP_PASSWORD BELIB_STAGE_DB_MIGRATE_PASSWORD BELIB_STAGE_AUTH_SECRET; do
  grep -Eq "^${key}=[a-f0-9]{64}$" "$file" || fail "$key must be a local 64-character lowercase hex value"
done
bootstrap_password="$(sed -n 's/^BELIB_STAGE_DB_BOOTSTRAP_PASSWORD=//p' "$file")"
app_password="$(sed -n 's/^BELIB_STAGE_DB_APP_PASSWORD=//p' "$file")"
migrate_password="$(sed -n 's/^BELIB_STAGE_DB_MIGRATE_PASSWORD=//p' "$file")"
auth_secret="$(sed -n 's/^BELIB_STAGE_AUTH_SECRET=//p' "$file")"
[[ "$bootstrap_password" != "$app_password" && "$bootstrap_password" != "$migrate_password" && "$bootstrap_password" != "$auth_secret" && "$app_password" != "$migrate_password" && "$app_password" != "$auth_secret" && "$migrate_password" != "$auth_secret" ]] || fail 'Bootstrap, app, migration, and auth secrets must all be distinct'
unset bootstrap_password app_password migrate_password auth_secret
grep -Eq '^BELIB_STAGE_AUTH_SECRET=[a-f0-9]{64}$' "$file" || fail 'Auth secret must be local 64-character hex'

command -v docker >/dev/null || fail 'Docker CLI missing; do not install with sudo'
# CLI access does not prove rootless: docker-group access to a rootful daemon is forbidden.
# Resolve the effective endpoint, including DOCKER_CONTEXT/DOCKER_HOST overrides.
rootless_socket="unix:///run/user/$(id -u)/docker.sock"
if [[ -n "${DOCKER_CONTEXT:-}" ]]; then
  docker_endpoint="$(docker context inspect "$DOCKER_CONTEXT" --format '{{ (index .Endpoints "docker").Host }}')" || fail 'Cannot resolve Docker context'
elif [[ -n "${DOCKER_HOST:-}" ]]; then
  docker_endpoint="$DOCKER_HOST"
else
  docker_endpoint="$(docker context inspect "$(docker context show)" --format '{{ (index .Endpoints "docker").Host }}')" || fail 'Cannot resolve current Docker context'
fi
[[ "$docker_endpoint" == "$rootless_socket" ]] || fail 'Docker endpoint is not this i5 user rootless socket; rootful/remote daemon forbidden'
docker_identity="$(docker info --format '{{.Name}}|{{json .SecurityOptions}}')" || fail 'Current nonroot user has no Docker access'
[[ "$docker_identity" == "ubuntu-worker|"* ]] || fail 'Docker daemon is not on the expected i5 host'
[[ "$docker_identity" == *'"name=rootless"'* ]] || fail 'Docker daemon does not report rootless security option'
docker compose version >/dev/null || fail 'Docker Compose plugin unavailable'

free_kib="$(df -Pk "$root" | awk 'END {print $4}')"
[[ "$free_kib" =~ ^[0-9]+$ && "$free_kib" -ge 8388608 ]] || fail 'Require >=8 GiB free on target i5 filesystem'
mem_kib="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
[[ "$mem_kib" =~ ^[0-9]+$ && "$mem_kib" -ge 2097152 ]] || fail 'Require >=2 GiB available memory on i5'
command -v ss >/dev/null || fail 'ss is required for read-only loopback port check'
if ss -H -ltn | awk '{print $4}' | grep -Eq "(^|:)${port}$"; then
  fail 'Proposed staging loopback port is already bound; inspect existing deployment before choosing another'
fi

# --env-file explicitly replaces implicit production .env interpolation.
docker compose -p belib-isolated-i5 --env-file "$file" -f "$root/compose.staging.yaml" config --quiet || fail 'Staging Compose configuration invalid'
printf 'PREFLIGHT_PASS host=ubuntu-worker principal=hiroyuki available_disk_kib=%s available_memory_kib=%s port=%s revision=%s compose_project=belib-isolated-i5\n' "$free_kib" "$mem_kib" "$port" "$rev"
