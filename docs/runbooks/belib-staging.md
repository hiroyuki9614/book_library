# i5 BeLib isolated staging: controlled candidate runbook

This is a **non-production candidate**, not evidence that the i5 environment is running. Read current `AGENTS.md` and the PV3 Work Prompt `agents/prompts/2026-11-07/belib-isolated-test-environment.md` on its approved branch before each operation. Confirm i5 host/principal and the existing start gate independently. Never deploy this on KAGOYA.

## Current status: launcher blocked pending an independent bootstrap

The previously reported i5 state is application SHA `81256268276d7ead95029849037f69ec6beec202`; HTTPS acceptance is unavailable. This is historical operator-reported state, not a fresh runtime check. The starting PR source was clean at `cb13bf038354ad6e2540bd0bcc4c328374ef5b8a`, with 4/4 staging and 4/4 release CI reported passing. This patch changes that source, so those checks do not attest the resulting worktree; rerun CI at its eventual reviewed HEAD. The source-only change was made in the KAGOYA editing worktree and did not inspect or alter i5. The `launch-guard.py` command now returns `BLOCKED` for `deploy`, `resume-ingress`, and `manifest-ingress` before parsing arguments, importing candidate helpers, sourcing a shell file, or invoking Docker. No runtime approval is implied.

### P1 bootstrap trust boundary — BLOCKED

A **source-only, untrusted reference** verifier is proposed in
`scripts/staging/operator_bootstrap_reference.py`, with offline adversarial tests in
`scripts/staging/tests/test_operator_bootstrap_reference.py`. Its code and test
success do **not** install an external trust root, verify the operational signer,
approve an i5 deployment, or lift the unconditional `launch-guard.py` block.
The reference consumes only ephemeral test grants. A separately reviewed,
operator-owned immutable installation and independent digest/key/manifest
pinning outside the candidate principal, including an immutable expected
operation/evidence SHA256 digest (currently unmet), protected nonce state, a
real signed one-use grant and TOCTOU-safe execution snapshot are still
prerequisites.
Do not run the reference file as a deployment entrypoint or supply real grants
to a candidate-controlled checkout.


The reported defect is real: the candidate-controlled `launch-guard.py` imported candidate-controlled `seed.py` before requesting approval through candidate-controlled `approval.py`. Its own path/SHA self-check and the admitted tree cannot authenticate the launcher that performs those checks. Moving those imports or adding a digest check inside the same candidate tree would not create an independent trust root.

This patch preserves the existing deploy, resume-ingress, and manifest-ingress transaction implementations and their safety tests, but blocks entry to all three operations before candidate helper imports. The refusal is a fail-closed stop for this checkout, not proof that candidate code has independent provenance, and not an operator-installed bootstrap artifact. Until a separately trusted bootstrap exists, do not run any old command example in this runbook.

The minimum external operation needed before a later runtime integration is considered is separate owner and signer review of an operator-owned launcher/verifier, followed by its installation and independent digest pinning outside the repository. That bootstrap must start from the pinned immutable bytes and trusted system runtime; before any candidate Python import, shell source, Docker CLI/action, or DB operation, it must independently verify the exact candidate tree and verify and consume a fresh one-use grant bound to the source SHA, operation, fixed staging target, nonce, and expiry. Candidate-controlled `approval.py`, candidate hashes, or self-reported approvals cannot supply that root. Tests for a source implementation cannot replace this external installation and review. The bootstrap installation, external pin, key/trust provisioning, and integration are NOT DONE here.

The signed 6963103fd6bec332b4057457d07b890fe5c11e73 bootstrap initialized the isolated DB and synthetic seed, including nonroot book-volume and runtime DB-role checks. Its HTTP failure rolled back services while retaining volumes. A subsequent signed resume reached application revision 81256268276d7ead95029849037f69ec6beec202. On 2026-11-11 00:06 JST, the Work supervisor validated all four running container identities, both retained volumes, isolated/ingress networks, and application OCI revisions against that lineage. Loopback HTTP /health returned 200 on 2026-11-10 23:47 JST; custom HTTPS still failed TLS. These are dated observations, not permission to reuse an old approval.

Any post-fix source must pass fixed-SHA CI and independent review before a separately trusted bootstrap could authorize rootless nginx preparation. The existing application, DB, credentials, and volumes were not inspected or changed by this source-only work. The owner certificate was issued, but certificate issuance and nginx syntax validation alone do not establish HTTPS or browser acceptance. Preserve all prior worktrees; use a clean detached admission checkout for any separately reviewed source.

- **Database privilege boundary:** the source candidate initializes the isolated `belib_staging` database with `belib_staging_bootstrap` as the official image's one-time superuser. A non-executable init hook requires a fresh data directory, checks the database and public schema are empty, creates distinct `belib_staging_app` and `belib_staging_migrate` login roles, and records a role-bootstrap marker. The migration role owns `public`, receives schema DDL only, and has no final database `CREATE` or `TEMPORARY` privilege. The app/seed role receives database `CONNECT`, schema `USAGE`, table `SELECT/INSERT/UPDATE/DELETE`, and sequence `USAGE/SELECT/UPDATE`; it has no schema `CREATE`, database `CREATE/TEMPORARY`, or grant options. Future migration-created tables and sequences receive the same runtime grants by default. The seed also checks its live connection is `belib_staging_app` and not a superuser. The app, migration, and bootstrap passwords use separate host-only keys and must all differ from each other and the auth secret.
- **Fresh-volume guard:** the official PostgreSQL entrypoint runs this `.sh` hook only while initializing an empty data directory. The hook additionally requires `DATABASE_ALREADY_EXISTS` to be empty, the exact bootstrap/database identity, no prior marker/runtime roles, and no objects or unexpected schemas in `public`. Existing data directories skip initialization; the app-role database healthcheck requires the marker and expected non-superuser roles, so an old or partial volume stays unhealthy and dependent services do not start. No bootstrap DDL is run against an existing database.
- **i5 Docker admission:** the rootless Docker CLI/socket is available to `hiroyuki`, but this does not authorize deployment. The signed 6963103 transaction created and inspected the isolated DB/book volumes and completed the nonroot seed write probe; the later 8125626 resume restored HTTP service and retained both volumes. Do not reuse fresh-deploy admission for this nonempty project.

