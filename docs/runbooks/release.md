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

`.github/workflows/release-ci.yml` runs the existing release gates for pull requests and again on each `main` push, so both the reviewed PR head and merged main SHA have exact backend, frontend, reader E2E, and Compose smoke results. `.github/workflows/production-frontend-cd.yml` runs after that main run succeeds. It checks that the SHA is still current `main`, identifies its merged PR, requires a non-author approval on the exact PR head, and checks all four release jobs on both the PR head and main merge SHA. It then creates a lightweight `belib-deploy-request-<run-id>-<attempt>` tag pointing to that immutable SHA. Manual replay is limited to the current `main` SHA and uses the same checks. Workflow runs are serialized; stale SHAs are rejected.

The repository is public, so the owner-side pull dispatcher reads the handoff tag and release evidence through unauthenticated GitHub HTTPS. It needs no GitHub token, SSH key, registry credential, or Actions secret. The Actions workflow uses only its short-lived `GITHUB_TOKEN` to read Actions/PR data and create the handoff tag. The host independently revalidates the exact main SHA, merged PR review, and mandatory jobs before it downloads source.

At the time this candidate was prepared, GitHub reported no branch-protection rule for `main`, no self-hosted Actions runner, no protected environment, and no Actions secrets. The CD workflow still refuses direct pushes without a merged, exactly approved PR and green release gates. Keep this PR in Draft; do not merge it until the owner has enabled the required `main` review and status-check protection.

After merge, the one-time owner bootstrap is run from a clean checkout of reviewed `main` on KAGOYA as the existing `agent` account, using the existing protected production Compose env-file path:

```bash
scripts/deploy/install_kagoya_pull_agent.sh /absolute/path/to/existing-production.env
```

The bootstrap checks rootless Docker, the running `belib-production-frontend-1` container, its actual Compose project/service/config/network labels, the protected env-file permissions, the existing immutable image ID, and a persistent `agent` user systemd manager. It refuses unknown or multi-file Compose identities and refuses to duplicate an existing BeLib deploy timer. It retains the current image by ID, installs a user-scoped five-minute poll timer, and makes no container change. The frontend build explicitly clears both public-demo build arguments until a dedicated non-admin account is verified, so a password from the host env file is never published by CD. It must be run by the authorized host owner; the `ubuntu` CI/supervision principal must not run it or access the rootless Docker socket.

For each accepted request, the dispatcher checks the SHA is still `main`, downloads it into an isolated source directory, validates the resolved frontend Compose service against the approved no-mount/no-extra-capability contract, builds only `frontend`, and runs Compose `up --no-deps --no-build frontend`. It rejects frontend volumes, host devices, privilege/capability changes, altered networks, extra services, and nonstandard build contexts before any image build or switch. It verifies the live container's Compose project/service/network, exact source-revision label and image ID, `/login` title, versioned favicon as SVG, `/health`, and anonymous 401 responses for book and admin APIs. It never runs migrations, changes backend/DB/R2/volumes, touches preview or OdoTrack services, or prunes images. Prior frontend image IDs receive retained rollback tags. A failed switch or smoke restores only that saved frontend image and verifies its ID and smoke. To request the last saved frontend-only rollback manually:

```bash
python3 ~/.local/libexec/belib-production-cd/kagoya_pull_agent.py \\
  --rollback --config ~/.config/belib-production-cd/config.json
```

`CICD_ACCEPTED` remains `INCOMPLETE` until a GitHub Actions-triggered request is consumed by the authorized KAGOYA timer and the exact running image plus public smoke are read back. Demo login/permissions and authenticated EPUB/PDF acceptance are reported separately when the dedicated non-admin account is verified.
