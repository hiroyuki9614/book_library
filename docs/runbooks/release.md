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

`.github/workflows/release-ci.yml` runs the existing release gates for pull requests and again on each `main` push, so both the reviewed PR head and merged main SHA have exact backend, frontend, reader E2E, and Compose smoke results. `.github/workflows/production-frontend-cd.yml` runs after that main run succeeds. It checks that the SHA is still current `main`, identifies its merged PR, requires an approval from a non-author repository collaborator/owner on the exact PR head, rejects a latest unresolved collaborator/owner changes request, and checks all four release jobs on both the PR head and main merge SHA. It then creates a lightweight `belib-deploy-request-<run-id>-<attempt>` tag pointing to that immutable SHA. Manual replay is limited to the current `main` SHA and uses the same checks. Workflow runs are serialized; stale SHAs are rejected.

The repository is public, so the owner-side pull dispatcher reads the handoff tag and release evidence through unauthenticated GitHub HTTPS. It needs no GitHub token, SSH key, registry credential, or Actions secret. The Actions workflow uses only its short-lived `GITHUB_TOKEN` to read Actions/PR data and create the handoff tag. The host independently revalidates the exact main SHA, merged PR review, and mandatory jobs before it downloads source.

### One-time owner activation

The sole-maintainer state cannot satisfy the release rule: a PR author cannot approve their own change, and a public user's approval is not an eligible substitute. The release gate accepts only `OWNER`/`COLLABORATOR` review associations; `main` branch protection must separately require an approving reviewer with write access. Before activation, confirm a distinct reviewer and an enforced `main` protection rule exist. Keep the PR in Draft and do not merge it until one of these owner decisions is complete:

1. **Keep the independent-review policy (minimum activation path):** invite one trusted person other than the PR author as a repository collaborator with **Write** permission. They review the exact PR head and approve only after inspection. Configure `main` protection to require one approval, dismiss stale approvals, require the four checks below, require the branch to be up to date, enforce the rule for administrators, and disallow force pushes and branch deletion. The authorized reviewer must be able to approve every future release PR.
2. **Solo-maintainer policy:** if no separate reviewer can be appointed, the owner must explicitly authorize a replacement policy in writing. Review and implement that policy as a separate focused change, including its release-gate behavior and tests, before changing branch protection or merging. Do not treat self-approval, an admin bypass, or an external public approval as an exception.

Configure the classic `main` branch protection rule in **Settings → Branches** (branch pattern `main`) with these exact check names from `.github/workflows/release-ci.yml`:

- `Backend tests, build, Prisma, and migrations`
- `Frontend tests, lint, and build`
- `Reader Playwright E2E`
- `Production images, Compose config, and isolated smoke`

Also select **Require a pull request before merging**, **1 required approval**, **Dismiss stale pull request approvals when new commits are pushed**, **Require branches to be up to date before merging**, and apply enforcement to administrators. Leave force pushes and deletion disabled. After saving, verify the rule through **Settings → Branches** or `gh api repos/hiroyuki9614/book_library/branches/main/protection`; do not proceed if the API still returns 404 or any required context/setting is missing.

For each release PR, keep it Draft until an authorized independent reviewer has reviewed and approved the exact head SHA, the latest collaborator/owner review history has no unresolved `CHANGES_REQUESTED`, and all four exact-head checks succeed. The Actions CD gate and KAGOYA dispatcher then independently require the merged PR head approval plus green checks for both the PR head and exact `main` merge SHA. The handoff is a tag named `belib-deploy-request-<run-id>-<attempt>` that points to that main SHA. A green PR run alone is not deployment evidence.

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

The dispatcher reuses the protected env file by reference. Each Compose command names the clean release checkout's `compose.yaml` first and the same protected production override second, with `--project-directory` set to the exact-SHA checkout. Compose therefore resolves relative build and mount paths from that checkout while preserving the approved override order. It rechecks the live overlay label and file permissions, the frontend contract, all four active services, and the stored protected-service/network/volume digest before any build or switch. The frontend build explicitly clears both public-demo build arguments until a dedicated non-admin account is verified, so a password from the host env file is never published by CD. It retains the current image by ID and installs a user-scoped five-minute poll timer without changing containers. It must be run by the authorized host owner; the `ubuntu` CI/supervision principal must not run it or access the rootless Docker socket.

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
