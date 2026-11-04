# Release control runbook

For the R2 portfolio MVP, the owner-approved scope in [portfolio-demo](../portfolio-demo.md) takes precedence over the broader recovery and device gates below. A verified private DB backup and documented authorized recovery procedure are required; offsite encryption, paired DB/R2 restore drills and exhaustive device coverage are deferred. Branch protection, independent code review, production execution authority and P0/P1 stops remain in force.

## Status vocabulary

- **Draft review:** the candidate and evidence are available for review; this is the current default state and is not formal MVP completion.
- **Artifact publication eligibility:** required deterministic gates passed and the immutable image/artifact evidence is complete. This does not grant registry credentials or publish an artifact.
- **Deployment approval:** named authority has reviewed the candidate, migration decision, backup/restore evidence, smoke evidence, and stop conditions for a specific target. This is separate from publication eligibility.
- **Live deployment:** the dedicated frontend CD handoff below was accepted by the owner-side KAGOYA `agent` dispatcher and its exact image/source read-back succeeded. Pull request CI itself never accesses production.

## Release evidence record

For each candidate, record:

- commit identifier and repository state;
- backend and frontend required-check results, including tests, builds, lint, Prisma validation, and migration verification;
- image/artifact checksums and registry digests, if an operator has published them;
- lockfile identity and the selected package scripts (`npm test`, backend build/test, frontend build/test/lint) used by the gates;
- migration decision, compatibility review, fileHash backfill decision, and any hold;
- coordinated PostgreSQL/protected-file backup IDs, consistency boundary, encryption/access review, checksums, and latest isolated restore drill;
- smoke route evidence, synthetic-data identifier, browser/device status, approvers, and stop/go decision;
- PR CI, GitHub Actions handoff, and owner-side deployment evidence as separate events.

Do not place credentials, public Production hostnames, or secret values in this record. Public origin and `VITE_API_BASE_URL` remain operator-supplied in the protected production environment. The bootstrap resolves `VITE_API_BASE_URL` through the existing production Compose env file while clearing any ambient shell override for that variable, then stores the resolved value in the mode-0600 dispatcher config. Candidate Compose validation, frontend builds, and frontend `up` commands all receive that pinned value explicitly; a changed candidate build argument fails before build or switch.

Backend runtime builds now require `VCS_REF`, the full reviewed source SHA supplied by the build host. Compose passes it to the Dockerfile's immutable revision file and OCI label. Record the source-to-registry-digest-to-image-ID provenance; a self-declared label is insufficient. Management commands run compiled JavaScript through `docker exec` in the approved running backend container, using the same DB network and storage mounts. Follow `docs/portfolio-demo.md` for revision/identity verification and the scoped MVP database backup gate before public-demo writes. Never infer the running revision from Git inside a runtime container.

## Stop/go controls

Stop and preserve Draft status when any frontend or backend gate fails, the CD handoff is not tied to the exact successful main SHA, the owner-side target identity differs from the approved production frontend, a rollback image cannot be retained, the frontend smoke fails, migration compatibility is unclear, the fileHash hold is unresolved, required approvers are absent, or formal MVP requirements are incomplete. The frontend CD path does not require a demo password; authenticated demo acceptance remains a separate check unless an approved public demo pair has been verified.

Go requires named ownership for release engineering, migration, backup/restore, application verification, and target approval, plus exact source/image evidence. R2 cleanup, file replacement, logical deletion, permanent deletion, recovery, and storage reconciliation remain outside release packaging.

## Frontend CI/CD handoff

`.github/workflows/release-ci.yml` runs the existing release gates for pull requests and again on each `main` push, so both the reviewed PR head and merged main SHA have exact backend, frontend, reader E2E, and Compose smoke results. `.github/workflows/production-frontend-cd.yml` runs after that main run succeeds. It checks that the SHA is still current `main`, identifies its merged PR, accepts either the existing distinct non-author repository collaborator/owner approval or the bounded solo-owner evidence pair below, rejects a latest unresolved collaborator/owner changes request, and checks all four release jobs on both the PR head and main merge SHA. In solo-owner mode the trusted repository owner must be the actual merger; a GitHub self-approval is never counted. It then creates a lightweight `belib-deploy-request-<run-id>-<attempt>` tag pointing to that immutable SHA. Manual replay is limited to the current `main` SHA and uses the same checks. Workflow runs are serialized; stale SHAs are rejected.

