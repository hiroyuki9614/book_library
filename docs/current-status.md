# BeLib Current Implementation Status

- Updated: 2026-11-14
- Purpose: 現在のMVP実装、暫定実装、正式要件との差分、次の作業境界を把握する
- Current main checkpoint for the scoped frontend release candidate: `f8d69c7f1f9a6676ff268ef3da7d815232a779d2`
- The prior 2026-10-27 implementation snapshot remains historical; this scoped update does not refresh unrelated R2 or portfolio branch state.
- The 2026-10-31 frontend candidate is in `codex/belib-frontend-release-20261031`, based on the checkpoint above. It is not merged or deployed.
- The owner reports that the checkpoint above was deployed frontend-only at 2026-10-31 18:42:59 JST with image `sha256:49bf745ed0f5b7fd3d8951d64cbeed51d33034c762eed93547be8081c71f63c9`; this candidate did not independently read back that running image identity.
- Candidate scope, test evidence and release holds: [2026-10-31 frontend candidate record](records/2026-10-31-frontend-candidate.md).
- The portfolio MVP is live. The detailed immutable deployment and production-browser evidence is in [the 2026-10-26 production release record](records/2026-10-26-mvp-production-release.md).
- Scoped update (2026-11-07): authenticated-route session revalidation and stale Admin view masking candidate below; this is not a production acceptance.

## 2026-11-13 cross-device reading-save CAS repair candidate

`ReadingInfo.saveSequence` is exposed by authenticated reading-info GET as an
authoritative decimal string. New authenticated PATCH callers send that
reservation together with `expectedSaveSequence`, the exact server base
observed when the intent was created. Both book route layers enforce the base
with a database compare-and-set predicate; a high local sequence without the
matching base is rejected as `STALE_READING_SAVE` instead of overwriting a
newer position from another device. Creation remains limited to base `0` and
legacy sequence-zero callers remain compatible only while the row is still at
server version `0`.

The browser durable outbox is version 3 and records the observed server base.
Fresh devices perform an authoritative GET before reserving a save. A stale
outbox is not automatically rebased or replayed: reload returns the
authoritative server position and marks an initial conflict, while a stale
write drops only that intent and exposes a distinct stale status without a
dead retry action. Genuine network failures retain the outbox and remain
retryable. Account/book scope, generation invalidation, decimal-string
precision, ordinary same-owner saves, and the proven same-browser
acknowledgement race remain covered by focused backend/frontend tests. This is
a source candidate only; no production database or deployment action was
performed.

The CAS repair now also uses an explicit backend-first rollout marker:
authenticated reading-info GET responses from the repaired backend include
`readingSaveProtocol: "cas-v1"`. The repaired frontend validates that marker
for the current account/auth generation before the first PATCH, on account
revalidation, and before durable outbox replay; a missing or different marker
fails closed and leaves the intent durable without a PATCH. The rollout order
is therefore backend (marker plus enforced expected-base CAS) first, then the
frontend. This worktree has not changed deployment or production state, and
the current production runtime has not been independently verified against
this marker.

The authenticated frontend now sends saves to the distinct
`PATCH /api/v1/books/:bookId/reading-info/cas-v1` path. Both mounted book
route modules authorize this path and use one shared strict handler that
requires both sequence fields. Older backend instances do not register this
path, so a mixed new-GET/old-PATCH request receives a 404 before the legacy
sequence-zero handler can mutate progress. The frontend does not retry the
legacy path after that response. The existing `PATCH .../reading-info` remains
for older callers under its existing compatibility behavior. This is a local
candidate change only; it has not been independently reviewed or deployed.

## 2026-11-07 authenticated-route revalidation candidate

The `RequireAuth` boundary now owns focus, visibility-return and `pageshow`
session revalidation for every authenticated route. Its router outlet stays
mounted under an inert hidden veil until the current identity is confirmed.
Book hooks retain route-entry revalidation without registering duplicate
browser listeners. `RequireAdmin` redirects after role loss and remounts its
nested view when the authenticated user or role changes. It unmounts the admin
outlet while any identity check is pending so body-mounted portals are removed;
admin form state can reset during same-account focus checks. Same-session reader
content remains mounted across focus checks. This is a candidate-only source
change; no production or release action was performed.

## 2026-11-05 Issue #90 shelf actions and reading percentage candidate

The BookTable no longer renders the inert MoreHorizontal/Star controls or a
trailing empty action column. Row click, keyboard navigation, responsive
column alignment, empty-state colspan, and loading skeleton cell counts remain
covered by focused frontend tests.

