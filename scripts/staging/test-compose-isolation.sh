#!/usr/bin/env bash
# Offline regression check for the staging-only Compose isolation contract.
set -euo pipefail

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
compose_file="$(cd -- "$script_dir/../.." && pwd -P)/compose.staging.yaml"
[[ -f "$compose_file" ]] || fail 'Cannot find compose.staging.yaml'

if ! awk '
  /^networks:[[:space:]]*$/ { in_networks = 1; next }
  in_networks && /^[^[:space:]#]/ { in_networks = 0; in_isolated = 0 }
  in_networks && /^  isolated:[[:space:]]*$/ { in_isolated = 1; next }
  in_isolated && /^  [^[:space:]#]/ { in_isolated = 0 }
  in_isolated && /^    internal:[[:space:]]*true[[:space:]]*(#.*)?$/ { found = 1 }
  END { exit !found }
' "$compose_file"; then
  fail 'The isolated staging network must set internal: true'
fi

# Compose must remain standalone and carry no production env-file or R2 settings.
if awk '
  /^[[:space:]]*#/ { next }
  {
    sub(/[[:space:]]+#.*/, "")
    if ($0 ~ /^[[:space:]]*(env_file|extends|include):/ ||
        $0 ~ /compose\.yaml/ ||
        $0 ~ /(^|[^[:alnum:]_])\.env([^[:alnum:]_]|$)/ ||
        $0 ~ /(^|[^[:alnum:]_])R2_[[:alnum:]_]+/) {
      found = 1
    }
  }
  END { exit found }
' "$compose_file"; then
  :
else
  fail 'The staging Compose file references production config or R2 settings'
fi

grep -Fq '      - "127.0.0.1:${BELIB_STAGE_PORT:?Set free i5 loopback port}:8080"' "$compose_file" || \
  fail 'The nginx host port must remain bound to the staging loopback address'

printf 'PASS: staging network is internal, standalone, R2-free, and loopback-published\n'