Read-only checks cannot authorize a new mutation. Recheck the current state, CI, independent review, and staging approval for each exact source. Historical bootstrap/seed instructions below are not part of the TLS continuation.

## Candidate CI and protected staging evidence

`.github/workflows/staging-ci.yml` is candidate-source CI: it runs PR-controlled code with read-only repository access. A green run proves only that its jobs completed; it cannot establish trusted CD provenance or authorize staging. It creates no admission artifact and does not start deployment.

The staging Compose images and build bases use amd64 child manifest digests fetched and independently rehashed from Docker Hub's registry v2 on 2026-11-09; each referenced image config reports `linux/amd64`. `platform: linux/amd64` is fixed on each Compose service. PostgreSQL in the CI reader job uses the same amd64 manifest as staging Compose. The backend and frontend build contexts select the dedicated recipes at `../scripts/staging/Dockerfile.backend.staging` and `../scripts/staging/Dockerfile.frontend.staging`, resolved relative to `./backend` and `./frontend`; these keep the current multi-stage targets and runtime behavior while pinning every external `FROM` image. The reviewed immutable references are:

| Use | Immutable reference |
| --- | --- |
| PostgreSQL (staging and CI) | `postgres:16.14-bookworm@sha256:714b08dd70c05160db032e4321033ac4e5b6328935b2d03ed419c3a898aa8219` |
| Staging Nginx | `nginx:1.27-alpine@sha256:62223d644fa234c3a1cc785ee14242ec47a77364226f1c811d2f669f96dc2ac8` |
| Node build stage | `node:22-bookworm@sha256:539d4d5b9c0df96bb9d1229264d358dc816e8592cb8867828a580b527e7ad0fa` |
| Node runtime stage | `node:22-bookworm-slim@sha256:aaa4b5baf1ecff3b283486bb1f90405cdda225a3e6f79660d2c559fa510a7292` |
| Frontend Nginx runtime stage | `nginxinc/nginx-unprivileged:1.27-alpine@sha256:28d91bdce70ad09025ea901458fdd149259d8e05982ade79d4ef2c0d9470eb48` |

The guard checks these exact references, the staging-only Dockerfile paths and `FROM` lines, and the CI PostgreSQL image. Offline Compose configuration and guard checks do not build images or establish deployment evidence.

`.github/workflows/staging-cd.yml` is a protected evidence handoff. It must run from the exact immutable workflow revision on authoritative `main`; dispatch from an unmerged branch is unavailable in GitHub and fails its job context checks. Before writing candidate evidence, its helper pins one `main` commit and requires both the candidate `staging-ci.yml` and `release-ci.yml` blob IDs to match the definitions at that same commit. Missing or changed definitions fail closed. It then verifies the current same-repository PR head SHA, terminal successful staging CI and release CI runs, the selected run attempt, each required job's conclusion and head SHA, independent review, and explicit owner request. The required release jobs are `Backend tests, build, Prisma, and migrations`, `Frontend tests, lint, and build`, `Reader Playwright E2E`, and `Production images, Compose config, and isolated smoke`.

The uploaded JSON is **candidate check evidence**, not a trusted deployment receipt. Its status remains pending until the existing staging supervisor independently verifies the GitHub workflow run and immutable provenance, including the actual workflow ref/revision, exact PR source SHA, run attempt, job conclusions/head SHAs, and the workflow definitions from `main`. The GitHub workflow has no deployment command, staging secret, SSH key, or self-hosted runner. Only the existing owner staging supervisor may act after its separate provenance and staging-admission checks pass. A successful workflow exit never means deployment started or completed.

## Canonical HTTPS origin and current access boundary

### Guarded custom TLS continuation

The source separates rootless nginx preparation from the owner-only Serve transaction. No assistant-run sudo, permanent OperatorUser, new secret, runner, timer, SSH path, or production change is permitted. tls-prepare.py never writes Serve; the standalone owner-serve-cas.py never invokes Docker or imports mutable repository modules.

After exact-source CI and independent review, Work creates a bounded, expiring plan from fresh retained state, Serve JSON/ETag, certificate fingerprint, and TLS bundle digest. owner-command.py packages a root loader whose helper and plan hashes are literals from that reviewed checkpoint. The loader reads bounded regular files, hashes the actual bytes, and executes those same bytes under isolated Python. Locally generated JSON alone is not approval.

The owner receives one reviewed command: rootless preparation first, followed only on success by the explicit owner sudo invocation. Preparation requires the owner-ready flag, clean fixed source, unchanged application SHA 81256268276d7ead95029849037f69ec6beec202 and resource lineage 6963103fd6bec332b4057457d07b890fe5c11e73, and a fresh external Work signature. The fresh nonce is created under the existing project lock; Work verifies and signs it while the command waits. No pre-placed grant, migration, reseed, application build, or app/DB recreation is allowed.

The nginx overlay publishes TLS only at 127.0.0.1:18443 alongside existing HTTP 127.0.0.1:18083. It uses the owner's existing certificate read-only and rejects unknown/no SNI. Syntax, local TLS/HTTP, exact retained identities, and a receipt must succeed before the owner helper can proceed.

