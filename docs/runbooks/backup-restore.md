# Backup and restore runbook

## Required protection boundary

The recovery boundary depends on the active storage driver. With `local`, PostgreSQL records and the protected files under `BOOK_FILE_STORAGE_ROOT` are one recovery unit. With `r2`, the MVP does not create a separate R2 backup, but restore acceptance must verify that every restored `BookFile.fileUrl` still resolves to the expected private R2 object. Do not package, clean, replace, reconcile, recover, logically delete, or permanently delete book files as part of release work.

## Public portfolio demo gate

The owner's 2026-10-26 MVP scope supersedes the former portfolio-only requirement for encrypted offsite copies, paired DB/R2 backups, an isolated restore drill and independent backup acceptance. These remain [roadmap](../roadmap.md) work. Existing R2 objects must remain untouched; this exception does not cover storage migration, replacement or deletion, or local-storage deployments.

### Required R2 MVP procedure

1. Identify the approved existing private backup directory and authorized recovery operator. If either is unknown, stop production writes. Do not create an external destination or copy personal data to a workstation, GitHub, or Drive without authorization.
2. Freeze book/file/permission writes. Privately retain the original personal-book IDs, DB/storage identities and reviewed snapshot hash. Verify existing R2 references, size/hash and private delivery read-only.
3. Through the approved operator lane, set `umask 077` and use the running PostgreSQL tooling to create a fresh `pg_dump --format=custom` artifact in the approved directory. Keep credentials out of argv/logs; use the existing configured connection. Never overwrite an earlier dump. Check command exit status before accepting the artifact.
4. Verify ownership, directory/file modes (0700/0600 or stricter), nonzero byte size, SHA-256 and read permission. Run `pg_restore --list` and retain only success/count evidence in public records; its output can expose schema details and stays private. Listing is a format/readability check, **not a completed restore drill**.
5. Record tool version, start/end time, private artifact reference, checksum and the authorized recovery procedure. Fill `portfolio-mvp-db-v1` attestation only from these observations. Public evidence contains verification results, not credentials, private IDs, titles, keys or backup contents.
6. Revalidate the same inventory before the permission transaction. Keep admin access and all books/files/reading records. Never regrant general-user permissions as rollback.

### Recovery procedure (execute only with recovery approval)

Close public ingress and disable demo access first. Preserve the failed state for diagnosis. The authorized operator restores the verified custom dump with compatible PostgreSQL tools into an approved separate private database; do not use `--clean` against the live database or overwrite its volume. Keep application startup and public traffic blocked. Confirm schema, counts and each R2 reference without altering objects. Apply and verify admin-only protection for the **original frozen personal-book IDs** before any application can serve users; an old dump may contain user permissions and active sessions. Invalidate shared sessions and allow prior signed URLs to expire before reopening. Verify admin reads and general-user denials, then obtain target-specific approval for the DB cutover. If a private recovery target or authority is unavailable, remain closed and request it; never restore pre-demo permissions into a public service.

## Broader backup and restore roadmap

The following coordinated procedure is retained for local-storage recovery and future operational hardening. It is not an additional R2 portfolio publication gate.

## Coordinated backup

1. Record the database identity, application commit/image digest, active storage driver, storage identity (local root or R2 bucket), inventory count, and backup owner.
2. Establish a quiescence window: stop writes and book registration, or obtain an approved consistency boundary that covers PostgreSQL and storage references. Record the boundary time and request/response state.
3. Create an encrypted PostgreSQL backup using the operator-approved PostgreSQL tooling and retain its tool/version, format, start/end time, and checksum.
4. For `local`, capture the protected-file tree without following paths outside `BOOK_FILE_STORAGE_ROOT`, including file key, size, metadata, and SHA-256 manifest. For `r2`, capture a read-only inventory of the DB-referenced R2 keys and verify object existence/size/hash evidence where available; do not create an independent R2 backup unless separately approved.
5. Verify the database backup can be read/listed and that the storage evidence corresponds to the same consistency boundary. For `local`, verify archive checksums. For `r2`, verify the referenced objects still exist and are private.
6. Reopen writes only after required artifacts and evidence are durable.

Backups must use approved encryption at rest and in transit. Restrict access to named operators and service identities using least privilege; never place passwords, auth secrets, or connection strings in this repository or in a runbook artifact. Define operator-supplied retention, legal hold, and disposal policy when adopting this broader procedure, and maintain an inventory of artifact locations, owners, expiration, and checksum.

## Isolated restore drill

Restore into isolated resources with isolated credentials and no public exposure. For `local`, restore PostgreSQL and the protected-file tree into a non-shared `BOOK_FILE_STORAGE_ROOT`, verify the manifest/checksums, and confirm DB mappings. For `r2`, restore PostgreSQL to an isolated application/database while using an approved private R2 verification target or the production bucket only through read-only verification; confirm each restored `BookFile.fileUrl` maps to an existing expected object before smoke. Do not mutate or delete R2 objects during a restore drill.

Start the candidate application against the isolated pair only after integrity checks. The restore is accepted only when smoke proves database records and an authorized protected-file stream, including the evidenced `/api/v1/books/:bookId/file` route, work together. A successful database restore without matching storage-object evidence is not recovery evidence.

Record isolated resource identifiers, backup IDs, checksums, restore logs, schema version, smoke request outcomes, and approver. Do not use real books or credentials in synthetic drills. No recovery claim is permitted until the database, active-storage evidence, and application smoke are accepted.