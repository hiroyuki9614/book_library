# Admin EPUB/PDF Real Registration Implementation

- Updated: 2026-08-23
- Scope: `feat/admin-book-file-registration-epub-pdf-20260823`
- Base: `fix/belib-phase-c-fresh-env-repro-20260818`

## Implemented

The Admin registration flow now submits real data to the backend instead of only updating frontend local state.

Full registration path:

```text
/admin
→ GET /api/v1/admin/categories
→ POST /api/v1/admin/book-registrations (multipart)
→ validate metadata / publication scope / EPUB or PDF
→ SHA-256 duplicate check
→ protected local storage
→ Book + BookFile + RoleBookPermission DB create
```

Supported registration files:

- EPUB (`application/epub+zip`)
- PDF (`application/pdf`)
- maximum 200 MB
- extension, MIME type, and file-content validation
- duplicate rejection through `BookFile.fileHash`
- one file per book for the MVP registration path

Publication scope is required on the full registration path and has no UI default:

- `all_users`: creates admin and user role permissions
- `admin_only`: creates admin role permission only

The full registration path writes the protected local file before the DB transaction-like nested create. If DB creation fails, the newly written local file is removed. The formal Cloudflare R2 cutover remains a later storage-layer task and is not changed by this implementation.

## Compatibility

The existing metadata-only `POST /api/v1/admin/books` endpoint is kept for the earlier vertical-slice callers. The Admin UI no longer uses it for new full registrations.

The existing `POST /api/v1/admin/books/:bookId/files` endpoint is extended to accept EPUB/PDF and enforce the MVP one-file-per-book and duplicate-hash checks.

## Concurrent EPUB reader integration

This implementation is based on the Phase C line after protected EPUB reading PR #18 was merged. It does not replace the reader implementation. A successfully registered EPUB therefore uses the same protected book-file model expected by the EPUB reader path.

## Still open

- Cloudflare R2 storage / signed URL cutover
- EPUB ReadingInfo/CFI persistence to PostgreSQL
- loading the complete persisted Admin book list after page reload
- remaining MVP management functions (edit/delete/restore/replace/etc.)

This record describes the focused implementation only. `docs/requirements.md` remains the formal target behavior and `backend/prisma/schema.prisma` remains the database-structure source of truth.
