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
  awk \
    -v s1='./scripts/staging/10-belib-staging-roles.sh' \
    -v t1='/docker-entrypoint-initdb.d/10-belib-staging-roles.sh' \
    -v s2='./nginx/nginx.conf' \
    -v t2='/etc/nginx/nginx.conf' \
    -v s3='./scripts/staging/nginx-default.conf' \
    -v t3='/etc/nginx/conf.d/default.conf' '
      function trim(value) {
        sub(/^[[:space:]]+/, "", value)
        sub(/[[:space:]]+$/, "", value)
        return value
      }
      function expected_index(source) {
        if (source == s1) return 1
        if (source == s2) return 2
        if (source == s3) return 3
        return 0
      }
      function leading_spaces(value) {
        match(value, /[^ ]/)
        return RSTART ? RSTART - 1 : length(value)
      }
      function finish_bind() {
        if (!bind_started) return
        if (bind_create_host_path_count != 1 || bind_create_host_path_value != "false") {
          bind_mapping_invalid = 1
        }
        bind_started = 0
        bind_create_host_path_count = 0
        bind_create_host_path_value = ""
      }
      function finish_mount() {
        if (!in_mount) return
        finish_bind()
        mount_index = expected_index(mount_source)
        if (mount_index == 0) {
          in_mount = 0
          return
        }
        mount_count[mount_index]++
        expected_target = (mount_index == 1 ? t1 : mount_index == 2 ? t2 : t3)
        if (mount_kind != "bind" || mount_target != expected_target ||
            read_only_count != 1 || read_only_value != "true" ||
            bind_count != 1 || create_host_path_count != 1 || bind_mapping_invalid) {
          invalid = 1
        }
        in_mount = 0
      }
      /^      - / {
        finish_mount()
        in_mount = 1
        mount_kind = ""
        mount_source = ""
        mount_target = ""
        read_only_count = 0
        read_only_value = ""
        bind_started = 0
        bind_count = 0
        bind_create_host_path_count = 0
        bind_create_host_path_value = ""
        create_host_path_count = 0
        bind_mapping_invalid = 0
        if ($0 ~ /^      - type:[[:space:]]*/) {
          mount_kind = $0
          sub(/^      - type:[[:space:]]*/, "", mount_kind)
          mount_kind = trim(mount_kind)
        }
        next
      }
      in_mount {
        if ($0 ~ /^[[:space:]]*($|#)/) next
        indent = leading_spaces($0)
        if (indent <= 6) {
          finish_mount()
          next
        }
        if (indent <= 8) finish_bind()
        if ($0 ~ /^        type:[[:space:]]*/) {
          mount_kind = $0
          sub(/^        type:[[:space:]]*/, "", mount_kind)
          mount_kind = trim(mount_kind)
          next
        }
        if ($0 ~ /^        source:[[:space:]]*/) {
          mount_source = $0
          sub(/^        source:[[:space:]]*/, "", mount_source)
          mount_source = trim(mount_source)
          next
        }
        if ($0 ~ /^        target:[[:space:]]*/) {
          mount_target = $0
          sub(/^        target:[[:space:]]*/, "", mount_target)
          mount_target = trim(mount_target)
          next
        }
        if ($0 ~ /^        read_only:[[:space:]]*/) {
          read_only_count++
          read_only_value = $0
          sub(/^        read_only:[[:space:]]*/, "", read_only_value)
          read_only_value = trim(read_only_value)
          next
        }
        if ($0 ~ /^        bind:[[:space:]]*$/) {
          bind_count++
          bind_started = 1
          bind_create_host_path_count = 0
          bind_create_host_path_value = ""
          next
        }
        if ($0 ~ /create_host_path/) {
          create_host_path_count++
          value = $0
          sub(/^.*create_host_path[[:space:]]*:[[:space:]]*/, "", value)
          value = trim(value)
          if (indent == 10 && bind_started) {
            bind_create_host_path_count++
            if (bind_create_host_path_count == 1) {
              bind_create_host_path_value = value
            } else {
              bind_mapping_invalid = 1
            }
          } else {
            bind_mapping_invalid = 1
          }
          next
        }
      }
      END {
        finish_mount()
        if (invalid || mount_count[1] != 1 || mount_count[2] != 1 || mount_count[3] != 1) {
          exit 1
        }
      }
    ' "$candidate"
}

mutate_bind_mount_fixture() {
  local source_file="$1"
  local output_file="$2"
  local mount_source="$3"
  local property="$4"
  local mutation="$5"
  awk \
    -v target_source="$mount_source" \
    -v target_property="$property" \
    -v mutation="$mutation" '
      /^      - / { current_source = ""; target_mount = 0 }
      /^        source:[[:space:]]*/ {
        current_source = $0
        sub(/^        source:[[:space:]]*/, "", current_source)
        sub(/[[:space:]]+$/, "", current_source)
        target_mount = current_source == target_source
        print
        next
      }
      target_mount && target_property == "read_only" && /^        read_only:[[:space:]]*true[[:space:]]*$/ {
        changed++
        if (mutation != "removed") print "        read_only: false"
        next
      }
      target_mount && target_property == "create_host_path" && /^          create_host_path:[[:space:]]*false[[:space:]]*$/ {
        changed++
        if (mutation == "true") {
          print "          create_host_path: true"
        } else if (mutation == "sibling") {
          print "        volume:"
          print "          create_host_path: false"
          injected++
        } else if (mutation == "repeated-bind") {
          print "        bind:"
          print "          create_host_path: false"
          injected++
        }
        next
      }
      { print }
      END {
        if (changed != 1 ||
            ((mutation == "sibling" || mutation == "repeated-bind") && injected != 1)) exit 1
      }
    ' "$source_file" > "$output_file"
}

assert_bind_scope_fixture_shape() {
  local candidate="$1"
  local mount_source="$2"
  local mutation="$3"
  awk \
    -v target_source="$mount_source" \
    -v mutation="$mutation" '
      /^      - / {
        current_source = ""
        target_mount = 0
        current_bind = 0
      }
      /^        source:[[:space:]]*/ {
        current_source = $0
        sub(/^        source:[[:space:]]*/, "", current_source)
        sub(/[[:space:]]+$/, "", current_source)
        target_mount = current_source == target_source
        next
      }
      target_mount && mutation == "sibling" && /^        volume:[[:space:]]*$/ { sibling_count++ }
      target_mount && mutation == "sibling" && /^          create_host_path:[[:space:]]*false[[:space:]]*$/ {
        if (sibling_count == 1) misplaced_count++
      }
      target_mount && mutation == "repeated-bind" && /^        bind:[[:space:]]*$/ {
        current_bind++
        target_bind_count++
      }
      target_mount && mutation == "repeated-bind" && current_bind == 2 && /^          create_host_path:[[:space:]]*false[[:space:]]*$/ { misplaced_count++ }
      END {
        if (mutation == "sibling") exit !(sibling_count == 1 && misplaced_count == 1)
        if (mutation == "repeated-bind") exit !(target_bind_count == 2 && misplaced_count == 1)
        exit 1
      }
    ' "$candidate"
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

# Exercise the assertion against temporary fixture copies so each unsafe
# setting is rejected without changing the real Compose file.
fixture_dir="$(mktemp -d)"
trap 'rm -rf -- "$fixture_dir"' EXIT
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
for mutation in sibling repeated-bind; do
  fixture="$fixture_dir/scope-${mutation}.yaml"
  mount_source='./scripts/staging/10-belib-staging-roles.sh'
  if ! mutate_bind_mount_fixture "$compose_file" "$fixture" "$mount_source" create_host_path "$mutation"; then
    fail "Could not create the $mutation bind-scope fixture for $mount_source"
  fi
  if ! assert_bind_scope_fixture_shape "$fixture" "$mount_source" "$mutation"; then
    fail "The $mutation fixture did not place its create_host_path value in the intended out-of-scope mapping"
  fi
  if assert_safe_staging_bind_mounts "$fixture"; then
    fail "The shell regression accepted the $mutation bind-scope fixture for $mount_source"
  fi
  scope_negative_case_count=$((scope_negative_case_count + 1))
done
[[ "$scope_negative_case_count" -eq 2 ]] || fail 'The bind-scope negative fixture checks are incomplete'
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

printf 'PASS: 3 staging bind mounts are read-only with host-path creation disabled; 12 unsafe mount fixtures and 2 bind-scope fixtures fail closed; roles, database markers, origins, proxy headers, production separation, and node-owned book volume remain intact\n'
