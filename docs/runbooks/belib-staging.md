# i5 BeLib isolated staging: controlled candidate runbook

This is a **non-production candidate**, not evidence that the i5 environment is running. Read current `AGENTS.md` and the PV3 Work Prompt `agents/prompts/2026-11-07/belib-isolated-test-environment.md` on its approved branch before each operation. Confirm i5 host/principal and the existing start gate independently. Never deploy this on KAGOYA.

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

The local file must be 0600/0400 and owned by `hiroyuki`. Do not source it in a shell. Use the same path for all commands below:

```bash
envfile="$HOME/.config/belib-staging/i5.env"
bash scripts/staging/preflight.sh "$envfile"
```

The preflight checks the exact i5 hostname/user, local Git revision, staging-only values, no R2/general env keys, >=8 GiB free disk, >=2 GiB available memory, a free port, nonroot Docker access, and the parsed Compose configuration. It performs no container or Docker daemon mutation. If a stage project already exists, first inventory it and reuse the legitimate same-owner deployment rather than replacing it.

## Candidate-only launch (only after successful admission)

The operator must first review the candidate HEAD, independently confirm existing project and volume ownership, and keep the above values private. The following is **not** a production deployment procedure:

```bash
envfile="$HOME/.config/belib-staging/i5.env"
docker compose --env-file "$envfile" -f compose.staging.yaml up -d db
docker compose --env-file "$envfile" -f compose.staging.yaml --profile migration run --rm migrate
docker compose --env-file "$envfile" -f compose.staging.yaml up -d --build backend frontend nginx
docker compose --env-file "$envfile" -f compose.staging.yaml ps
```

Never execute the launch if preflight fails or if Docker image build would exceed the checked capacity. Use a deliberate per-step review of services and image build size. Since the backend/frontend sources are built locally, the final `BELIB_STAGE_VCS_REF` must match the checkout, not an unrelated production revision.

## Acceptance after actual i5 deployment

Use `http://127.0.0.1:<verified-port>` on the **physical i5 Google Chrome GUI**, not a headless substitute. Check public `/health`, anonymous protected API 401, separate staging admin/general identities, general-user denial of admin-only books and management API, synthetic EPUB and PDF body/page displays, reading-position save and restore after reload and new login, and Shelf/Reader UI. Read the real `frontend/package.json` scripts before executing tests; the current reader script is `npm --prefix frontend run test:readers`. Confirm test fixtures reference only the isolated staging DB/storage and never delete real users/books.

Report separately: candidate code, fixed HEAD, current CI, independent review, i5 preflight, real Compose services/volume identities, HTTP smoke, Playwright, physical i5 Chrome, and final acceptance. Only observed results count. If the stage is running, persist the observed loopback URL and an evidence pointer in the original PV3 Work Prompt/owner PR. Do not commit confidential browser traces or uploaded books.