**Old Fedora image-handler retirement — source update (2026-11-13):** The owner explicitly authorized retiring only the old `fedora-worker.tail63cd2d.ts.net:443` BRI image-publication mapping. This does not authorize deleting the PNG or changing any book, database, volume, or other Serve route. The current transaction accepts only the exact reviewed old Fedora HTTPS/handler baseline. It first CAS-clears HTTPS 443 and its Web handler to an empty Serve config, then CAS-adds `TCP 443 -> 127.0.0.1:18443` with `Web: {}`; this separate clear-and-set sequence is required when changing Tailscale Serve types on the same port. Its strict retirement-plan schema and disposition reject the prior preserve-legacy packet. nginx routes only `stg-bl.hiroyuki9614.com`; unknown SNI, no SNI, and both old and current machine SNI must fail TLS on loopback 18443 before the Serve writes and on 443 after cutover. The custom hostname still requires the pinned CA-verified certificate and exact leaf fingerprint. The owner transaction retains its lock, one-use nonce journal, fresh ETag CAS, expiry validation at each POST boundary, post-write probes, and ownership-checked rollback. No runtime change has been executed or verified; fresh exact-source CI, independent review, trusted bootstrap, and the separate external one-use signed owner approval remain required. HTTPS browser and physical GUI acceptance remain HOLD.

The original PNG was observed absent on the current installation; it is no longer a retirement precondition, and the transaction does not create, copy, or delete it. Valid old-host certificate continuity was not established and is intentionally not used as a route or fallback. The explicit owner decision applies to that one old image-publication handler only.

A fixed root-owned operation lock, exclusive nonce journal, write-ahead phase records, and ownership-checked CAS rollback prevent replay and foreign-state overwrite. Definitive permission/CAS failures do not authorize adopting another writer's matching state. If rollback ownership is uncertain, fail closed and preserve the prepared nginx plus all application data for inspection. Never fall back to raw Serve/Docker commands. The precise source, plan expiry, hashes, and owner command belong in the current Work checkpoint; this runbook is not a deployment grant.

The staging application origins are fixed to `https://stg-bl.hiroyuki9614.com` for `FRONTEND_URL`, `BETTER_AUTH_URL`, and the frontend build's `VITE_API_BASE_URL`. Staging Nginx overwrites `Host`, `X-Forwarded-Host`, and `X-Forwarded-Proto` with that canonical host and `https`, resets `X-Forwarded-For` to the direct peer, and discards inbound `X-Forwarded-Port` and RFC `Forwarded` values. In the HTTP baseline its only published port is `127.0.0.1:${BELIB_STAGE_PORT}:8080`; the separately approved TLS overlay adds loopback TLS termination.

The loopback HTTP endpoint and browser HTTPS origin have different purposes. After a separately admitted deployment, `http://127.0.0.1:<verified-port>/health` is a local backend health smoke check only. Browser acceptance must use the canonical HTTPS origin through a separately owner-approved, Tailnet-only ingress with a certificate valid for that exact hostname, proxying to the loopback listener with the canonical hostname in the upstream `Host` header. The current source candidate does not establish a working TLS endpoint. The existing Tailscale Serve configuration has no custom-host mapping, and Funnel is not an approved path. Do not use it as custom-origin ingress or enable public exposure.

Before any future ingress setup, the owner must approve the certificate and ingress method, verify Tailnet ACLs and device identity, ensure there is no Funnel or public listener, and confirm that only the intended host ingress can reach the loopback-published service. Recheck DNS, certificate hostname/SNI, and HTTPS browser access from an authorized Tailnet client before GUI acceptance. None of these host-side gates is met by offline Compose checks. At the last recorded inspection, the four retained staging services were reported healthy at application source SHA `81256268276d7ead95029849037f69ec6beec202`; no HTTPS ingress was applied or verified.

## Same physical i5 after Fedora-to-Ubuntu reinstall (2026-11-13)

The owner confirmed that historical `fedora-worker` and current `ubuntu-worker`
are the same physical i5. Do not ask for a separate Fedora machine or repeatedly
check the already missing PNG on Ubuntu. The owner has explicitly authorized
retiring the old Fedora image-publication Serve handler only; this does not
authorize data, book, database, volume, asset, or unrelated route deletion.

`scripts/staging/plan-legacy-host-migration.py` is a historical **proposal-only**
tool from bounded, nonsecret observations. It never contacts a host, applies a
Serve configuration, signs a grant, or authorizes deployment. Its original
configuration and digest identify rollback evidence, not proof that rollback was
executed. Its old observation rules are not prerequisites for the separately
authorized exact-handler retirement transaction.

The planner remains historical proposal evidence only; it does not execute the
new retirement transaction or authorize runtime changes. The dedicated owner
helper, exact plan disposition, nginx SNI allowlist, trusted bootstrap, external
signature, fresh live checks, and rollback gates remain separate runtime controls.

## Safety boundaries

- Use `compose.staging.yaml` **alone**; never combine it with `compose.yaml` or the existing production `.env`.
- The dedicated Compose project is `belib-isolated-i5`; only its own PostgreSQL and local book volumes may be created. Do not mount or copy production data, credentials, or R2 storage.
- Publish only on the local i5 loopback address `127.0.0.1`. A Tailnet URL requires a separate approved and verified network exposure path.
- Always use the actual checkout's full Git SHA. Do not read `#93` or `#98` Draft branches as approved for Production; staging candidates do not authorize merges.
- Do not execute `docker system prune`, `docker volume prune`, `docker compose down -v`, `sudo`, system service changes, or any production CD/timer actions. The service user must have Docker access without privilege escalation.

