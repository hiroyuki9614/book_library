#!/usr/bin/env bash
# Offline preflight trust-boundary and secret-validation checks with synthetic fixtures.
set -euo pipefail

fail() { printf 'BLOCKED: %s\n' "$*" >&2; exit 1; }
[[ "$(id -un)" == hiroyuki ]] || fail 'Run as i5 user hiroyuki'
[[ "$(hostname)" == ubuntu-worker ]] || fail 'Run on i5 host ubuntu-worker'

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source_preflight="$script_dir/preflight.sh"
source_guard="$script_dir/ci-isolation-guard.py"
source_backend_dockerfile="$script_dir/Dockerfile.backend.staging"
source_frontend_dockerfile="$script_dir/Dockerfile.frontend.staging"
source_staging_ci="$(cd -- "$script_dir/../../.github/workflows" && pwd -P)/staging-ci.yml"
source_compose="$(cd -- "$script_dir/../.." && pwd -P)/compose.staging.yaml"
source_role_init="$script_dir/10-belib-staging-roles.sh"
source_nginx_default="$script_dir/nginx-default.conf"
source_nginx_conf="$(cd -- "$script_dir/../.." && pwd -P)/nginx/nginx.conf"
[[ -f "$source_preflight" ]] || fail 'Cannot find staging preflight script'
[[ -f "$source_guard" && -f "$source_backend_dockerfile" && -f "$source_frontend_dockerfile" && -f "$source_staging_ci" && -f "$source_compose" && -f "$source_role_init" ]] || fail 'Cannot find staging guard inputs'

test_root="$(mktemp -d "/tmp/belib-staging-preflight-test.XXXXXX")"
trap 'rm -rf -- "$test_root"' EXIT
repo_root="$test_root/repo"
fixture_preflight="$repo_root/scripts/staging/preflight.sh"
test_bin="$test_root/bin"
mkdir -p "$(dirname -- "$fixture_preflight")" "$test_bin" "$repo_root/nginx" \
  "$repo_root/.github/workflows" \
  "$repo_root/backend" "$repo_root/frontend"
cp -- "$source_preflight" "$fixture_preflight"
chmod 700 "$fixture_preflight"
missing_docker_cli="$test_root/trusted-docker-unavailable"
python3 -B - "$fixture_preflight" "$missing_docker_cli" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
needle = "readonly docker_cli='/usr/bin/docker'"
if source.count(needle) != 1:
    raise SystemExit("Expected one pinned Docker CLI path in the preflight fixture")
path.write_text(source.replace(needle, f"readonly docker_cli='{sys.argv[2]}'", 1), encoding="utf-8")
PY
cp -- "$source_guard" "$repo_root/scripts/staging/ci-isolation-guard.py"
cp -- "$source_backend_dockerfile" "$repo_root/scripts/staging/Dockerfile.backend.staging"
cp -- "$source_frontend_dockerfile" "$repo_root/scripts/staging/Dockerfile.frontend.staging"
cp -- "$source_staging_ci" "$repo_root/.github/workflows/staging-ci.yml"
cp -- "$source_role_init" "$repo_root/scripts/staging/10-belib-staging-roles.sh"
cp -- "$source_nginx_default" "$repo_root/scripts/staging/nginx-default.conf"
cp -- "$source_compose" "$repo_root/compose.staging.yaml"
cp -- "$source_nginx_conf" "$repo_root/nginx/nginx.conf"

GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C "$repo_root" init -q
git -C "$repo_root" add .github/workflows/staging-ci.yml compose.staging.yaml nginx/nginx.conf scripts/staging
GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C "$repo_root" \
  -c user.name='BeLib staging test' \
  -c user.email='belib-staging-test@example.invalid' \
  -c core.hooksPath=/dev/null \
  -c commit.gpgSign=false \
  commit -qm 'Offline staging preflight fixture'
revision="$(GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C "$repo_root" rev-parse HEAD)"
base_revision="$revision"

# The missing pinned executable makes this fixture fail before any Docker invocation.
for utility in bash dirname grep hostname id sed sh stat; do
  utility_path="$(command -v "$utility")"
  [[ -x "$utility_path" ]] || fail "Required test utility unavailable: $utility"
  ln -s -- "$utility_path" "$test_bin/$utility"
done

synthetic_secret_a='0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef'
synthetic_secret_b='fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210'
synthetic_secret_c='00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff'
synthetic_secret_d='ffeeddccbbaa99887766554433221100ffeeddccbbaa99887766554433221100'

