# Portfolio demo preparation and production gate

This runbook prepares Issue #36 without changing the production database, deployment, or R2 objects. Production execution remains an operator-approved step after a read-only preflight, coordinated backup, and isolated restore evidence.

## Safety boundary

- Keep the R2 bucket private. Browser delivery must continue through authenticated `GET /api/v1/books/:bookId/file` signed-URL issuance.
- Do not change `BOOK_FILE_STORAGE_DRIVER` while any current `BookFile.fileUrl` uses another driver's key format.
- Do not delete or replace a `Book`, `BookFile`, `ReadingInfo`, or storage object in this procedure.
- Store snapshots, backup evidence, generated EPUB/PDF files, credentials, and production command logs outside Git. `.tmp/` is ignored for local rehearsal only.
- Never print `DATABASE_URL`, passwords, R2 credentials, cookies, signed URLs, book titles from the existing library, or object keys.

## 1. Reviewed runtime and read-only production preflight

Keep public ingress closed and freeze all book/file/permission writes from the initial snapshot through backup, restore acceptance, protection, account creation and registration. Do not share demo credentials until the final acceptance gate. An already shared account/session or previously issued signed URL means permission removal alone is insufficient: close public ingress and block object delivery until outstanding URLs expire (currently up to one hour). Never reopen a pre-demo permission state publicly.

The supported management runner is `docker exec` in the **approved running backend container**. It uses that container's DB network, environment, local volume or R2 configuration, and compiled `dist/scripts/*.js` with production dependencies. Do not use a host checkout's `tsx`, `docker compose run` with possibly different settings, a mutable tag, or an arbitrary remote registration API. Registration targets the API on loopback inside this same container. The application source/build directories must not be bind-mounted or modified at runtime.

During the separately approved image build, supply `VCS_REF` as the full 40-character reviewed checkout SHA. The Dockerfile requires it, writes a root-owned `/app/build-revision.txt`, and labels the image `org.opencontainers.image.revision`. CI derives the SHA from its checkout **on the build host**. The runtime never calls Git. Record build provenance connecting that source, registry digest and image ID. A build argument or label alone does not prove the image came from reviewed source.

Before every management session, use the approved deployment inventory to select the exact backend container ID, Compose project/config, expected source revision, registry digest and image ID. Inspect only selected fields; never dump `docker inspect`, Compose environment/config, `env`, or secrets. Verify the running container's image ID against the approved digest's image ID, its revision label, its DB/network identity, and its actual volume mount or private R2 bucket. A local storage fingerprint includes the resolved root and filesystem device/inode; R2 uses the endpoint and bucket. These fingerprints detect drift, but do not prove object existence, correct mounts, private bucket policy, or backup success. Any unknown or mismatch is a stop condition.

Example Bash operator session (non-secret variables come from the approved inventory, not guessed values):

```bash
set -Eeuo pipefail
set +x
umask 077
trap 'unset DEMO_USER_EMAIL DEMO_USER_NAME DEMO_USER_PASSWORD PORTFOLIO_ADMIN_EMAIL PORTFOLIO_ADMIN_PASSWORD' EXIT
# Set BELIB_CONTAINER, BELIB_APPROVED_IMAGE_ID, BELIB_REVIEWED_SHA,
# and BELIB_ARTIFACT_DIR from the approved inventory.
PORTFOLIO_IMAGE_ID=$(docker inspect --format '{{.Image}}' "$BELIB_CONTAINER")
test "$PORTFOLIO_IMAGE_ID" = "$BELIB_APPROVED_IMAGE_ID" || exit 1
PORTFOLIO_EXPECTED_REVISION=$BELIB_REVIEWED_SHA
test "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$PORTFOLIO_IMAGE_ID")" = "$PORTFOLIO_EXPECTED_REVISION" || exit 1
export PORTFOLIO_IMAGE_ID PORTFOLIO_EXPECTED_REVISION

# Prefer the approved secret store. Interactive fallback uses a private TTY.
read -r -s -p 'Demo email: ' DEMO_USER_EMAIL </dev/tty
export DEMO_USER_EMAIL
portfolio() {
  docker exec \
    -e PORTFOLIO_IMAGE_ID -e PORTFOLIO_EXPECTED_REVISION \
    -e DEMO_USER_EMAIL -e DEMO_USER_NAME -e DEMO_USER_PASSWORD \
    -e PORTFOLIO_ADMIN_EMAIL -e PORTFOLIO_ADMIN_PASSWORD \
    -e PORTFOLIO_DEMO_CATEGORY_ID \
    "$BELIB_CONTAINER" npm run "$@"
}
portfolio portfolio:preflight -- --output /tmp/belib-portfolio-preflight.json
docker cp "$BELIB_CONTAINER:/tmp/belib-portfolio-preflight.json" "$BELIB_ARTIFACT_DIR/preflight.json"
```

The output path must be new; preflight refuses overwrite and write flags. It performs database reads only, records IDs/counts, permission booleans, storage format counts, revision/image and identity hashes, and omits existing titles, keys and credentials. The snapshot format is now v2; old snapshots are rejected. Supply the intended demo email so its current role/active state can be inspected.