## Former read-only preflight (blocked pending independent bootstrap)

The preflight script is candidate-controlled Bash/Python and invokes Docker during its checks. It is not independently trusted by this source-only change. Do not run the command or follow the env-file setup below before the external bootstrap and one-use grant boundary has been independently installed and reviewed. The details below are retained as historical procedure only.

After the normal owner gate admits the i5 `hiroyuki` principal and resource inspection, create an **outside-repository** mode-0600 file `~/.config/belib-staging/i5.env` containing only the six keys below. Generate independent disposable values locally; never copy production secrets, and never paste values into Git, issues, logs, or chat. Generate each of the four credential values independently with `openssl rand -hex 32` on i5; each must be 64 lowercase hexadecimal characters.

```text
BELIB_STAGE_DB_BOOTSTRAP_PASSWORD=<new i5-local 64-character hex>
BELIB_STAGE_DB_APP_PASSWORD=<different i5-local 64-character hex>
BELIB_STAGE_DB_MIGRATE_PASSWORD=<different i5-local 64-character hex>
BELIB_STAGE_AUTH_SECRET=<different i5-local 64-character hex>
BELIB_STAGE_VCS_REF=<exact full 40-character Git checkout SHA>
BELIB_STAGE_PORT=<free unused i5 loopback port, 1024-65535>
```

The local file must be 0600/0400 and owned by `hiroyuki`. Do not source it in a shell. Invoke preflight as the executable shown below; do not run it through `bash`. Its absolute `/usr/bin/env -S -i` shebang starts `/bin/bash` with only the fixed `HOME=/home/hiroyuki` and `PATH=/usr/bin:/bin`, before Bash can read `BASH_ENV`, `ENV`, or exported shell functions. The in-script environment checks are defense in depth and cannot detect startup code that already ran and unset its variable. The fixed `-p belib-isolated-i5` prevents accidental project-name changes. The preflight and seed wrapper independently enforce the exact six-key allowlist, validate formats and pairwise secret separation, and confirm the revision matches a clean checked-out Git tree before proceeding. The seed wrapper snapshots the validated env file into a sealed descriptor for Compose to avoid reopening a replaceable path. Use the same path for all commands below:

```bash
envfile="$HOME/.config/belib-staging/i5.env"
source_sha='<full-reviewed-40-character-sha>'
evidence_sha256='<64-character-canonical-evidence-sha256>'
source_root="$HOME/workspaces/belib-i5-admission-${source_sha:0:7}"
"$source_root/scripts/staging/preflight.sh" "$envfile"
```

The preflight checks the exact i5 hostname/user, local Git revision, six-key staging-only file, pairwise-distinct credentials, no ambient staging/Docker/Buildx/BuildKit/Compose/database/production overrides, no R2/general env keys, >=8 GiB free disk, >=2 GiB available memory, a free port, the **effective local user socket** `unix:///run/user/$(id -u)/docker.sock`, an i5-local Docker daemon reporting the `rootless` security option, and the selected Buildx builder. It invokes only root-owned `/usr/bin/git`, `/usr/bin/python3 -I -B`, and `/usr/bin/docker` after checking their file types, owners and permissions; Git is pinned to `/usr/bin/git` and must also resolve to that path through the sanitized `PATH`. Before any Docker CLI call, it checks the root-owned, non-writable plugin search directories and the exact root-owned mode-0755 `/usr/libexec/docker/cli-plugins/docker-compose` and `docker-buildx` files. Duplicate Compose/Buildx plugins in alternate system search directories fail closed. Docker commands use an empty mode-0700 config in a private `/tmp` directory, plus a temporary Docker context pinned to this user's rootless socket; that config and context are removed when preflight exits. User Docker config, user plugin directories, user contexts, and builder overrides are not used. Identity checks use absolute `/usr/bin/id`, `/usr/bin/hostname`, and `/usr/bin/stat` paths. Git status disables filesystem-monitor and untracked-cache shortcuts and explicitly includes untracked files and submodules. The preflight also runs `source-tree-guard.py`: it verifies the exact commit tree and index, rejects assume-unchanged and skip-worktree flags, hashes tracked source bytes and modes, rejects unsupported symlinks, and checks the effective backend/frontend Docker contexts after their committed `.dockerignore` rules. Untracked or Git-ignored files that Docker would include fail admission. Unsupported ignore syntax fails closed. The selected builder must report the local `docker` driver, and its endpoint must be the rootless socket or a Docker context that resolves to it. Preflight inspects the builder without creating, switching, or bootstrapping one. Docker CLI access via the rootful system daemon or a remote context/builder is not sufficient. It performs no container build, launch, migration, seed, or Docker daemon mutation. If a stage project already exists, first inventory it and reuse the legitimate same-owner deployment rather than replacing it.
The source guard additionally rejects symlinks for Compose-selected Dockerfiles outside the contexts and checks full safe permission modes (`0600` or `0644` for non-executable files, `0700` or `0755` for executable files) for every tracked file in the backend/frontend contexts and both staging Dockerfiles. It rejects unsafe writable context directories. Do not modify `frontend/.dockerignore` to make the current ignored Playwright artifacts disappear from admission. Keep the original checkout's ignored artifacts and cache intact; use only the exact-SHA detached admission path above.

The existing `staging-cd.yml` protected-main GitHub formal review gate remains intact as the future protected-workflow path. The separate bootstrap path uses the embedded Ed25519 public key with SHA-256 fingerprint `8f49fad277be0d1c05bf0b75246a5895a8b3489ddaaeef4f43a3e8b90e25e629`; its private key stays only with the external Work supervisor and is never installed on i5. The supervisor independently verifies exact source SHA, CI run IDs/attempts, review, and provenance, creates a canonical evidence record, and supplies its lowercase SHA-256 digest to the launcher. The signature binds that digest and fixed branch codex/belib-i5-isolated-staging-20261107 in addition to the exact current challenge. This signature is not a substitute claim for GitHub formal `APPROVED`.