write_fixture() {
  local destination="$1" bootstrap_password="$2" app_password="$3" migrate_password="$4" auth_secret="$5"
  printf 'BELIB_STAGE_DB_BOOTSTRAP_PASSWORD=%s\nBELIB_STAGE_DB_APP_PASSWORD=%s\nBELIB_STAGE_DB_MIGRATE_PASSWORD=%s\nBELIB_STAGE_AUTH_SECRET=%s\nBELIB_STAGE_VCS_REF=%s\nBELIB_STAGE_PORT=49199\n' \
    "$bootstrap_password" "$app_password" "$migrate_password" "$auth_secret" "$revision" > "$destination"
  chmod 600 "$destination"
}

equal_env="$test_root/equal.env"
write_fixture "$equal_env" "$synthetic_secret_a" "$synthetic_secret_a" "$synthetic_secret_c" "$synthetic_secret_d"
set +e
equal_output="$(/usr/bin/env -i PATH="$test_bin:/usr/bin:/bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  /bin/bash --noprofile --norc "$fixture_preflight" "$equal_env" 2>&1)"
equal_status=$?
set -e
[[ "$equal_status" -eq 1 && "$equal_output" == *'BLOCKED: Bootstrap, app, migration, and auth secrets must all be distinct'* ]] || \
  fail 'Equal synthetic secrets did not stop at the distinct-secret guard'
[[ "$equal_output" != *'Docker CLI missing'* ]] || fail 'Equal-secret rejection occurred after the Docker check'
printf 'PASS: equal synthetic secrets rejected before Docker check (exit=%s)\n' "$equal_status"

distinct_env="$test_root/distinct.env"
write_fixture "$distinct_env" "$synthetic_secret_a" "$synthetic_secret_b" "$synthetic_secret_c" "$synthetic_secret_d"
set +e
distinct_output="$(/usr/bin/env -i PATH="$test_bin:/usr/bin:/bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  /bin/bash --noprofile --norc "$fixture_preflight" "$distinct_env" 2>&1)"
distinct_status=$?
set -e
[[ "$distinct_status" -eq 1 && "$distinct_output" == *'BLOCKED: Trusted Docker CLI missing or unsafe; do not use a PATH fallback'* ]] || \
  fail 'Distinct synthetic secrets did not reach the expected trusted Docker CLI guard'
printf 'PASS: distinct synthetic secrets reached trusted Docker CLI guard (exit=%s)\n' "$distinct_status"
printf 'NOTE: the unavailable trusted executable is only the expected offline stopping point, not preflight acceptance.\n'

legacy_env="$test_root/legacy.env"
printf 'BELIB_STAGE_DB_PASSWORD=%s\nBELIB_STAGE_AUTH_SECRET=%s\nBELIB_STAGE_VCS_REF=%s\nBELIB_STAGE_PORT=49199\n' \
  "$synthetic_secret_a" "$synthetic_secret_d" "$revision" > "$legacy_env"
chmod 600 "$legacy_env"
set +e
legacy_output="$(/usr/bin/env -i PATH="$test_bin:/usr/bin:/bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  /bin/bash --noprofile --norc "$fixture_preflight" "$legacy_env" 2>&1)"
legacy_status=$?
set -e
[[ "$legacy_status" -eq 1 && "$legacy_output" == *'BLOCKED: Staging env must contain only the six approved staging keys'* ]] || \
  fail 'The removed single-password environment key was not rejected'
printf 'PASS: legacy single-password env rejected before Docker check (exit=%s)\n' "$legacy_status"

integration_bin="$test_root/integration-bin"
mkdir -p "$integration_bin"
for utility in bash dirname grep hostname id sed sh stat awk mktemp rm cat python3; do
  utility_path="$(command -v "$utility")"
  [[ -x "$utility_path" ]] || fail "Required integration utility unavailable: $utility"
  ln -s -- "$utility_path" "$integration_bin/$utility"
done

cat > "$integration_bin/df" <<'SH'
#!/usr/bin/env bash
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\nfixture 20000000 0 12000000 0%% /tmp\n'
SH
cat > "$integration_bin/ss" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod 700 "$integration_bin/df" "$integration_bin/ss"