`ReadingInfo.progressPercentage` is now a nullable 0–100 integer persisted per
user and book under the Issue #90 owner-requested exception recorded in
requirements §6.8. Derived-only shelf percentages are not honest across reload:
PDF `currentPosition` stores only the current page while the total page count
is known only to the reader runtime, and an EPUB CFI is an anchor rather than a
numeric position; persisting total-page or EPUB-location metadata would be a
speculative schema expansion. Authorized list/detail/reading-info responses map
unread to 0, completed to 100, and an unmeasured in-progress record to null.
PDF saves a rounded current-page/total-page percentage when both values are
valid; EPUB saves a generated-location percentage when the current location
and generated total are valid. Home averages only non-null percentages and
displays the measured-book denominator. This is a candidate-only source
change; no production database, storage, or deployment action was performed.

## 2026-11-04 stale admin-list follow-up

An active-list response captured before a deletion could arrive after the
mutation and put the book back in the displayed list. The admin screen now
versions list requests, invalidates earlier reads before deletion/restoration,
and re-fetches the relevant server list before reporting success. A server list
that still contains the book is an explicit verification failure. Admin list
requests use no-store. Regression coverage delays real API responses across
tab changes and deletion, then checks the list after a full reload.

This source change does not establish production deployment. The live frontend
observed during this follow-up lacked the previously merged deletion and admin
demo UI. Verify the deployed source/image separately before production acceptance.

## 2026-11-04 deletion candidate

The admin deletion candidate adds permanent deletion from the deleted list with
confirmation, storage-first deletion, database transaction/rollback and retry.
Restore and purge serialize on the book row; missing files are reported in the
deleted list and cannot be restored. No schema migration is required.
Shelf/detail caches are discarded after an admin state change. Reader entry
waits for current server authorization instead of rendering cached metadata.

Production activation and production i5 acceptance are not completed by these
repository changes. The physical i5 test uses an isolated PostgreSQL database,
local protected storage and synthetic books. Production admin authentication
must be completed through the normal login screen before a sample can be added
there. Run the dedicated destructive-fixture test with
`cd frontend && npx playwright test --config playwright.admin-deletion.config.ts`
and the existing E2E environment variables against a fresh task-owned database.
It is excluded from the ordinary E2E suite because it deletes its PDF fixture.
For physical i5 GUI verification set DISPLAY to the existing GUI session and
E2E_HEADED=true; the dedicated configuration uses installed Google Chrome.
Previously opened readers and previously issued R2 URLs retain the
existing lifetime semantics in requirements section 6.6.

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

