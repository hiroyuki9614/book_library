#!/usr/bin/env bash
# Read-only i5 admission check. Never sources the env file or changes Docker state.
set -euo pipefail
umask 077

fail() { printf 'BLOCKED: %s\n' "$*" >&2; exit 1; }
if [[ -v BASH_ENV || -v ENV ]]; then
  fail 'Bash startup overrides are forbidden; invoke preflight from a clean Bash environment'
fi
if declare -F docker >/dev/null; then
  fail 'Ambient Docker shell function is forbidden; invoke preflight from a clean Bash environment'
fi
if alias docker >/dev/null 2>&1; then
  fail 'Ambient Docker alias is forbidden; invoke preflight from a clean Bash environment'
fi
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
[[ $# -eq 1 ]] || fail 'Supply exact absolute path of the staging-only env file'
file="$1"
[[ "$file" = /* && -f "$file" && ! -L "$file" ]] || fail 'Staging env must be an existing regular absolute-path file (not symlink)'
[[ "$(id -un)" == hiroyuki ]] || fail 'Wrong principal: require i5 user hiroyuki'
[[ "$(hostname)" == ubuntu-worker ]] || fail 'Wrong host: require i5 ubuntu-worker'
[[ "$(stat -c %u -- "$file")" == "$(id -u)" ]] || fail 'Env file must be owned by current principal'
mode="$(stat -c %a -- "$file")"
[[ "$mode" == 600 || "$mode" == 400 ]] || fail 'Env file must have mode 0600 or 0400'
# Compose gives ambient variables precedence over --env-file. Docker, Buildx, and BuildKit
# variables can redirect daemon/build traffic, so reject those along with other ambient
# staging, Compose, database, and SSH-agent credential inputs before reading the host-only file.
if compgen -e | grep -Eq '^(BELIB_STAGE_|COMPOSE_|DOCKER_|BUILDX_|BUILDKIT_|DATABASE_URL$|DIRECT_URL$|POSTGRES_|BETTER_AUTH_SECRET$|R2_|AWS_|S3_|PROD_|PRODUCTION_|VITE_PUBLIC_DEMO_|BELIB_FRONTEND_|SSH_AUTH_SOCK$|SSH_AGENT_PID$|GIT_SSH_COMMAND$)'; then
  fail 'Ambient staging, Docker, Buildx, BuildKit, Compose, database, or SSH-agent overrides are forbidden; use the staging env file only'
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

# Never resolve Docker through PATH: it could select a wrapper or an attacker-controlled
# stub. The system package path must be a root-owned executable without unsafe permissions.
readonly docker_cli='/usr/bin/docker'
[[ "$docker_cli" == /* && -f "$docker_cli" && -x "$docker_cli" && ! -L "$docker_cli" ]] || \
  fail 'Trusted Docker CLI missing or unsafe; do not use a PATH fallback'
docker_owner="$(/usr/bin/stat -c %u -- "$docker_cli" 2>/dev/null)" || fail 'Cannot verify trusted Docker CLI ownership'
docker_mode="$(/usr/bin/stat -c %a -- "$docker_cli" 2>/dev/null)" || fail 'Cannot verify trusted Docker CLI permissions'
[[ "$docker_owner" == 0 && "$docker_mode" =~ ^[0-7]{3,4}$ ]] || \
  fail 'Trusted Docker CLI must be root-owned with a valid permission mode'
docker_mode_octal=$((8#$docker_mode))
(( (docker_mode_octal & 07022) == 0 )) || fail 'Trusted Docker CLI must not be group/world-writable or have special permission bits'
unset docker_owner docker_mode docker_mode_octal

# CLI access does not prove rootless: docker-group access to a rootful daemon is forbidden.
# Ambient Docker overrides are forbidden above; verify the active context directly.
rootless_socket="unix:///run/user/$(id -u)/docker.sock"
docker_context="$("$docker_cli" context show 2>/dev/null)" || fail 'Cannot resolve current Docker context'
[[ -n "$docker_context" ]] || fail 'Cannot resolve current Docker context'
docker_endpoint="$("$docker_cli" context inspect "$docker_context" --format '{{ (index .Endpoints "docker").Host }}' 2>/dev/null)" || fail 'Cannot resolve Docker context'
[[ "$docker_endpoint" == "$rootless_socket" ]] || fail 'Docker endpoint is not this i5 user rootless socket; rootful/remote daemon forbidden'
docker_identity="$("$docker_cli" info --format '{{.Name}}|{{json .SecurityOptions}}')" || fail 'Current nonroot user has no Docker access'
[[ "$docker_identity" == "ubuntu-worker|"* ]] || fail 'Docker daemon is not on the expected i5 host'
[[ "$docker_identity" == *'"name=rootless"'* ]] || fail 'Docker daemon does not report rootless security option'
"$docker_cli" compose version >/dev/null || fail 'Docker Compose plugin unavailable'

# Inspect only; do not bootstrap, create, or switch builders. The docker driver uses
# the Docker daemon's BuildKit, and its endpoint must resolve to the same rootless socket.
if ! buildx_info="$("$docker_cli" buildx inspect 2>/dev/null)"; then
  fail 'Cannot inspect the selected Buildx builder'
fi
if ! printf '%s\n' "$buildx_info" | awk '
  $1 == "Driver:" {
    driver_count++
    driver = $2
    if (NF != 2) invalid = 1
  }
  $1 == "Endpoint:" {
    endpoint_count++
    if (NF != 2) invalid = 1
  }
  END {
    exit !(driver_count == 1 && endpoint_count == 1 && !invalid && driver == "docker")
  }
'; then
  fail 'Selected Buildx builder must use the local docker driver'
fi
buildx_endpoint="$(printf '%s\n' "$buildx_info" | awk '$1 == "Endpoint:" { print $2 }')"
if [[ "$buildx_endpoint" != "$rootless_socket" ]]; then
  [[ "$buildx_endpoint" != *"://"* ]] || fail 'Selected Buildx builder is not bound to this i5 rootless Docker socket'
  buildx_endpoint="$("$docker_cli" context inspect "$buildx_endpoint" --format '{{ (index .Endpoints "docker").Host }}' 2>/dev/null)" || \
    fail 'Cannot resolve the selected Buildx builder endpoint'
fi
[[ "$buildx_endpoint" == "$rootless_socket" ]] || fail 'Selected Buildx builder is not bound to this i5 rootless Docker socket'
unset buildx_info buildx_endpoint

free_kib="$(df -Pk "$root" | awk 'END {print $4}')"
[[ "$free_kib" =~ ^[0-9]+$ && "$free_kib" -ge 8388608 ]] || fail 'Require >=8 GiB free on target i5 filesystem'
mem_kib="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
[[ "$mem_kib" =~ ^[0-9]+$ && "$mem_kib" -ge 2097152 ]] || fail 'Require >=2 GiB available memory on i5'
command -v ss >/dev/null || fail 'ss is required for read-only loopback port check'
if ss -H -ltn | awk '{print $4}' | grep -Eq "(^|:)${port}$"; then
  fail 'Proposed staging loopback port is already bound; inspect existing deployment before choosing another'
fi

# Compose config resolves the staging source and env file without contacting the daemon,
# creating resources, or launching a build. Keep its resolved model and diagnostics private:
# the model contains the synthetic staging credentials from the host-only env file.
command -v python3 >/dev/null || fail 'Python 3 is required for the staging isolation guard'
compose_tmpdir="$(mktemp -d)" || fail 'Could not create a private Compose validation directory'
trap 'rm -rf -- "$compose_tmpdir"' EXIT
compose_json="$compose_tmpdir/compose.json"
compose_diagnostics="$compose_tmpdir/compose.stderr"
if ! "$docker_cli" compose -p belib-isolated-i5 --env-file "$file" \
  -f "$root/compose.staging.yaml" --profile migration config --format json \
  >"$compose_json" 2>"$compose_diagnostics"; then
  fail 'Staging Compose configuration invalid'
fi
python3 -B "$root/scripts/staging/ci-isolation-guard.py" \
  --source-sha "$rev" \
  --compose-source "$root/compose.staging.yaml" \
  --compose-json "$compose_json" \
  --port "$port" || fail 'Staging Compose isolation guard rejected configuration'
printf 'PREFLIGHT_PASS host=ubuntu-worker principal=hiroyuki available_disk_kib=%s available_memory_kib=%s port=%s revision=%s compose_project=belib-isolated-i5\n' "$free_kib" "$mem_kib" "$port" "$rev"