cat > "$integration_bin/docker" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$STUB_DOCKER_LOG"
case "$*" in
  'context show') printf 'default\n' ;;
  'context inspect '*) printf 'unix:///run/user/%s/docker.sock\n' "$(id -u)" ;;
  'info --format '*) printf 'ubuntu-worker|["name=rootless"]\n' ;;
  'compose version') printf 'Docker Compose version fixture\n' ;;
  'buildx inspect') cat -- "$STUB_BUILDX_INFO" ;;
  *' config --format json') cat -- "$STUB_COMPOSE_JSON" ;;
  *' up '*|*' build '*) printf 'Unexpected deployment command in preflight test\n' >&2; exit 90 ;;
  *) printf 'Unexpected Docker command in preflight test\n' >&2; exit 91 ;;
esac
SH
chmod 700 "$integration_bin/docker"

# These disposable fixture variants let the integration checks exercise a safe synthetic
# CLI and an untrusted path. The actual repository script stays pinned to /usr/bin/docker.
fixture_preflight_synthetic="$repo_root/scripts/staging/preflight-synthetic.sh"
fixture_preflight_untrusted="$repo_root/scripts/staging/preflight-untrusted.sh"
cp -- "$source_preflight" "$fixture_preflight_synthetic"
cp -- "$source_preflight" "$fixture_preflight_untrusted"
python3 -B - "$fixture_preflight_synthetic" "$fixture_preflight_untrusted" "$integration_bin/docker" "$(id -u)" <<'PY'
import sys
from pathlib import Path

synthetic_path, untrusted_path, docker_cli, owner = sys.argv[1:]
needle = "readonly docker_cli='/usr/bin/docker'"
owner_check = '[[ "$docker_owner" == 0 && "$docker_mode" =~ ^[0-7]{3,4}$ ]]'
for fixture, expected_owner in ((synthetic_path, owner), (untrusted_path, "0")):
    path = Path(fixture)
    source = path.read_text(encoding="utf-8")
    if source.count(needle) != 1:
        raise SystemExit(f"Expected one pinned Docker CLI path in {path.name}")
    source = source.replace(needle, f"readonly docker_cli='{docker_cli}'", 1)
    replacement = f'[[ "$docker_owner" == {expected_owner} && "$docker_mode" =~ ^[0-7]{{3,4}}$ ]]'
    if source.count(owner_check) != 1:
        raise SystemExit(f"Expected one Docker ownership check in {path.name}")
    path.write_text(source.replace(owner_check, replacement, 1), encoding="utf-8")
PY
git -C "$repo_root" add scripts/staging/preflight-synthetic.sh scripts/staging/preflight-untrusted.sh
GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C "$repo_root" \
  -c user.name='BeLib staging test' \
  -c user.email='belib-staging-test@example.invalid' \
  -c core.hooksPath=/dev/null \
  -c commit.gpgSign=false \
  commit -qm 'Add offline Docker executable trust fixtures'
revision="$(GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C "$repo_root" rev-parse HEAD)"

valid_buildx_info="$test_root/buildx-local.txt"
printf 'Name: default\nDriver: docker\n\nNodes:\nName: default\nEndpoint: default\nStatus: running\n' > "$valid_buildx_info"

make_compose_model() {
  local destination="$1" mode="$2"
  python3 -B - "$script_dir/test-ci-isolation-guard.py" "$repo_root" "$revision" "$mode" "$destination" <<'PY'
import importlib.util
import json
import sys
from pathlib import Path

test_path, repo_root_text, revision, mode, output_path = sys.argv[1:]
spec = importlib.util.spec_from_file_location("staging_guard_test", test_path)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
repo_root = Path(repo_root_text)
model = module.compose_model()
source_root = str(module.ROOT)
for service in model["services"].values():
    for mount in service.get("volumes", []):
        source = mount.get("source")
        if isinstance(source, str) and source.startswith(source_root + "/"):
            mount["source"] = str(repo_root) + source[len(source_root):]
for service_name in ("backend", "frontend"):
    model["services"][service_name]["build"]["args"]["VCS_REF"] = revision
if mode == "unsafe-model":
    model["services"]["backend"]["build"]["ssh"] = ["default"]
Path(output_path).write_text(json.dumps(model), encoding="utf-8")
PY
}

valid_env="$test_root/preflight-valid.env"
write_fixture "$valid_env" "$synthetic_secret_a" "$synthetic_secret_b" "$synthetic_secret_c" "$synthetic_secret_d"
valid_model="$test_root/compose-valid.json"
make_compose_model "$valid_model" valid
docker_log="$test_root/docker-calls.log"

