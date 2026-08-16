# BeLib Current Implementation Status

- Updated: 2026-08-14
- Purpose: 現在のMVP実装、暫定実装、正式要件との差分、次の作業境界を把握する
- Current implementation checkpoint reviewed: `checkpoint/belib-mvp-phase6-20260812` at `a64fd12dd53b2f090d913fc71f6dded5b0c2883a`
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
real browser + PostgreSQL E2E                    implemented
minimal admin book metadata API                  implemented
minimal admin PDF upload API                     implemented
admin UI -> admin API wiring                     not implemented
per-user readStatus in book list                 implemented
Cloudflare R2 / formal file delivery             not implemented
EPUB protected backend path                      not implemented
formal publication-scope selection               not implemented
completed auto transition                        not implemented
full MVP management functions                    not implemented
```

The core path is verified, but the formal requirements are not all complete.

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

### Per-user list readStatus

`GET /api/v1/books` loads at most the requesting user's `ReadingInfo` for each book and maps that bounded relation to `readStatus`. A missing `ReadingInfo` remains `unread`.

The list and detail APIs therefore use the same current-user isolation boundary without changing the `ReadingInfo` schema or migration history.

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

Known migration issue from the latest verification record:

- current schema includes `book_files.file_hash`
- fresh isolated E2E required temporary schema synchronization because committed migrations did not fully reproduce the current schema
- migration/schema drift remains a Phase 6 completion item

Use a forward migration; do not rewrite an old committed migration just to erase the historical gap.

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
- search and category filtering
- category management
- general-user administration
- logical delete / restore / permanent delete
- file replacement and recovery
- PostgreSQL backup/restore acceptance
- PC Chrome + Android Chrome formal acceptance

See `docs/requirements.md` for the full target.

## Current priority

1. Resolve `book_files.file_hash` migration/schema drift and prove fresh-DB reproducibility.
2. Make README setup reproducible on a fresh environment.
3. Wire Admin UI to existing admin APIs.
4. Implement explicit publication scope.
5. Implement PDF `completed` transition.
6. Cut protected local storage over to the confirmed formal storage design while preserving authorization.
7. Connect EPUB to the same authorization/storage/progress boundary.
8. Continue remaining management requirements.

## Documentation responsibilities

- `docs/requirements.md`: target behavior
- `backend/prisma/schema.prisma`: current DB structure
- `docs/api.yaml`: implemented versioned HTTP contract
- `docs/current-status.md`: current implementation and known drift
- `docs/mvp_plan.md`: execution order and completion gates
- Git history / Personal Vault records: dated execution evidence
