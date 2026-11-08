#!/usr/bin/env bash
# Official Postgres image init hook: keep this file non-executable so it is sourced.
# The entrypoint already enables errexit and pipefail; do not change its shell options.
# This hook is intentionally limited to the one fresh staging database initialization.

fail() {
  printf 'BLOCKED: %s\n' "$*" >&2
  exit 1
}

[[ "${BASH_SOURCE[0]##*/}" == 10-belib-staging-roles.sh ]] || fail 'Unexpected PostgreSQL init script path'
[[ ! -x "${BASH_SOURCE[0]}" ]] || fail 'This PostgreSQL init script must be sourced by the official image'
[[ -z "${DATABASE_ALREADY_EXISTS:-}" ]] || fail 'PostgreSQL role bootstrap is allowed only for a fresh data volume'
[[ "${POSTGRES_USER:-}" == belib_staging_bootstrap ]] || fail 'Unexpected PostgreSQL bootstrap role'
[[ "${POSTGRES_DB:-}" == belib_staging ]] || fail 'Unexpected PostgreSQL database'

for name in BELIB_STAGE_DB_APP_PASSWORD BELIB_STAGE_DB_MIGRATE_PASSWORD; do
  value="${!name:-}"
  [[ "$value" =~ ^[a-f0-9]{64}$ ]] || fail "$name must be a staging-only 64-character lowercase hex value"
done
unset value name
[[ "$BELIB_STAGE_DB_APP_PASSWORD" != "$BELIB_STAGE_DB_MIGRATE_PASSWORD" ]] || fail 'App and migration database passwords must be distinct'
[[ "$POSTGRES_PASSWORD" =~ ^[a-f0-9]{64}$ ]] || fail 'Bootstrap password must be a staging-only 64-character lowercase hex value'
[[ "$POSTGRES_PASSWORD" != "$BELIB_STAGE_DB_APP_PASSWORD" && "$POSTGRES_PASSWORD" != "$BELIB_STAGE_DB_MIGRATE_PASSWORD" ]] || fail 'PostgreSQL role passwords must be distinct'

# Keep the runtime-role credentials available to the shell for the psql prompts,
# but out of the psql process environment.
export -n BELIB_STAGE_DB_APP_PASSWORD BELIB_STAGE_DB_MIGRATE_PASSWORD

{
  cat <<'SQL'
BEGIN;

DO $fresh_volume_guard$
DECLARE
  bootstrap_oid OID;
BEGIN
  IF current_database() <> 'belib_staging'
     OR current_user <> 'belib_staging_bootstrap'
     OR session_user <> 'belib_staging_bootstrap' THEN
    RAISE EXCEPTION 'Unexpected PostgreSQL bootstrap identity';
  END IF;

  SELECT oid INTO bootstrap_oid
  FROM pg_roles
  WHERE rolname = 'belib_staging_bootstrap'
    AND rolsuper
    AND rolcanlogin;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'The configured bootstrap role is not the expected initialization superuser';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_database
    WHERE datname = 'belib_staging' AND datdba = bootstrap_oid
  ) THEN
    RAISE EXCEPTION 'The dedicated staging database is not freshly owned by its bootstrap role';
  END IF;

  IF obj_description((SELECT oid FROM pg_database WHERE datname = 'belib_staging'), 'pg_database') IS NOT NULL THEN
    RAISE EXCEPTION 'The dedicated staging database already has an initialization marker';
  END IF;

  IF EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname IN ('belib_staging_app', 'belib_staging_migrate')
  ) THEN
    RAISE EXCEPTION 'Staging runtime roles already exist; refusing to mutate this database';
  END IF;

  IF EXISTS (
    SELECT 1 FROM pg_namespace
    WHERE nspname !~ '^pg_' AND nspname NOT IN ('public', 'information_schema')
  ) THEN
    RAISE EXCEPTION 'The dedicated staging database contains an unexpected schema';
  END IF;

  IF EXISTS (
    SELECT 1 FROM pg_class
    WHERE relnamespace = 'public'::regnamespace
  ) THEN
    RAISE EXCEPTION 'The dedicated staging public schema is not empty';
  END IF;
END
$fresh_volume_guard$;

CREATE ROLE belib_staging_app
  LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE belib_staging_migrate
  LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

REVOKE ALL ON DATABASE belib_staging FROM PUBLIC;
GRANT CONNECT ON DATABASE belib_staging TO belib_staging_app, belib_staging_migrate;
GRANT CREATE ON DATABASE belib_staging TO belib_staging_migrate;
ALTER SCHEMA public OWNER TO belib_staging_migrate;
REVOKE CREATE ON DATABASE belib_staging FROM belib_staging_migrate;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO belib_staging_app;
GRANT USAGE, CREATE ON SCHEMA public TO belib_staging_migrate;

ALTER DEFAULT PRIVILEGES FOR ROLE belib_staging_migrate IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO belib_staging_app;
ALTER DEFAULT PRIVILEGES FOR ROLE belib_staging_migrate IN SCHEMA public
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO belib_staging_app;

