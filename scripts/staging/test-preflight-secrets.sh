#!/usr/bin/env bash
# Offline secret-validation regression check. Uses only synthetic fixtures and a controlled PATH.
set -euo pipefail

fail() { printf 'BLOCKED: %s\n' "$*" >&2; exit 1; }
[[ "$(id -un)" == hiroyuki ]] || fail 'Run as i5 user hiroyuki'
[[ "$(hostname)" == ubuntu-worker ]] || fail 'Run on i5 host ubuntu-worker'

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source_preflight="$script_dir/preflight.sh"
[[ -f "$source_preflight" ]] || fail 'Cannot find staging preflight script'

test_root="$(mktemp -d "/tmp/belib-staging-preflight-test.XXXXXX")"
trap 'rm -rf -- "$test_root"' EXIT
repo_root="$test_root/repo"
fixture_preflight="$repo_root/scripts/staging/preflight.sh"
test_bin="$test_root/bin"
mkdir -p "$(dirname -- "$fixture_preflight")" "$test_bin"
cp -- "$source_preflight" "$fixture_preflight"

GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git -C "$repo_root" init -q
git -C "$repo_root" add scripts/staging/preflight.sh
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
