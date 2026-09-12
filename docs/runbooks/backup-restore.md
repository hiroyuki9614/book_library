# Backup and restore runbook

## Required protection boundary

The recovery boundary depends on the active storage driver. With `local`, PostgreSQL records and the protected files under `BOOK_FILE_STORAGE_ROOT` are one recovery unit. With `r2`, the MVP does not create a separate R2 backup, but restore acceptance must verify that every restored `BookFile.fileUrl` still resolves to the expected private R2 object. Do not package, clean, replace, reconcile, recover, logically delete, or permanently delete book files as part of release work.

## Coordinated backup

1. Record the database identity, application commit/image digest, active storage driver, storage identity (local root or R2 bucket), inventory count, and backup owner.
2. Establish a quiescence window: stop writes and book registration, or obtain an approved consistency boundary that covers PostgreSQL and storage references. Record the boundary time and request/response state.
3. Create an encrypted PostgreSQL backup using the operator-approved PostgreSQL tooling and retain its tool/version, format, start/end time, and checksum.
4. For `local`, capture the protected-file tree without following paths outside `BOOK_FILE_STORAGE_ROOT`, including file key, size, metadata, and SHA-256 manifest. For `r2`, capture a read-only inventory of the DB-referenced R2 keys and verify object existence/size/hash evidence where available; do not create an independent R2 backup unless separately approved.
5. Verify the database backup can be read/listed and that the storage evidence corresponds to the same consistency boundary. For `local`, verify archive checksums. For `r2`, verify the referenced objects still exist and are private.
6. Reopen writes only after required artifacts and evidence are durable.

Backups must use approved encryption at rest and in transit. Restrict access to named operators and service identities using least privilege; never place passwords, auth secrets, or connection strings in this repository or in a runbook artifact. Define operator-supplied retention, legal hold, and disposal policy before release, and maintain an inventory of artifact locations, owners, expiration, and checksum.

## Isolated restore drill

Restore into isolated resources with isolated credentials and no public exposure. For `local`, restore PostgreSQL and the protected-file tree into a non-shared `BOOK_FILE_STORAGE_ROOT`, verify the manifest/checksums, and confirm DB mappings. For `r2`, restore PostgreSQL to an isolated application/database while using an approved private R2 verification target or the production bucket only through read-only verification; confirm each restored `BookFile.fileUrl` maps to an existing expected object before smoke. Do not mutate or delete R2 objects during a restore drill.

Start the candidate application against the isolated pair only after integrity checks. The restore is accepted only when smoke proves database records and an authorized protected-file stream, including the evidenced `/api/v1/books/:bookId/file` route, work together. A successful database restore without matching storage-object evidence is not recovery evidence.

Record isolated resource identifiers, backup IDs, checksums, restore logs, schema version, smoke request outcomes, and approver. Do not use real books or credentials in synthetic drills. No recovery claim is permitted until the database, active-storage evidence, and application smoke are accepted.