\password belib_staging_app
SQL
  printf '%s\n%s\n' "$BELIB_STAGE_DB_APP_PASSWORD" "$BELIB_STAGE_DB_APP_PASSWORD"
  cat <<'SQL'
\password belib_staging_migrate
SQL
  printf '%s\n%s\n' "$BELIB_STAGE_DB_MIGRATE_PASSWORD" "$BELIB_STAGE_DB_MIGRATE_PASSWORD"
  cat <<'SQL'

COMMENT ON DATABASE belib_staging IS 'belib-staging-role-bootstrap-v1';

DO $role_assertions$
DECLARE
  app_oid OID;
  migrate_oid OID;
BEGIN
  SELECT oid INTO app_oid
  FROM pg_roles
  WHERE rolname = 'belib_staging_app'
    AND rolcanlogin
    AND NOT rolsuper
    AND NOT rolcreatedb
    AND NOT rolcreaterole
    AND NOT rolreplication
    AND NOT rolbypassrls
    AND NOT rolinherit;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'The staging application role has unexpected privileges';
  END IF;

  SELECT oid INTO migrate_oid
  FROM pg_roles
  WHERE rolname = 'belib_staging_migrate'
    AND rolcanlogin
    AND NOT rolsuper
    AND NOT rolcreatedb
    AND NOT rolcreaterole
    AND NOT rolreplication
    AND NOT rolbypassrls
    AND NOT rolinherit;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'The staging migration role has unexpected privileges';
  END IF;

  IF (
    SELECT count(*) FROM pg_authid
    WHERE oid IN (app_oid, migrate_oid) AND rolpassword IS NOT NULL
  ) <> 2 THEN
    RAISE EXCEPTION 'Staging runtime roles must have initialized passwords';
  END IF;

  IF EXISTS (
    SELECT 1 FROM pg_auth_members
    WHERE roleid IN (app_oid, migrate_oid) OR member IN (app_oid, migrate_oid)
  ) THEN
    RAISE EXCEPTION 'Staging runtime roles must not have role memberships';
  END IF;

  IF NOT has_database_privilege(app_oid, 'belib_staging', 'CONNECT')
     OR has_database_privilege(app_oid, 'belib_staging', 'CREATE')
     OR has_database_privilege(app_oid, 'belib_staging', 'TEMPORARY') THEN
    RAISE EXCEPTION 'The staging application database privileges are too broad';
  END IF;

  IF NOT has_database_privilege(migrate_oid, 'belib_staging', 'CONNECT')
     OR has_database_privilege(migrate_oid, 'belib_staging', 'CREATE')
     OR has_database_privilege(migrate_oid, 'belib_staging', 'TEMPORARY') THEN
    RAISE EXCEPTION 'The staging migration database privileges are too broad';
  END IF;

  IF NOT has_schema_privilege(app_oid, 'public', 'USAGE')
     OR has_schema_privilege(app_oid, 'public', 'CREATE')
     OR NOT has_schema_privilege(migrate_oid, 'public', 'USAGE')
     OR NOT has_schema_privilege(migrate_oid, 'public', 'CREATE')
     OR NOT EXISTS (
       SELECT 1 FROM pg_namespace
       WHERE nspname = 'public' AND nspowner = migrate_oid
     ) THEN
    RAISE EXCEPTION 'The staging schema ownership or privileges are unexpected';
  END IF;

  IF (
    SELECT array_agg(acl.privilege_type ORDER BY acl.privilege_type)
    FROM pg_default_acl AS defaults
    CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS acl
    WHERE defaults.defaclrole = migrate_oid
      AND defaults.defaclnamespace = 'public'::regnamespace
      AND defaults.defaclobjtype = 'r'
      AND acl.grantee = app_oid
  ) IS DISTINCT FROM ARRAY['DELETE', 'INSERT', 'SELECT', 'UPDATE']::text[] THEN
    RAISE EXCEPTION 'The staging application table defaults are unexpected';
  END IF;

  IF (
    SELECT array_agg(acl.privilege_type ORDER BY acl.privilege_type)
    FROM pg_default_acl AS defaults
    CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS acl
    WHERE defaults.defaclrole = migrate_oid
      AND defaults.defaclnamespace = 'public'::regnamespace
      AND defaults.defaclobjtype = 'S'
      AND acl.grantee = app_oid
  ) IS DISTINCT FROM ARRAY['SELECT', 'UPDATE', 'USAGE']::text[] THEN
    RAISE EXCEPTION 'The staging application sequence defaults are unexpected';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM pg_default_acl AS defaults
    CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS acl
    WHERE defaults.defaclrole = migrate_oid
      AND defaults.defaclnamespace = 'public'::regnamespace
      AND acl.grantee = app_oid
      AND acl.is_grantable
  ) THEN
    RAISE EXCEPTION 'The staging application role must not receive grant options';
  END IF;
END
$role_assertions$;

COMMIT;
SQL
} | psql --no-psqlrc --no-password -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"

unset BELIB_STAGE_DB_APP_PASSWORD BELIB_STAGE_DB_MIGRATE_PASSWORD
