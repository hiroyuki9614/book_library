#!/usr/bin/env bash
# Offline secret-validation regression check. Uses only synthetic fixtures and a controlled PATH.
set -euo pipefail

fail() { printf 'BLOCKED: %s\n' "$*" >&2; exit 1; }
[[ "$(id -un)" == hiroyuki ]] || fail 'Run as i5 user hiroyuki'
[[ "$(hostname)" == ubuntu-worker ]] || fail 'Run on i5 host ubuntu-worker'

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source_preflight="$script_dir/preflight.sh"
source_guard="$script_dir/ci-isolation-guard.py"
source_compose="$(cd -- "$script_dir/../.." && pwd -P)/compose.staging.yaml"
source_role_init="$script_dir/10-belib-staging-roles.sh"
source_nginx_default="$script_dir/nginx-default.conf"
source_nginx_conf="$(cd -- "$script_dir/../.." && pwd -P)/nginx/nginx.conf"
[[ -f "$source_preflight" ]] || fail 'Cannot find staging preflight script'
[[ -f "$source_guard" && -f "$source_compose" && -f "$source_role_init" ]] || fail 'Cannot find staging guard inputs'

test_root="$(mktemp -d "/tmp/belib-staging-preflight-test.XXXXXX")"
trap 'rm -rf -- "$test_root"' EXIT
repo_root="$test_root/repo"
fixture_preflight="$repo_root/scripts/staging/preflight.sh"
test_bin="$test_root/bin"
mkdir -p "$(dirname -- "$fixture_preflight")" "$test_bin" "$repo_root/nginx"
cp -- "$source_preflight" "$fixture_preflight"
cp -- "$source_guard" "$repo_root/scripts/staging/ci-isolation-guard.py"
cp -- "$source_role_init" "$repo_root/scripts/staging/10-belib-staging-roles.sh"
cp -- "$source_nginx_default" "$repo_root/scripts/staging/nginx-default.conf"
cp -- "$source_compose" "$repo_root/compose.staging.yaml"
cp -- "$source_nginx_conf" "$repo_root/nginx/nginx.conf"

GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C "$repo_root" init -q
git -C "$repo_root" add compose.staging.yaml nginx/nginx.conf scripts/staging
GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C "$repo_root" \
  -c user.name='BeLib staging test' \
  -c user.email='belib-staging-test@example.invalid' \
  -c core.hooksPath=/dev/null \
  -c commit.gpgSign=false \
  commit -qm 'Offline staging preflight fixture'
revision="$(GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C "$repo_root" rev-parse HEAD)"

# A constrained PATH makes the preflight's Docker CLI check fail without invoking Docker.
for utility in bash dirname git grep hostname id sed sh stat; do
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
equal_output="$(/usr/bin/env -i PATH="$test_bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  bash "$fixture_preflight" "$equal_env" 2>&1)"
equal_status=$?
set -e
[[ "$equal_status" -eq 1 && "$equal_output" == *'BLOCKED: Bootstrap, app, migration, and auth secrets must all be distinct'* ]] || \
  fail 'Equal synthetic secrets did not stop at the distinct-secret guard'
[[ "$equal_output" != *'Docker CLI missing'* ]] || fail 'Equal-secret rejection occurred after the Docker check'
printf 'PASS: equal synthetic secrets rejected before Docker check (exit=%s)\n' "$equal_status"

distinct_env="$test_root/distinct.env"
write_fixture "$distinct_env" "$synthetic_secret_a" "$synthetic_secret_b" "$synthetic_secret_c" "$synthetic_secret_d"
set +e
distinct_output="$(/usr/bin/env -i PATH="$test_bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  bash "$fixture_preflight" "$distinct_env" 2>&1)"
distinct_status=$?
set -e
[[ "$distinct_status" -eq 1 && "$distinct_output" == *'BLOCKED: Docker CLI missing; do not install with sudo'* ]] || \
  fail 'Distinct synthetic secrets did not reach the expected Docker CLI-missing guard'
printf 'PASS: distinct synthetic secrets reached Docker CLI-missing guard (exit=%s)\n' "$distinct_status"
printf 'NOTE: Docker CLI absence is only the expected offline stopping point, not preflight acceptance.\n'

legacy_env="$test_root/legacy.env"
printf 'BELIB_STAGE_DB_PASSWORD=%s\nBELIB_STAGE_AUTH_SECRET=%s\nBELIB_STAGE_VCS_REF=%s\nBELIB_STAGE_PORT=49199\n' \
  "$synthetic_secret_a" "$synthetic_secret_d" "$revision" > "$legacy_env"
chmod 600 "$legacy_env"
set +e
legacy_output="$(/usr/bin/env -i PATH="$test_bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  bash "$fixture_preflight" "$legacy_env" 2>&1)"
legacy_status=$?
set -e
[[ "$legacy_status" -eq 1 && "$legacy_output" == *'BLOCKED: Staging env must contain only the six approved staging keys'* ]] || \
  fail 'The removed single-password environment key was not rejected'
printf 'PASS: legacy single-password env rejected before Docker check (exit=%s)\n' "$legacy_status"

integration_bin="$test_root/integration-bin"
mkdir -p "$integration_bin"
for utility in bash dirname git grep hostname id sed sh stat awk mktemp rm cat python3; do
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
  *' config --format json') cat -- "$STUB_COMPOSE_JSON" ;;
  *' up '*|*' build '*) printf 'Unexpected deployment command in preflight test\n' >&2; exit 90 ;;
  *) printf 'Unexpected Docker command in preflight test\n' >&2; exit 91 ;;
esac
SH
chmod 700 "$integration_bin/docker"

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
set +e
valid_output="$(/usr/bin/env -i PATH="$integration_bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  STUB_DOCKER_LOG="$docker_log" STUB_COMPOSE_JSON="$valid_model" \
  bash "$fixture_preflight" "$valid_env" 2>&1)"
valid_status=$?
set -e
[[ "$valid_status" -eq 0 && "$valid_output" == *'STAGING_COMPOSE_GUARD_PASS '* && "$valid_output" == *'PREFLIGHT_PASS '* ]] || \
  fail 'Preflight did not run the isolation guard on the resolved Compose model'
for synthetic_secret in "$synthetic_secret_a" "$synthetic_secret_b" "$synthetic_secret_c" "$synthetic_secret_d"; do
  [[ "$valid_output" != *"$synthetic_secret"* ]] || fail 'Preflight output exposed a synthetic credential'
done
printf 'PASS: preflight passes only after offline resolved-model guard (exit=%s)\n' "$valid_status"

unsafe_model="$test_root/compose-unsafe.json"
make_compose_model "$unsafe_model" unsafe-model
set +e
model_output="$(/usr/bin/env -i PATH="$integration_bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  STUB_DOCKER_LOG="$docker_log" STUB_COMPOSE_JSON="$unsafe_model" \
  bash "$fixture_preflight" "$valid_env" 2>&1)"
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
source_output="$(/usr/bin/env -i PATH="$integration_bin" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
  STUB_DOCKER_LOG="$docker_log" STUB_COMPOSE_JSON="$source_model" \
  bash "$fixture_preflight" "$source_attack_env" 2>&1)"
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
