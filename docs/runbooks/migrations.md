# Migration runbook

## Scope and release state

This runbook is for a Draft review candidate only. It does not approve publication, deployment, or a live migration. The operator must supply the target environment, approved `DATABASE_URL`, and maintenance owner; no Production hostname or credential is defined here.

Migrations are forward-only and must be reviewed against the deployed application, Prisma schema, and existing data before release. A migration is never an opportunity to implement R2 cleanup, file replacement, logical deletion, permanent deletion, recovery, or storage reconciliation.

## Preflight

1. Record the candidate commit, application image digest, schema revision, migration list, and operator/approver.
2. Confirm the target database and active storage identity are the intended pair (`BOOK_FILE_STORAGE_ROOT` for local or the private R2 bucket for r2) and that current backup/restore evidence exists.
3. Review the migration for lock duration, indexes, nullable-to-required transitions, destructive operations, and application compatibility during a rolling or delayed restart.
4. Run the repository migration verification and the dedicated file-hash verification gate in an isolated environment. Do not represent unobserved gates as passed.
5. Confirm the application version can run with the pre-migration schema and that the post-migration version can run with the resulting schema.

## fileHash backfill hold

`BookFile.fileHash` is unique and non-null in the current Prisma schema. A non-empty database must be placed on hold unless an approved, deterministic backfill plan exists for every existing protected file and its corresponding row. The plan must read each authorized file from the active storage driver, calculate the expected SHA-256 hash, verify the file mapping and size, detect duplicates, and record exceptions for human review.

Do not make a production `fileHash` backfill automatic merely because the schema migration exists. If any row has a missing, unreadable, mismatched, duplicate, or otherwise unverified file, stop. The exact hold is: **fileHash backfill hold: non-empty database requires verified file inventory and an approved backfill before migration; no automatic production migration.**

An empty disposable database may be used for schema and migration verification, but that does not clear the non-empty-database hold.

## Execution and stop conditions

After backup evidence, compatibility approval, and a written go decision, apply the reviewed migration once using the repository's approved migration mechanism. Monitor locks, errors, and application health. Stop before any migration if backup evidence, compatibility review, fileHash evidence, required checks, or ownership is missing.

Never use `prisma db push`, reset commands, ad hoc down migrations, or an automatic production migration. Do not edit applied migration history to force compatibility. If the migration fails, preserve logs and the database state for the incident owner; do not improvise a reverse migration.

## Evidence

Attach the migration name and checksum, preflight review, database backup identifier/checksum, active-storage inventory/checksum evidence, fileHash verification output, start/end times, operator, approver, and smoke results. A failed frontend or backend gate preserves Draft status; it does not authorize migration.