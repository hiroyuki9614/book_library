# Public demo login accounts — 2026-11-04

## Scope and authority

The owner requested administrator/user ID/PW on the BeLib login page, then explicitly requested creation and publication of a **new administrator**. Use that newly provisioned identity. It has the existing full administrator role; a new account does not isolate books or reduce permissions. Preserve existing credentials, users, books, R2 and runtime data. No old credential recovery/reset, storage mutation, migration, privilege transport, new timer or shared-runtime restart is authorized by this task.

Base frontend is main `1f41e1feafd0ae7aa333f4c2b676c563f00fc764`; existing PR #83 owns this task. PR #75 merged concurrently at main `779acaa1cbdaaed1397abab14e4796a9d2eb7bb8`. Its general-only sidecar validation and tests are preserved. The new two-role mode is explicit and mutually exclusive with the old configuration key; it uses newly generated public identities and does not recover any prior candidate credential.

## Verified account provisioning

Owner-native KAGOYA `agent` UID 1002 was confirmed through the permitted fallback after PV3 MCP operational calls repeatedly returned initialization-required before execution. The independent November retry-gate job `126491053736` reported `retry_allowed=true`; no later block was present in the current operator inbox.

Before account writes, the backend image/revision and DB image were pinned. Backend Prisma and PostgreSQL returned matching current-database/cluster identities; only a fingerprint was retained. A new custom dump was created in the existing owner-only BeLib backup root, nonzero size/SHA-256 and `pg_restore --list` verified. This is backup readability evidence, not a restore drill.

- New administrator: user ID **4**, role `admin`, created via existing same-backend `createInitialAdmin`; actual login and same-ID/email/role verified; Admin books API **200**; verification session signed out.
- New general demo user: user ID **5**, role `user`, created via protected Admin API using only the newly created administrator; actual login and same-ID/email/role verified; Admin books API **403**, book shelf API **200**; verification sessions signed out.
- New generated passwords remain only in mode-0600 owner files; no existing credential was retrieved or changed. Account-result receipts and backup evidence remain private.
- Independent fixed-script review identified backup-target binding, optimized-away asserts and timeouts; all were corrected and the corrected admin script independently passed before execution. Reader creation script independently passed before execution.

## Release verification

The login cards and autofill had 8 focused Chromium tests plus frontend build/lint PASS before the CD extension. Default CD remains blank. The new optional CD path verifies the same new account pairs and active roles before build and switch, then binds the public configuration fingerprint to the image. Required source checks, independent exact-head review, protected merge, main CI and genuine Actions handoff remain in force.

**Status: accounts created and verified; frontend publication pending.** Do not report the login credentials visible until the exact live frontend/image/config and browser rendering have been checked.

Measurement decision: existing account-result receipts, source CI and CD state/image readback provide the task outcomes; no new collector/capability is needed. Capability-catalog update is not applicable to this application-only change. Full release closeout must update this same record.
