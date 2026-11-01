# BeLib PR #61 release hold follow-up — 2026-11-01

Status: **release hold remains**. This record does not authorize merge, deployment, database changes, or R2 operations.

## Inspected revision and PR state

- Candidate checkout: `codex/belib-frontend-release-20261031-gap-fix`, HEAD `24243f03e256695986f63d617c6e9433112a88aa`; working tree was clean at inspection start.
- `main`: `f8d69c7f1f9a6676ff268ef3da7d815232a779d2`.
- PR #61 was open with base `main` at that SHA and head `codex/belib-frontend-release-20261031` at `24243f03e256695986f63d617c6e9433112a88aa`.
- All five PR-triggered GitHub workflows associated with that head completed successfully. No PR review submission was present at inspection time.

## Favicon hold

- `frontend/index.html` sets the title to `BeLib` and has no favicon link. `frontend/public/vite.svg` remains in the source tree but is not referenced by the frontend.
- The available image-generation tool does not expose an already-approved BeLib credential and enforceable per-image cost cap. No image-generation request was made and no favicon was invented.
- The operator-reported production GET at `2026-11-01 17:31 JST` showed title `frontend` and icon `/vite.svg`. That observation does not identify the deployed source revision or image.

## Public demo account hold

- The login page displays build-time `VITE_PUBLIC_DEMO_EMAIL` and `VITE_PUBLIC_DEMO_PASSWORD` only when both are set. The frontend Dockerfile defaults both build arguments to empty; the example environment file is blank, and neither variable is set in this executor.
- These code and build-input checks do not establish the role of any account already listed by production. No production login was performed, and no production authentication or book-permission boundary was verified.
- The missing operator path is a safe, read-only production browser/API session using the intended dedicated demo account, with access to its `/api/v1/me` role result and requests that verify the demo shelf, an admin-only book denial, and an Admin API denial. The account values must be supplied/handled through that protected path and must not be added to Git. No database access is needed for these checks.

## Production revision and rollback hold

- A read-only browser open of the production login URL was unavailable from this executor. An unprivileged `docker ps` attempt failed because this executor is denied access to `/var/run/docker.sock`; no `sudo` or access bypass was attempted.
- No repository-local November Desktop Commander gate file was found, and Desktop Commander was not used. No approved production host session or runtime snapshot was available here.
- Therefore the active production revision/image and retained rollback image/Compose identity remain unverified. The 2026-10-31 candidate record says the owner reported `f8d69c7` deployed, but also says runtime image and asset identity were not read back. The older 2026-10-26 production record is historical evidence only.

## Checks and impact

- `npm --prefix frontend test`: 23 files and 110 tests passed.
- `npm --prefix frontend run lint`: passed.
- `npm --prefix frontend run build`: passed with the existing large-chunk warning.
- No backend, database, R2, production service, or deployment state was changed.
- Treat this as a **P1 release hold** until the demo account is proven non-admin with the requested denials and the production revision/rollback identity is read back. No P0 defect was established by this inspection; account privilege remains unverified.

## PDF reload restore follow-up

- On PR head `073e0bd6536241e4726141e7bcfa0fa6783f2078`, Reader Playwright job `124798479302` failed after the page-2 save and resize/reload; its single replay `124805562566` passed. This is intermittent evidence, not proof of an environment-only flake or a product race.
- The PDF E2E now checks that the page-2 PATCH request and response both contain page 2, observes PATCHes triggered after resize across the reader's 750 ms save debounce, and reads the reload GET body before the visible restore assertions. The page-number, page visibility, visible-page position, and page-image comparisons remain in place.
- Local focused frontend tests passed (12 unit tests and 9 node tests); lint and production build passed. The real API Playwright E2E was not run locally because its isolated database and fixture environment variables are not configured here. The updated PR head needs CI execution to verify the real API path.
- This follow-up changes E2E diagnostics only. No reader runtime code, production service, database, R2 object, credential, or deployment state changed.
- First CI run of the new diagnostic (`41297572739`) confirmed the reload GET returned page 2 and observed a post-resize PATCH requesting page 2 with HTTP 200. The test stopped because the late PATCH response body could not be decoded in that navigation window; it did not reach the visible UI assertions. The late-write check now uses the request page, HTTP status, and reload GET as persistence evidence, while recording any decoded response fields when available. The corrected diagnostic needs another CI run before classifying the original intermittent UI result.