The following command documents the former candidate launcher path. It is disabled by the current fail-closed CLI and must not be run as an operation path:

```bash
env -i HOME=/home/hiroyuki PATH=/usr/bin:/bin \
  /usr/bin/python3 -I -B "$source_root/scripts/staging/launch-guard.py" \
  --source-sha "$source_sha" \
  --evidence-sha256 "$evidence_sha256" \
  --env-file /home/hiroyuki/.config/belib-staging/i5.env deploy
```

The former launcher checked its own resolved path, branch, Git common directory, detached HEAD, status, and tracked source bytes. Those checks ran in candidate-controlled code and did not establish an independent bootstrap. The previous path also imported `seed.py` before the one-use grant; the current CLI does not reach those checks or import helpers.

With the existing project lock held, the launcher emits canonical UTF-8 JSON challenge schema belib-staging-bootstrap-challenge/v1: repository hiroyuki9614/book_library, PR 103, fixed branch codex/belib-i5-isolated-staging-20261107, exact full SHA, evidence_sha256, host ubuntu-worker, principal hiroyuki, UID 1000, project belib-isolated-i5, operation deploy, fresh 64-character hex nonce, exact response path, and integer creation time. It waits no more than 300 seconds. The supervisor response is an atomically written mode-0600 file owned by the operator, with exactly payload and signature fields encoded as standard Base64. The decoded payload is canonical sorted-key compact UTF-8 JSON signed byte-for-byte with Ed25519 and has exactly schema, repository, pull_request, branch, source_sha, evidence_sha256, host, principal, uid, project, operation, nonce, issued_at, and expires_at. Schema is belib-staging-bootstrap-approval/v1; the evidence digest must equal the CLI value; time fields are integer Unix seconds and the grant lifetime cannot exceed 300 seconds. Duplicate/unexpected fields, unsafe files, a mismatched challenge, invalid signature, expiry, and replay fail closed. Sanitized phase and transaction receipts include the evidence digest. The launcher consumes the response only in this invocation and removes only its challenge files. OpenSSL uses a private temporary directory explicitly under trusted /tmp, independent of inherited TMPDIR.

Source admission accepts only `/home/hiroyuki/workspaces/belib-i5-admission-<first-7-SHA>` when it has the exact SHA, is detached and clean, and shares the original checkout's Git common directory. The launcher builds a durable snapshot at `/home/hiroyuki/.local/state/belib-staging/snapshots/<full-SHA>` from Git blobs. Its manifest records every selected path, blob, and mode; reuse checks the full selection, bytes, modes, and markers. Untracked or Git-ignored Playwright output and ignored build products such as `dist`, `.env.*`, `storage`, `node_modules`, `.git`, `coverage`, and logs are absent. Compose paths resolve from that persistent snapshot, which remains available for bind mounts and later restart. Same-UID access remains the local trust boundary for the snapshot and Docker socket.

The old `/tmp` project lock coordinated only callers using that old launcher. The current CLI does not acquire it or support any operation. Any process running as `hiroyuki` with access to the rootless Docker socket can call the Docker API directly and bypass a launcher; this source-only work does not change host access, Docker, or permissions. The seed's direct-invocation checks and all prior transaction checks are defense in depth, not a replacement for the external bootstrap trust root. Offline checks do not clear owner, independent-review, supervisor-authority, or staging-admission gates.

The official Postgres image makes the configured `POSTGRES_USER` the initialization superuser and creates `POSTGRES_DB` under that account. This candidate names it `belib_staging_bootstrap` and gives it only the bootstrap password. The mounted `scripts/staging/10-belib-staging-roles.sh` is deliberately non-executable, so the official entrypoint sources it in its fresh-volume initialization shell. It checks that `DATABASE_ALREADY_EXISTS` is empty and that `belib_staging` is the expected empty database before creating roles. Passwords are passed to `psql`'s `\password` prompts over stdin; they are not SQL literals or psql command-line arguments. The hook validates the hex-only format, checks app/migration/bootstrap passwords are distinct, asserts the app and migration role flags, effective database/schema privileges, migration schema ownership, default table/sequence grants, and password initialization, then clears the app/migration password variables from the initialization process. The bootstrap account is absent from backend, migration, seed, and database-healthcheck connections.

The database healthcheck connects locally as `belib_staging_app` and requires the role-bootstrap marker plus non-superuser app/migration attributes, app `CONNECT` and schema `USAGE`, and no app database/schema creation privileges. On an existing volume, the official entrypoint skips initialization; without the marker and role contract the healthcheck fails and dependent services remain stopped. Do not try to repair or reinitialize such a volume with this hook.

## Signed bootstrap and retained-state resume (historical interface; blocked)

The operational descriptions and command examples in this historical section are disabled by the current launcher block above. No `manifest-ingress`, `resume-ingress`, or `deploy` command can be used until an external immutable bootstrap is independently reviewed, pinned, installed, and integrated in a separately reviewed change.

## Retained application/schema upgrade preflight (not deployable)

`scripts/staging/retained-upgrade.py` is an offline source-side evidence checker only and cannot authorize an operation. External independent provision of trusted issuer/key/fingerprint pins is **BLOCKED** in this source-only scope. Public `preflight` accepts no signer pins or clock override, checks evidence issue/expiry windows against system time, and then fails closed because no external trust source is configured. Caller-supplied issuer names, public keys, and fingerprints—even matching signatures—are not independent authority and must not be treated as security assurance. The private fixture helper exists for synthetic unit tests only. No deployment permission follows from any result; synthetic evidence remains `NOT_DEPLOYABLE_SOURCE_ONLY`.

