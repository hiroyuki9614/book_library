# Isolated post-start smoke runbook

## Boundary

Run only against isolated resources with synthetic data, synthetic accounts, operator-supplied test secrets, and a private operator-controlled origin. Never use real books, real user credentials, shared cleanup, or a shared protected-file store. Do not infer browser or device support from an API-only result.

The smoke test is evidence for a candidate or restore drill; it is not formal MVP completion, deployment approval, or a live deployment.

## Checks

1. Confirm the application starts with the isolated `DATABASE_URL`, `BETTER_AUTH_SECRET`, `FRONTEND_URL`, `BETTER_AUTH_URL`, `VITE_API_BASE_URL`, and the intended storage-driver values supplied by the operator. For `local`, confirm `BOOK_FILE_STORAGE_ROOT`; for `r2`, confirm the R2 variables are injected without printing them and run `cd backend && npm run verify:r2` before application smoke. Confirm the frontend build received an operator-supplied API origin rather than a guessed public domain.
2. Request `GET /health` and record the expected JSON status `ok` and service `belib-api`.
3. Open the UI through the isolated origin and record UI reachability. Verify the authentication boundary with a synthetic account: unauthenticated access to protected API behavior is rejected, authentication succeeds with the synthetic account, and sign-out/session expiry is not bypassed.
4. Verify authenticated `GET /api/v1/me` and the evidenced books API route under `/api/v1/books`. Use only synthetic metadata and a synthetic fixture created for the isolated run.
5. Verify authorized protected-file access at `GET /api/v1/books/:bookId/file` using a synthetic fixture. For `local`, verify the backend stream and checksum/size. For `r2`, verify the backend returns authorized signed URL metadata, the frontend fetches the signed GET without credentials from the browser origin with configured CORS, and the downloaded checksum/size matches. Verify an unauthorized or unauthenticated request is rejected. Do not copy or expose real book content.
6. Exercise the UI/API reachability needed for the candidate, including the reader boundary when applicable, in an evidenced supported browser/device matrix. Record browser/device versions and outcomes; do not claim unverified compatibility.
7. Preserve logs, request IDs, synthetic fixture ID, route/status evidence, and the isolated cleanup owner. Remove only synthetic isolated resources after evidence is secured; perform no shared cleanup.

## Failure handling

A failed check, missing route evidence, authentication-boundary failure, protected-file integrity mismatch, or unverified browser/device result is a stop condition. Do not retry by weakening authentication or changing shared data. Preserve Draft status and escalate to the release owner.

Release packaging must not add R2 lifecycle cleanup beyond the already-defined DB-failure compensation path, file replacement, logical deletion, permanent deletion, recovery, or storage reconciliation. Any such behavior requires separate product and operational approval.
