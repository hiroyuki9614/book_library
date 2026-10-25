# Portfolio demo preparation and production gate

This runbook prepares Issue #36 without changing the production database, deployment, or R2 objects. Production execution remains an operator-approved step after a read-only preflight, coordinated backup, and isolated restore evidence.

## Safety boundary

- Keep the R2 bucket private. Browser delivery must continue through authenticated `GET /api/v1/books/:bookId/file` signed-URL issuance.
- Do not change `BOOK_FILE_STORAGE_DRIVER` while any current `BookFile.fileUrl` uses another driver's key format.
- Do not delete or replace a `Book`, `BookFile`, `ReadingInfo`, or storage object in this procedure.
- Store snapshots, backup evidence, generated EPUB/PDF files, credentials, and production command logs outside Git. `.tmp/` is ignored for local rehearsal only.
- Never print `DATABASE_URL`, passwords, R2 credentials, cookies, signed URLs, book titles from the existing library, or object keys.

## 1. Read-only production preflight

On the deployment host, first record the live application revision, immutable image digest, Compose project/config identity, and active storage driver using the existing deployment inventory. If the live revision differs from the reviewed candidate, stop and inspect the live revision as the source of truth.

Load production environment values without echoing them. `portfolio:preflight` performs database reads only and refuses write flags. It records book IDs and counts, permission booleans, storage-key format counts, active driver, revision, and whether the configured demo account exists. It deliberately excludes existing titles and exact file keys.

```bash
cd backend
npm run portfolio:preflight -- --output /secure/operator/path/belib-portfolio-preflight.json
```

Review these stop conditions:

- mixed `localKeyFormat` and `r2KeyFormat`, or any `unknownKeyFormat`;
- live driver/key-format mismatch;
- missing `admin` or `user` role;
- unexpected book/file/permission counts;
- an existing demo email that is disabled or is not a general user;
- missing backup/restore ownership or a live revision mismatch.

For `local` storage, do not switch the driver. Migrate files with upload, SHA-256/size verification, DB update after object verification, read-back, and rollback in a separately reviewed operation. The Issue #36 permission command never migrates storage.

## 2. Protect the initial existing-book set

The preflight snapshot freezes the initial book ID set. Later demo books are outside that set and cannot be changed by this command.

Dry-run is the default:

```bash
cd backend
npm run portfolio:protect-existing -- \
  --snapshot /secure/operator/path/belib-portfolio-preflight.json
```

Create a coordinated PostgreSQL/storage backup and complete an isolated restore drill according to `docs/runbooks/backup-restore.md`. Prepare a protected evidence JSON file outside Git:

```json
{
  "databaseIdentityHash": "<from snapshot>",
  "snapshotSha256": "<from dry-run output>",
  "backupId": "<operator backup identifier>",
  "checksum": "<operator checksum>",
  "completedAt": "2026-10-25T00:00:00.000Z",
  "restoreVerified": true
}
```

Only after review and explicit production approval may an operator execute:

```bash
npm run portfolio:protect-existing -- \
  --snapshot /secure/operator/path/belib-portfolio-preflight.json \
  --backup-evidence /secure/operator/path/backup-evidence.json \
  --confirm-snapshot-sha256 '<exact dry-run hash>' \
  --execute
```

The write runs in one transaction, ensures the admin role retains every snapshotted book, removes the user-role permission only from those IDs, and re-reads the result before commit. Any mismatch throws and rolls the transaction back. `--rollback` uses the same snapshot, confirmation hash, and backup evidence and will run only if the target set is still in the exact protected state.

## 3. Create the demo account

Public Better Auth email sign-up is disabled. Supply the credentials through the deployment secret channel; do not place them in a command argument, shell history, repository file, Issue, PR, or log.

```bash
export DEMO_USER_EMAIL='<operator supplied>'
export DEMO_USER_NAME='BeLib Portfolio Demo'
export DEMO_USER_PASSWORD='<operator supplied, at least 12 characters>'
cd backend
npm run create:demo-user
```

The command uses the existing Better Auth credential account model and password hasher. It creates exactly one active `user`-role account. A rerun succeeds only when role, active state, and supplied password already match; it does not silently reset or elevate an existing account. Share the credentials with reviewers through a separate secret channel.

## 4. Prepare Aozora Bunko EPUB/PDF

`docs/portfolio/aozora-demo-books.json` pins two untranslated works by Natsume Soseki, the official Aozora Bunko card and XHTML URLs, author death date, source update date, source edition, input/proofread credits, and conversion history. The choices follow the official Aozora Bunko usage policy for works whose copyright has expired. Recheck the official pages immediately before production use.

Install the Playwright Chromium runtime in the local/rehearsal environment when it is not already present, then generate files outside Git:

```bash
npx --prefix frontend playwright install chromium
npm run prepare:portfolio-demo-books -- --output .tmp/aozora-demo
```

The builder downloads only the pinned official XHTML and same-origin images, decodes Shift-JIS, preserves ruby and source notes, adds a conversion record, emits an EPUB 3 TOC, renders the PDF with Chromium, and writes a generated manifest containing SHA-256 and byte size. Review Japanese text, ruby, TOC, PDF page movement, and the source/credit section before registration.

Registration is dry-run by default and performs local hash/header/size verification without network mutation:

```bash
cd backend
npm run portfolio:register-demo-books -- \
  --manifest ../.tmp/aozora-demo/manifest.generated.json
```

For an approved target, provide admin credentials and the active novel category ID through environment variables. Execution authenticates through Better Auth and registers each file through the protected Admin API with `all_users`; it does not write directly to PostgreSQL or R2.

```bash
export PORTFOLIO_API_BASE_URL='<approved HTTPS origin>'
export PORTFOLIO_ADMIN_EMAIL='<operator supplied>'
export PORTFOLIO_ADMIN_PASSWORD='<operator supplied>'
export PORTFOLIO_DEMO_CATEGORY_ID='<active category id>'

npm run portfolio:register-demo-books -- \
  --manifest ../.tmp/aozora-demo/manifest.generated.json \
  --confirm-manifest-sha256 '<exact dry-run hash>' \
  --execute
```

If execution stops after one registration, stop and inspect the Admin list. The command refuses a title collision rather than guessing whether a partial prior run is safe to resume.

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