The v2 manifest schema defines fields for repository/PR/branch, staging host/principal/UID/project, candidate full source SHA, fixed prior application SHA and backend/frontend OCI digests, database lineage, retained DB container and immutable image digest, both named volumes with opaque SHA-256 identity digests, the sorted isolated/ingress network identity pair, and a sorted book-file path/size/SHA-256 inventory. Book paths reject absolute paths, traversal, hidden components, backslashes, control characters, duplicate paths, and unsorted entries. The source checker can compare a manifest with a signed network inventory, but this code has no independently provisioned authority for that inventory. Volume identity digests are structurally bound into the manifest only; the checker does not inspect Docker or establish who observed them.

The backup-root contract schema names the repository tree, retained PostgreSQL storage, and retained books storage. No external authority provisions trust pins for this contract in the current source-only scope, so it cannot provide independent backup-root assurance. Lower-level fixture checks exercise no-follow hashing, owner/mode/link/size checks, before/after inode metadata checks, and a 64 GiB bound; fixture success is not an authorization or deployment result. A manifest digest alone is never restore proof.

The attestation binds exact backup and manifest digests, PostgreSQL major version, disposable restore target identity, integrity-query report digest, actual restore-success boolean, issuer, and integer issue/expiry times. Its validity is limited to 300 seconds. JSON parsing rejects duplicate keys and oversized inputs. Signature fixture checks use the installed `cryptography` Ed25519 implementation; backup fixture checks read only their supplied local artifact. Tests generate all keys in memory. Current source has no independently approved verifier issuer/key/fingerprint; until the owner provisions and independently pins those exact values and the reviewer confirms the verifier's inventory/restore procedure, there is no accepted runtime evidence. Even a valid source-only result remains non-deployable.

Do not pass this preflight as authorization to `resume-ingress` or to the existing `deploy` operation. `resume-ingress` intentionally starts the prior database and current application without running migrations. The staging Compose migration service runs `prisma migrate deploy`, including `20261105120000_add_reading_progress_percentage`; the migration adds a check constraint and this source does not establish a safe schema rollback or failure recovery transaction. No retained application/schema upgrade operation is admitted by the launch guard.

### Required design and owner decisions before runtime code

This source-only continuation stops before adding an upgrade operation to `launch-guard.py` or widening `approval.py`. The available Ed25519 key is the staging deployment approver key; no independently pinned backup/restore verifier issuer, public key, or raw-key fingerprint is present. No live backup or isolated restore evidence was collected. Also, a migration changes the retained database volume, so “rollback” cannot honestly mean both restoring the prior database bytes and preserving that same volume's identity and contents. The existing resume operation remains unchanged and cannot authorize a migration.

Before runtime implementation, the staging owner and independent reviewer must approve this exact contract:

1. **Inventory tuple:** canonical manifest schema `belib-retained-upgrade-manifest/v2`, bound to repository `hiroyuki9614/book_library`, PR 103, the fixed staging branch/host/principal/UID, Compose project `belib-isolated-i5`, candidate full Git SHA, prior application SHA `81256268276d7ead95029849037f69ec6beec202`, prior backend and frontend OCI manifest digests, DB container ID and immutable DB image digest, database lineage, exact PostgreSQL and book volume names/IDs/mounts, Compose network IDs, and a sorted book-file inventory of relative path, size, and SHA-256. Reject unknown projects, extra/missing services or volumes, mutable image tags, and any mismatch from the reviewed prior inventory. Hash the canonical manifest; do not treat that hash alone as proof of backup contents.
2. **Backup proof:** take an off-repository PostgreSQL custom-format backup from the exact retained DB identity. Independently check its format/catalog and source identity, restore it into a disposable isolated PostgreSQL instance, and run approved catalog/integrity queries against the restored DB. The verifier must issue canonical `belib-retained-upgrade-restore-attestation/v1` evidence binding the backup SHA-256, source manifest SHA-256, PostgreSQL major version, restore target identity, restore result, issuer, short validity, and query-report SHA-256. The validator expects a detached envelope with exactly `payload` and base64 `signature`; the Ed25519 signature covers canonical UTF-8 JSON bytes of the payload. External provision of an independent trust source is BLOCKED; caller-supplied values and synthetic signatures do not establish authority. A file digest, `pg_restore --list`, self-reported success, or deployment approver signature is not restore proof. The restore verifier must have no production DB, R2, deployment env, or retained-volume credentials.
3. **One-use authorization:** only after independent source/review/CI/provenance and restore-attestation checks, issue a canonical Ed25519 challenge/approval for operation `upgrade-retained`. The signed payload must have an exact field set and bind the candidate SHA, prior app SHA and backend/frontend OCI digests, v2 manifest digest, backup digest, restore-attestation digest, fixed stage tuple, fresh 256-bit nonce, integer issue/expiry times, and operation. Grant lifetime is at most 300 seconds. If an external evidence object is fetched, bind its strong ETag into the signed tuple and require an exact `If-Match` on every read; reject missing, weak, or changed ETags. Under the existing project lock, reject duplicate/unknown fields, bad signatures, stale source/challenge/manifest/ETag, expiry, replay, and any mismatch; consume the challenge exactly once. Never reuse `deploy`, `resume-ingress`, or `tls-ingress` approval.
4. **Bounded phases and receipts:** under that same lock, re-read and compare the fixed-SHA source, Compose project inventory, image digests, manifest, and approval tuple before mutation. Emit sanitized phase receipts for `preflight`, `backup-restore-proof`, `migration`, `backend-frontend-start`, `health-readback`, and terminal `PASS`/`FAILED`/`ROLLBACK_CONFIRMED`/`ROLLBACK_UNCONFIRMED`. Run migration as its own bounded phase; then start only the backend and frontend images built from the authorized SHA. Read back DB/container/volume/image identity and require database health, backend health, frontend health, and HTTP health before `PASS`. No production DB/R2/environment lookup, remote executor, SSH, new runner, or automated scheduler is permitted.
5. **Rollback choice (must be explicit before implementation):** owner must choose and review either (a) an atomic cutover to a separately restored/migrated staging DB volume while retaining the original DB container and volume untouched until health acceptance, or (b) a separately authorized in-place restore procedure with explicit approval to replace/mutate the retained DB state. Specify how the prior app images are restored by immutable digest and how the post-failure DB identity, volume identity, contents, and book volume are verified. Until one choice is approved and its failure cases are testable, any migration or health failure must stop further phases and report `ROLLBACK_UNCONFIRMED`; the code must never claim rollback success from a successful `compose stop` alone.