# Use a dirty synthetic checkout to prove Git shims cannot fabricate a clean status.
git_attack_repo="$test_root/git-attack-repo"
cp -a -- "$repo_root" "$git_attack_repo"
git_attack_preflight="$git_attack_repo/scripts/staging/preflight.sh"
git_attack_env="$test_root/git-attack.env"
printf '\n# synthetic dirty-worktree regression\n' >> "$git_attack_repo/compose.staging.yaml"
write_fixture "$git_attack_env" "$synthetic_secret_a" "$synthetic_secret_b" "$synthetic_secret_c" "$synthetic_secret_d"

grep -F "readonly docker_cli='/usr/bin/docker'" "$source_preflight" >/dev/null || \
  fail 'Host preflight is not pinned to the trusted absolute Docker executable'
[[ -x "$integration_bin/docker" ]] || fail 'Expected fake PATH Docker binary is unavailable'

# A PATH-only fake Docker binary cannot satisfy the pinned executable check.
runtime_path_log="$test_root/path-docker-calls.log"
set +e
runtime_path_output="$(/usr/bin/env -i PATH="$integration_bin:/usr/bin:/bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  STUB_DOCKER_LOG="$runtime_path_log" STUB_COMPOSE_JSON="$valid_model" STUB_BUILDX_INFO="$valid_buildx_info" \
  /bin/bash --noprofile --norc "$fixture_preflight" "$valid_env" 2>&1)"
runtime_path_status=$?
set -e
[[ "$runtime_path_status" -eq 1 && "$runtime_path_output" == *'BLOCKED: Trusted Docker CLI missing or unsafe; do not use a PATH fallback'* ]] || \
  fail 'PATH fake Docker binary satisfied an unavailable pinned executable'
[[ ! -s "$runtime_path_log" ]] || fail 'PATH fake Docker binary was invoked by the trust-boundary check'
printf 'PASS: PATH Docker stub cannot satisfy the pinned runtime binary check\n'

# An exported git() function that lies about status must not admit the dirty fixture.
function_git_log="$test_root/function-git-calls.log"
set +e
function_git_output="$(/usr/bin/env -i PATH="$integration_bin:/usr/bin:/bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  GIT_ATTACK_REPO="$git_attack_repo" FUNCTION_GIT_LOG="$function_git_log" \
  /bin/bash --noprofile --norc -c '
git() {
  printf "%s\\n" "$*" >> "$FUNCTION_GIT_LOG"
  case " $* " in
    *" rev-parse "*) /usr/bin/git -C "$GIT_ATTACK_REPO" rev-parse --verify HEAD ;;
    *" status "*) printf "" ;;
    *) /usr/bin/git "$@" ;;
  esac
}
export -f git
exec /bin/bash --noprofile --norc "$1" "$2"
' _ "$git_attack_preflight" "$git_attack_env" 2>&1)"
function_git_status=$?
set -e
[[ "$function_git_status" -eq 1 && "$function_git_output" == *'BLOCKED: Ambient Git shell function is forbidden'* ]] || \
  fail 'Exported git() that fabricates a clean status was not rejected'
[[ ! -s "$function_git_log" ]] || fail 'Exported git() ran before preflight rejected it'
printf 'PASS: exported git() cannot conceal a dirty synthetic checkout\n'

# A PATH git shim that fabricates the same clean outputs must be rejected before execution.
path_git_bin="$test_root/path-git-bin"
path_git_log="$test_root/path-git-calls.log"
mkdir -p "$path_git_bin"
cat > "$path_git_bin/git" <<SH
#!/bin/bash
printf 'called\\n' >> '$path_git_log'
case " \$* " in
  *" rev-parse "*) exec /usr/bin/git -C '$git_attack_repo' rev-parse --verify HEAD ;;
  *" status "*) exit 0 ;;
  *) exec /usr/bin/git "\$@" ;;
esac
SH
chmod 700 "$path_git_bin/git"
set +e
path_git_output="$(/usr/bin/env -i PATH="$path_git_bin:/usr/bin:/bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  /bin/bash --noprofile --norc "$git_attack_preflight" "$git_attack_env" 2>&1)"
path_git_status=$?
set -e
[[ "$path_git_status" -eq 1 && "$path_git_output" == *'BLOCKED: Git must resolve to trusted /usr/bin/git on PATH'* ]] || \
  fail 'PATH git shim was not rejected'
