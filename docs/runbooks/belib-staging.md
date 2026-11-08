# i5 BeLib isolated staging: controlled candidate runbook

This is a **non-production candidate**, not evidence that the i5 environment is running. Read current `AGENTS.md` and the PV3 Work Prompt `agents/prompts/2026-11-07/belib-isolated-test-environment.md` on its approved branch before each operation. Confirm i5 host/principal and the existing start gate independently. Never deploy this on KAGOYA.

At this checkpoint, rootless Docker is still missing on i5. The commands below are instructions for a later, independently admitted staging operation; they have not been run and are blocked until the approved `hiroyuki` rootless Docker socket and existing start gate are verified. Do not retry the known Docker failure or use a rootful/remote Docker daemon.

## Safety boundaries

- Use `compose.staging.yaml` **alone**; never combine it with `compose.yaml` or the existing production `.env`.
- The dedicated Compose project is `belib-isolated-i5`; only its own PostgreSQL and local book volumes may be created. Do not mount or copy production data, credentials, or R2 storage.
- Publish only on the local i5 loopback address `127.0.0.1`. A Tailnet URL requires a separate approved and verified network exposure path.
- Always use the actual checkout's full Git SHA. Do not read `#93` or `#98` Draft branches as approved for Production; staging candidates do not authorize merges.
- Do not execute `docker system prune`, `docker volume prune`, `docker compose down -v`, `sudo`, system service changes, or any production CD/timer actions. The service user must have Docker access without privilege escalation.

## Read-only preflight

After the normal owner gate admits the i5 `hiroyuki` principal and resource inspection, create an **outside-repository** mode-0600 file `~/.config/belib-staging/i5.env` containing only the four keys below. Generate independent disposable values locally; never copy production secrets, and never paste values into Git, issues, logs, or chat. Use `openssl rand -hex 32` on i5 for both secrets; a 64-character lowercase hex value is required.

```text
BELIB_STAGE_DB_PASSWORD=<new i5-local 64-character hex>
BELIB_STAGE_AUTH_SECRET=<different new i5-local 64-character hex>
BELIB_STAGE_VCS_REF=<exact full 40-character Git checkout SHA>
BELIB_STAGE_PORT=<free unused i5 loopback port, 1024-65535>
```

The local file must be 0600/0400 and owned by `hiroyuki`. Do not source it in a shell. Clear any ambient `BELIB_STAGE_*`, `COMPOSE_PROJECT_NAME` and `COMPOSE_FILE` variables before invoking preflight/Compose; do not reuse a Production shell environment. The fixed `-p belib-isolated-i5` prevents accidental project-name changes. Use the same path for all commands below:

```bash
envfile="$HOME/.config/belib-staging/i5.env"
bash scripts/staging/preflight.sh "$envfile"
```

The preflight checks the exact i5 hostname/user, local Git revision, staging-only values, distinct database password and auth secret (before checking Docker), no R2/general env keys, >=8 GiB free disk, >=2 GiB available memory, a free port, the **effective local user socket** `unix:///run/user/$(id -u)/docker.sock`, an i5-local Docker daemon reporting the `rootless` security option, Compose availability, and the parsed Compose configuration. Docker CLI access via the rootful system daemon or a remote context is not sufficient. It performs no container or Docker daemon mutation. If a stage project already exists, first inventory it and reuse the legitimate same-owner deployment rather than replacing it.

## Candidate-only launch (only after successful admission)

The operator must first review the candidate HEAD, independently confirm existing project and volume ownership, and keep the above values private. The following is **not** a production deployment procedure:

```bash
envfile="$HOME/.config/belib-staging/i5.env"
docker compose -p belib-isolated-i5 --env-file "$envfile" -f compose.staging.yaml up -d db
docker compose -p belib-isolated-i5 --env-file "$envfile" -f compose.staging.yaml --profile migration run --rm migrate
docker compose -p belib-isolated-i5 --env-file "$envfile" -f compose.staging.yaml up -d --build backend frontend nginx
docker compose -p belib-isolated-i5 --env-file "$envfile" -f compose.staging.yaml ps
```

Never execute the launch if preflight fails or if Docker image build would exceed the checked capacity. Use a deliberate per-step review of services and image build size. Since the backend/frontend sources are built locally, the final `BELIB_STAGE_VCS_REF` must match the checkout, not an unrelated production revision.

