# Backup and restore runbook

## Required protection boundary

PostgreSQL records and the protected files under `BOOK_FILE_STORAGE_ROOT` are one recovery unit. A database-only backup is insufficient because authorized streaming uses the database file record and the protected local file. Do not package, clean, replace, reconcile, recover, logically delete, or permanently delete book files as part of release work. R2 cleanup remains outside release packaging.

## Coordinated backup

1. Record the database identity, application commit/image digest, storage root identity, inventory count, and backup owner.
2. Establish a quiescence window: stop writes and book registration, or obtain an approved consistency boundary that covers both PostgreSQL and the protected-file tree. Record the boundary time and the request/response state. Do not rely on two unrelated backup timestamps.
3. Create an encrypted PostgreSQL backup using the operator-approved PostgreSQL tooling and retain its tool/version, format, start/end time, and checksum.
4. Capture the protected-file tree without following paths outside `BOOK_FILE_STORAGE_ROOT`. Inventory each file key, size, modification metadata, and SHA-256 checksum, and retain the manifest separately from the data backup.
5. Verify the database backup can be read/listed, the protected-file archive is complete, checksums match the source inventory, and the two artifacts carry the same consistency-boundary identifier.
6. Reopen writes only after both artifacts and evidence are durable.

Backups must use approved encryption at rest and in transit. Restrict access to named operators and service identities using least privilege; never place passwords, auth secrets, or connection strings in this repository or in a runbook artifact. Define operator-supplied retention, legal hold, and disposal policy before release, and maintain an inventory of artifact locations, owners, expiration, and checksum.

## Isolated restore drill

Restore both stores into isolated resources with isolated credentials, a non-shared `BOOK_FILE_STORAGE_ROOT`, and no public exposure. Never restore over a live or shared resource. Restore PostgreSQL, restore the protected-file tree, verify the manifest and SHA-256 checksums, and confirm database `BookFile` records map to the expected protected files and sizes.

Start the candidate application against the isolated pair only after integrity checks. The restore is accepted only when smoke proves database records and an authorized protected-file stream, including the evidenced `/api/v1/books/:bookId/file` route, work together. A successful database restore without the matching file store is not recovery evidence.

Record isolated resource identifiers, backup IDs, checksums, restore logs, schema version, smoke request outcomes, and approver. Do not use real books or credentials in synthetic drills. No recovery claim is permitted until both stores and the application smoke are accepted.