Only after those owner decisions, independent design review, and availability of pinned verifier/approver trust material may runtime source changes be proposed. Actual backup consistency and isolated restore remain live evidence gates and are `NOT_RUN` in this source-only work. The owner must authorize any later runtime action separately, review the exact candidate and tests, rerun CI at its final HEAD, and provide fresh runtime approval. No i5 runtime action is authorized here.

The existing launcher still contains its guarded signed transaction implementations for future independently reviewed integration, but the operator bootstrap gate currently blocks every invocation before reaching them. The following details are historical procedure, not usable deployment authority. Do not treat the candidate-controlled approval verifier as independent trust.

For the retained deployment after the recorded HTTP failure, first generate a read-only inventory from the exact admitted current source. This prints canonical JSON containing the manifest and `retained_state_sha256`; the output is evidence to the external Work supervisor, never an approval by itself:

```bash
/usr/bin/python3 -I -B /home/hiroyuki/workspaces/belib-i5-admission-<current-sha-prefix>/scripts/staging/launch-guard.py \\
  --source-sha <current-full-sha> \\
  --prior-source-sha 6963103fd6bec332b4057457d07b890fe5c11e73 \\
  --env-file "$HOME/.config/belib-staging/i5.env" manifest-ingress
```

The supervisor independently reviews the exact source and CI/review/provenance evidence, checks the stopped resource inventory, and signs a fresh `resume-ingress` grant binding the current source SHA, fixed prior SHA, evidence digest, and exact retained-state digest. Resume requires that grant and a fresh matching read-only inventory under the project lock. Its bounded transaction starts only the reviewed retained DB container, verifies health and immutable DB image identity, then builds and starts `backend`, `frontend`, and `nginx` with `--no-deps`; it does not run migration or seed. The DB init hook and DB data remain on the prior snapshot/volume, while nginx binds the current verified snapshot. It verifies unchanged DB ID, volumes and isolated network, exact nginx bridge attachments, exact loopback binding, and actual HTTP 200. Any mismatch blocks before mutation; any later failure stops this project's services, confirms none remain running, and preserves volumes. Use the same launcher path and arguments to present the grant:

```bash
/usr/bin/python3 -I -B /home/hiroyuki/workspaces/belib-i5-admission-<current-sha-prefix>/scripts/staging/launch-guard.py \\
  --source-sha <current-full-sha> \\
  --evidence-sha256 <work-record-sha256> \\
  --prior-source-sha 6963103fd6bec332b4057457d07b890fe5c11e73 \\
  --retained-state-sha256 <manifest-sha256> \\
  --env-file "$HOME/.config/belib-staging/i5.env" resume-ingress
```

This paragraph records the historical pre-resume checkpoint; use the current status above and fresh Work evidence for current signer and runtime state. The factual 2026-11-10 result remains: the signed 6963103 transaction completed DB initialization, migration, synthetic seed, and runtime role checks; HTTP acceptance failed; rollback stopped all four services and preserved both volumes. No deployment/browser acceptance is claimed. Keep staging values private. Migration uses an explicit build from the admitted snapshot; seed has a bounded timeout and duplicates borrowed environment descriptors. The guard uses the fixed lock, exact SHA, clean detached-source admission, rootless socket/context, trusted CLI/plugins, normalized Compose model, immutable image digests, and persistent SHA snapshot. No raw Compose command is supported.

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

