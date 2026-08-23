# BeLib Current Implementation Status

- Updated: 2026-08-23
- Purpose: 現在のMVP実装、暫定実装、正式要件との差分、次の作業境界を把握する
- Current implementation line: `fix/belib-phase-c-fresh-env-repro-20260818` plus focused feature integrations
- Default `main` is older than this implementation line and must not be used alone to judge current MVP progress

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

Temporary implementation does not silently change a confirmed requirement. Protected local PDF/EPUB storage is an implementation step; the R2 requirement remains in `docs/requirements.md`.

## High-level state

BeLib now has a real protected PDF MVP vertical slice and a protected EPUB viewing path rather than a mock-only frontend.

```text
Better Auth session                              implemented
GET /api/v1/me                                   implemented
role-authorized book list/detail                 implemented
protected PDF delivery                           implemented with local ignored storage
protected EPUB delivery                          implemented with local ignored storage
EPUB preferred when EPUB and PDF both exist      implemented
reading-info GET/PATCH                           implemented for numeric PDF page positions
reading position persistence to PostgreSQL       implemented for PDF page positions
frontend book/detail/PDF integration             implemented
frontend protected EPUB integration              implemented
file_hash migration/schema drift on fresh DB      resolved and runtime-verified
backend Prisma validation/build/tests             passing on verified checkpoints
README fresh setup and MVP browser demo           verified on task-owned environment
real browser + PostgreSQL E2E                    implemented for the PDF vertical slice
admin category API                               implemented
admin metadata compatibility API                 implemented
admin EPUB/PDF full registration API             implemented
admin UI -> real admin API wiring                implemented
explicit publication-scope selection             implemented for full registration
per-user readStatus in book list                 not implemented correctly yet
Cloudflare R2 / formal file delivery             not implemented
EPUB ReadingInfo/CFI persistence                 not implemented; per-book localStorage is used
completed auto transition                        not implemented on the current Phase C line
full MVP management functions                    not implemented
```

The core paths are usable, but the formal requirements are not all complete.

The frontend's existing full-project TypeScript errors remain a separate known issue. The EPUB protected-reader work uses a focused TypeScript check for the changed production path plus a Vite production bundle build; unrelated existing errors in About/MiniCard/Home/BookTable remain outside that feature boundary.

Phase C fresh-environment reproduction is verified separately from the frontend full-build issue. The fresh run used a clean checkout, fresh npm installation, task-owned PostgreSQL, committed migrations, Prisma generate, development seed, backend/frontend startup, and the existing MVP Playwright fixture. The MVP browser E2E passed independently.

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
- deleted users are rejected on the current Phase C authorization path
- list is filtered by `role_book_permissions`
- detail/file/reading-info require the same role permission
- logically deleted books are excluded
- list supports `page` and `limit`
- protected file endpoint supports PDF and EPUB
- when both supported files exist, EPUB is selected before PDF, matching the confirmed requirement
- EPUB responses use `application/epub+zip`; PDF responses use `application/pdf`
- file keys are resolved below `BOOK_FILE_STORAGE_ROOT`
- storage-root escape patterns are rejected
- protected file responses use private no-store caching

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

- the current Phase C PATCH contract stores numeric page positions and `readStatus = reading`
- EPUB CFI/location persistence is not connected to this API yet
- EpubReader currently keeps a per-book CFI under `reader-location:<bookId>` in browser localStorage
- progress percentage is not stored in DB, consistent with the requirement

### Admin registration

Implemented:

```text
GET  /api/v1/admin/categories
POST /api/v1/admin/books
POST /api/v1/admin/book-registrations
POST /api/v1/admin/books/:bookId/files
```

Current full registration behavior:

- Admin UI uses `POST /api/v1/admin/book-registrations` rather than local-state-only registration
- EPUB and PDF are supported, with a 200 MB maximum
- extension, MIME type, and file content are checked before registration
- EPUB validation checks the ZIP container's required uncompressed `mimetype` entry; PDF validation checks the `%PDF-` header
- SHA-256 `BookFile.fileHash` rejects duplicate content, including content attached to logically deleted books
- the MVP full-registration path creates exactly one `BookFile` for a new book
- publication scope is explicitly required with no UI default
- `all_users` creates `admin` and `user` role permissions; `admin_only` creates only `admin`
- the protected local file is written before the nested DB create; if DB creation fails, the newly written local file is removed
- `POST /api/v1/admin/books/:bookId/files` also accepts EPUB/PDF and rejects a second file for the same book
- the formal Cloudflare R2 storage/signed-URL cutover remains open

Compatibility note:

- `POST /api/v1/admin/books` remains as the earlier metadata-only vertical-slice endpoint
- the current Admin UI does not use that legacy endpoint for new full registrations

## Frontend

### Connected to real API

