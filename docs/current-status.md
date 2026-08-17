# BeLib Current Implementation Status

- Updated: 2026-08-18
- Purpose: 現在のMVP実装、暫定実装、正式要件との差分、次の作業境界を把握する
- Current implementation checkpoint reviewed: `fix/file-hash-fresh-db-20260816` at `d7311d0c2c3ad8c8955498fb610983834a55e57d`
- Default `main` is older than this checkpoint and must not be used alone to judge current MVP progress

## Source-of-truth boundary

This is a status snapshot, not a requirements/schema source of truth.

```text
current user request
> AGENTS.md
> backend/prisma/schema.prisma (database structure)
> docs/requirements.md (target behavior)
> current implementation + tests (implementation fact)
> docs/api.yaml (implemented HTTP contract)
> this status document
```

Temporary implementation does not silently change a confirmed requirement. Protected local PDF storage is an implementation step; the R2 requirement remains in `docs/requirements.md`.

## High-level state

BeLib now has a real PDF MVP vertical slice rather than a mock-only frontend.

```text
Better Auth session                              implemented
GET /api/v1/me                                   implemented
role-authorized book list/detail                 implemented
protected PDF delivery                           implemented with local ignored storage
reading-info GET/PATCH                           implemented
reading position persistence to PostgreSQL       implemented
frontend book/detail/PDF integration             implemented
file_hash migration/schema drift on fresh DB      resolved and runtime-verified
backend Prisma validation/build/tests             passing (63 passed / 11 skipped)
README fresh setup and MVP browser demo           verified on task-owned environment
real browser + PostgreSQL E2E                    implemented
minimal admin book metadata API                  implemented
minimal admin PDF upload API                     implemented
admin UI -> admin API wiring                     not implemented
per-user readStatus in book list                 not implemented correctly yet
Cloudflare R2 / formal file delivery             not implemented
EPUB protected backend path                      not implemented
formal publication-scope selection               not implemented
completed auto transition                        not implemented
full MVP management functions                    not implemented
```

The core path is verified, but the formal requirements are not all complete.

The frontend's existing build/type errors remain a separate known issue; they are
not evidence of a backend file-hash migration failure and are not changed by this
verification.

Phase C fresh-environment reproduction is now verified separately from the
frontend full-build issue. The fresh run used a clean checkout, fresh npm
installation, task-owned PostgreSQL, committed migrations, Prisma generate,
development seed, backend/frontend startup, and the existing MVP Playwright
fixture. Frontend full build still has known TypeScript errors, and the full
frontend Vitest run had one Chromium dynamic-import failure (44 passed); the MVP
browser E2E passed independently.

## Backend

### Implemented book endpoints

```text
GET   /api/v1/books
GET   /api/v1/books/:bookId
GET   /api/v1/books/:bookId/file
GET   /api/v1/books/:bookId/reading-info
PATCH /api/v1/books/:bookId/reading-info
```

Current behavior:

- session user is resolved through Better Auth
- deleted users are rejected
- list is filtered by `role_book_permissions`
- detail/file/reading-info require the same role permission
- logically deleted books are excluded
- list supports `page` and `limit`
- protected file endpoint currently serves PDF only
- file keys are resolved below `BOOK_FILE_STORAGE_ROOT`
- storage-root escape patterns are rejected
- PDF responses use private no-store caching

### Known list drift

`GET /api/v1/books` currently calls `toBookResponse(book)` without loading the current user's `ReadingInfo` for each list item. Because the response helper defaults `readStatus` to `unread`, the list does not yet return the real per-user status.

This means:

- detail API can return the current user's stored status
- list-level status filters/summary cannot be considered complete
- frontend summary values based on list `readStatus` may be inaccurate until the list joins reading info

This is implementation drift, not a requirement change.

### Reading info

Current request contract:

```json
{ "currentPage": 2 }
```

The backend stores the page as `ReadingInfo.currentPosition` and upserts per `userId + bookId`.

Current limitations:

- PATCH currently stores `readStatus = reading`
- automatic `completed` transition is not implemented
- EPUB position persistence is not connected
- progress percentage is not stored in DB, consistent with the requirement

### Minimal admin registration

Implemented:

```text
POST /api/v1/admin/books
POST /api/v1/admin/books/:bookId/files
```

Current PDF upload is backend-admin-only, PDF-only, maximum 200 MB, validates metadata and actual PDF header, stores SHA-256 hash, uses a generated relative storage key, and removes the newly written file if the DB create fails.

Current requirement drift:

- metadata registration currently creates `user` role permission automatically
- confirmed requirements require explicit publication-scope selection with no default
- current admin API is therefore a vertical-slice implementation rather than final publication behavior

## Frontend

### Connected to real API

- session-based authentication
- current user / role
- book list
- book detail
- protected PDF fetch
- PDF reading-info fetch/save/restore
- authenticated Reader route
- admin route guard

