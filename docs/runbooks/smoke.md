# Isolated post-start smoke runbook

## Boundary

Run only against isolated resources with synthetic data, synthetic accounts, operator-supplied test secrets, and a private operator-controlled origin. Never use real books, real user credentials, shared cleanup, or a shared protected-file store. Do not infer browser or device support from an API-only result.

The smoke test is evidence for a candidate or restore drill; it is not formal MVP completion, deployment approval, or a live deployment.

## Checks

1. Confirm the application starts with the isolated `DATABASE_URL`, `BETTER_AUTH_SECRET`, `FRONTEND_URL`, `BETTER_AUTH_URL`, `VITE_API_BASE_URL`, and `BOOK_FILE_STORAGE_ROOT` values supplied by the operator. Confirm the frontend build received an operator-supplied API origin rather than a guessed public domain.
2. Request `GET /health` and record the expected JSON status `ok` and service `belib-api`.
3. Open the UI through the isolated origin and record UI reachability. Verify the authentication boundary with a synthetic account: unauthenticated access to protected API behavior is rejected, authentication succeeds with the synthetic account, and sign-out/session expiry is not bypassed.
4. Verify authenticated `GET /api/v1/me` and the evidenced books API route under `/api/v1/books`. Use only synthetic metadata and a synthetic fixture created for the isolated run.
5. Verify an authorized protected-file stream at `GET /api/v1/books/:bookId/file` using the synthetic fixture and isolated `BOOK_FILE_STORAGE_ROOT`; record content type, status, and checksum/size. Verify an unauthorized or unauthenticated request is rejected. Do not copy or expose real book content.
6. Exercise the UI/API reachability needed for the candidate, including the reader boundary when applicable, in an evidenced supported browser/device matrix. Record browser/device versions and outcomes; do not claim unverified compatibility.
7. Preserve logs, request IDs, synthetic fixture ID, route/status evidence, and the isolated cleanup owner. Remove only synthetic isolated resources after evidence is secured; perform no shared cleanup.

## Failure handling

A failed check, missing route evidence, authentication-boundary failure, protected-file integrity mismatch, or unverified browser/device result is a stop condition. Do not retry by weakening authentication or changing shared data. Preserve Draft status and escalate to the release owner.

Release packaging must not implement R2 cleanup, file replacement, logical deletion, permanent deletion, recovery, or storage reconciliation. Any such behavior requires separate product and operational approval.