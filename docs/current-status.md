# BeLib Current Implementation Status

- Updated: 2026-08-14
- Purpose: 現在のMVP実装、暫定実装、正式要件との差分、次の作業境界を把握する
- Current implementation checkpoint: `checkpoint/belib-mvp-phase6-20260812`
- Checkpoint reviewed: `a64fd12dd53b2f090d913fc71f6dded5b0c2883a`
- Default `main` is older than this checkpoint and must not be used alone to judge current MVP progress

## 1. Source-of-truth boundary

This document is a status snapshot, not a requirements or schema source of truth.

```text
current user request
> AGENTS.md
> backend/prisma/schema.prisma (database structure)
> docs/requirements.md (target behavior)
> current implementation + tests (implementation fact)
> docs/api.yaml (currently implemented HTTP contract)
> this status document
```

A temporary implementation does not silently change a confirmed requirement. Protected local PDF storage is an implementation step; the confirmed R2 requirement remains in `docs/requirements.md`.

## 2. High-level state

BeLib now has a real PDF MVP vertical slice rather than a mock-only frontend.

```text
Better Auth Cookie authentication                implemented
GET /api/v1/me                                   implemented
role-authorized book list/detail                 implemented
protected PDF delivery                           implemented with local ignored storage
reading-info GET/PATCH                           implemented
reading position persistence to PostgreSQL       implemented
frontend book/detail/PDF integration             implemented
real browser + PostgreSQL + Cookie E2E            implemented
minimal admin book metadata API                  implemented
minimal admin PDF upload API                     implemented
admin UI -> admin API wiring                     not implemented
Cloudflare R2 upload / signed URL                 not implemented
EPUB protected backend path                      not implemented
formal publication-scope selection               not implemented
completed auto transition                        not implemented
full MVP management functions                    not implemented
```

The core path has reached a verified implementation checkpoint, but the formal requirements in `docs/requirements.md` are not all complete.

## 3. Backend current implementation

`backend/src/index.ts` mounts the authentication, `/api/v1/me`, books, and admin routers. CORS for `/api/*` currently allows `GET`, `POST`, `PATCH`, and `OPTIONS` with credentials.

### Books

Implemented endpoints:

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
- book list is filtered by `role_book_permissions`
- book detail/file/reading-info require the same role permission
- logically deleted books are not returned
- list supports `page` and `limit`
- protected file endpoint currently serves PDF only
- local file keys are resolved under `BOOK_FILE_STORAGE_ROOT`
- path traversal, absolute-path escape, remote URL use, and symlink escape are rejected
- PDF responses use `application/pdf` and private no-store caching

Not yet implemented on the list endpoint:

- title/author search
- category filter
- read-status filter

### Reading info

Current API contract is intentionally minimal:

```json
{ "currentPage": 2 }
```

The backend stores `currentPage` as `ReadingInfo.currentPosition` and upserts per `userId + bookId`.

Current limitations:

- PATCH currently persists `readStatus = reading`
- automatic `completed` transition is not implemented yet
- EPUB CFI persistence is not connected yet
- progress percentage is not stored in the DB, consistent with the requirement

### Minimal admin registration

Implemented endpoints:

```text
POST /api/v1/admin/books
POST /api/v1/admin/books/:bookId/files
```

Current metadata registration accepts `title` and an active `categoryId`.

Current PDF upload:

- admin-only on the backend
- PDF only
- maximum 200 MB
- validates extension, MIME metadata, PDF header, and byte length
- uses SHA-256 for `BookFile.fileHash`
- stores a generated relative key under protected local storage
- removes the newly written local file when DB creation fails

Current formal-requirement drift:

- metadata registration currently creates a `user` role permission automatically
- the formal requirement says publication scope must be explicitly selected and have no default
- current admin API is therefore a minimal vertical-slice implementation, not the final publication-scope implementation

## 4. Frontend current implementation

### Authentication

- `GuestRoute` and `RequireAuth` use the real session path
- `/admin` is additionally protected by `RequireAdmin`
- `/reader/:id` is under the authenticated route tree

### Books / PDF