### Admin screen remains prototype

`frontend/src/pages/Admin/index.tsx` still starts from `booksData` and local component state. `BookRegistar` submission updates local state only and does not call the implemented admin registration endpoints.

### EPUB

EPUB Reader UI exists, but the protected backend/storage/progress vertical slice currently verified is PDF-specific. Do not describe EPUB as server-integrated yet.

## Database / Prisma

DB structure source of truth: `backend/prisma/schema.prisma`.

Current models:

```text
Authentication infrastructure:
User, Session, Account, Verification, Role

BeLib domain:
Book, Category, BookFile, ReadingInfo, RoleBookPermission
```

Important constraints:

- `BookFile.fileHash` unique
- `ReadingInfo` unique by `userId + bookId`
- `RoleBookPermission` unique by `roleId + bookId`
- `Book.categoryId` required
- `ReadingInfo.currentPosition` nullable
- `ReadingInfo.readStatus` defaults to `unread`
- `Book.pageTurnDirection` defaults to `ltr`

Migration reproducibility status:

- `backend/prisma/migrations/20260816140000_add_book_file_hash/migration.sql` adds the required `book_files.file_hash` column and unique index without rewriting historical migrations
- fresh PostgreSQL 16.14 verification on Fedora applies all four migrations from zero with `prisma migrate deploy`; a second deploy reports no pending migrations and `prisma migrate status` reports the schema is up to date
- fresh runtime confirms `book_files.file_hash` is `VARCHAR(64) NOT NULL` and `book_files_file_hash_key` is a unique index; migration `20260816140000_add_book_file_hash` is finished and not rolled back
- Prisma validation, backend build, and backend tests pass (63 passed / 11 skipped)
- this is the only required file-hash migration; `20260817120000_add_book_file_hash` must not be created
- `backend/scripts/verify-file-hash-migration.ts` provides the repeatable contract check
- an existing non-empty `book_files` table requires an explicit real-content hash backfill policy before applying; production/shared DBs were not modified by this work

### Phase C fresh-environment reproduction

- source checkout: `fix/file-hash-fresh-db-20260816` at `da282b168a711e4cd43aff21986b4826a88f538b`
- README setup now documents env preparation, PostgreSQL startup, dependency installation, Prisma generate/migration/seed, optional initial-admin bootstrap, and the PDF demo command
- fresh runtime startup: backend and frontend both ready
- MVP Playwright E2E: 4 passed; it covers login, permissioned list, protected PDF, page move, reading-info persistence/reload, and forbidden access
- `README.md` and `.env.example` are synchronized with the reproducible path; the seed password variables required by README are present and secrets/generated PDF files remain outside Git
- the fixture is reproducible from a fresh E2E DB/storage; rerunning against the same task-owned DB/storage requires the documented cleanup because fixture roles use unique names and the PDF write is exclusive

## Verification evidence

The checkpoint contains a Playwright real-runtime path covering:

```text
permissioned login
→ current user API
→ permitted book visible
→ protected PDF
→ page 1 -> 2
→ reading-info save
→ PostgreSQL read-back
→ reload
→ page 2 restore
```

It also covers:

- unpermissioned user: book hidden, detail/file 403
- unauthenticated user: protected APIs 401 and Reader redirects to login

The Personal Vault schedule records the broader checkpoint verification as backend full tests green, backend build green, Prisma validation green, and real browser E2E green. Frontend full build still has known non-MVP TypeScript failures separated from the PDF core path.

These are checkpoint evidence, not a promise that later code is automatically green.

## Confirmed requirements still open

- Cloudflare R2 storage and formal file-delivery behavior
- EPUB upload/viewing through the protected server path
- explicit publication scope (`all users` / `admin only`) with no default
- automatic `unread -> reading -> completed`
- correct per-user readStatus in book list
- search and category filtering
- category management
- general-user administration
- logical delete / restore / permanent delete
- file replacement and recovery
- PostgreSQL backup/restore acceptance
- PC Chrome + Android Chrome formal acceptance

See `docs/requirements.md` for the full target.

## Current priority

1. Correct per-user `readStatus` on the book list before treating list summary/filter as complete.
2. Wire Admin UI to existing admin APIs.
3. Implement explicit publication scope.
4. Implement PDF `completed` transition.
5. Cut protected local storage over to the confirmed formal storage design while preserving authorization.
6. Connect EPUB to the same authorization/storage/progress boundary.
7. Continue remaining management requirements.

## Documentation responsibilities

- `docs/requirements.md`: target behavior
- `backend/prisma/schema.prisma`: current DB structure
- `docs/api.yaml`: implemented versioned HTTP contract
- `docs/current-status.md`: current implementation and known drift
- `docs/mvp_plan.md`: execution order and completion gates
- Git history / Personal Vault records: dated execution evidence
