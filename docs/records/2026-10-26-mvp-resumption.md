# Portfolio MVP resumption — 2026-10-26

Status: **production publication incomplete**. Changes below are local working-tree changes, not a new published PR head. No merge, deploy, DB mutation, demo account, R2 mutation or authenticated reading E2E was performed.

## Scope decision and implementation

The owner explicitly moved offsite encryption, key-management制度, three generations, paired DB/R2 isolated restore drills, independent backup audit, periodic Cloudflare configuration audit, exhaustive browser coverage and low-impact P3 work after MVP publication. See [roadmap](../roadmap.md), [portfolio procedure](../portfolio-demo.md) and [backup procedure](../runbooks/backup-restore.md). Existing authorization, branch protection, independent code review and production approval remain required.

- Added `portfolio-mvp-db-v1` evidence for R2 only. It requires the exact DB/storage/snapshot binding, custom-format dump hash and positive size, private-access/readability checks, unchanged-object verification, recovery procedure, approval reference and consistency boundary. Existing coordinated evidence remains supported; rollback remains disabled. Attestation is not proof of actual backup or permission.
- Aozora source/image fetches reject redirects before following them, external origins and embedded credentials.
- Release CI now includes frontend `test:node`. New remote CI has not run.
- Updated current status, execution order and runbooks to distinguish the scoped gate from deferred operations.

## Read-only findings

- GitHub PR #37 was open/Draft, unmerged, head `7053356045dad1e6e8b4379df3708c3568d95ccf`.
- Four associated Actions workflows report success. Release run `40217263706` used synthetic merge `81fa3172c9b3e511961f989e7241d98280e23518` according to the existing independent review.
- Fresh GitHub Git commit metadata confirms both head and merge commit have tree `e76501647b4e3c8588421f0713ce6831e43ebf5c`. This validates their source-tree equality, not identical commit provenance or CI for these new changes.
- The existing `belib-mvp` runner's private log contains this session's unique tool calls, identifying it as this execution. It was not started twice. Old Codex processes were left alone; their presence alone does not prove an active conflicting task.
- Public origin GET: 200. Anonymous `/api/v1/books`: 401. Empty JSON POST to `/api/auth/sign-up/email`: 403. No account was created by the empty request. Nginx configuration was not re-inspected, so the 403 is an observed endpoint result only.
- Official fixed Aozora source URLs: both 200 without redirects. After the existing extractor removes script/iframe/form, neither inspected body has script/iframe/form/object/embed/svg tags or event attributes. Both retain a `JavaScript:goLibCard()` navigation link whose definition is removed; this is not evidence of exploitable content in this fixed sample, nor a general sanitizer guarantee. Strict sanitization remains P3 roadmap work; re-inspect sources at actual generation time.
- Production image/revision, DB/permission counts, current personal-book references, object hashes, signed GET/CORS, anonymous R2 paths, public bucket configuration and backup inventory were **not freshly verified**. Earlier two-book/two-file observations must not be reused as current evidence.

## Public browser evidence

At `2026-10-26T03:17:38.294Z` (12:17 JST), Playwright Chromium `151.0.7922.34` visited the actual public origin. Desktop 1440×1000 and mobile 390×844 screenshots show the existing unauthenticated login form. Mobile has no horizontal overflow; the form is narrow with wrapped text. No credentials were entered. The initial button-name probe found zero because the existing button is labelled `Submit`, not `login`; screenshots were visually inspected afterward. This is login-page rendering evidence only.

Private/local artifact paths (ignored by Git): `.tmp/mvp-public-preflight/desktop-login.png`, `.tmp/mvp-public-preflight/mobile-login.png`. Images contain no personal books, credentials or signed URLs. No demo IDs exist from this session.

| Required step | Result |
| --- | --- |
| Public login page renders in PC Chromium | PASS |
| Anonymous books API / signup denial | PASS (401 / 403) |
| Mobile login basic layout | PASS for no overflow; narrow form noted |
| General demo login / exactly two legal books | NOT RUN |
| EPUB Japanese text/ruby/TOC/chapter/page/CFI restore | NOT RUN |
| PDF Japanese text/page 1→2 difference/restore | NOT RUN |
| Logout/login progress restoration | NOT RUN |
| General-user management and private-book denials | NOT RUN |
| Admin existing-book read | NOT RUN |
| R2 signed GET/CORS and known anonymous routes | NOT RUN in this session |
| DB backup existence/format/access and permission before/after | NOT RUN |

## Tests and independent review

- Backend `npm test -- tests/portfolioProtection.test.ts tests/portfolioGate.test.ts`: 15 passed.
- Backend `npm run build`: passed.
- Frontend `npm run test:node`: 9 passed (independent reviewer); focused asset suite: 3 passed.
- Frontend build and lint: passed. Vite retains its large-chunk advisory.
- `git diff --check`: passed.
- No schema changes or migrations; no DB-dependent or authenticated production tests claimed.

The user requested separation of implementation and review. An independent read-only agent reviewed a frozen patch based on the existing head, with no source edits or production access. No change-induced P0/P1 or mandatory bug was found. Full captured patch SHA-256: `78338b43f5ae3700d2b1bdeff9b00258c21c7343d2c51a99dc40ea7b9fbc895b`. Its code/tests/CI subset SHA-256 is `99a05148b4670aeb948499bfecd37a023e3d211af2fb94055cc66117cafaf794`, rechecked unchanged after review. Later roadmap/record/execution-order documentation was not in that frozen review. This does not replace new fixed-commit CI/review after publication.

## Execution boundary and concrete continuation

Remote Desktop Commander `start_process` and GitHub PR progress-comment creation both returned `MCP tool call requires approval, but approval policy is never`. No alternate write route was used. The configured filesystem also exposes repository Git metadata read-only, so no commit/push was attempted. The PR comment was **not posted**; Issue #36 was also not updated. This record is the retained progress text, not evidence of external publication.

Personal Vault v3 `AGENTS.md` additionally requires owner-native VPS operations, then `/v3-ops`, then `/v3-ops-local`; Desktop Commander requires an independent monthly ALLOW gate. Its public VPS contract grants no arbitrary-shell/deploy authority. This gate was discovered after initial read-only device/process inspection; no further Desktop Commander work was used after discovery. Do not infer approval from an online device or an old process.

Single continuation request: enable the existing approved KAGOYA BeLib operator lane plus Git commit/push and GitHub PR/Issue write access for this task, with the existing private backup location and recovery operator identified. First resume read-only runtime/DB/R2/backup inventory; then publish this reviewed candidate and obtain fixed-head CI/review. Present the exact discovered target, private backup verification and permission dry-run for any target-specific production approval still required. Do not authorize a guessed directory, a new external destination, arbitrary remote shell, or an unreviewed image.

If the old image cannot run the reviewed management commands, use a separately approved immutable candidate deployment with public signup/demo access closed before taking its bound snapshot. Never inject scripts into the old running image or fabricate revision/restore evidence. Only after verified backup and approved target protection may demo creation, registration and full public-browser E2E proceed. No additional commercial-grade audit is a prerequisite.