- This is the last independently recorded full MVP snapshot from 2026-10-26. The owner reports a later frontend-only baseline on 2026-10-31; its runtime identity was not read back in this scoped candidate work.
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
PATCH /api/v1/books/:bookId/reading-info/cas-v1
```

Current behavior:

- Better Auth session identifies the current user.
- books are filtered by `RoleBookPermission` and logical deletion state.
- list/detail/file/reading-info use the same role-based authorization boundary.
- list joins only the current user's `ReadingInfo` and returns the persisted `readStatus`.
- the authenticated route boundary refreshes the current session when the tab regains focus or returns visible, and hides account-scoped route content while that refresh is pending.
- book-list and reading-info requests that provide an expected user ID reject a mismatch with the cookie session; legacy reading-info callers may omit the hint.
- authenticated frontend saves use the CAS-only path, which requires an observed expected sequence and an increasing save sequence; authenticated GET capability checks remain required. A 404 on the CAS path is terminal for that request and never falls back to the legacy mutating endpoint.
- list supports `page`, `limit`, `q`, and `categoryId`; the Home client retrieves all authorized pages in batches capped at 100 records so its summary is not limited to the first page.
- `q` searches title and author name case-insensitively and is limited to 100 characters.
- `categoryId` must be a positive integer.
- EPUB is preferred when both supported file formats exist.
- protected file responses remain backend-mediated and `private, no-store`.

Known authorization requirement drift:

- current book access still resolves only non-deleted users.
- the confirmed requirement says disabling a user should block new login while an already-established session remains valid until session expiry.
- this session/disabled-user semantic remains a separate non-R2 item.

## ReadingInfo

The candidate schema uses nullable `TEXT` for `ReadingInfo.currentPosition`, with a forward migration; this task does not apply it to a database. The reading API accepts non-empty positions up to 16,384 characters.
`ReadingInfo.progressPercentage INTEGER` is nullable and stores the per-user PDF/EPUB reader measurement when valid; it is the narrow §6.8 exception for Issue #90, not a total-page/location-history table.
`ReadingInfo.saveSequence BIGINT` is a per-user/book compare-and-set version with default `0`; the reading-info API serializes it as a decimal string to avoid JavaScript safe-integer loss.

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

- exactly one of `currentPage` or `currentPosition` is accepted; `currentPosition` must be non-empty and at most 16,384 characters.
- omitted `readStatus` defaults to `reading` for backward compatibility.
- explicit status may be `reading` or `completed`.
- GET returns both `currentPosition` and a numeric compatibility `currentPage`.
- authenticated GET also returns the authoritative `saveSequence` decimal string. New authenticated PATCH callers send an exact `expectedSaveSequence` base with their reserved `saveSequence`; a stale base returns `STALE_READING_SAVE` and is never automatically rebased or replayed. Initial reload returns the authoritative position and marks a conflict; a stale write drops only its old intent and leaves the displayed reader position unchanged. Genuine network failures retain the durable outbox for retry.
- a numeric position restores the PDF page.
- an EPUB CFI restores through `currentPosition`; `currentPage` remains the compatibility value `1` for non-numeric positions.
- EpubReader reads the DB CFI first and keeps `reader-location:<bookId>` localStorage only as a fallback/cache.
- PDF Reader saves `completed` when the final page is displayed.
- EPUB Reader saves `completed` only when epub.js reports `relocated.atEnd`.

PDF displays the current page, total pages, and percentage from EmbedPDF scroll state and persists the rounded percentage when both values are valid. EPUB generates epub.js locations, persists a percentage only when the generated total and current location are valid, and displays a one-based location. Home's status counts and BookTable's status badges/filter continue to use backend `readStatus` through `Book.status`; BookTable uses the current user's persisted percentage. Home averages only measured percentages and states the measured-book/total-book denominator; an in-progress book with no valid measurement remains `未計測`. Unread and completed books are represented as 0% and 100% respectively. The existing completed status remains monotonic.

## Reader navigation and usability

- EPUB renders nested `navigation.toc` / `subitems`, highlights the current resource when it can be identified, closes after a jump, and keeps a bounded in-memory stack for returning to the pre-TOC CFI.
- PDF uses the matching EmbedPDF bookmark plugin and its existing PDFium engine. PDFs without an outline explicitly report that no table of contents is present.
- `Book.pageTurnDirection` is included in the frontend book contract and passed to both readers as the common `ltr` / `rtl` direction source.
- Both readers use approximately the outer 15% as tap navigation. Pointer travel, long press, multi-touch, existing text selection, links and interactive controls are excluded. EPUB listeners are installed inside each epub.js iframe through content hooks, de-duplicated per document, removed with the view, and fully cleaned up on reader teardown.
- ReadingInfo writes are delayed briefly during ordinary navigation and serialized; the final PDF page and epub.js `atEnd` retain the existing immediate completed transition. Standalone readers may omit `X-BeLib-User-Id`; authenticated frontend saves include it as an optional account hint, and the backend checks an explicitly mismatched PATCH hint only after book permission authorization, preserving `403` for unauthorized books. The save hook keeps queues scoped by account and clears stale pending payloads when the authenticated account changes, preventing queued or retried writes from crossing accounts.
- Physical i5 GUI-browser acceptance is the release gate for this reader repair and is not replaced by headless or mobile emulation. Android hardware acceptance is tracked and reported separately; it is not a blocker for this approved release path.
- The 2026-10-28 reader branch (`6c4c6d0`) is historical reference work. The current 2026-10-31 candidate applies the accepted Paper & Navy EPUB surface on the real `ReaderPage -> EpubReader` route, keeps the viewer full-width with a 16px iframe text inset at every viewport, and preserves existing menus and the book title. Its local harness/browser checks pass; production and physical i5 acceptance remain incomplete. See the candidate record for exact scope and blockers.

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
- `ReadingInfo.currentPosition` is nullable `TEXT` and can contain a PDF page string or EPUB CFI; the API caps values at 16,384 characters.
- `ReadingInfo.progressPercentage` is nullable `INTEGER` with a database check constraint from 0 through 100.
- the reading percentage is scoped by the existing `ReadingInfo.userId + bookId` unique key and is returned only through the current user's authorized book/reading-info queries.

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
6. Run final backup/restore and browser/device acceptance after the management surface is complete.

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