[[ ! -s "$path_git_log" ]] || fail 'PATH git shim ran before preflight rejected it'
printf 'PASS: PATH git shim cannot conceal a dirty synthetic checkout\n'

# A user-owned executable cannot become a trusted Docker CLI just by using an absolute path.
untrusted_path_log="$test_root/untrusted-docker-calls.log"
set +e
untrusted_path_output="$(/usr/bin/env -i PATH="$integration_bin:/usr/bin:/bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  STUB_DOCKER_LOG="$untrusted_path_log" STUB_COMPOSE_JSON="$valid_model" STUB_BUILDX_INFO="$valid_buildx_info" \
  /bin/bash --noprofile --norc "$fixture_preflight_untrusted" "$valid_env" 2>&1)"
untrusted_path_status=$?
set -e
[[ "$untrusted_path_status" -eq 1 && "$untrusted_path_output" == *'BLOCKED: Trusted Docker CLI must be root-owned with a valid permission mode'* ]] || \
  fail 'Preflight accepted a user-owned absolute Docker executable'
[[ ! -s "$untrusted_path_log" ]] || fail 'User-owned Docker executable was invoked before trust rejection'
printf 'PASS: user-owned absolute Docker executable rejected before invocation\n'

# An exported Bash function that fabricates an allowed rootless host must be rejected.
function_docker_log="$test_root/function-docker-calls.log"
set +e
function_output="$(/usr/bin/env -i PATH="$integration_bin:/usr/bin:/bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  FUNCTION_DOCKER_LOG="$function_docker_log" STUB_COMPOSE_JSON="$valid_model" STUB_BUILDX_INFO="$valid_buildx_info" \
  /bin/bash --noprofile --norc -c '
docker() {
  printf "%s\\n" "$*" >> "$FUNCTION_DOCKER_LOG"
  case "$*" in
    "context show") printf "default\\n" ;;
    "context inspect "*) printf "unix:///run/user/%s/docker.sock\\n" "$(id -u)" ;;
    "info --format "*) printf "ubuntu-worker|[\\\"name=rootless\\\"]\\n" ;;
    "compose version") printf "Docker Compose version fixture\\n" ;;
    "buildx inspect") cat -- "$STUB_BUILDX_INFO" ;;
    *" config --format json") cat -- "$STUB_COMPOSE_JSON" ;;
    *) return 91 ;;
  esac
}
export -f docker
exec /bin/bash --noprofile --norc "$1" "$2"
' _ "$fixture_preflight" "$valid_env" 2>&1)"
function_status=$?
set -e
[[ "$function_status" -eq 1 && "$function_output" == *'BLOCKED: Ambient Docker shell function is forbidden'* ]] || \
  fail 'Exported Docker function that fakes rootless host output was not rejected'
[[ ! -s "$function_docker_log" ]] || fail 'Exported Docker function ran before preflight rejected it'
printf 'PASS: exported Docker function faking allowed rootless output rejected before Docker\n'

# BASH_ENV is sourced by Bash before a script starts, so never run preflight with it set.
startup_override="$test_root/bash-env-override"
: > "$startup_override"
for startup_name in BASH_ENV ENV; do
  set +e
  startup_output="$(/usr/bin/env -i PATH="$integration_bin:/usr/bin:/bin" "$startup_name=$startup_override" \
    GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null /bin/bash --noprofile --norc "$fixture_preflight" "$valid_env" 2>&1)"
  startup_status=$?
  set -e
  [[ "$startup_status" -eq 1 && "$startup_output" == *'BLOCKED: Bash startup overrides are forbidden'* ]] || \
    fail "$startup_name startup override was not rejected"
  printf 'PASS: %s startup override rejected before preflight checks\n' "$startup_name"
done

# A Bash startup file can leave an alias behind even when it unsets BASH_ENV.
alias_override="$test_root/bash-env-alias-override"
cat > "$alias_override" <<'SH'
shopt -s expand_aliases
alias docker='printf "spoofed Docker output\\n"'
unset BASH_ENV
SH
set +e
alias_output="$(/usr/bin/env -i PATH="$integration_bin:/usr/bin:/bin" BASH_ENV="$alias_override" \
  GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null /bin/bash --noprofile --norc "$fixture_preflight" "$valid_env" 2>&1)"
alias_status=$?
set -e
[[ "$alias_status" -eq 1 && "$alias_output" == *'BLOCKED: Ambient Docker alias is forbidden'* ]] || \
  fail 'Docker alias from a Bash startup file was not rejected'
