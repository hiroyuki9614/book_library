# i5 BeLib isolated staging: controlled candidate runbook

This is a **non-production candidate**, not evidence that the i5 environment is running. Read current `AGENTS.md` and the PV3 Work Prompt `agents/prompts/2026-11-07/belib-isolated-test-environment.md` on its approved branch before each operation. Confirm i5 host/principal and the existing start gate independently. Never deploy this on KAGOYA.

## Current gate: no deployment or seed

This candidate may be retained as a draft PR, but it is **not approved for staging deployment or seeding**. The fixture cleanup race is remediated by retaining fixture paths after failures and never unlinking them. The book-volume code gate uses a node-owned image mount point and a fail-closed nonroot write probe. The PostgreSQL bootstrap-role boundary is implemented in this source candidate with offline checks, but neither role initialization nor volume ownership has been observed on i5. The rootless Docker user context/socket is available, but no staging service is running. Do not launch, migrate, or seed the staging Compose project, or treat deployment/browser acceptance as complete. Offline tests do not clear this gate.

- **Database privilege boundary:** the source candidate initializes the isolated `belib_staging` database with `belib_staging_bootstrap` as the official image's one-time superuser. A non-executable init hook requires a fresh data directory, checks the database and public schema are empty, creates distinct `belib_staging_app` and `belib_staging_migrate` login roles, and records a role-bootstrap marker. The migration role owns `public`, receives schema DDL only, and has no final database `CREATE` or `TEMPORARY` privilege. The app/seed role receives database `CONNECT`, schema `USAGE`, table `SELECT/INSERT/UPDATE/DELETE`, and sequence `USAGE/SELECT/UPDATE`; it has no schema `CREATE`, database `CREATE/TEMPORARY`, or grant options. Future migration-created tables and sequences receive the same runtime grants by default. The seed also checks its live connection is `belib_staging_app` and not a superuser. The app, migration, and bootstrap passwords use separate host-only keys and must all differ from each other and the auth secret.
- **Fresh-volume guard:** the official PostgreSQL entrypoint runs this `.sh` hook only while initializing an empty data directory. The hook additionally requires `DATABASE_ALREADY_EXISTS` to be empty, the exact bootstrap/database identity, no prior marker/runtime roles, and no objects or unexpected schemas in `public`. Existing data directories skip initialization; the app-role database healthcheck requires the marker and expected non-superuser roles, so an old or partial volume stays unhealthy and dependent services do not start. No bootstrap DDL is run against an existing database.
- **i5 Docker admission:** the rootless Docker CLI/socket is available to `hiroyuki`, but this does not authorize deployment. No live Compose service or persistent volume has been inspected; runtime volume ownership and write access have not been observed on i5.

Read-only checks below cannot clear this gate. The launch, migration, seed, and rollback commands are future reference only and must not be run until every P2 has a reviewed resolution and separate staging admission. No deployment or seed is evidenced by this runbook.

## Safety boundaries

- Use `compose.staging.yaml` **alone**; never combine it with `compose.yaml` or the existing production `.env`.
- The dedicated Compose project is `belib-isolated-i5`; only its own PostgreSQL and local book volumes may be created. Do not mount or copy production data, credentials, or R2 storage.
- Publish only on the local i5 loopback address `127.0.0.1`. A Tailnet URL requires a separate approved and verified network exposure path.
- Always use the actual checkout's full Git SHA. Do not read `#93` or `#98` Draft branches as approved for Production; staging candidates do not authorize merges.
- Do not execute `docker system prune`, `docker volume prune`, `docker compose down -v`, `sudo`, system service changes, or any production CD/timer actions. The service user must have Docker access without privilege escalation.

## Read-only preflight

After the normal owner gate admits the i5 `hiroyuki` principal and resource inspection, create an **outside-repository** mode-0600 file `~/.config/belib-staging/i5.env` containing only the six keys below. Generate independent disposable values locally; never copy production secrets, and never paste values into Git, issues, logs, or chat. Generate each of the four credential values independently with `openssl rand -hex 32` on i5; each must be 64 lowercase hexadecimal characters.

```text
BELIB_STAGE_DB_BOOTSTRAP_PASSWORD=<new i5-local 64-character hex>
BELIB_STAGE_DB_APP_PASSWORD=<different i5-local 64-character hex>
BELIB_STAGE_DB_MIGRATE_PASSWORD=<different i5-local 64-character hex>
BELIB_STAGE_AUTH_SECRET=<different i5-local 64-character hex>
BELIB_STAGE_VCS_REF=<exact full 40-character Git checkout SHA>
BELIB_STAGE_PORT=<free unused i5 loopback port, 1024-65535>
```

