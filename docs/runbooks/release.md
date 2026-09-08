# Release control runbook

## Status vocabulary

- **Draft review:** the candidate and evidence are available for review; this is the current default state and is not formal MVP completion.
- **Artifact publication eligibility:** required deterministic gates passed and the immutable image/artifact evidence is complete. This does not grant registry credentials or publish an artifact.
- **Deployment approval:** named authority has reviewed the candidate, migration decision, backup/restore evidence, smoke evidence, and stop conditions for a specific target. This is separate from publication eligibility.
- **Live deployment:** an operator has changed a live environment. CI performs no live deployment and this runbook does not perform one.

## Release evidence record

For each candidate, record:

- commit identifier and repository state;
- backend and frontend required-check results, including tests, builds, lint, Prisma validation, and migration verification;
- image/artifact checksums and registry digests, if an operator has published them;
- lockfile identity and the selected package scripts (`npm test`, backend build/test, frontend build/test/lint) used by the gates;
- migration decision, compatibility review, fileHash backfill decision, and any hold;
- coordinated PostgreSQL/protected-file backup IDs, consistency boundary, encryption/access review, checksums, and latest isolated restore drill;
- smoke route evidence, synthetic-data identifier, browser/device status, approvers, and stop/go decision;
- explicit statement that CI performed no live deployment.

Do not place credentials, public Production hostnames, or secret values in this record. Public origin and `VITE_API_BASE_URL` remain operator-supplied.

## Stop/go controls

Stop and preserve Draft status when any frontend or backend gate fails, publication credentials are missing, an artifact checksum or immutable digest is absent, browser/device behavior is unverified, an isolated restore drill is absent, migration compatibility is unclear, the fileHash hold is unresolved, required approvers are absent, or formal MVP requirements are incomplete. Exact status: **Draft status is preserved: failed frontend/backend gates, missing publication credentials, unverified browser/device behavior, absent restore drills, or incomplete formal MVP requirements block release approval.**

Go requires named ownership for release engineering, migration, backup/restore, application verification, and target approval, plus complete evidence and an explicit confirmation that no live deployment is performed by CI. R2 cleanup, file replacement, logical deletion, permanent deletion, recovery, and storage reconciliation remain outside release packaging.