Stop on mixed/unknown formats, driver/key mismatch (including a legacy local path requiring separate inspection), missing roles, unexpected counts, an existing disabled/admin demo email, missing identity/provenance, or drift. Preflight preserves its diagnostic snapshot but exits unsuccessfully on incompatible storage. A successful preflight does **not** verify object bytes or grant write approval. Independently check every existing DB reference against the live local root/private R2 store, with size/hash verification and authorized read-back, before mutation. Missing or incompatible files require a separately reviewed preservation/migration plan; these commands never migrate files or change drivers.

## 2. Coordinated backup, isolated restore, then protection

Follow [backup/restore](runbooks/backup-restore.md). For this public-demo operation both PostgreSQL and **all referenced storage bytes** need a coordinated, recoverable, encrypted backup under the write freeze. An R2 listing, object HEAD responses, or the continued existence of the live bucket is not a storage backup. If the approved storage backup/retention mechanism is unavailable, stop; obtain separate approval for it before proceeding.

Restore the paired backups into isolated resources with isolated credentials and no public access. Verify all reference mappings, checksums/sizes and authenticated file delivery with the candidate. A synthetic smoke is useful but cannot substitute for verification of the actual protected recovery set. Keep real restored content restricted to named operators. Record tool versions, boundary times, backup locations/checksums, private restore target, validation logs, and independent acceptance. Do not put that content, titles, keys or credentials in public evidence.

Dry-run the frozen ID set:

```bash
portfolio portfolio:protect-existing -- --snapshot /tmp/belib-portfolio-preflight.json
```

After the real drill, an operator prepares an attestation outside Git. Required fields (the `*Sha256` values are actual 64-character lowercase hashes of the indicated artifacts):

```json
{
  "databaseIdentityHash": "<snapshot value>",
  "storageIdentityHash": "<snapshot value>",
  "snapshotSha256": "<dry-run hash>",
  "backupId": "<database backup reference>",
  "databaseSha256": "<database backup hash>",
  "storageBackupId": "<recoverable storage backup reference>",
  "storageManifestSha256": "<backed-up bytes manifest hash>",
  "consistencyBoundary": "<recorded coordinated write freeze>",
  "restoreTarget": "<isolated restore reference>",
  "restoreReportSha256": "<restore validation report hash>",
  "approvedBy": "<independent acceptance reference>",
  "completedAt": "<actual restore acceptance ISO timestamp>",
  "restoreVerified": true
}
```

The code checks shape, target/snapshot binding and completion time (after snapshot, not in the future). **Human-written JSON is not proof of backup or restore.** The release owner must independently retrieve and hash the actual backup, manifest and report, verify storage completeness, inspect the drill evidence, and approve the frozen target before any write. Never manufacture values to pass the gate. Changed inventory or reopened writes invalidates acceptance; stop and reassess with the original private-book baseline retained.

Transfer only reviewed non-secret artifacts into the runtime's private temporary directory as its existing `node` user. For example, put `backup-evidence.json` and later the generated asset directory in a dedicated operator directory, then stream that directory (never environment files or backup contents):

```bash
tar -C "$BELIB_ARTIFACT_DIR/input" -cf - . | \
  docker exec -i "$BELIB_CONTAINER" sh -c 'umask 077; mkdir -p /tmp/belib-portfolio-input; tar --no-same-owner -xf - -C /tmp/belib-portfolio-input'
```

After separate independent review and explicit production approval:

```bash
portfolio portfolio:protect-existing -- \
  --snapshot /tmp/belib-portfolio-preflight.json \
  --backup-evidence /tmp/belib-portfolio-input/backup-evidence.json \
  --confirm-snapshot-sha256 '<exact reviewed hash>' --execute
```

All write commands require that same snapshot, hash and backup attestation. They re-read the live book/file inventory and reject changed revision, image, DB, storage or inventory. Keep the write freeze throughout: these checks are not a distributed lock against concurrent admins. Protection runs its permission changes in one transaction, adds missing admin access, removes only general-role permissions for the initial IDs, and verifies before commit. No book, file, reading record or storage object is deleted.

`--rollback` and the library rollback function **always reject**, including when the demo account is absent or disabled. Shared credentials, other general users, sessions and signed URLs cannot be made safe by checking a single account flag. Keep existing books admin-only; use a reviewed forward fix or a fully isolated restore. Never restore old general permissions into the public deployment. See [rollback](runbooks/rollback.md).

## 3. Demo account (dry-run by default)

Public Better Auth email sign-up stays disabled. Use deployment secret injection or this private, non-recorded Bash TTY fallback; never type passwords as exports/arguments. Disable shell tracing and terminal/session recording; do not log environment dumps. Environment forwarding below passes names, without expanding secret values into the command line. Unset values on completion/failure, and never attach them to evidence, tickets or PRs.

