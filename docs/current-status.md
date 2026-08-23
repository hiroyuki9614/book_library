# BeLib Current Implementation Status

- Updated: 2026-08-24
- Purpose: 現在のMVP実装、暫定実装、正式要件との差分、次の作業境界を把握する
- Current implementation line: `fix/belib-phase-c-fresh-env-repro-20260818` plus focused feature integrations
- Current non-R2 candidate: `feat/non-r2-reading-admin-core-20260824` / PR #20
- Default `main` is older than this implementation line and must not be used alone to judge current MVP progress

## Source-of-truth boundary

```text
current user request
> AGENTS.md
> backend/prisma/schema.prisma
> docs/requirements.md
> current implementation + tests
> docs/api.yaml
> this status document
```

Protected local EPUB/PDF storage is a temporary implementation boundary. It does not replace the confirmed Cloudflare R2 requirement.

## High-level state

```text
Better Auth session                                  implemented
GET /api/v1/me                                       implemented
role-authorized book list/detail                     implemented
protected PDF delivery                               implemented with local ignored storage
protected EPUB delivery                              implemented with local ignored storage
EPUB preferred when EPUB and PDF both exist          implemented
per-user readStatus in authorized book list          implemented
book title/author backend search                     implemented
book categoryId backend filtering                    implemented
PDF page ReadingInfo persistence                     implemented
EPUB CFI ReadingInfo persistence                     implemented
PDF final-page -> completed transition               implemented
EPUB final-location -> completed transition          implemented
frontend book/detail/PDF integration                 implemented
frontend protected EPUB integration                  implemented
Admin EPUB/PDF full registration                     implemented
Admin explicit publication scope                     implemented
Admin persisted book list after reload               implemented
Home/BookTable readStatus source = backend            implemented
file_hash migration/schema drift                     resolved and runtime-verified
README fresh setup / PDF browser demo                 verified on Phase C checkpoint
Cloudflare R2 / formal file delivery                 not implemented
full MVP management functions                        not implemented
```

## Book list and authorization

Implemented endpoints:

```text
GET   /api/v1/books
GET   /api/v1/books/:bookId
GET   /api/v1/books/:bookId/file
GET   /api/v1/books/:bookId/reading-info
PATCH /api/v1/books/:bookId/reading-info
```

Current behavior:

- Better Auth session identifies the current user.
- books are filtered by `RoleBookPermission` and logical deletion state.
- list/detail/file/reading-info use the same role-based authorization boundary.
- list joins only the current user's `ReadingInfo` and returns the persisted `readStatus`.
- list supports `page`, `limit`, `q`, and `categoryId`.
- `q` searches title and author name case-insensitively and is limited to 100 characters.
- `categoryId` must be a positive integer.
- EPUB is preferred when both supported file formats exist.
- protected file responses remain backend-mediated and `private, no-store`.

Known authorization requirement drift:

- current book access still resolves only non-deleted users.
- the confirmed requirement says disabling a user should block new login while an already-established session remains valid until session expiry.
- this session/disabled-user semantic remains a separate non-R2 item.

## ReadingInfo

`ReadingInfo.currentPosition VARCHAR(255)` is used without a schema migration for both formats.

PDF request example:

```json
{
  "currentPage": 12,
  "readStatus": "completed"
}
```

EPUB request example:

```json
{
  "currentPosition": "epubcfi(/6/4!/4/2/8:0)",
  "readStatus": "reading"
}
```

Rules:

- exactly one of `currentPage` or `currentPosition` is accepted.
- omitted `readStatus` defaults to `reading` for backward compatibility.
- explicit status may be `reading` or `completed`.
- GET returns both `currentPosition` and a numeric compatibility `currentPage`.
- a numeric position restores the PDF page.
- an EPUB CFI restores through `currentPosition`; `currentPage` remains the compatibility value `1` for non-numeric positions.
- EpubReader reads the DB CFI first and keeps `reader-location:<bookId>` localStorage only as a fallback/cache.
- PDF Reader saves `completed` when the final page is displayed.
- EPUB Reader saves `completed` when the generated final location is displayed.

Progress percentage is still a separate concern. The DB intentionally does not store percentage. Home's status counts now use backend `readStatus`, while the existing average-progress percentage remains on its prior prototype/mock path until final percentage semantics are defined from each reader.

## Admin

Implemented endpoints:

```text
GET  /api/v1/admin/categories
GET  /api/v1/admin/books
POST /api/v1/admin/books
POST /api/v1/admin/book-registrations
POST /api/v1/admin/books/:bookId/files
```

Full registration behavior:

- Admin UI uses `POST /api/v1/admin/book-registrations`.
- EPUB/PDF, maximum 200 MB.
- extension, MIME type, and actual file content are validated.
- EPUB validation checks the required uncompressed `mimetype` ZIP entry.
- PDF validation checks the `%PDF-` header.
- SHA-256 `BookFile.fileHash` rejects duplicate content, including files belonging to logically deleted books.
- full registration requires explicit `all_users` or `admin_only`; there is no UI default.
- `all_users` creates admin + user role permissions; `admin_only` creates admin permission only.
- DB creation failure removes the just-written protected local file.
- one file per book is enforced on the current MVP registration path.

Persisted Admin list:

- `/admin` loads `GET /api/v1/admin/books` on mount.
- reload no longer loses the visible administrative book list.
- the response includes category, publication scope, and the current file summary.
- legacy metadata-only books remain visible with `file = null`.

Compatibility:

- `POST /api/v1/admin/books` remains as the earlier metadata-only compatibility endpoint.
- the current Admin UI does not use it for new full registrations.

## Frontend

Connected to real backend paths:

- session/current user
- authorized book list/detail
- protected PDF/EPUB
- PDF ReadingInfo restore/save
- EPUB CFI restore/save
- Reader PDF/EPUB dispatch
- Admin categories
- Admin EPUB/PDF full registration
- Admin persisted list reload
- Home/BookTable read status

The shelf status filter and read-status summary now use the backend-derived book state rather than the old reading-status mock. The old reading-progress mock remains only for percentage display.

## Storage

Current protected file path:

```text
Admin upload
  -> backend validation
  -> protected local storage below BOOK_FILE_STORAGE_ROOT
  -> BookFile metadata/hash in PostgreSQL
  -> authorized backend file delivery
```

Formal target remains:

```text
protected local storage
  -> Cloudflare R2
```

R2 upload, object lifecycle, signed delivery behavior, and production cutover are intentionally untouched by the current non-R2 implementation.

## Database / Prisma

DB structure source of truth: `backend/prisma/schema.prisma`.

Important constraints already present:

- `BookFile.fileHash` unique.
- `ReadingInfo` unique by `userId + bookId`.
- `RoleBookPermission` unique by `roleId + bookId`.
- `ReadingInfo.currentPosition` is nullable `VARCHAR(255)` and can contain a PDF page string or EPUB CFI.
- no schema migration is required for the non-R2 ReadingInfo expansion.

The earlier fresh PostgreSQL verification for `book_files.file_hash` remains valid and is not modified by this work.

## Verification

The non-R2 focused CI covers:

```text
backend:
  per-user list readStatus
  title/author + categoryId query contract
  EPUB CFI save/restore
  PDF completed persistence
  Admin persisted list + authorization
  existing book authorization/file tests
  existing EPUB protected delivery tests
  existing Admin registration tests
  TypeScript build

frontend:
  Admin API + persisted list
  ReadingInfo PDF/EPUB payloads
  Admin page reload + registration
  EPUB DB CFI restore/save/completed
  focused production-path TypeScript check
  Vite production bundle
```

Existing dedicated Admin registration CI and EPUB protected-reader CI are also retained as regression gates.

## Confirmed requirements still open

R2-dependent:

- Cloudflare R2 upload/storage cutover.
- formal R2 file-delivery / signed-URL behavior.
- R2 object cleanup/replacement lifecycle.

Non-R2:

- disabled-user / existing-session semantic alignment.
- final reader-derived progress-percentage presentation.
- category management CRUD and Admin UI.
- general-user administration and disable/restore flow.
- logical book delete / restore / permanent delete.
- file replacement and recovery flow.
- category filtering UI wiring (backend `categoryId` filter exists).
- PostgreSQL backup/restore acceptance.
- PC Chrome + Android Chrome final acceptance.

## Current priority excluding R2

1. Close disabled-user / existing-session semantics without weakening new-login blocking.
2. Connect remaining shelf filtering/category UX to real backend data.
3. Implement logical book delete/restore/permanent-delete lifecycle.
4. Implement category management and user administration.
5. Implement file replacement/recovery while keeping the storage adapter boundary R2-ready.
6. Replace the remaining percentage prototype with reader-derived display semantics.
7. Run final backup/restore and browser/device acceptance after the management surface is complete.

## Documentation responsibilities

- `docs/requirements.md`: target behavior
- `backend/prisma/schema.prisma`: current DB structure
- `docs/api.yaml`: implemented versioned HTTP contract
- `docs/current-status.md`: current implementation and known drift
- `docs/mvp_plan.md`: execution order and completion gates
- Git history / Personal Vault records: dated execution evidence
