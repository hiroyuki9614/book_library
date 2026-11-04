# BeLib rootless Docker/OCI incident preflight

This is an offline, read-only snapshot validator. It does not inspect or control the host, Docker, OCI runtime, processes, network, or containers. It never authorizes repair. Snapshot fields are operator-supplied claims and are not authenticated receipts.

## Preservation boundary

Keep the named PostgreSQL volume `belib-production_postgres-data`, protected file storage, R2 objects, retained images, and rollback identities untouched. This tool does not run Docker, Compose, `runc`, systemd, shell commands, or process inspection. Do not use it to justify container recreation, database access, migration, volume changes, cleanup, service restart, or reboot. A shared runtime issue requires a separate owner decision and impact review.

Do not supply arbitrary service names, container IDs, host paths, commands, environment values, tokens, private logs, book titles, object keys, database contents, or credentials. The parser accepts only a fixed BeLib production identity and a strict JSON schema.

## Offline snapshot check

Run:

```sh
python3 scripts/deploy/belib_runtime_preflight.py --evidence SANITIZED.json --expected-head REVIEWED_BOOK_LIBRARY_MAIN_SHA
python3 -m unittest discover -s scripts/deploy -p 'test_belib_runtime_preflight.py' -v
```

The input must contain schema version 2 and the source commit/document identity, rootless runtime state, a closed runtime error class, shared-project impact inventory, namespace status, old database process access status, exactly four fixed BeLib services, and both fixed named volumes. Each service has separately observed Docker, task, and runc state fields that must agree, plus a unique container ID, fixed project/service identity, health, restart policy, expected and observed immutable image IDs, network, and volume attachments. Both postgres-data and book-files volumes must exist with matching expected and observed 64-character identities. Expected IDs and identities are claims in the same untrusted snapshot; comparison catches internal mismatch but authenticates neither expected values nor the observer. Extra fields are rejected. Output contains only allowlisted reason codes and never reflects input values. The stable outcome codes provide an observable pass/block measurement for each supplied snapshot; they are not a historical metric, CI coverage gate, authenticated receipt, or live-health signal.

Exit 0 means only READY_FOR_OWNER_REVIEW; exit 10 is NORMAL_CONTAINER_STOPPED; exit 11 is SERVICE_UNHEALTHY; exit 20 is SHARED_RUNTIME_RECOVERY_REQUIRED; exit 30 is PREFLIGHT_EVIDENCE_INVALID. A consistent ordinary stopped container or unhealthy service is distinguished from shared-runtime conflicts. Duplicate IDs or runtime duplicate-ID events, Docker/task/runc contradictions, namespace errors, unknown old database process access, image or volume identity mismatch, incomplete shared-project inventory, or another project’s impact require the shared-runtime result. Invalid input receives its own fail-closed result. Every result reports authorization=NONE, mutation_attempted=false, and safe_to_repair=false. A self-reported confirmed_absent database-process value is not independent verification; if that state is unknown, the result is blocked. No result establishes live recovery or authorizes action.

The checked-in base Compose file defines a PostgreSQL healthcheck. The effective production override may add health behavior (the backend currently has an effective production healthcheck), so assess the reviewed effective service observations rather than inferring runtime health from the base file alone. Backend/frontend/nginx changes to the protected Compose contract require separate review. This preflight is intentionally not wired into the CD dispatcher, and it does not change deployment behavior or protected Compose contracts.

## Incident recovery acceptance

Live recovery remains a separate owner-run operational task. It must use the approved operational channel, preserve the named database volume and protected storage, independently establish old database process access state, check all four services and application routes, verify the existing CD lock and cross-project impact, and retain rollback identities. An offline snapshot is not a substitute for those receipts.