The local file must be 0600/0400 and owned by `hiroyuki`. Do not source it in a shell. Clear ambient staging, Compose, Docker, database, production, and R2 overrides before invoking preflight/Compose; do not reuse a Production shell environment. The fixed `-p belib-isolated-i5` prevents accidental project-name changes. The preflight and seed wrapper independently enforce the exact six-key allowlist, validate formats and pairwise secret separation, and confirm the revision matches a clean checked-out Git tree before proceeding. The seed wrapper snapshots the validated env file into a sealed descriptor for Compose to avoid reopening a replaceable path. Use the same path for all commands below:

```bash
envfile="$HOME/.config/belib-staging/i5.env"
bash scripts/staging/preflight.sh "$envfile"
```

The preflight checks the exact i5 hostname/user, local Git revision, six-key staging-only file, pairwise-distinct credentials, no ambient staging/Docker/Compose/database/production overrides, no R2/general env keys, >=8 GiB free disk, >=2 GiB available memory, a free port, the **effective local user socket** `unix:///run/user/$(id -u)/docker.sock`, an i5-local Docker daemon reporting the `rootless` security option, Compose availability, and the parsed Compose configuration. Docker CLI access via the rootful system daemon or a remote context is not sufficient. It performs no container or Docker daemon mutation. If a stage project already exists, first inventory it and reuse the legitimate same-owner deployment rather than replacing it.

The official Postgres image makes the configured `POSTGRES_USER` the initialization superuser and creates `POSTGRES_DB` under that account. This candidate names it `belib_staging_bootstrap` and gives it only the bootstrap password. The mounted `scripts/staging/10-belib-staging-roles.sh` is deliberately non-executable, so the official entrypoint sources it in its fresh-volume initialization shell. It checks that `DATABASE_ALREADY_EXISTS` is empty and that `belib_staging` is the expected empty database before creating roles. Passwords are passed to `psql`'s `\password` prompts over stdin; they are not SQL literals or psql command-line arguments. The hook validates the hex-only format, checks app/migration/bootstrap passwords are distinct, asserts the app and migration role flags, effective database/schema privileges, migration schema ownership, default table/sequence grants, and password initialization, then clears the app/migration password variables from the initialization process. The bootstrap account is absent from backend, migration, seed, and database-healthcheck connections.

The database healthcheck connects locally as `belib_staging_app` and requires the role-bootstrap marker plus non-superuser app/migration attributes, app `CONNECT` and schema `USAGE`, and no app database/schema creation privileges. On an existing volume, the official entrypoint skips initialization; without the marker and role contract the healthcheck fails and dependent services remain stopped. Do not try to repair or reinitialize such a volume with this hook.

## Candidate-only launch (currently blocked)

The operator must first review the candidate HEAD, independently confirm existing project and volume ownership, and keep the above values private. For any pre-existing `staging-book-files` volume, inspect that the `/home/node/books` layout is already in use and there is no non-hidden book data at `/home/node`; a mismatch blocks startup until separately reviewed. The seed also rejects such root-level data without moving or deleting it. The following is **not** a production deployment procedure:

```bash
envfile="$HOME/.config/belib-staging/i5.env"
docker compose -p belib-isolated-i5 --env-file "$envfile" -f compose.staging.yaml up -d db
docker compose -p belib-isolated-i5 --env-file "$envfile" -f compose.staging.yaml --profile migration run --rm migrate
docker compose -p belib-isolated-i5 --env-file "$envfile" -f compose.staging.yaml up -d --build backend frontend nginx
docker compose -p belib-isolated-i5 --env-file "$envfile" -f compose.staging.yaml ps
```

Never execute the launch if preflight fails or if Docker image build would exceed the checked capacity. Use a deliberate per-step review of services and image build size. Since the backend/frontend sources are built locally, the final `BELIB_STAGE_VCS_REF` must match the checkout, not an unrelated production revision.

## Synthetic-only seed (currently blocked)

After the isolated PostgreSQL migration succeeds, the optional one-off seed adds two synthetic `.invalid` credential users, two tiny deterministic books, local EPUB/PDF files, and admin-role permissions only. The general `user` role receives no permission for either book. It uses Better Auth's password hashing and never deletes existing books. The database URL must use `belib_staging_app`; before seed writes the script checks the exact local fixture root, absence of cloud/production environment keys, and a live database identity whose role is not a superuser.