- session-based authentication
- current user / role
- book list
- book detail
- protected PDF fetch
- protected EPUB fetch
- PDF reading-info fetch/save/restore
- authenticated Reader route
- EPUB/PDF Reader dispatch based on the protected book detail
- admin route guard
- active Admin category loading
- EPUB/PDF full Admin registration
- explicit publication-scope selection

### Admin registration

`frontend/src/pages/Admin/index.tsx` now submits the registration form through the real Admin API.

Current behavior:

- categories come from `GET /api/v1/admin/categories`
- title, category, publication scope, and EPUB/PDF file are required by the full registration form
- the form sends multipart data to `POST /api/v1/admin/book-registrations`
- the success sheet closes only after the backend returns a successful persisted registration
- the page shows successful registrations from the current page session, including file type/name and publication scope

Current limitation:

- reloading `/admin` does not yet fetch and render the complete persisted administrative book list; that remains a separate management feature

### EPUB

The existing EpubReader UI is connected to the protected book-file API.

Current verified behavior:

- `ReaderPage` routes `fileType = epub` to `EpubReader`
- `EpubReader` fetches `/api/v1/books/:bookId/file` with the authenticated cookie path rather than a hard-coded public EPUB URL
- the returned EPUB Blob is exposed to `epubjs` through a temporary object URL and revoked during cleanup
- TOC, spread selection, keyboard/page navigation, and back-history logic remain in the existing reader
- saved CFI is namespaced per book in localStorage
- PDF remains supported through the same Reader route
- EPUB can now be registered through the Admin full-registration path into the same protected `BookFile` model

Current EPUB limitations:

- CFI/location persistence is localStorage-only and is not yet synchronized to `ReadingInfo`
- R2 cutover remains open

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
- Prisma validation, backend build, and backend tests pass on the recorded Phase C verification
- this is the only required file-hash migration; `20260817120000_add_book_file_hash` must not be created
- `backend/scripts/verify-file-hash-migration.ts` provides the repeatable contract check
- an existing non-empty `book_files` table requires an explicit real-content hash backfill policy before applying; production/shared DBs were not modified by that migration verification work

### Phase C fresh-environment reproduction

- implementation line: `fix/belib-phase-c-fresh-env-repro-20260818`
- README setup documents env preparation, PostgreSQL startup, dependency installation, Prisma generate/migration/seed, optional initial-admin bootstrap, and the PDF demo command
- fresh runtime startup: backend and frontend both ready on the recorded Phase C verification
- MVP Playwright E2E: 4 passed on the recorded PDF vertical-slice verification; it covers login, permissioned list, protected PDF, page move, reading-info persistence/reload, and forbidden access
- `README.md` and `.env.example` are synchronized with the reproducible path; secrets/generated book files remain outside Git

## Verification evidence

The PDF checkpoint contains a Playwright real-runtime path covering:

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

The 2026-08-23 protected EPUB feature verification additionally covers:

```text
protected EPUB file response
→ application/epub+zip
→ EPUB preferred over PDF when both exist
→ frontend accepts protected EPUB Blob
→ ReaderPage dispatches to EpubReader
→ EpubReader passes a protected object URL to epubjs
```

Focused verification result for that feature:

```text
backend EPUB/PDF route tests: 11 passed
backend TypeScript build: PASS
frontend EPUB/PDF focused tests: 6 passed in Chromium
focused EPUB production-path TypeScript check: PASS
Vite production bundle build: PASS
```

The Admin EPUB/PDF registration feature has a dedicated focused CI covering backend registration tests/build plus frontend Admin API/UI tests and lint. Its final result must be read from the feature PR/checks rather than inferred from this status document.

The repository-wide frontend `npm run build` still reports pre-existing TypeScript errors in unrelated legacy/prototype files; those are tracked as separate baseline debt rather than being hidden or weakened by focused feature checks.

## Confirmed requirements still open

- Cloudflare R2 storage and formal file-delivery behavior
- EPUB CFI/location persistence through `ReadingInfo`
- automatic `unread -> reading -> completed` completion across the final intended reading flows
- correct per-user readStatus in book list
- persisted Admin book-list retrieval after reload
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
2. Cut protected local storage over to the confirmed Cloudflare R2 design while preserving the Admin registration and protected-reader contracts.
3. Connect EPUB CFI/location persistence to `ReadingInfo`.
4. Close the confirmed PDF/auth state-transition findings on their dedicated implementation paths.
5. Add persisted Admin list retrieval and continue the remaining management requirements.
6. Continue search/category/user/delete/restore/replacement/backup/acceptance work.

## Documentation responsibilities

- `docs/requirements.md`: target behavior
- `backend/prisma/schema.prisma`: current DB structure
- `docs/api.yaml`: implemented versioned HTTP contract
- `docs/current-status.md`: current implementation and known drift
- `docs/mvp_plan.md`: execution order and completion gates
- Git history / Personal Vault records: dated execution evidence