Production hooks now use real API clients for the main book path. `frontend/src/api/books.ts` provides `fetchBooks`, `fetchBook`, and `fetchBookFile`. PDF fetches include browser credentials and reject non-PDF responses.

### Reading progress

`frontend/src/api/readingInfo.ts` provides `fetchReadingInfo` and `saveReadingInfo`. The PDF reader restores the server-side page and saves page changes through the backend.

### Admin screen

`frontend/src/pages/Admin/index.tsx` is still a prototype:

- starts from `booksData`
- uses component state
- submitting `BookRegistar` only updates local state
- does not call the implemented admin metadata/PDF APIs

Therefore, "admin API exists" and "admin UI is fully connected" are different completion states.

### EPUB

The EPUB reader UI remains in the repository, but the current protected backend file path and real E2E vertical slice are PDF-specific. EPUB must not be described as server-integrated until its backend/storage/progress path is verified.

## 5. Database / Prisma

The schema source of truth is `backend/prisma/schema.prisma`.

```text
Better Auth infrastructure:
User, Session, Account, Verification, Role

BeLib domain:
Book, Category, BookFile, ReadingInfo, RoleBookPermission
```

Important current constraints:

- `BookFile.fileHash` is unique
- `ReadingInfo` is unique by `userId + bookId`
- `RoleBookPermission` is unique by `roleId + bookId`
- `Book.categoryId` is required
- `ReadingInfo.currentPosition` is nullable
- `ReadingInfo.readStatus` defaults to `unread`
- `Book.pageTurnDirection` defaults to `ltr`

Known migration issue from the latest MVP verification record:

- schema includes `book_files.file_hash`
- a fresh isolated E2E database required temporary schema synchronization because committed migrations did not fully reproduce the current schema
- this migration/schema drift remains a Phase 6 completion item

Do not solve this by rewriting an old committed migration without an explicit migration decision.

## 6. Verification evidence

The current checkpoint includes a Playwright real-runtime test covering:

```text
permissioned user login
→ GET /api/v1/me
→ permitted book visible
→ protected PDF
→ page 1 -> 2
→ reading-info PATCH
→ PostgreSQL read-back
→ browser reload
→ page 2 restore
```

It also covers:

- unpermissioned user: book hidden, detail/file return 403
- unauthenticated user: protected list/detail return 401 and Reader redirects to login

The Personal Vault schedule records the broader checkpoint verification as backend full tests green, backend build green, Prisma validation green, and real browser E2E green. Frontend full build still has known non-MVP TypeScript failures separated from the PDF core path.

These are historical checkpoint evidence and should be rerun after behavior-changing code changes.

## 7. Confirmed requirements not yet satisfied

The following remain requirements, not deleted scope:

- Cloudflare R2 storage and the formal signed-URL behavior
- EPUB upload/viewing through the protected server path
- explicit publication scope (`all users` / `admin only`) with no default
- automatic `unread -> reading -> completed` behavior
- search and category filtering
- category management
- general-user administration
- logical delete / restore / permanent delete
- file replacement and recovery behavior
- PostgreSQL backup/restore acceptance
- PC Chrome + Android Chrome formal acceptance

See `docs/requirements.md` for the complete target.

## 8. Current priority

1. Resolve `book_files.file_hash` migration/schema drift with a forward migration and fresh-DB verification.
2. Make README setup reproducible on a fresh environment.
3. Keep the verified PDF vertical slice green while wiring the admin UI to the existing admin APIs.
4. Implement explicit publication-scope behavior without weakening the confirmed requirement.
5. Implement automatic `completed` transition for PDF.
6. Replace protected local storage with the confirmed R2/signed-URL design while preserving authorization checks.
7. Connect EPUB to the same protected authorization/storage/progress boundary.
8. Continue remaining MVP management features from `docs/requirements.md`.

## 9. Documentation maintenance rule

- `docs/requirements.md` describes target behavior.
- `backend/prisma/schema.prisma` describes current DB structure.
- `docs/api.yaml` describes the currently implemented versioned HTTP contract.
- this document describes current implementation status and known drift.
- `docs/mvp_plan.md` describes execution order and completion gates.
- Git history and Personal Vault records hold dated execution evidence; do not duplicate long logs here.