The backend service and one-off seed explicitly run as the image's `node` account, and the seed independently rejects UID 0 or any account other than `node`. The dedicated `staging-book-files` named volume mounts at the pre-existing `/home/node` directory, which the [Node 22 slim image recipe](https://github.com/nodejs/docker-node/blob/main/22/bookworm-slim/Dockerfile#L371-L375) creates for UID/GID 1000 with `--create-home`. Docker copies the destination contents into a new empty volume by default; Moby's [volume copy-up implementation](https://github.com/moby/moby/blob/master/daemon/container/container_unix.go#L1756-L1784) uses `fs.CopyDir` to copy the pre-existing directory, including its filesystem metadata. The seed verifies that `/home/node` is an actual mount point owned by its nonroot UID, refuses unexpected non-hidden top-level content, creates `/home/node/books` as that user with mode `0700` when absent, then probes file creation and removal before connecting to PostgreSQL. Compose keeps the npm cache under `/tmp` so it does not add incidental cache data to the book volume. The seed never changes volume ownership or permissions. Docker does not reinitialize a nonempty volume, so any existing volume that is not node-owned, contains unexpected root-level data, or is not writable fails closed and must not be repaired by this procedure. Do not use UID 0, `chown`, runtime `chmod`, `sudo`, or a rootful/remote daemon to work around a failed check. This copy-up behavior and the first-use write probe have automated offline coverage, but the signed 6963103 transaction also passed this nonroot ownership/write probe on i5 before its later HTTP failure and service rollback. See [Docker volume copy-up behavior](https://docs.docker.com/engine/storage/volumes/#mounting-a-volume-over-existing-data).

Do not run `seed.py` directly or create a local approval claim. Seeding runs only as the signed `deploy` transaction's middle phase after migration.

The seed is additive and idempotent. It never removes a book or overwrites a fixture file whose content differs from the deterministic bytes. The DB writes run in one transaction and roll back if the operation fails. If fixture writing or the DB transaction fails, the seed leaves every fixture path as-is, including newly created or incomplete files; it performs no fixture-path cleanup or pathname-based unlink. After a failure, inspect only `belib-staging-synthetic.epub` and `belib-staging-synthetic.pdf` in the dedicated named local book volume. A later attempt reuses a file only when it is a safe regular file with bytes exactly matching its deterministic fixture; incomplete or differing files fail closed and are never overwritten. If cleanup is required, perform it only through separately reviewed and approved manual cleanup scoped to those isolated paths, after confirming they are unreferenced. Keep both volumes. The current launch guard stops project services on transaction failure but provides no fixture or volume deletion operation. The SQL below is reference only; do not run it or use raw `docker compose exec` until a separately authorized guarded rollback operation is available. It matches the synthetic book titles, author, and description, and the reserved `.invalid` account emails only:

```sql
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
```

If the transaction aborts or its file-reference check fails, do not run filesystem cleanup. Even after it commits, the current launch guard does not support fixture deletion. Any future cleanup requires a separately reviewed and authorized narrow guard operation for only these two synthetic file paths; do not invoke raw Compose or delete a volume.

Keep the migration-managed `未分類` category and both staging volumes. Do not use `docker compose down -v` or any volume deletion as rollback.

Offline checks for this step are `bash scripts/staging/test-compose-isolation.sh`, `python3 scripts/staging/test-seed-wrapper.py`, `npm --prefix backend test -- scripts/seed-staging.test.ts`, and `npm --prefix backend run build`. They validate the separate bootstrap/app/migration connections, fresh-volume-only role SQL, strict host env allowlist, ambient override guards, production Compose separation, isolated volume mapping, nonroot directory initialization, and fail-closed seed checks without Docker or a database. They are **not** staging acceptance evidence and do not replace the physical i5 volume and browser checks below.

## Acceptance after actual i5 deployment

### Retained database transaction implementation

`scripts/staging/retained-db-transaction.py` implements the bounded transaction
order for the independently admitted staging executor: quiesce writes, durable
backup, actual isolated restore verification, forward migration, schema/data/
authorization/progress checks, read-only application activation, then active-state readback.
Only after the commit decision is durably journaled does the executor release
the application write barrier. A failed or interrupted release never restores an
old backup: retry reconciles the committed application and idempotent write release.
It uses one private fsynced journal per admitted operation identity. A retry of a
completed operation only checks active state. An interruption before the commit decision, after migration
starts restores the same backup and prior application instead of re-running the
migration. Recovery failure keeps the operation blocked and preserves its backup;
the caller must keep application writes quiesced and reconcile that exact journal.

The caller must hold the existing project lock, authenticate fresh independent
admission and bind the source, host/principal, resources and immutable backup. The
module does not provide that authority, discover Docker resources, expose a CLI,
or replace the pending installed bootstrap and CD integration. Its `Operations`
adapter must perform actual PostgreSQL restore and integrity checks; matching
supplied hashes alone is insufficient. The current production staging launcher
does not yet invoke this engine.

Run `python3 -B scripts/staging/test-retained-db-transaction.py` for offline
transaction/recovery tests. Explicitly setting `BELIB_DISPOSABLE_POSTGRES_TEST=1`
also runs actual PostgreSQL dump/restore/migration tests on two fresh rootless
containers per test. They use the existing pinned PostgreSQL image, tmpfs data,
no ports, no network and no retained volumes. Only those uniquely named test
containers are removed. These synthetic DB tests simulate application activation;
they are not BeLib application deployment, a signature, or physical Chrome acceptance.

The first transaction showed that nginx had the expected loopback HostConfig but no published port while attached only to the internal isolated network. The source correction adds a project bridge ingress network to nginx alone; db, migrate, backend, and frontend remain on the internal network, and the loopback port binding is unchanged. This source change is not evidence that runtime publication now works. After a separately reviewed resume operation, verify the configured loopback port at `http://127.0.0.1:<verified-port>/health`; treat failure as a deployment blocker. Then, only after the owner-approved Tailnet-only custom HTTPS ingress and certificate gates above are met, use the physical i5 Google Chrome GUI, not a headless substitute, at `https://stg-bl.hiroyuki9614.com` to check anonymous protected API 401, separate staging admin/general identities, general-user denial of admin-only books and management API, synthetic EPUB and PDF body/page displays, reading-position save and restore after reload and new login, and Shelf/Reader UI. Read the real `frontend/package.json` scripts before executing tests; the current reader script is `npm --prefix frontend run test:readers`. Confirm test fixtures reference only the isolated staging DB/storage and never delete real users/books.

Report separately: candidate code, fixed HEAD, current CI, independent review, i5 preflight, real Compose services/volume identities, HTTP smoke, Playwright, physical i5 Chrome, and final acceptance. Only observed results count. If the stage is running, persist the observed loopback and verified Tailnet HTTPS URLs with an evidence pointer in the original PV3 Work Prompt/owner PR. Do not commit confidential browser traces or uploaded books.