printf 'PASS: Docker alias from Bash startup file rejected before Docker\n'

# The executable's sanitized shebang must prevent startup code from running at all,
# including code that unsets BASH_ENV and exports a git() function to hide a dirty tree.
startup_marker="$test_root/bash-env-started"
startup_git_override="$test_root/bash-env-git-override"
cat > "$startup_git_override" <<SH
printf 'started\\n' > '$startup_marker'
git() {
  case " \$* " in
    *" rev-parse "*) /usr/bin/git -C '$git_attack_repo' rev-parse --verify HEAD ;;
    *" status "*) printf "" ;;
    *) /usr/bin/git "\$@" ;;
  esac
}
export -f git
unset BASH_ENV
SH
set +e
startup_git_output="$(BASH_ENV="$startup_git_override" "$git_attack_preflight" "$git_attack_env" 2>&1)"
startup_git_status=$?
set -e
[[ "$startup_git_status" -eq 1 && "$startup_git_output" == *'BLOCKED: Dirty source tree: fixed staging SHA cannot describe local changes'* ]] || \
  fail 'Sanitized executable did not use trusted Git after hostile BASH_ENV input'
[[ ! -e "$startup_marker" ]] || fail 'BASH_ENV startup code ran before preflight despite the clean shebang boundary'
printf 'PASS: BASH_ENV startup code is cleared before Bash starts\n'

set +e
valid_output="$(/usr/bin/env -i PATH="$integration_bin:/usr/bin:/bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  STUB_DOCKER_LOG="$docker_log" STUB_COMPOSE_JSON="$valid_model" STUB_BUILDX_INFO="$valid_buildx_info" \
  /bin/bash --noprofile --norc "$fixture_preflight_synthetic" "$valid_env" 2>&1)"
valid_status=$?
set -e
[[ "$valid_status" -eq 0 && "$valid_output" == *'STAGING_COMPOSE_GUARD_PASS '* && "$valid_output" == *'PREFLIGHT_PASS '* ]] || \
  fail 'Preflight did not run the isolation guard on the resolved Compose model'
for synthetic_secret in "$synthetic_secret_a" "$synthetic_secret_b" "$synthetic_secret_c" "$synthetic_secret_d"; do
  [[ "$valid_output" != *"$synthetic_secret"* ]] || fail 'Preflight output exposed a synthetic credential'
done
grep -F 'buildx inspect' "$docker_log" >/dev/null || fail 'Preflight did not inspect the selected Buildx builder'
printf 'PASS: preflight passes only after offline resolved-model guard (exit=%s)\n' "$valid_status"

# Buildx and BuildKit environment overrides can select remote build infrastructure.
for override in 'BUILDX_BUILDER=remote' 'BUILDKIT_HOST=tcp://builder.invalid:1234'; do
  calls_before="$(wc -l < "$docker_log")"
  set +e
  override_output="$(/usr/bin/env -i PATH="$integration_bin:/usr/bin:/bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    "$override" STUB_DOCKER_LOG="$docker_log" STUB_COMPOSE_JSON="$valid_model" STUB_BUILDX_INFO="$valid_buildx_info" \
    /bin/bash --noprofile --norc "$fixture_preflight_synthetic" "$valid_env" 2>&1)"
  override_status=$?
  set -e
  [[ "$override_status" -eq 1 && "$override_output" == *'BLOCKED: Ambient staging, Docker, Buildx, BuildKit, Compose, database, or SSH-agent overrides are forbidden'* ]] || \
    fail 'Ambient Buildx or BuildKit override was not rejected'
  [[ "$(wc -l < "$docker_log")" -eq "$calls_before" ]] || fail 'Ambient Buildx or BuildKit override reached Docker'
  for synthetic_secret in "$synthetic_secret_a" "$synthetic_secret_b" "$synthetic_secret_c" "$synthetic_secret_d"; do
    [[ "$override_output" != *"$synthetic_secret"* ]] || fail 'Buildx/BuildKit override rejection exposed a synthetic credential'
  done
  printf 'PASS: ambient Buildx/BuildKit override rejected before Docker\n'
done

