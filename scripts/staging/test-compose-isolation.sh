#!/usr/bin/env bash
# Offline regression check for the staging-only Compose isolation contract.
set -euo pipefail

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "$script_dir/../.." && pwd -P)"
compose_file="$repo_root/compose.staging.yaml"
nginx_site="$repo_root/scripts/staging/nginx-default.conf"
[[ -f "$compose_file" ]] || fail 'Cannot find compose.staging.yaml'
[[ -f "$nginx_site" ]] || fail 'Cannot find staging-only nginx site configuration'

assert_safe_staging_bind_mounts() {
  local candidate="$1"
  /usr/bin/python3 -I -B - "$script_dir/ci-isolation-guard.py" "$candidate" <<'PY'
import runpy
import sys
from pathlib import Path

guard = runpy.run_path(sys.argv[1])
try:
    guard["_source_contract"](
        Path(sys.argv[2]).read_text(encoding="utf-8"), "a" * 40
    )
except (guard["GuardError"], OSError, UnicodeError, ValueError):
    raise SystemExit(1)
PY
}

mutate_bind_mount_fixture() {
  local source_file="$1"
  local output_file="$2"
  local mount_source="$3"
  local property="$4"
  local mutation="$5"
  /usr/bin/python3 -I -B - "$source_file" "$output_file" "$mount_source" "$property" "$mutation" <<'PY'
import sys
from pathlib import Path

source_path, output_path = map(Path, sys.argv[1:3])
mount_source, property, mutation = sys.argv[3:6]
lines = source_path.read_text(encoding="utf-8").splitlines(keepends=True)
source_line = f"        source: {mount_source}\n"
source_indexes = [index for index, line in enumerate(lines) if line == source_line]
if len(source_indexes) != 1:
    raise SystemExit(1)
source_index = source_indexes[0]
mount_start = next(
    (index for index in range(source_index, -1, -1) if lines[index].startswith("      - ")),
    None,
)
if mount_start is None:
    raise SystemExit(1)
mount_end = len(lines)
for index in range(source_index + 1, len(lines)):
    line = lines[index]
    if not line.strip() or line.lstrip().startswith("#"):
        continue
    indentation = len(line) - len(line.lstrip(" "))
    if indentation <= 6:
        mount_end = index
        break
mount_lines = lines[mount_start:mount_end]
host_path_indexes = [
    index for index, line in enumerate(mount_lines)
    if line == "          create_host_path: false\n"
]
bind_indexes = [
    index for index, line in enumerate(mount_lines)
    if line == "        bind:\n"
]
if len(host_path_indexes) != 1 or len(bind_indexes) != 1:
    raise SystemExit(1)
host_path_index = host_path_indexes[0]
bind_index = bind_indexes[0]

if property == "read_only" and mutation in {"removed", "false"}:
    read_only_indexes = [
        index for index, line in enumerate(mount_lines)
        if line == "        read_only: true\n"
    ]
    if len(read_only_indexes) != 1:
        raise SystemExit(1)
    read_only_index = read_only_indexes[0]
    if mutation == "removed":
        del mount_lines[read_only_index]
    else:
        mount_lines[read_only_index] = "        read_only: false\n"
elif property == "create_host_path" and mutation in {"removed", "true"}:
    if mutation == "removed":
        del mount_lines[host_path_index]
    else:
        mount_lines[host_path_index] = "          create_host_path: true\n"
elif property == "structure" and mutation == "comment-safe":
    mount_lines[host_path_index] = "          create_host_path: false # explicit safe value\n"
elif property == "structure" and mutation == "inline-empty-bind":
    mount_lines.append("        bind: {}\n")
elif property == "structure" and mutation == "duplicate-bind":
    mount_lines.extend(("        bind:\n", "          create_host_path: false\n"))
elif property == "structure" and mutation == "duplicate-create-host-path":
    mount_lines.insert(host_path_index + 1, "          create_host_path: true\n")
elif property == "structure" and mutation == "quoted-bind-key":
    mount_lines.append('        "bind": {}\n')
elif property == "structure" and mutation == "quoted-create-host-path":
    mount_lines[host_path_index] = '          "create_host_path": false\n'
elif property == "structure" and mutation == "unsafe-option":
    mount_lines.insert(host_path_index + 1, "          propagation: rshared\n")
elif property == "structure" and mutation == "nested-option":
    mount_lines[host_path_index:host_path_index + 1] = [
        "          create_host_path:\n",
        "            enabled: true\n",
    ]
elif property == "structure" and mutation == "sibling":
    mount_lines.extend(("        volume:\n", "          create_host_path: false\n"))
elif property == "structure" and mutation == "repeated-bind":
    mount_lines.extend(("        bind:\n", "          create_host_path: false\n"))
elif property == "structure" and mutation == "yaml-alias":
    mount_lines[bind_index:host_path_index + 1] = [
        "        bind: &safe_bind\n",
        "          create_host_path: false\n",
        "        bind: *safe_bind\n",
    ]
elif property == "structure" and mutation == "flow-bind":
    mount_lines[bind_index:host_path_index + 1] = [
        "        bind: { create_host_path: false }\n",
    ]
else:
    raise SystemExit(1)

lines[mount_start:mount_end] = mount_lines
output_path.write_text("".join(lines), encoding="utf-8")
PY
}

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

