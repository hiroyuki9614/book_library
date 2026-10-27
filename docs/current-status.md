# BeLib Current Implementation Status

- Updated: 2026-10-27
- Purpose: 現在のMVP実装、暫定実装、正式要件との差分、次の作業境界を把握する
- Base checkpoint: `main@a615f8760fb238586ac34d30c9600d8ac26c928b`
- Current R2 implementation branch: `feat/r2-storage-cutover-20260912`
- Current portfolio implementation branch: `feat/belib-portfolio-demo-20261025`
- The portfolio MVP is live. The detailed immutable deployment and production-browser evidence is in [the 2026-10-26 production release record](records/2026-10-26-mvp-production-release.md).
- Scoped update (2026-10-01): Home/BookTable reading-evidence behavior below; this is not a fresh R2 or production acceptance.

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

Book storage uses a driver boundary: local storage is explicitly selected for development/E2E, while production uses the Cloudflare R2 driver. Production signed fetch, origin CORS, object size/hash, and known unsigned-path denial passed on 2026-10-26. Cloudflare control-plane configuration remains unverified and is tracked in the roadmap.

## Production MVP snapshot

- PR #37, the loopback-origin hotfix, and the EPUB ArrayBuffer hotfix are merged.
- Production runs application revision `0d0a610d7d94ddfbafb979a9dee65c9e68f6e784` from immutable backend/frontend image IDs recorded in the release record.
- The two pre-existing personal books are admin-only. The general demo user sees exactly the two public-domain demo books.
- Verified custom-format database backups were taken before permission and registration writes.
- Production Playwright Chromium acceptance passed for EPUB/PDF rendering and resume, authorization denials, signed R2/CORS, administrator access, and the mobile layout smoke.
- Post-MVP operational hardening remains in [the roadmap](roadmap.md); it is not retroactively a publication blocker.

## High-level state

```text
Better Auth session                                  implemented
GET /api/v1/me                                       implemented
role-authorized book list/detail                     implemented
protected PDF delivery                               implemented: local stream / R2 signed URL JSON
protected EPUB delivery                              implemented: local stream / R2 signed URL JSON
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
Cloudflare R2 adapter + 1h signed URL               implemented and live; auto-renew pending
full MVP management functions                        not implemented
portfolio demo production cutover                    published and production-E2E verified
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
- EPUB Reader saves `completed` only when epub.js reports `relocated.atEnd`.

The reader derives progress without adding a database percentage column. PDF displays the current page, total pages, and percentage from EmbedPDF scroll state. EPUB generates epub.js locations and displays a one-based location, generated total, and percentage when those values are valid. Home's status counts and BookTable's status badges/filter continue to use backend `readStatus` through `Book.status`; shelf-wide average percentage remains `未計測`.

## Reader navigation and usability

- EPUB renders nested `navigation.toc` / `subitems`, highlights the current resource when it can be identified, closes after a jump, and keeps a bounded in-memory stack for returning to the pre-TOC CFI.
- PDF uses the matching EmbedPDF bookmark plugin and its existing PDFium engine. PDFs without an outline explicitly report that no table of contents is present.
- `Book.pageTurnDirection` is included in the frontend book contract and passed to both readers as the common `ltr` / `rtl` direction source.
- Both readers use approximately the outer 15% as tap navigation. Pointer travel, long press, multi-touch, existing text selection, links and interactive controls are excluded. EPUB listeners are installed inside each epub.js iframe through content hooks, de-duplicated per document, removed with the view, and fully cleaned up on reader teardown.
- ReadingInfo writes are delayed briefly during ordinary navigation and serialized; the final PDF page and epub.js `atEnd` retain the existing immediate completed transition.
- Physical i5 GUI-browser acceptance is the release gate for this reader repair and is not replaced by headless or mobile emulation. Android hardware acceptance is tracked and reported separately; it is not a blocker for this approved release path.

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
- DB creation failure triggers compensation cleanup for the just-written local file or R2 object; cleanup failure is logged.
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

The shelf status filter and read-status summary use the backend-derived book state. Production Home no longer imports or calls `useReadingProgresses` or supplies mock percentages to BookTable; the legacy helper/mock remains only for existing tests. Initial loading and API failure are displayed separately from a successful empty library, so failed or pending requests are not presented as valid zero counts.

## Storage

Current storage boundary:

```text
Admin upload
  -> backend validation
  -> storage driver
       local: BOOK_FILE_STORAGE_ROOT/<uuid>.<ext>
       r2:    R2 bucket / books/<uuid>.<ext>
  -> BookFile metadata/hash in PostgreSQL

Authorized read
  -> Better Auth + RoleBookPermission check
  -> local: backend stream
  -> r2: HEAD existence check -> 1 hour GET presigned URL metadata JSON -> frontend GET without credentials
