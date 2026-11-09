#!/usr/bin/env -S -i HOME=/home/hiroyuki PATH=/usr/bin:/bin /bin/bash --noprofile --norc
# Read-only i5 admission check. Never sources the env file or changes Docker daemon state.
set -euo pipefail
umask 077

fail() { printf 'BLOCKED: %s\n' "$*" >&2; exit 1; }
# The executable shebang clears the caller environment before Bash starts. This prevents
# BASH_ENV startup code and exported functions from running. The checks below are defense
# in depth; they cannot undo startup code from an invocation such as `bash script.sh`.
if [[ -v BASH_ENV || -v ENV ]]; then
  fail 'Bash startup overrides are forbidden; invoke preflight from a clean Bash environment'
fi
readonly git_cli='/usr/bin/git'
readonly python_cli='/usr/bin/python3'
if declare -F git >/dev/null; then
  fail 'Ambient Git shell function is forbidden; invoke the preflight executable directly'
fi
if builtin alias git >/dev/null 2>&1; then
  fail 'Ambient Git alias is forbidden; invoke the preflight executable directly'
fi
git_path="$(builtin type -P git || true)"
[[ "$git_path" == "$git_cli" ]] || fail 'Git must resolve to trusted /usr/bin/git on PATH'
[[ -f "$git_cli" && -x "$git_cli" && ! -L "$git_cli" ]] || \
  fail 'Trusted Git executable missing or unsafe'
git_owner="$(/usr/bin/stat -c %u -- "$git_cli" 2>/dev/null)" || fail 'Cannot verify trusted Git ownership'
git_mode="$(/usr/bin/stat -c %a -- "$git_cli" 2>/dev/null)" || fail 'Cannot verify trusted Git permissions'
[[ "$git_owner" == 0 && "$git_mode" =~ ^[0-7]{3,4}$ ]] || \
  fail 'Trusted Git executable must be root-owned with a valid permission mode'