canonical_origin='https://stg-bl.hiroyuki9614.com'
grep -Fq "      FRONTEND_URL: $canonical_origin" "$compose_file" || \
  fail 'The backend frontend origin must use the canonical HTTPS origin'
grep -Fq "      BETTER_AUTH_URL: $canonical_origin" "$compose_file" || \
  fail 'The Better Auth origin must use the canonical HTTPS origin'
grep -Fq "        VITE_API_BASE_URL: $canonical_origin" "$compose_file" || \
  fail 'The frontend build API origin must use the canonical HTTPS origin'
grep -Fq '        source: ./nginx/nginx.conf' "$compose_file" || \
  fail 'The staging service must retain the shared nginx main config'
grep -Fq '        target: /etc/nginx/nginx.conf' "$compose_file" || \
  fail 'The staging nginx main config must retain its target path'
grep -Fq '        source: ./scripts/staging/nginx-default.conf' "$compose_file" || \
  fail 'The staging service must use its staging-only nginx site config'
grep -Fq '        target: /etc/nginx/conf.d/default.conf' "$compose_file" || \
  fail 'The staging nginx site config must retain its target path'
if ! assert_safe_staging_bind_mounts "$compose_file"; then
  fail 'Every fixed PostgreSQL/nginx bind mount must be read-only and set bind.create_host_path: false'
fi
safe_case_count=1

# Exercise the assertion against temporary fixture copies so each unsafe
# setting is rejected without changing the real Compose file.
fixture_dir="$(mktemp -d)"
trap 'rm -rf -- "$fixture_dir"' EXIT
safe_fixture="$fixture_dir/safe-comment.yaml"
if ! mutate_bind_mount_fixture \
  "$compose_file" "$safe_fixture" './scripts/staging/10-belib-staging-roles.sh' structure comment-safe; then
  fail 'Could not create the safe commented bind fixture'
fi
if ! assert_safe_staging_bind_mounts "$safe_fixture"; then
  fail 'The shell regression rejected a safe bind mapping with a trailing comment'
fi
safe_case_count=$((safe_case_count + 1))
negative_case_count=0
mount_index=0
for mount_source in \
  './scripts/staging/10-belib-staging-roles.sh' \
  './nginx/nginx.conf' \
  './scripts/staging/nginx-default.conf'; do
  mount_index=$((mount_index + 1))
  for property in read_only create_host_path; do
    for mutation in removed false true; do
      if [[ "$property/$mutation" == 'read_only/true' || \
            "$property/$mutation" == 'create_host_path/false' ]]; then
        continue
      fi
      fixture="$fixture_dir/mount-${mount_index}-${property}-${mutation}.yaml"
      mutated_fixture="$fixture_dir/mutated-mount-${mount_index}-${property}-${mutation}.yaml"
      cp -- "$compose_file" "$fixture"
      if ! mutate_bind_mount_fixture "$fixture" "$mutated_fixture" "$mount_source" "$property" "$mutation"; then
        fail "Could not create the $property=$mutation fixture for $mount_source"
      fi
      mv -- "$mutated_fixture" "$fixture"
      if assert_safe_staging_bind_mounts "$fixture"; then
        fail "The shell regression accepted $property=$mutation for $mount_source"
      fi
      negative_case_count=$((negative_case_count + 1))
    done
  done