```

R2 upload and DB-failure compensation cleanup are implemented, including cleanup after an ambiguous failed PUT. The runtime token is not stored in Git. Browser access to the presigned URL requires bucket CORS for the deployed frontend origin. `npm run verify:r2` performs an upload -> HEAD/sign -> GET -> delete smoke without printing credentials.

Still open: Cloudflare control-plane configuration audit, reader-side automatic URL reissue before the one-hour expiry, and the full delete/replace lifecycle. Live signed delivery, production-origin CORS, object size/hash, and known unsigned-path denial passed on 2026-10-26. Existing local `BookFile.fileUrl` rows must not be interpreted as R2 keys without an explicit migration.

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

- Cloudflare control-plane audit for bucket exposure, custom domains, and configured CORS. Runtime signed delivery and production-origin CORS have passed.
- reader-side automatic signed-URL reissue before the one-hour expiry.
- R2 object cleanup/replacement lifecycle beyond DB-failure compensation.

Non-R2:

- disabled-user / existing-session semantic alignment.
- shelf-wide aggregate reader-derived progress presentation (the reader-local percentage is implemented).
- category management CRUD and Admin UI.
- general-user administration and disable/restore flow.
- logical book delete / restore / permanent delete.
- file replacement and recovery flow.
- category filtering UI wiring (backend `categoryId` filter exists).
- PostgreSQL backup/restore acceptance.
- PC Chrome + Android Chrome final acceptance.

## Portfolio demo preparation (Issue #36)

Implemented on the portfolio branch:

- public Better Auth email sign-up is disabled;
- an idempotent `create:demo-user` command creates one active `user` account from secret environment values and refuses role/credential drift;
- a read-only preflight snapshot records only existing book IDs/counts, role-permission booleans, storage-key format counts, revision, driver, and demo-account state;
- all four management commands run compiled JavaScript in the reviewed runtime container; baked revision, externally verified image ID, DB/storage fingerprints and live inventory must match the v2 snapshot;
- protection, demo-account creation and registration are dry-run by default; execution requires snapshot confirmation and verified backup evidence; the R2-only portfolio MVP evidence policy accepts a private readable PostgreSQL custom dump plus recovery procedure and unchanged R2 verification, while the coordinated backup/restore policy remains supported for local storage;
- the protection transaction adds missing admin permissions, removes user permissions only from the frozen initial ID set, and verifies the result before commit; permission rollback is unconditionally disabled to prevent re-exposure through shared/general accounts;
- an official-source Aozora Bunko catalog and reproducible EPUB/PDF builder preserve source edition, input/proofread credits, ruby/source notes, and conversion history without committing binaries;
- demo registration verifies generated hashes/sizes/headers and uses Better Auth plus the protected Admin registration API with explicit `all_users` scope;
- the login screen identifies the portfolio demo and warns that shared-account reading positions are shared;
- isolated Playwright coverage uses the real `user` role to verify demo-only visibility, private-book denial, Admin API denial, PDF page restore, EPUB CFI restore, and admin visibility.

Not performed or claimed by repository changes:

- live deployment revision/Compose/storage/DB inventory preflight;
- production database permission writes or demo-account creation;
- production R2 object creation, replacement, deletion, or driver change;
- production backup/isolated restore acceptance;
- production browser CORS and EPUB/PDF acceptance.

Production preflight, verified private DB backup, protection, deployment and public-browser acceptance remain operator-controlled gates in `docs/portfolio-demo.md`. The owner deferred paired DB/R2 restore drills, offsite encryption and exhaustive device coverage to `docs/roadmap.md`; they are not added MVP publication gates. Repository changes do not establish that production has completed any of these steps.

## Current priority excluding R2

1. Close disabled-user / existing-session semantics without weakening new-login blocking.
2. Connect remaining shelf filtering/category UX to real backend data.
3. Implement logical book delete/restore/permanent-delete lifecycle.
4. Implement category management and user administration.
5. Implement file replacement/recovery while keeping the storage adapter boundary R2-ready.
6. Decide whether reader-derived percentages should also appear in shelf-wide aggregate UI.
7. Run final backup/restore and browser/device acceptance after the management surface is complete.

## Documentation responsibilities

- `docs/requirements.md`: target behavior
- `backend/prisma/schema.prisma`: current DB structure
- `docs/api.yaml`: implemented versioned HTTP contract
- `docs/current-status.md`: current implementation and known drift
- `docs/mvp_plan.md`: execution order and completion gates
- Git history / Personal Vault records: dated execution evidence

## Reader position and navigation semantics

The reader distinguishes current-section screen pages from approximate
1600-character EPUB locations; the saved EPUB anchor remains CFI. Logical next
and previous follow publication order, with arrow placement and keyboard/edge
mapping following page progression. The publisher's text direction and writing
mode remain separate from page progression. Nested formal TOC entries are shown;
when the formal TOC has at most one entry, actual body headings are presented as
an explicitly labeled supplemental TOC without replacing the original file.

Spreads use equal integer pane widths, leaving an odd spare pixel in the central
gap, so native integer remeasurement does not reload a spine on each turn. Native
text selection keeps the explicit page offset while allowing selection expansion.

Reflowable paginated chapters retain their loaded iframe documents on size changes,
with CFI redisplay and new size locks. Conditional or unreadable publisher CSS uses
native reconstruction so responsive writing modes can be rediscovered. The latest
320px i5 spread acceptance failure and follow-up status are recorded in the dated
reader repair record; implementation alone is not final production acceptance.

PDF restoration waits for the real document layout and runs once per document,
independently of subsequent saves and zoom/layout changes. Reading-info writes
are ordered per book in a browser tab, and immediate reentry waits for pending
writes. Failures are visible and retryable. Cross-device conflict resolution is
not implemented. See [reader acceptance](runbooks/reader-acceptance.md) for the
production GUI and real rendering gates.