The repository is public, so the owner-side pull dispatcher reads the handoff tag and release evidence through unauthenticated GitHub HTTPS. It needs no GitHub token, SSH key, registry credential, or Actions secret. The Actions workflow uses only its short-lived `GITHUB_TOKEN` to read Actions/PR data and create the handoff tag. The host independently revalidates the exact main SHA, merged PR review, and mandatory jobs before it downloads source.

### One-time owner activation

The release gate keeps the distinct-human path for repositories or releases that have an eligible reviewer, but it does not count a PR author's self-approval or a public user's approval. For this repository's single-maintainer mode, independent AI review evidence and explicit owner release approval are separate CD-verified comments; neither is a formal GitHub human approval. Keep the PR in Draft and do not merge it until the exact-head CI and one of these evidence paths are complete:

1. **Existing distinct-human path:** a repository collaborator/owner other than the PR author reviews and approves the exact PR head. The gate still requires the latest eligible review history to have no unresolved `CHANGES_REQUESTED`; a self-approval, admin bypass, or external public approval is not a substitute.
2. **Implemented solo-maintainer path for `hiroyuki9614/book_library`:** the trusted repository owner (`hiroyuki9614`, GitHub `User`, `OWNER` association) must be the actual merger. The owner posts one unedited review comment and one later, distinct, unedited release comment in the exact format below. The review must identify a separately commissioned independent AI session and bounded evidence digest, state `PASS`, and state zero unresolved high findings. The release comment must reference the review comment ID. The gate validates the repository, PR number, full current PR-head SHA, owner identity and association, immutable timestamps (`created_at == updated_at`), canonical JSON shape, comment order, and the absence of revocation or contradictory current-head evidence. Stale comments cannot satisfy the current head; edited, missing, forged, revoked, malformed, or ambiguous evidence denies the release. No comment URL is fetched and no generic `PASS` flag is accepted.

Configure the classic `main` branch protection rule in **Settings → Branches** (branch pattern `main`) with these exact check names from `.github/workflows/release-ci.yml`:

- `Backend tests, build, Prisma, and migrations`
- `Frontend tests, lint, and build`
- `Reader Playwright E2E`
- `Production images, Compose config, and isolated smoke`

For the single-maintainer branch rule, also select **Require a pull request before merging**, **0 required human approvals**, **Require branches to be up to date before merging**, and enforcement for administrators. Keep the existing four exact required CI checks above. Leave force pushes and branch deletion disabled. GitHub's formal human-approval count is zero because the separate independent-AI evidence and explicit owner release approval are verified by the CD gate. Do not administratively change branch protection as part of this code task; when the owner performs the separate activation, verify the rule through **Settings → Branches** or `gh api repos/hiroyuki9614/book_library/branches/main/protection`, and do not proceed if the API still returns 404 or any required context/setting is missing.

For each release PR, keep it Draft until the selected review path has completed, the latest eligible review history has no unresolved `CHANGES_REQUESTED`, and all four exact-head checks succeed. The Actions CD gate and KAGOYA dispatcher then independently require the merged PR head evidence plus green checks for both the PR head and exact `main` merge SHA. The handoff is a tag named `belib-deploy-request-<run-id>-<attempt>` that points to that main SHA. A green PR run alone is not deployment evidence.

#### Solo-owner comment format

These are complete two-line comment bodies; do not add Markdown fences, prose, links, or extra JSON keys. Replace each placeholder with the lowercase full value for the current PR.

```text
BELIB-SOLO-REVIEW v1
{"evidence_sha256":"<64 lowercase hex>","head_sha":"<40 lowercase hex>","pr":66,"repository":"hiroyuki9614/book_library","result":"PASS","review_session":"<bounded independent-session-id>","review_type":"independent-ai","unresolved_high_findings":0}
```