done
[[ "$negative_case_count" -eq 12 ]] || fail 'The bind mount negative fixture matrix is incomplete'
scope_negative_case_count=0
scope_mount_index=0
for mount_source in \
  './scripts/staging/10-belib-staging-roles.sh' \
  './nginx/nginx.conf' \
  './scripts/staging/nginx-default.conf'; do
  scope_mount_index=$((scope_mount_index + 1))
  for mutation in sibling repeated-bind; do
    fixture="$fixture_dir/scope-${scope_mount_index}-${mutation}.yaml"
    if ! mutate_bind_mount_fixture "$compose_file" "$fixture" "$mount_source" structure "$mutation"; then
      fail "Could not create the $mutation bind-scope fixture for $mount_source"
    fi
    if assert_safe_staging_bind_mounts "$fixture"; then
      fail "The shell regression accepted the $mutation bind-scope fixture for $mount_source"
    fi
    scope_negative_case_count=$((scope_negative_case_count + 1))
  done
done
[[ "$scope_negative_case_count" -eq 6 ]] || fail 'The bind-scope negative fixture matrix is incomplete'
structure_negative_case_count=0
structure_mount_index=0
for mount_source in \
  './scripts/staging/10-belib-staging-roles.sh' \
  './nginx/nginx.conf' \
  './scripts/staging/nginx-default.conf'; do
  structure_mount_index=$((structure_mount_index + 1))
  for mutation in \
    inline-empty-bind \
    duplicate-bind \
    duplicate-create-host-path \
    quoted-bind-key \
    quoted-create-host-path \
    unsafe-option \
    nested-option \
    yaml-alias \
    flow-bind; do
    fixture="$fixture_dir/structure-${structure_mount_index}-${mutation}.yaml"
    if ! mutate_bind_mount_fixture "$compose_file" "$fixture" "$mount_source" structure "$mutation"; then
      fail "Could not create the $mutation bind structure fixture for $mount_source"
    fi
    if assert_safe_staging_bind_mounts "$fixture"; then
      fail "The shell regression accepted the $mutation bind structure fixture for $mount_source"
    fi
    structure_negative_case_count=$((structure_negative_case_count + 1))
  done
done
[[ "$structure_negative_case_count" -eq 27 ]] || fail 'The bind structure negative fixture matrix is incomplete'
[[ "$(grep -Fc 'proxy_set_header Host stg-bl.hiroyuki9614.com;' "$nginx_site")" -eq 3 ]] || \
  fail 'Every staging upstream route must receive the canonical Host header'
[[ "$(grep -Fc 'proxy_set_header X-Forwarded-Host stg-bl.hiroyuki9614.com;' "$nginx_site")" -eq 3 ]] || \
  fail 'Every staging upstream route must receive the canonical forwarded host'
[[ "$(grep -Fc 'proxy_set_header X-Forwarded-Proto https;' "$nginx_site")" -eq 3 ]] || \
  fail 'Every staging upstream route must receive the trusted HTTPS scheme'
if grep -Eq '\$http_x_forwarded_(proto|host)' "$nginx_site"; then
  fail 'Staging nginx must not trust client-supplied forwarded scheme or host'
fi
[[ "$(grep -Fc 'proxy_set_header X-Forwarded-Port "";' "$nginx_site")" -eq 3 ]] || \
  fail 'Every staging upstream route must discard client-supplied forwarded port'
[[ "$(grep -Fc 'proxy_set_header Forwarded "";' "$nginx_site")" -eq 3 ]] || \
  fail 'Every staging upstream route must discard client-supplied RFC Forwarded header'
if grep -Eq '^[[:space:]]*listen[[:space:]]+[^;]*(443|ssl)' "$nginx_site"; then
  fail 'Staging nginx must not claim to terminate TLS or listen on 443'
fi