# The selected builder can be remote even when the active Docker context is local.
remote_buildx_info="$test_root/buildx-remote.txt"
printf 'Name: remote\nDriver: remote\n\nNodes:\nName: remote0\nEndpoint: tcp://builder.invalid:1234\nStatus: running\n' > "$remote_buildx_info"
set +e
remote_builder_output="$(/usr/bin/env -i PATH="$integration_bin:/usr/bin:/bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  STUB_DOCKER_LOG="$docker_log" STUB_COMPOSE_JSON="$valid_model" STUB_BUILDX_INFO="$remote_buildx_info" \
  /bin/bash --noprofile --norc "$fixture_preflight_synthetic" "$valid_env" 2>&1)"
remote_builder_status=$?
set -e
[[ "$remote_builder_status" -eq 1 && "$remote_builder_output" == *'BLOCKED: Selected Buildx builder must use the local docker driver'* ]] || \
  fail 'Preflight accepted a preselected remote Buildx builder'
for synthetic_secret in "$synthetic_secret_a" "$synthetic_secret_b" "$synthetic_secret_c" "$synthetic_secret_d"; do
  [[ "$remote_builder_output" != *"$synthetic_secret"* ]] || fail 'Remote-builder rejection exposed a synthetic credential'
done
[[ "$remote_builder_output" != *'builder.invalid'* ]] || fail 'Remote-builder rejection exposed endpoint details'
printf 'PASS: preselected remote Buildx builder rejected without exposing credentials (exit=%s)\n' "$remote_builder_status"

wrong_socket_buildx_info="$test_root/buildx-wrong-socket.txt"
printf 'Name: default\nDriver: docker\n\nNodes:\nName: default\nEndpoint: unix:///run/user/1001/docker.sock\nStatus: running\n' > "$wrong_socket_buildx_info"
set +e
wrong_socket_output="$(/usr/bin/env -i PATH="$integration_bin:/usr/bin:/bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  STUB_DOCKER_LOG="$docker_log" STUB_COMPOSE_JSON="$valid_model" STUB_BUILDX_INFO="$wrong_socket_buildx_info" \
  /bin/bash --noprofile --norc "$fixture_preflight_synthetic" "$valid_env" 2>&1)"
wrong_socket_status=$?
set -e
[[ "$wrong_socket_status" -eq 1 && "$wrong_socket_output" == *'BLOCKED: Selected Buildx builder is not bound to this i5 rootless Docker socket'* ]] || \
  fail 'Preflight accepted a local-driver builder bound to another socket'
[[ "$wrong_socket_output" != *'1001'* ]] || fail 'Wrong-socket rejection exposed endpoint details'
printf 'PASS: local Buildx driver bound to a different user socket rejected (exit=%s)\n' "$wrong_socket_status"

unsafe_model="$test_root/compose-unsafe.json"
make_compose_model "$unsafe_model" unsafe-model
set +e
model_output="$(/usr/bin/env -i PATH="$integration_bin:/usr/bin:/bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  STUB_DOCKER_LOG="$docker_log" STUB_COMPOSE_JSON="$unsafe_model" STUB_BUILDX_INFO="$valid_buildx_info" \
  /bin/bash --noprofile --norc "$fixture_preflight_synthetic" "$valid_env" 2>&1)"
model_status=$?
set -e
[[ "$model_status" -eq 1 && "$model_output" == *'BLOCKED: Staging Compose isolation guard rejected configuration'* ]] || \
  fail 'Preflight accepted an unsafe resolved build.ssh model'
for synthetic_secret in "$synthetic_secret_a" "$synthetic_secret_b" "$synthetic_secret_c" "$synthetic_secret_d"; do
  [[ "$model_output" != *"$synthetic_secret"* ]] || fail 'Unsafe-model rejection exposed a synthetic credential'
done
printf 'PASS: preflight blocks resolved build.ssh without exposing credentials (exit=%s)\n' "$model_status"

python3 -B - "$repo_root/compose.staging.yaml" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
start = source.index("  backend:\n")
end = source.index("\n  frontend:\n", start)
block = source[start:end]
block = block.replace(
    "      target: runtime\n",
    "      target: runtime\n      ssh: [default]\n",
    1,
)
path.write_text(source[:start] + block + source[end:], encoding="utf-8")
PY
git -C "$repo_root" add compose.staging.yaml
GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C "$repo_root" \
  -c user.name='BeLib staging test' \
  -c user.email='belib-staging-test@example.invalid' \
  -c core.hooksPath=/dev/null \
  -c commit.gpgSign=false \
  commit -qm 'Unsafe source build fixture'