git_mode_octal=$((8#$git_mode))
(( (git_mode_octal & 07022) == 0 )) || fail 'Trusted Git executable must not be group/world-writable or have special permission bits'
unset git_owner git_mode git_mode_octal git_path
python_parent_owner="$(/usr/bin/stat -c %u -- /usr/bin 2>/dev/null)" || fail 'Cannot verify trusted Python directory ownership'
python_parent_mode="$(/usr/bin/stat -c %a -- /usr/bin 2>/dev/null)" || fail 'Cannot verify trusted Python directory permissions'
[[ "$python_parent_owner" == 0 && "$python_parent_mode" =~ ^[0-7]{3,4}$ ]] || \
  fail 'Trusted Python directory must be root-owned with a valid permission mode'
python_parent_mode_octal=$((8#$python_parent_mode))
(( (python_parent_mode_octal & 07022) == 0 )) || fail 'Trusted Python directory must not be group/world-writable or have special permission bits'
python_real="$(/usr/bin/readlink -f -- "$python_cli" 2>/dev/null)" || fail 'Cannot resolve trusted /usr/bin/python3'
[[ "$python_real" == /usr/bin/python3.* && -f "$python_real" && -x "$python_real" && ! -L "$python_real" ]] || \
  fail 'Trusted Python must resolve to a regular /usr/bin/python3.x executable'
python_owner="$(/usr/bin/stat -c %u -- "$python_real" 2>/dev/null)" || fail 'Cannot verify trusted Python ownership'
python_mode="$(/usr/bin/stat -c %a -- "$python_real" 2>/dev/null)" || fail 'Cannot verify trusted Python permissions'
[[ "$python_owner" == 0 && "$python_mode" =~ ^[0-7]{3,4}$ ]] || \
  fail 'Trusted Python must be root-owned with a valid permission mode'
python_mode_octal=$((8#$python_mode))
(( (python_mode_octal & 07022) == 0 )) || fail 'Trusted Python must not be group/world-writable or have special permission bits'
unset python_parent_owner python_parent_mode python_parent_mode_octal python_real python_owner python_mode python_mode_octal
if declare -F docker >/dev/null; then
  fail 'Ambient Docker shell function is forbidden; invoke preflight from a clean Bash environment'
fi
if builtin alias docker >/dev/null 2>&1; then
  fail 'Ambient Docker alias is forbidden; invoke preflight from a clean Bash environment'
fi
root="$(builtin cd -- "$(/usr/bin/dirname -- "${BASH_SOURCE[0]}")/../.." && builtin pwd -P)"
source_tree_guard="$root/scripts/staging/source-tree-guard.py"
[[ $# -eq 1 ]] || fail 'Supply exact absolute path of the staging-only env file'
file="$1"
[[ "$file" = /* && -f "$file" && ! -L "$file" ]] || fail 'Staging env must be an existing regular absolute-path file (not symlink)'
[[ "$(/usr/bin/id -un)" == hiroyuki ]] || fail 'Wrong principal: require i5 user hiroyuki'
[[ "$(/usr/bin/hostname)" == ubuntu-worker ]] || fail 'Wrong host: require i5 ubuntu-worker'
[[ "$(/usr/bin/stat -c %u -- "$file")" == "$(/usr/bin/id -u)" ]] || fail 'Env file must be owned by current principal'
mode="$(/usr/bin/stat -c %a -- "$file")"
[[ "$mode" == 600 || "$mode" == 400 ]] || fail 'Env file must have mode 0600 or 0400'
# Compose gives ambient variables precedence over --env-file. Docker, Buildx, and BuildKit
# variables can redirect daemon/build traffic, so reject those along with other ambient
# staging, Compose, database, and SSH-agent credential inputs before reading the host-only file.
if compgen -e | /usr/bin/grep -Eq '^(BELIB_STAGE_|COMPOSE_|DOCKER_|BUILDX_|BUILDKIT_|DATABASE_URL$|DIRECT_URL$|POSTGRES_|BETTER_AUTH_SECRET$|R2_|AWS_|S3_|PROD_|PRODUCTION_|VITE_PUBLIC_DEMO_|BELIB_FRONTEND_|SSH_AUTH_SOCK$|SSH_AGENT_PID$|GIT_SSH_COMMAND$)'; then
  fail 'Ambient staging, Docker, Buildx, BuildKit, Compose, database, or SSH-agent overrides are forbidden; use the staging env file only'
fi

allowed_keys='BELIB_STAGE_DB_BOOTSTRAP_PASSWORD|BELIB_STAGE_DB_APP_PASSWORD|BELIB_STAGE_DB_MIGRATE_PASSWORD|BELIB_STAGE_AUTH_SECRET|BELIB_STAGE_VCS_REF|BELIB_STAGE_PORT'
if /usr/bin/grep -Ev "^(${allowed_keys})=[^[:space:]]+$" "$file" >/dev/null; then
  fail 'Staging env must contain only the six approved staging keys'
fi
for key in BELIB_STAGE_DB_BOOTSTRAP_PASSWORD BELIB_STAGE_DB_APP_PASSWORD BELIB_STAGE_DB_MIGRATE_PASSWORD BELIB_STAGE_AUTH_SECRET BELIB_STAGE_VCS_REF BELIB_STAGE_PORT; do
  [[ "$(/usr/bin/grep -Ec "^(${key})=" "$file")" -eq 1 ]] || fail "Missing or duplicate $key (no value displayed)"
done

port="$(/usr/bin/sed -n 's/^BELIB_STAGE_PORT=//p' "$file")"
rev="$(/usr/bin/sed -n 's/^BELIB_STAGE_VCS_REF=//p' "$file")"
[[ "$port" =~ ^[0-9]{4,5}$ && "$port" -ge 1024 && "$port" -le 65535 ]] || fail 'Invalid loopback port'
[[ "$rev" =~ ^[0-9a-f]{40}$ ]] || fail 'Revision must be full lowercase 40-character Git SHA'
actual_revision="$(GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_OPTIONAL_LOCKS=0 \
  "$git_cli" -c core.fsmonitor=false -c core.untrackedCache=false -C "$root" rev-parse --verify HEAD 2>/dev/null)" || \
  fail 'Cannot resolve checked-out Git HEAD'
[[ "$actual_revision" == "$rev" ]] || fail 'Checked-out Git HEAD differs from staging revision'
[[ -f "$source_tree_guard" && ! -L "$source_tree_guard" ]] || fail 'Exact-source verifier is missing or unsafe'
"$python_cli" -I -B "$source_tree_guard" --repo-root "$root" --source-sha "$rev" || \
  fail 'Exact Git source verification rejected staging build inputs'
tree_status="$(GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_OPTIONAL_LOCKS=0 \
  "$git_cli" -c core.fsmonitor=false -c core.untrackedCache=false -c status.showUntrackedFiles=all \
  -C "$root" status --porcelain=v1 --untracked-files=all --ignore-submodules=none 2>/dev/null)" || \
  fail 'Cannot verify clean Git worktree'
[[ -z "$tree_status" ]] || fail 'Dirty source tree: fixed staging SHA cannot describe local changes'
unset actual_revision tree_status
# Hex-only values are safe for Compose interpolation, libpq URLs, and psql password prompts.
for key in BELIB_STAGE_DB_BOOTSTRAP_PASSWORD BELIB_STAGE_DB_APP_PASSWORD BELIB_STAGE_DB_MIGRATE_PASSWORD BELIB_STAGE_AUTH_SECRET; do
  /usr/bin/grep -Eq "^${key}=[a-f0-9]{64}$" "$file" || fail "$key must be a local 64-character lowercase hex value"
done
bootstrap_password="$(/usr/bin/sed -n 's/^BELIB_STAGE_DB_BOOTSTRAP_PASSWORD=//p' "$file")"
app_password="$(/usr/bin/sed -n 's/^BELIB_STAGE_DB_APP_PASSWORD=//p' "$file")"
migrate_password="$(/usr/bin/sed -n 's/^BELIB_STAGE_DB_MIGRATE_PASSWORD=//p' "$file")"
auth_secret="$(/usr/bin/sed -n 's/^BELIB_STAGE_AUTH_SECRET=//p' "$file")"
[[ "$bootstrap_password" != "$app_password" && "$bootstrap_password" != "$migrate_password" && "$bootstrap_password" != "$auth_secret" && "$app_password" != "$migrate_password" && "$app_password" != "$auth_secret" && "$migrate_password" != "$auth_secret" ]] || fail 'Bootstrap, app, migration, and auth secrets must all be distinct'
unset bootstrap_password app_password migrate_password auth_secret
/usr/bin/grep -Eq '^BELIB_STAGE_AUTH_SECRET=[a-f0-9]{64}$' "$file" || fail 'Auth secret must be local 64-character hex'

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

trusted_system_uid=0
check_trusted_docker_directory() {
  local path="$1" owner mode mode_octal
  [[ -d "$path" && ! -L "$path" ]] || fail 'Docker CLI plugin search directory is missing or unsafe'
  owner="$(/usr/bin/stat -c %u -- "$path" 2>/dev/null)" || fail 'Cannot verify Docker CLI plugin directory ownership'
  mode="$(/usr/bin/stat -c %a -- "$path" 2>/dev/null)" || fail 'Cannot verify Docker CLI plugin directory permissions'
  [[ "$owner" == "$trusted_system_uid" && "$mode" =~ ^[0-7]{3,4}$ ]] || \
    fail 'Docker CLI plugin search directories must be root-owned with valid permissions'
  mode_octal=$((8#$mode))
  (( (mode_octal & 07022) == 0 )) || fail 'Docker CLI plugin search directories must not be writable by group/world or have special permission bits'
}
check_trusted_docker_plugin() {
  local path="$1" owner mode
  [[ -f "$path" && -x "$path" && ! -L "$path" ]] || fail 'Trusted Docker CLI plugin missing or unsafe'
  owner="$(/usr/bin/stat -c %u -- "$path" 2>/dev/null)" || fail 'Cannot verify Docker CLI plugin ownership'
  mode="$(/usr/bin/stat -c %a -- "$path" 2>/dev/null)" || fail 'Cannot verify Docker CLI plugin permissions'
  [[ "$owner" == "$trusted_system_uid" && "$mode" == 755 ]] || \
    fail 'Docker Compose and Buildx plugins must be root-owned regular 0755 executables'
}

# Docker resolves CLI plugins before validating Compose inputs. Only the package-owned
# system directory is accepted; check every existing search directory and reject duplicate
# plugin names so user/system path precedence cannot substitute another executable.
for trusted_dir in /usr /usr/bin /usr/libexec /usr/libexec/docker /usr/libexec/docker/cli-plugins \
  /usr/local /usr/local/lib /usr/local/lib/docker /usr/local/lib/docker/cli-plugins \
  /usr/local/libexec /usr/local/libexec/docker /usr/local/libexec/docker/cli-plugins \
  /usr/lib /usr/lib/docker /usr/lib/docker/cli-plugins; do
  if [[ -e "$trusted_dir" || -L "$trusted_dir" ]]; then
    check_trusted_docker_directory "$trusted_dir"
  fi
done
readonly docker_plugin_dir='/usr/libexec/docker/cli-plugins'
check_trusted_docker_plugin "$docker_plugin_dir/docker-compose"
check_trusted_docker_plugin "$docker_plugin_dir/docker-buildx"
for plugin_search_dir in /usr/local/lib/docker/cli-plugins /usr/local/libexec/docker/cli-plugins \
  /usr/lib/docker/cli-plugins /usr/libexec/docker/cli-plugins; do
  for plugin_name in docker-compose docker-buildx; do
    plugin_candidate="$plugin_search_dir/$plugin_name"
    if [[ "$plugin_search_dir/$plugin_name" != "$docker_plugin_dir/$plugin_name" && \
        ( -e "$plugin_candidate" || -L "$plugin_candidate" ) ]]; then
      fail 'Unapproved duplicate Docker Compose or Buildx plugin is present'
    fi
  done
done

# An empty private config removes user plugin directories, cliPluginsExtraDirs, context
# selection, and Buildx builder overrides. The only context used below is created here for
# this exact local rootless socket. No user config or environment endpoint is inherited.
[[ -d /tmp && ! -L /tmp ]] || fail 'System temporary directory must be a real directory'
tmp_owner="$(/usr/bin/stat -c %u -- /tmp 2>/dev/null)" || fail 'Cannot verify system temporary directory ownership'
tmp_mode="$(/usr/bin/stat -c %a -- /tmp 2>/dev/null)" || fail 'Cannot verify system temporary directory permissions'
[[ "$tmp_owner" == 0 && "$tmp_mode" == 1777 ]] || fail 'System temporary directory must be root-owned mode 1777'
unset tmp_owner tmp_mode
preflight_tmpdir="$(/usr/bin/mktemp -d /tmp/belib-staging-preflight.XXXXXX)" || \
  fail 'Could not create a private Docker validation directory'
/usr/bin/chmod 700 -- "$preflight_tmpdir" || fail 'Could not protect the Docker validation directory'
[[ -d "$preflight_tmpdir" && ! -L "$preflight_tmpdir" && \
   "$(/usr/bin/stat -c %u -- "$preflight_tmpdir")" == "$(/usr/bin/id -u)" && \
   "$(/usr/bin/stat -c %a -- "$preflight_tmpdir")" == 700 ]] || \
  fail 'Docker validation directory must be private and owned by the current user'
docker_config="$preflight_tmpdir/docker-config"
/usr/bin/mkdir -m 700 -- "$docker_config" || fail 'Could not isolate Docker client configuration'
trap '/usr/bin/rm -rf -- "$preflight_tmpdir"' EXIT
export DOCKER_CONFIG="$docker_config"

# CLI access does not prove rootless: docker-group access to a rootful daemon is forbidden.
rootless_socket="unix:///run/user/$(/usr/bin/id -u)/docker.sock"
docker_context='belib-i5-preflight-rootless'
"$docker_cli" context create "$docker_context" --docker "host=$rootless_socket" >/dev/null 2>&1 || \
  fail 'Cannot create the isolated rootless Docker context'
docker_endpoint="$("$docker_cli" context inspect "$docker_context" --format '{{ (index .Endpoints "docker").Host }}' 2>/dev/null)" || \
  fail 'Cannot resolve the isolated Docker context'
[[ "$docker_endpoint" == "$rootless_socket" ]] || fail 'Docker endpoint is not this i5 user rootless socket; rootful/remote daemon forbidden'
docker_identity="$("$docker_cli" --context "$docker_context" info --format '{{.Name}}|{{json .SecurityOptions}}')" || fail 'Current nonroot user has no Docker access'
[[ "$docker_identity" == "ubuntu-worker|"* ]] || fail 'Docker daemon is not on the expected i5 host'
[[ "$docker_identity" == *'"name=rootless"'* ]] || fail 'Docker daemon does not report rootless security option'
"$docker_cli" --context "$docker_context" compose version >/dev/null || fail 'Docker Compose plugin unavailable'

# Inspect only; do not bootstrap, create, or switch builders. The docker driver uses
# the Docker daemon's BuildKit, and its endpoint must resolve to the same rootless socket.
if ! buildx_info="$("$docker_cli" --context "$docker_context" buildx inspect 2>/dev/null)"; then
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
compose_tmpdir="$preflight_tmpdir"
compose_json="$compose_tmpdir/compose.json"
compose_diagnostics="$compose_tmpdir/compose.stderr"
if ! "$docker_cli" --context "$docker_context" compose -p belib-isolated-i5 --env-file "$file" \
  -f "$root/compose.staging.yaml" --profile migration config --format json \
  >"$compose_json" 2>"$compose_diagnostics"; then
  fail 'Staging Compose configuration invalid'
fi
"$python_cli" -I -B "$root/scripts/staging/ci-isolation-guard.py" \
  --source-sha "$rev" \
  --compose-source "$root/compose.staging.yaml" \
  --compose-json "$compose_json" \
  --port "$port" || fail 'Staging Compose isolation guard rejected configuration'
printf 'PREFLIGHT_PASS host=ubuntu-worker principal=hiroyuki available_disk_kib=%s available_memory_kib=%s port=%s revision=%s compose_project=belib-isolated-i5\n' "$free_kib" "$mem_kib" "$port" "$rev"