```text
BELIB-SOLO-RELEASE v1
{"decision":"RELEASE","head_sha":"<40 lowercase hex>","pr":66,"repository":"hiroyuki9614/book_library","review_comment_id":<review-comment-id>}
```

The review comment's `review_session` and `evidence_sha256` must refer to the separate fixed-head independent review and its evidence; this task does not create either evidence. To revoke current-head solo evidence, the owner may post an unedited comment using the same identity fields and this exact marker; the CD gate then denies the release:

```text
BELIB-SOLO-REVOKE v1
{"comment_id":<comment-id>,"head_sha":"<40 lowercase hex>","pr":66,"repository":"hiroyuki9614/book_library"}
```

The formal GitHub review record and these comments are different controls: the comments do not manufacture an independent human review, and a GitHub self-approval never satisfies either path.

After merge, the one-time owner bootstrap is run from a clean checkout of reviewed `main` on KAGOYA in the existing `agent` user's owner-native session. That session must expose the same rootless Docker daemon and persistent user systemd manager inspected by the installer. The `ubuntu` CI/supervision user must not impersonate `agent`, access its Docker socket, or install the timer.

```bash
git status --short  # stop if this checkout is not clean; do not discard local work
git fetch origin main
git checkout --detach origin/main
git status --short  # must still be empty
git rev-parse HEAD  # compare with the reviewed main SHA
PRODUCTION_ENV_FILE=/absolute/path/to/existing-production.env
export XDG_RUNTIME_DIR=/run/user/1002
scripts/deploy/install_kagoya_pull_agent.sh "$PRODUCTION_ENV_FILE"
systemctl --user is-active belib-production-cd.timer
systemctl --user list-timers belib-production-cd.timer
```

Run the bootstrap from the existing KAGOYA `agent` owner session (currently UID 1002). The installer validates `/run/user/$UID`, pins `XDG_RUNTIME_DIR` to that path for every `systemctl --user` check, and refuses a conflicting inherited value. The explicit `XDG_RUNTIME_DIR=/run/user/1002` above covers owner sessions whose desktop route leaves it unset.

The bootstrap checks rootless Docker, the running `belib-production-frontend-1` container, and its canonical Compose labels. It accepts exactly `compose.yaml` followed by `.deployment/compose.production.yaml` beneath the labeled project directory. The override must be a regular, non-symlink file owned by `agent`, owner-readable, and inaccessible to group/world; no third or unrelated file is accepted. It resolves the base-only and effective two-file production configs in memory, requires the default active services to be exactly `db`, `backend`, `frontend`, and `nginx`, and resolves the effective config once with `docker compose --profile '*' config` so the `migrate` service can be checked for a non-active profile. The base-only config is checked for supported service/profile shape; it is not used as the protected-service baseline because the owner-approved overlay may define production backend, frontend, nginx, and named-volume settings. The bootstrap freezes a digest of the protected projection from the resolved effective two-file config in the mode-0600 dispatcher config, alongside the protected override path. It does not log the resolved Docker config or environment values.

The currently approved frontend override is a legacy bootstrap shape: it pins an image reference and resolves frontend build args to `VITE_API_BASE_URL` only. Bootstrap accepts that shape only when its image reference exactly matches the running container's configured reference and the rest of the frontend runtime/build boundary is valid. The reference is not treated as source provenance; rollback remains pinned to the inspected immutable image ID. The installed config records `frontend_bootstrap_mode=legacy-override-update-required`. This mode is bootstrap-only: strict exact-SHA Compose validation still fails before image build or frontend switch until the protected override has been updated by its owner.

### Owner-only legacy override transition

Before enabling the dispatcher timer or accepting a release, the authorized owner must approve and update only the existing private `.deployment/compose.production.yaml` frontend pins. From the production project directory, first make a private same-directory backup and confirm it remains owner-only. Because the old production base lacks `VCS_REF` build args, removing only the override's image pin is insufficient: prepare only `services.frontend` so it resolves a dynamic exact-SHA image and exactly four build args—`VCS_REF` set to the reviewed source SHA, the existing protected `VITE_API_BASE_URL`, and `VITE_PUBLIC_DEMO_EMAIL`/`VITE_PUBLIC_DEMO_PASSWORD` values resolved by the dispatcher (blank unless its separately approved private sidecar is valid). Resolve the reviewed source checkout plus the override and run the read-only preflight to prove protected runtime, image, environment, mount, network, and volume equality before applying the private file. Keep the existing production env file as the source of the API URL. Preserve all other override content. Do not edit the base Compose file, env file, backend, DB, R2, networks, volumes, or running containers.