revision="$(GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C "$repo_root" rev-parse HEAD)"
source_attack_env="$test_root/preflight-source-attack.env"
write_fixture "$source_attack_env" "$synthetic_secret_a" "$synthetic_secret_b" "$synthetic_secret_c" "$synthetic_secret_d"
source_model="$test_root/compose-source-attack.json"
make_compose_model "$source_model" valid
set +e
source_output="$(/usr/bin/env -i PATH="$integration_bin:/usr/bin:/bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  STUB_DOCKER_LOG="$docker_log" STUB_COMPOSE_JSON="$source_model" STUB_BUILDX_INFO="$valid_buildx_info" \
  /bin/bash --noprofile --norc "$fixture_preflight_synthetic" "$source_attack_env" 2>&1)"
source_status=$?
set -e
[[ "$source_status" -eq 1 && "$source_output" == *'BLOCKED: Staging Compose isolation guard rejected configuration'* ]] || \
  fail 'Preflight accepted unsafe raw build.ssh source after model normalization'
for synthetic_secret in "$synthetic_secret_a" "$synthetic_secret_b" "$synthetic_secret_c" "$synthetic_secret_d"; do
  [[ "$source_output" != *"$synthetic_secret"* ]] || fail 'Unsafe-source rejection exposed a synthetic credential'
done
if grep -Eq '(^| )(up|build)( |$)' "$docker_log"; then
  fail 'Preflight test invoked a Docker deployment or build command'
fi
grep -F -- '--profile migration config --format json' "$docker_log" >/dev/null || \
  fail 'Preflight did not request the fully resolved migration-profile JSON model'
printf 'PASS: preflight blocks unsafe source-only build.ssh even with safe normalized JSON (exit=%s)\n' "$source_status"
printf 'PASS: preflight used Compose config only; no Docker build or deployment command ran\n'

# Restore the clean source in the disposable fixture and inject an unapproved
# interpolation default. The resolved fixture model remains valid on purpose.
git -C "$repo_root" show "$base_revision:compose.staging.yaml" > "$repo_root/compose.staging.yaml"
python3 -B - "$repo_root/compose.staging.yaml" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
approved = "BETTER_AUTH_SECRET: ${BELIB_STAGE_AUTH_SECRET:?Set staging-only Better Auth secret}"
injected = "BETTER_AUTH_SECRET: ${UNRELATED_SECRET:-" + "f" * 64 + "}"
if source.count(approved) != 1:
    raise SystemExit("Expected one staging auth-secret source scalar")
path.write_text(source.replace(approved, injected, 1), encoding="utf-8")
PY
git -C "$repo_root" add compose.staging.yaml
GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C "$repo_root" \
  -c user.name='BeLib staging test' \
  -c user.email='belib-staging-test@example.invalid' \
  -c core.hooksPath=/dev/null \
  -c commit.gpgSign=false \
  commit -qm 'Unexpected staging environment interpolation fixture'
revision="$(GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C "$repo_root" rev-parse HEAD)"
source_env_attack="$test_root/preflight-source-env-attack.env"
write_fixture "$source_env_attack" "$synthetic_secret_a" "$synthetic_secret_b" "$synthetic_secret_c" "$synthetic_secret_d"
source_env_model="$test_root/compose-source-env-attack.json"
make_compose_model "$source_env_model" valid
set +e
source_env_output="$(/usr/bin/env -i PATH="$integration_bin:/usr/bin:/bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  STUB_DOCKER_LOG="$docker_log" STUB_COMPOSE_JSON="$source_env_model" STUB_BUILDX_INFO="$valid_buildx_info" \
  /bin/bash --noprofile --norc "$fixture_preflight_synthetic" "$source_env_attack" 2>&1)"
source_env_status=$?
set -e
[[ "$source_env_status" -eq 1 && "$source_env_output" == *'BLOCKED: Staging Compose isolation guard rejected configuration'* ]] || \
  fail 'Preflight accepted an unapproved raw environment interpolation with a valid resolved model'
for synthetic_secret in "$synthetic_secret_a" "$synthetic_secret_b" "$synthetic_secret_c" "$synthetic_secret_d"; do
  [[ "$source_env_output" != *"$synthetic_secret"* ]] || fail 'Raw environment rejection exposed a synthetic credential'
done
if grep -Eq '(^| )(up|build)( |$)' "$docker_log"; then
  fail 'Preflight test invoked a Docker deployment or build command'
fi
printf 'PASS: preflight blocks unapproved raw environment interpolation with safe normalized JSON (exit=%s)\n' "$source_env_status"