```bash
set +x
read -r -s -p 'Demo display name: ' DEMO_USER_NAME </dev/tty
read -r -s -p 'Demo password (at least 12 characters): ' DEMO_USER_PASSWORD </dev/tty
export DEMO_USER_NAME DEMO_USER_PASSWORD
portfolio create:demo-user -- --snapshot /tmp/belib-portfolio-preflight.json
portfolio create:demo-user -- \
  --snapshot /tmp/belib-portfolio-preflight.json \
  --backup-evidence /tmp/belib-portfolio-input/backup-evidence.json \
  --confirm-snapshot-sha256 '<exact reviewed hash>' --execute
unset DEMO_USER_PASSWORD
```

Both modes refuse unprotected initial books. Dry-run checks the user role and any existing account/password without creating or hashing a new credential. Execution uses the existing Better Auth credential model and hasher; reruns require the existing role, active state and password to match. It never resets or elevates an account. After registration changes the inventory, the original baseline intentionally blocks rerunning these setup commands: stop for a reviewed continuation, never silently redefine private/demo membership.

## 4. Prepare Aozora Bunko EPUB/PDF

`docs/portfolio/aozora-demo-books.json` pins two untranslated works by Natsume Soseki, the official Aozora Bunko card and XHTML URLs, author death date, source update date, source edition, input/proofread credits, and conversion history. The choices follow the official Aozora Bunko usage policy for works whose copyright has expired. Recheck the official pages immediately before production use.

Install the Playwright Chromium runtime in the local/rehearsal environment when it is not already present, then generate files outside Git:

```bash
npx --prefix frontend playwright install chromium
npm run prepare:portfolio-demo-books -- --output .tmp/aozora-demo
```

The builder downloads only the pinned official XHTML and same-origin images, decodes Shift-JIS, preserves ruby and source notes, adds a conversion record, emits an EPUB 3 TOC, renders the PDF with Chromium, and writes a generated manifest containing SHA-256 and byte size. Review Japanese text, ruby, TOC, PDF page movement, and the source/credit section before registration.

Copy the generated asset directory (relative manifest paths are preserved) into the reviewed artifact input directory above. Registration dry-run requires the same running-container identity, snapshot and protected-state checks, and verifies local file hash/header/size without API writes:

```bash
portfolio portfolio:register-demo-books -- \
  --snapshot /tmp/belib-portfolio-preflight.json \
  --manifest /tmp/belib-portfolio-input/aozora-demo/manifest.generated.json
```

For approved execution, supply an existing admin account and active novel category. The command authenticates and registers through the same container's protected Admin API with `all_users`, rejects redirects, and never accepts an external API destination. This prevents checking one DB while writing to a different target. Do not override `DATABASE_URL`, storage settings or `APP_PORT` for a management invocation.

```bash
set +x
read -r -s -p 'Admin email: ' PORTFOLIO_ADMIN_EMAIL </dev/tty
read -r -s -p 'Admin password: ' PORTFOLIO_ADMIN_PASSWORD </dev/tty
read -r -p 'Approved active category ID: ' PORTFOLIO_DEMO_CATEGORY_ID </dev/tty
export PORTFOLIO_ADMIN_EMAIL PORTFOLIO_ADMIN_PASSWORD PORTFOLIO_DEMO_CATEGORY_ID
portfolio portfolio:register-demo-books -- \
  --snapshot /tmp/belib-portfolio-preflight.json \
  --backup-evidence /tmp/belib-portfolio-input/backup-evidence.json \
  --confirm-snapshot-sha256 '<exact reviewed snapshot hash>' \
  --manifest /tmp/belib-portfolio-input/aozora-demo/manifest.generated.json \
  --confirm-manifest-sha256 '<exact dry-run manifest hash>' --execute
unset PORTFOLIO_ADMIN_EMAIL PORTFOLIO_ADMIN_PASSWORD DEMO_USER_EMAIL DEMO_USER_NAME
```

If registration partially succeeds, stop and inspect IDs/counts through the protected admin process. Inventory drift and title collision checks deliberately block automatic retry. Do not delete or overwrite books or regenerate a baseline merely to bypass the gate. Preserve the initial private ID set for a separately reviewed continuation.

## 5. Production acceptance still required

Before deployment approval, capture counts/IDs only before and after and verify:

- unauthenticated list/detail return 401;
- the demo user sees exactly the two demo books and cannot find or open any initial existing book;
- initial existing-book detail, file URL issuance, and reading-info GET/PATCH return 403 to the demo user;
- the demo user receives 403 from every Admin API checked;
- the admin still sees all initial and demo books;
- both demo formats load through R2 CORS, PDF page and EPUB CFI resume, ruby and TOC render correctly;
- R2 remains private and the API issues signed URL metadata only after authorization;
- no existing DB row, reading record, file, or R2 object was deleted or overwritten.

The repository Playwright suite proves the same role boundary and EPUB/PDF resume flow against an isolated PostgreSQL database and protected local storage. Live R2 CORS and production counts require the operator-controlled acceptance above and are intentionally not claimed by local CI.
