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

printf 'PASS: staging roles are separated, shared database markers are readable, origins and proxy headers are pinned, production Compose stays separate, and the node-owned book volume contract remains intact\n'
