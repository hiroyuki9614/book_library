# Rollback runbook

## Decision boundary

Rollback is a controlled release action, not a promise that an older binary can read a newer schema. Record the failed candidate commit, image digest, migration state, observed symptoms, owner, and stop time. Preserve logs and both backup artifacts.

A rollback to a previously recorded image digest is allowed only after schema compatibility is established for that exact digest and the current database state. Use recorded digests only; do not substitute a mutable tag, rebuild an unrecorded image, or guess a digest. If compatibility is not established, stop and choose an approved forward fix or a coordinated restore of both PostgreSQL and the protected `BOOK_FILE_STORAGE_ROOT` contents in isolated resources.

## Procedure

1. Stop promotion and prevent further writes when the incident owner determines that continued writes could widen divergence.
2. Confirm the candidate digest, prior approved digest, migration history, and compatibility decision. Confirm the database and protected-file backups share a consistency boundary.
3. If compatible, deploy the previously recorded digest through the operator-controlled process, without changing public origins or credentials in this runbook. Verify `/health`, the authentication boundary, UI/API reachability, authorized record access, and authorized protected-file streaming.
4. If incompatible, do not run an ad hoc down migration. Obtain approval for a forward fix, or restore both stores together into isolated resources, validate them, and obtain explicit recovery approval before any environment action.
5. Record the decision, commands executed by the operator, image digest, migration state, backup/checksum evidence, smoke evidence, approvers, and remaining risks.

## Prohibited shortcuts

Do not reset the database, use `prisma db push`, invent a down migration, restore only PostgreSQL, delete files to make records fit, replace files, perform R2 cleanup, or claim recovery from a database-only result. Logical/permanent deletion and storage reconciliation remain outside release packaging.

A failed frontend or backend gate, missing publication credentials, unverified browser/device behavior, absent restore drills, or incomplete formal MVP requirements preserves Draft status and blocks rollback-based release approval.