Keep the stage env file above unchanged at exactly its original six keys. Create a separate credentials file outside the repository for the disposable accounts. The two independent values below are generated directly into a mode-0600 file; they are not placed in shell arguments, environment variables, or output:

```bash
seed_file="$HOME/.config/belib-staging/seed-credentials"
mkdir -p "$HOME/.config/belib-staging"
(
  set -e
  set -C
  umask 077
  printf 'admin_password=' > "$seed_file"
  openssl rand -hex 32 >> "$seed_file"
  printf 'reader_password=' >> "$seed_file"
  openssl rand -hex 32 >> "$seed_file"
  chmod 600 "$seed_file"
)
```

Create this file once and retain it unchanged for seed retries; the command refuses to overwrite an existing file. Do not use shell tracing while creating or using credentials. The current wrapper uses this as a host-only mode-0600 credential file, not a Docker secret or volume: after source, env-file, and local rootless-Docker checks succeed, it opens the path without following symlinks and verifies that it is a regular file owned by `hiroyuki`. It passes the already-open file as inherited stdin to the one-off container. Passwords are not placed in command arguments, environment variables, Compose mounts, or output. The seed requires exactly the two password lines, rejects duplicate passwords and either staging secret reused as a test password, limits input size, and fails before database writes if stdin is empty or malformed.

