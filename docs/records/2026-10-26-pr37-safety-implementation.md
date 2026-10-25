# PR #37 safety implementation evidence — 2026-10-26 (Asia/Tokyo)

Implementation work only; this is not independent review or release approval.

- Branch: `fix/belib-pr37-safety-20261026`.
- Starting revision: `6dcca45699b66b0043227b999c6c832cb5d7e79b`.
- New commits: none. The assignment requires leaving changes uncommitted when checks do not pass; the browser suite could not start under this sandbox. The changes remain in this worktree for continuation/review.
- No production DB, storage, containers, credentials, deployment, GitHub secrets, pushes or merges were changed. No schema/migration change was made.

## Implemented behavior

The four portfolio commands use compiled JavaScript in the existing reviewed backend runtime. Docker requires a source revision and embeds a root-owned revision file plus OCI label. The runbook verifies external source/digest/image provenance and invokes the same running container with its actual DB network and storage configuration. Unknown/mismatched identity stops the commands. Registration uses only the same container's loopback API and rejects redirects.

Snapshots are v2 and include image, storage identity and a hash of the selected file inventory. Execution requires snapshot confirmation and coordinated DB/storage backup plus isolated-restore attestation. Live inventory/key-format checks run before writes; demo account creation and registration also require all initial books to be protected. Account creation now defaults to dry-run. Unexpected added/removed books or changed file mappings stop the workflow; partial registration cannot be blindly retried.

Permission rollback always rejects, including when shared demo credentials remain active or the account is absent. Regression tests preserve admin access and demonstrate that the general role cannot regain private-book access. No existing book/file/reading row or storage object is deleted by these fixes.

Credential examples use secret injection or non-echo TTY reads, named environment forwarding, disabled tracing and cleanup. CLI failures suppress raw dependency errors that could contain secrets/titles/keys. The runbook requires actual coordinated backups, isolated restore, independent artifact verification and live storage checks; JSON alone never proves a drill. A write freeze is operationally required: inventory checks are not a distributed lock.

## Commands and results

All commands ran locally in this worktree. No CI run or live validation is claimed.

| Command | Result |
| --- | --- |
| `npm --prefix backend test -- tests/portfolioProtection.test.ts tests/portfolioGate.test.ts tests/demoUser.test.ts tests/portfolioDemoManifest.test.ts` | Focused runs passed (latest focused run: 19 tests); the final full run below also includes the subsequent API-target and execute-gate tests. |
| `npm --prefix backend test` | Final: 23 files passed; 129 tests passed, 11 existing explicit skips in `src/api.mvp.red.test.ts`. |
| `npm --prefix backend run build` | Final pass. An earlier storage-driver return-type error introduced during implementation was corrected. |
| `npm --prefix frontend run test:node` | Pass: 3 files, 8 tests. |
| `npm --prefix frontend run lint` | Pass. |
| `npm --prefix frontend run build` | Pass; Vite reports its existing large-chunk warning. |
| `npm --prefix frontend test` | Blocked before test execution: `listen EPERM` on `127.0.0.1:63315`. No browser tests passed in this run. |
| From backend: `XDG_CACHE_HOME=/tmp/belib-pr37-cache npm exec --offline --cache /tmp/belib-pr37-npm-cache -- prisma validate --schema prisma/schema.prisma` | Pass. Default cache use initially failed with `EROFS`; an isolated writable cache copy resolved it. |
| `bash -n scripts/ci/isolated-smoke.sh` | Pass. |
| `docker compose --file compose.yaml config --quiet` with synthetic required environment and explicit `VCS_REF` | Pass. No daemon operation or resolved environment dump. |
| `docker version --format '{{.Server.Version}}'` | Blocked: permission denied on the configured Docker socket. Docker builds, disposable DB/migration smoke and the added runtime command smoke were not run. |
| `git diff --check` | Pass. |

Dependency setup: normal `npm ci` could not fetch packages (`EAI_AGAIN`). A copy of the existing npm cache allowed offline extraction, but lifecycle installation hit an esbuild `spawnSync EPERM`. Both packages were then installed locally with `npm ci --offline --ignore-scripts --cache /tmp/belib-pr37-npm-cache --no-audit --no-fund`; the checks above ran against those dependencies and the repository's generated Prisma client. A normal production dependency installation and full image build remain part of the Docker/CI gate. No dependency versions or lockfiles changed.

## Remaining blocking gates

1. Rerun browser tests in an environment permitted to bind loopback and launch Playwright; run the isolated PostgreSQL/reader E2E and migration gates as required by release CI.
2. In an authorized isolated Docker environment, build the candidate runtime with reviewed `VCS_REF` and run `scripts/ci/isolated-smoke.sh`. Its added synthetic smoke exercises all four read-only/dry-run commands plus rollback/missing-evidence rejection from the production image. This code is present and compiles but its Docker execution is not locally proven.
3. After checks pass, create the requested local commit(s) and obtain separate independent review of those exact revisions. This implementer does not approve the change.
4. Before any production action, independently verify source/image provenance, actual running DB/network/storage identity, private R2 policy and every live local/R2 file mapping and hash/size. Resolve any driver mismatch only through a separate preservation/migration plan.
5. Keep public ingress closed and writes frozen; obtain actual coordinated DB/storage backups, isolated restore and independent evidence acceptance. Account for existing sessions and outstanding signed URLs. The attestation is not cryptographic or external proof.
6. Obtain separate target-specific deployment approval, then perform protected execution and live role/API/browser/R2 acceptance. Verify demo-only visibility, denials for every original private book, admin preservation, reader behavior and no deletions before sharing credentials or opening public access.

## Changed paths

- `.github/workflows/release-ci.yml`
- `backend/Dockerfile`
- `backend/package.json`
- `backend/scripts/create-demo-user.ts`
- `backend/scripts/portfolio-preflight.ts`
- `backend/scripts/protect-existing-books.ts`
- `backend/scripts/register-portfolio-demo-books.ts`
- `backend/scripts/smoke-portfolio-runtime.ts`
- `backend/src/lib/demoUser.ts`
- `backend/src/lib/portfolioGate.ts`
- `backend/src/lib/portfolioProtection.ts`
- `backend/src/lib/portfolioRuntime.ts`
- `backend/tests/demoUser.test.ts`
- `backend/tests/portfolioGate.test.ts`
- `backend/tests/portfolioProtection.test.ts`
- `compose.yaml`
- `docs/current-status.md`
- `docs/portfolio-demo.md`
- `docs/records/2026-10-26-pr37-safety-implementation.md`
- `docs/runbooks/backup-restore.md`
- `docs/runbooks/release.md`
- `docs/runbooks/rollback.md`
- `docs/runbooks/smoke.md`
- `scripts/ci/isolated-smoke.sh`