The owner can preserve and restore the protected file with:

```bash
cp -p .deployment/compose.production.yaml .deployment/compose.production.yaml.pre-cd
chmod 600 .deployment/compose.production.yaml.pre-cd
# If post-edit preflight fails:
cp -p .deployment/compose.production.yaml.pre-cd .deployment/compose.production.yaml
chmod 600 .deployment/compose.production.yaml
```

After the edit, run the read-only KAGOYA preflight as the existing `agent` owner from a clean reviewed checkout. The dispatcher must resolve the release checkout plus that same protected override and pass the strict exact-SHA frontend contract and unchanged protected projection before it may build or switch. If any check fails, leave the timer disabled and restore the private backup; do not weaken the validator or substitute the pinned legacy tag as provenance. This owner-only change has not been authorized or performed by this code change. Until the owner approves and completes it, keep production activation blocked.

The dispatcher reuses the protected env file by reference. Each Compose command names the clean release checkout's `compose.yaml` first and the same protected production override second, with `--project-directory` set to the exact-SHA checkout. Compose therefore resolves relative build and mount paths from that checkout while preserving the approved override order. It rechecks the live overlay label and file permissions, the frontend contract, all four active services, and the stored protected-service/network/volume digest before any build or switch. Public-demo values are read only from the agent-owned, mode-0600 JSON sidecar recorded in the dispatcher config; absent opt-in keeps both values blank, and malformed opt-in or failed authorization closes the request without a build or switch. Before a nonblank sidecar is published, `expected_api_base_url` (the injected `VITE_API_BASE_URL`) must be a credential-free HTTPS URL whose origin is exactly the fixed protected origin `https://belib.hiroyuki9614.com`; only the empty or trailing-slash API base path is accepted, so HTTP downgrade, userinfo, query/fragment, arbitrary port, and subdomain confusion fail closed. A present sidecar is then checked through a fresh cookie jar against that fixed origin, proving `/api/v1/me` identity/exact `user` role, the exact Admin API `403`, and the exact public shelf response from `/api/v1/books?limit=100`: HTTP 200, `total=2`, `page=1`, `limit=100`, and unique book IDs exactly `{4,5}`. It does not use a private-book ID or book titles/authors as publication proof. It repeats the complete identity-and-shelf check immediately before switching the built image and rejects any identity or shelf change. The same validated pair is reused for Compose validation, build, and candidate up. Manual no-build rollback never reads the optional sidecar: it restores the saved immutable prior frontend image through `image_override` with a blank public-demo build pair, while retaining the protected Compose runtime, deployment lock, state transition, and verified image readback. It retains the current image by ID and installs a user-scoped five-minute poll timer without changing containers. The generated user service keeps `UMask=0077` and `NoNewPrivileges=true`, but omits `PrivateTmp`, `ProtectSystem`, and `ReadWritePaths` because those mount-namespace options break access to the agent-owned rootless Docker socket. It must be run by the authorized host owner; the `ubuntu` CI/supervision principal must not run it or access the rootless Docker socket.

The sidecar is evaluated for each request, but it is not a live bundle update mechanism. Each successful frontend state records only a non-secret identity marker for the validated same opened sidecar file descriptor, including an explicit absent marker; it never records a password hash or credential-derived value. When the exact source SHA is already running, the dispatcher permits a no-op only when that marker is present and unchanged and the runtime image is verified; it keeps the existing image, performs no frontend build or switch, and does not claim that credentials were changed or erased. A rotated or removed pair, an unsafe sidecar, or a legacy state without a recorded marker fails closed before any build or switch and consumes that request as unsuccessful. Publishing the new pair requires a new valid `main` SHA, the normal release gate, and a newly authorized deployment request; fail-closed handling does not purge already-published static Vite credentials.