The backend service and one-off seed explicitly run as the image's `node` account, and the seed independently rejects UID 0 or any account other than `node`. The dedicated `staging-book-files` named volume mounts at the pre-existing `/home/node` directory, which the [Node 22 slim image recipe](https://github.com/nodejs/docker-node/blob/main/22/bookworm-slim/Dockerfile#L371-L375) creates for UID/GID 1000 with `--create-home`. Docker copies the destination contents into a new empty volume by default; Moby's [volume copy-up implementation](https://github.com/moby/moby/blob/master/daemon/container/container_unix.go#L1756-L1784) uses `fs.CopyDir` to copy the pre-existing directory, including its filesystem metadata. The seed verifies that `/home/node` is an actual mount point owned by its nonroot UID, refuses unexpected non-hidden top-level content, creates `/home/node/books` as that user with mode `0700` when absent, then probes file creation and removal before connecting to PostgreSQL. Compose keeps the npm cache under `/tmp` so it does not add incidental cache data to the book volume. The seed never changes volume ownership or permissions. Docker does not reinitialize a nonempty volume, so any existing volume that is not node-owned, contains unexpected root-level data, or is not writable fails closed and must not be repaired by this procedure. Do not use UID 0, `chown`, runtime `chmod`, `sudo`, or a rootful/remote daemon to work around a failed check. This copy-up behavior and the first-use write probe have automated offline coverage, but actual i5 volume ownership remains unverified until the unavailable rootless Docker CLI/socket is restored and staging admission is separately granted. See [Docker volume copy-up behavior](https://docs.docker.com/engine/storage/volumes/#mounting-a-volume-over-existing-data).

Run only after admission, successful migration, and independent review of the exact staging project:

```bash
python3 scripts/staging/seed.py
```

The seed is additive and idempotent. It never removes a book or overwrites a fixture file whose content differs from the deterministic bytes. The DB writes run in one transaction and roll back if the operation fails. If fixture writing or the DB transaction fails, the seed leaves every fixture path as-is, including newly created or incomplete files; it performs no fixture-path cleanup or pathname-based unlink. After a failure, inspect only `belib-staging-synthetic.epub` and `belib-staging-synthetic.pdf` in the dedicated named local book volume. A later attempt reuses a file only when it is a safe regular file with bytes exactly matching its deterministic fixture; incomplete or differing files fail closed and are never overwritten. If cleanup is required, perform it only through separately reviewed and approved manual cleanup scoped to those isolated paths, after confirming they are unreferenced. Keep both volumes. To undo a successful seed while retaining both volumes, review the project and run this narrowly scoped SQL transaction against the named staging database. It matches the synthetic book titles, author, and description, and the reserved `.invalid` account emails only:

```bash
envfile="$HOME/.config/belib-staging/i5.env"
docker compose -p belib-isolated-i5 --env-file "$envfile" -f compose.staging.yaml exec -T db \
  psql -v ON_ERROR_STOP=1 -U belib_staging_app -d belib_staging <<'SQL'
BEGIN;
DELETE FROM role_book_permissions
WHERE book_id IN (
  SELECT id FROM books
  WHERE author_name = 'BeLib Staging Fixture'
    AND description = 'Deterministic synthetic fixture for isolated BeLib staging.'
    AND title IN ('BeLib i5 Synthetic EPUB Fixture', 'BeLib i5 Synthetic PDF Fixture')
);
DELETE FROM book_files
WHERE book_id IN (
  SELECT id FROM books
  WHERE author_name = 'BeLib Staging Fixture'
    AND description = 'Deterministic synthetic fixture for isolated BeLib staging.'
    AND title IN ('BeLib i5 Synthetic EPUB Fixture', 'BeLib i5 Synthetic PDF Fixture')
)
AND file_url IN ('belib-staging-synthetic.epub', 'belib-staging-synthetic.pdf');
DELETE FROM reading_infos
WHERE book_id IN (
  SELECT id FROM books
  WHERE author_name = 'BeLib Staging Fixture'
    AND description = 'Deterministic synthetic fixture for isolated BeLib staging.'
    AND title IN ('BeLib i5 Synthetic EPUB Fixture', 'BeLib i5 Synthetic PDF Fixture')
)
OR user_id IN (
  SELECT id FROM users
  WHERE (email = 'staging-admin@belib.invalid' AND name = 'Synthetic Staging Admin')
     OR (email = 'staging-reader@belib.invalid' AND name = 'Synthetic Staging Reader')
);
DELETE FROM books
WHERE author_name = 'BeLib Staging Fixture'
  AND description = 'Deterministic synthetic fixture for isolated BeLib staging.'
  AND title IN ('BeLib i5 Synthetic EPUB Fixture', 'BeLib i5 Synthetic PDF Fixture');
DELETE FROM accounts
WHERE user_id IN (
  SELECT id FROM users
  WHERE (email = 'staging-admin@belib.invalid' AND name = 'Synthetic Staging Admin')
     OR (email = 'staging-reader@belib.invalid' AND name = 'Synthetic Staging Reader')
);
DELETE FROM users
WHERE (email = 'staging-admin@belib.invalid' AND name = 'Synthetic Staging Admin')
   OR (email = 'staging-reader@belib.invalid' AND name = 'Synthetic Staging Reader');
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM book_files
    WHERE file_url IN ('belib-staging-synthetic.epub', 'belib-staging-synthetic.pdf')
  ) THEN
    RAISE EXCEPTION 'Synthetic fixture paths are still referenced';
  END IF;
END
$$;
COMMIT;
SQL
```

If the transaction aborts or its file-reference check fails, do not run the filesystem cleanup. Only after it commits, remove the two known synthetic files from the same named local book volume:

```bash
envfile="$HOME/.config/belib-staging/i5.env"
docker compose -p belib-isolated-i5 --env-file "$envfile" -f compose.staging.yaml run \
  --rm --no-deps backend rm -f -- \
  /home/node/books/belib-staging-synthetic.epub \
  /home/node/books/belib-staging-synthetic.pdf
```

Keep the migration-managed `未分類` category and both staging volumes. Do not use `docker compose down -v` or any volume deletion as rollback.

Offline checks for this step are `bash scripts/staging/test-compose-isolation.sh`, `python3 scripts/staging/test-seed-wrapper.py`, `npm --prefix backend test -- scripts/seed-staging.test.ts`, and `npm --prefix backend run build`. They validate the separate bootstrap/app/migration connections, fresh-volume-only role SQL, strict host env allowlist, ambient override guards, production Compose separation, isolated volume mapping, nonroot directory initialization, and fail-closed seed checks without Docker or a database. They are **not** staging acceptance evidence and do not replace the physical i5 volume and browser checks below.

## Acceptance after actual i5 deployment

The staging `isolated` Compose network is internal to restrict container egress. Port publishing from this internal network has not yet been proven on i5, so after installation first verify that the configured loopback port answers at `http://127.0.0.1:<verified-port>` and that public `/health` succeeds; treat failure as a deployment blocker. Then use the **physical i5 Google Chrome GUI**, not a headless substitute, to check anonymous protected API 401, separate staging admin/general identities, general-user denial of admin-only books and management API, synthetic EPUB and PDF body/page displays, reading-position save and restore after reload and new login, and Shelf/Reader UI. Read the real `frontend/package.json` scripts before executing tests; the current reader script is `npm --prefix frontend run test:readers`. Confirm test fixtures reference only the isolated staging DB/storage and never delete real users/books.

Report separately: candidate code, fixed HEAD, current CI, independent review, i5 preflight, real Compose services/volume identities, HTTP smoke, Playwright, physical i5 Chrome, and final acceptance. Only observed results count. If the stage is running, persist the observed loopback URL and an evidence pointer in the original PV3 Work Prompt/owner PR. Do not commit confidential browser traces or uploaded books.