if ! awk '
  /^  backend:[[:space:]]*$/ { in_backend = 1; next }
  in_backend && /^  [^[:space:]#]/ { in_backend = 0 }
  in_backend && /^    user:[[:space:]]*node[[:space:]]*$/ { has_node_user = 1 }
  in_backend && /^      BOOK_FILE_STORAGE_ROOT:[[:space:]]*\/home\/node\/books[[:space:]]*$/ { has_storage_root = 1 }
  in_backend && /^      NPM_CONFIG_CACHE:[[:space:]]*\/tmp\/npm-cache[[:space:]]*$/ { has_ephemeral_npm_cache = 1 }
  in_backend && /^      - staging-book-files:\/home\/node[[:space:]]*$/ { has_volume_mount = 1 }
  END { exit !(has_node_user && has_storage_root && has_ephemeral_npm_cache && has_volume_mount) }
' "$compose_file"; then
  fail 'The backend must run as node, keep npm cache ephemeral, and use its dedicated volume at /home/node with storage rooted at /home/node/books'
fi
grep -Fxq '  staging-book-files: {}' "$compose_file" || fail 'The isolated named book volume must remain declared'
if grep -Fq '/var/lib/belib/books' "$compose_file"; then
  fail 'The staging backend must not retain the root-owned /var/lib storage path'
fi

roles_file="$script_dir/10-belib-staging-roles.sh"
[[ -f "$roles_file" && ! -x "$roles_file" ]] || fail 'The PostgreSQL role initializer must remain a non-executable official-image init hook'
grep -Fq '      POSTGRES_USER: belib_staging_bootstrap' "$compose_file" || fail 'PostgreSQL init must use its distinct bootstrap superuser'
grep -Fq 'POSTGRES_PASSWORD: ${BELIB_STAGE_DB_BOOTSTRAP_PASSWORD:' "$compose_file" || fail 'The bootstrap role must use its own staging-only password key'
grep -Fq '        source: ./scripts/staging/10-belib-staging-roles.sh' "$compose_file" || fail 'Role setup must mount the official initialization source'
grep -Fq '        target: /docker-entrypoint-initdb.d/10-belib-staging-roles.sh' "$compose_file" || fail 'Role setup must use the official initialization target'
grep -Fq '      BELIB_STAGE_DB_APP_PASSWORD: ${BELIB_STAGE_DB_APP_PASSWORD:' "$compose_file" || fail 'The app password must be distinct and available only to fresh-volume initialization'
grep -Fq '      BELIB_STAGE_DB_MIGRATE_PASSWORD: ${BELIB_STAGE_DB_MIGRATE_PASSWORD:' "$compose_file" || fail 'The migration password must be distinct and available only to fresh-volume initialization'
grep -Fq 'DATABASE_URL: postgresql://belib_staging_migrate:${BELIB_STAGE_DB_MIGRATE_PASSWORD:' "$compose_file" || fail 'Migrations must connect through the non-superuser migration role'
grep -Fq 'DATABASE_URL: postgresql://belib_staging_app:${BELIB_STAGE_DB_APP_PASSWORD:' "$compose_file" || fail 'Backend runtime and seed must connect through the non-superuser app role'
if grep -Eq 'DATABASE_URL: postgresql://belib_staging_bootstrap' "$compose_file"; then
  fail 'The bootstrap superuser must never be used by the application, migration, or seed connection'
fi
grep -Fq 'psql -X -w -U belib_staging_app -d belib_staging' "$compose_file" || fail 'The database healthcheck must not use the bootstrap account'
grep -Fq "shobj_description((SELECT oid FROM pg_database WHERE datname = current_database()), 'pg_database')" "$compose_file" || fail 'The database healthcheck must read the shared database comment marker'
grep -Fq "shobj_description((SELECT oid FROM pg_database WHERE datname = 'belib_staging'), 'pg_database')" "$roles_file" || fail 'Role initialization must read the shared database comment marker'
if grep -Eq '(^|[^[:alpha:]])obj_description[[:space:]]*\(' "$compose_file" "$roles_file"; then
  fail 'Database comment checks must not use obj_description for shared pg_database objects'
fi
grep -Fq 'DATABASE_ALREADY_EXISTS' "$roles_file" || fail 'Role initialization must explicitly reject existing data volumes'
grep -Fq 'FROM pg_authid' "$roles_file" || fail 'Role initialization must assert both runtime passwords were set'
[[ "$(grep -Fc 'AND NOT rolsuper' "$roles_file")" -eq 2 ]] || fail 'Role initialization must assert both runtime roles are not superusers'
grep -Fq "OR has_schema_privilege(app_oid, 'public', 'CREATE')" "$roles_file" || fail 'Role initialization must assert the app cannot create schema objects'
if grep -Eq 'postgresql://belib_staging:' "$compose_file" "$roles_file"; then
  fail 'No staging runtime connection may use the bootstrap role'
fi

# Keep the staging project and data objects out of the production Compose contract.
if grep -Eq 'belib-isolated-i5|staging-postgres-data|staging-book-files|belib_staging' "$repo_root/compose.yaml"; then
  fail 'Production Compose must not reference the isolated staging project or data'
fi

printf 'PASS: 3 staging bind mounts are read-only with host-path creation disabled; %s safe fixture cases and %s unsafe/scope/structure fixtures fail closed; roles, database markers, origins, proxy headers, production separation, and node-owned book volume remain intact\n' \
  "$safe_case_count" "$((negative_case_count + scope_negative_case_count + structure_negative_case_count))"