The canonical `expected_api_base_url` check applies to every frontend candidate, including the blank-demo path. A missing sidecar still supplies blank public-demo build arguments, but no candidate may inject an API URL whose credential-free origin differs from `https://belib.hiroyuki9614.com`; only an empty or trailing-slash API base path is accepted.

For each accepted request, the dispatcher checks the SHA is still `main`, downloads it into an isolated source directory, and resolves that exact checkout's base Compose file followed by the same protected override. Before any build or switch, it compares the candidate effective protected projection with the digest frozen from the owner-approved effective config. The projection pins the `db`, `backend`, and `nginx` service definitions and images, DB/R2 settings, mounts, networks, and named volumes. Project identity is checked separately; `migrate` is checked only to ensure it remains behind a non-active profile, using `docker compose --profile '*' config` so the profile-only service is present in the resolved output. The frontend has a separate contract: its image and `VCS_REF` use the exact source SHA, its build context and Dockerfile stay inside the isolated frontend checkout, its API URL remains pinned, and it has no mounts or extra capabilities. The dispatcher builds only `frontend` and runs Compose `up --no-deps --no-build frontend`. It verifies the live container's Compose project/service/network, exact source-revision label and image ID, `/login` title, versioned favicon as SVG, `/health`, and anonymous 401 responses for book and admin APIs. It never runs migrations, changes backend/DB/R2/volumes, touches preview or OdoTrack services, or prunes images. Prior frontend image IDs receive retained rollback tags. A failed switch or smoke restores only that saved frontend image and verifies its ID and smoke.

After a merge, verify the `Release candidate gates` workflow succeeded on the exact main merge SHA with all four jobs green. Then verify the `Production frontend CD handoff` workflow succeeded and its `belib-deploy-request-<run-id>-<attempt>` tag points to that same SHA. Allow the timer up to five minutes to poll. In the existing `agent` session, inspect only the dispatcher result fields in `~/.local/state/belib-production-cd/state.json` and the service log:

```bash
python3 - <<'PY'
import json
from pathlib import Path
state = json.loads(Path.home().joinpath('.local/state/belib-production-cd/state.json').read_text())
print({key: state.get(key) for key in ('last_request', 'last_result', 'current_sha')})
print({'current_image_id': (state.get('current') or {}).get('image_id')})
PY
journalctl --user -u belib-production-cd.service --since '15 minutes ago' --no-pager
docker inspect --format '{{.Image}}' belib-production-frontend-1
```

Require the request tuple to match the handoff run ID/attempt, `last_result` to be `success`, `current_sha` to equal the merge SHA, and the live image ID to equal `current_image_id`. Confirm public `/login`, the linked versioned favicon as `image/svg+xml`, `/health` 200, and anonymous book/admin API 401 using the approved public origin. Do not print or record production environment values. `CICD_ACCEPTED` remains `INCOMPLETE` until this exact request consumption and read-back are complete.

For an owner-authorized manual rollback, first confirm that the live frontend image ID still matches `current.image_id` in dispatcher state and `previous.image_id` is the intended retained image. Run as the existing KAGOYA `agent` user; the dispatcher takes the same lock as automatic deployments before reading state, refuses a changed live image or missing saved rollback, restores only the frontend image, then verifies its ID and smoke. A manual rollback waits for any active deployment to finish before checking the resulting state. After rollback, read back the restored image ID and smoke. If an automatic deployment or manual rollback fails after a switch, the dispatcher records the observed live image; if it cannot read the live image, it clears the recorded current image, marks the runtime unknown, and stops automatic deployments until the owner reconciles it. To request the last saved frontend-only rollback manually:

```bash
python3 ~/.local/libexec/belib-production-cd/kagoya_pull_agent.py \\
  --rollback --config ~/.config/belib-production-cd/config.json
```

`CICD_ACCEPTED` remains `INCOMPLETE` until a GitHub Actions-triggered request is consumed by the authorized KAGOYA timer and the exact running image plus public smoke are read back. Demo login/permissions and authenticated EPUB/PDF acceptance are reported separately when the dedicated non-admin account is verified.