## Synthetic-only seed (candidate code)

After the isolated PostgreSQL migration succeeds, the optional one-off seed adds two synthetic `.invalid` credential users, two tiny deterministic books, local EPUB/PDF files, and admin-role permissions only. The general `user` role receives no permission for either book. It uses Better Auth's password hashing and never deletes existing books. The database URL, storage driver, exact local fixture root, and absence of cloud/production environment keys are checked before seed writes; the connected database identity is checked before writes as well.

Keep the stage env file above unchanged at exactly its original four keys. Create a separate credentials file outside the repository for the disposable accounts. The two independent values below are generated directly into a mode-0600 file; they are not placed in shell arguments, environment variables, or output:

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

Create this file once and retain it unchanged for seed retries; the command refuses to overwrite an existing file. Do not use shell tracing while creating or using credentials. The seed rejects duplicate passwords, either staging secret reused as a test password, additional credential-file lines, symlinks, and any mode other than 0600. The account passwords are read only from this file. The file is mounted read-only only for the one-off seed container; it is not added to the four-key stage env file or passed as a command argument. The one-off container starts as UID 0 inside the verified rootless user namespace to read the owner-only mount, changes the local fixture directory owner to the backend `node` account, then drops to `node` before database or fixture writes. This does not use host root or `sudo`; never use this procedure with rootful or remote Docker.

Run only after admission, successful migration, and independent review of the exact staging project. This uses the named staging project, standalone staging Compose file, no dependency startup, and mounts the credentials read-only for this execution only:

```bash
envfile="$HOME/.config/belib-staging/i5.env"
seed_file="$HOME/.config/belib-staging/seed-credentials"
docker compose -p belib-isolated-i5 --env-file "$envfile" -f compose.staging.yaml run \
  --rm --no-deps --build --user 0:0 \
  --volume "$seed_file:/run/secrets/belib-staging-seed-credentials:ro" \
  backend node dist/scripts/seed-staging.js
```

The seed is additive and idempotent. It never removes a book or overwrites a fixture file whose content differs from the deterministic bytes. If it stops partway through, leave the isolated volumes intact and rerun the same command after correcting the cause; the DB writes are transactional and the exact synthetic files are safe to reuse. There is no automatic rollback. To undo a successful seed while retaining both volumes, review the project and run this narrowly scoped SQL transaction against the named staging database. It matches the synthetic book titles, author, and description, and the reserved `.invalid` account emails only:

```bash
envfile="$HOME/.config/belib-staging/i5.env"
docker compose -p belib-isolated-i5 --env-file "$envfile" -f compose.staging.yaml exec -T db \
  psql -v ON_ERROR_STOP=1 -U belib_staging -d belib_staging <<'SQL'
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
  /var/lib/belib/books/belib-staging-synthetic.epub \
  /var/lib/belib/books/belib-staging-synthetic.pdf
```

Keep the migration-managed `未分類` category and both staging volumes. Do not use `docker compose down -v` or any volume deletion as rollback.

Offline checks for this step are `npm --prefix backend test -- scripts/seed-staging.test.ts` and `npm --prefix backend run build`. They validate generated file structure and fail-closed guards without Docker or a database; they are **not** staging acceptance evidence and do not replace the physical i5 browser checks below.

## Acceptance after actual i5 deployment

The staging `isolated` Compose network is internal to restrict container egress. Port publishing from this internal network has not yet been proven on i5, so after installation first verify that the configured loopback port answers at `http://127.0.0.1:<verified-port>` and that public `/health` succeeds; treat failure as a deployment blocker. Then use the **physical i5 Google Chrome GUI**, not a headless substitute, to check anonymous protected API 401, separate staging admin/general identities, general-user denial of admin-only books and management API, synthetic EPUB and PDF body/page displays, reading-position save and restore after reload and new login, and Shelf/Reader UI. Read the real `frontend/package.json` scripts before executing tests; the current reader script is `npm --prefix frontend run test:readers`. Confirm test fixtures reference only the isolated staging DB/storage and never delete real users/books.

Report separately: candidate code, fixed HEAD, current CI, independent review, i5 preflight, real Compose services/volume identities, HTTP smoke, Playwright, physical i5 Chrome, and final acceptance. Only observed results count. If the stage is running, persist the observed loopback URL and an evidence pointer in the original PV3 Work Prompt/owner PR. Do not commit confidential browser traces or uploaded books.
