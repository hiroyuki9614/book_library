# BeLib rootless Docker/OCI incident preflight

This is an offline, read-only snapshot validator. It does not inspect or control the host, Docker, OCI runtime, processes, network, or containers. It never authorizes repair. Snapshot fields are operator-supplied claims and are not authenticated receipts.

## Preservation boundary

Keep the named PostgreSQL volume `belib-production_postgres-data`, protected file storage, R2 objects, retained images, and rollback identities untouched. This tool does not run Docker, Compose, `runc`, systemd, shell commands, or process inspection. Do not use it to justify container recreation, database access, migration, volume changes, cleanup, service restart, or reboot. A shared runtime issue requires a separate owner decision and impact review.

Do not supply arbitrary service names, container IDs, host paths, commands, environment values, tokens, private logs, book titles, object keys, database contents, or credentials. The parser accepts only a fixed BeLib production identity and a strict JSON schema.

## Offline snapshot check

Run:

```sh
cat SANITIZED.json | python3 scripts/deploy/belib_runtime_preflight.py --expected-head REVIEWED_BOOK_LIBRARY_MAIN_SHA
python3 -m unittest discover -s scripts/deploy -p 'test_belib_runtime_preflight.py' -v
```

The validator accepts bounded JSON on standard input and no file-path argument. The input must contain schema version 2 and the source commit/document identity, rootless runtime state, a closed runtime error class, shared-project impact inventory, namespace status, old database process access status, exactly four fixed BeLib services, and both fixed named volumes. Each service has separately observed Docker, task, and runc state fields that must agree, plus a unique container ID, fixed project/service identity, health, restart policy, expected and observed immutable image IDs, network, and volume attachments. Both postgres-data and book-files volumes must exist with matching expected and observed 64-character identities. The fixed storage driver is either local (backend attaches book-files) or r2 (backend attaches no named volume); no other mount set is accepted. Docker exited with task/runc stopped or absent is treated as a consistent normal stop. Running states must agree across all planes, and any present task/runc container IDs must match Docker’s container ID; an exited Docker state paired with a running task or runc is a shared-runtime conflict. Expected IDs and identities are claims in the same untrusted snapshot; comparison catches internal mismatch but authenticates neither expected values nor the observer. Extra fields are rejected. Output contains only allowlisted reason codes and never reflects input values. The stable outcome codes provide an observable pass/block measurement for each supplied snapshot; they are not a historical metric, CI coverage gate, authenticated receipt, or live-health signal.

Exit 0 means only READY_FOR_OWNER_REVIEW; exit 10 is NORMAL_CONTAINER_STOPPED; exit 11 is SERVICE_UNHEALTHY; exit 20 is SHARED_RUNTIME_RECOVERY_REQUIRED; exit 30 is PREFLIGHT_EVIDENCE_INVALID. A consistent ordinary stopped container or unhealthy service is distinguished from shared-runtime conflicts. Duplicate IDs or runtime duplicate-ID events, Docker/task/runc contradictions, namespace errors, unknown old database process access, image or volume identity mismatch, incomplete shared-project inventory, or another project’s impact require the shared-runtime result. Invalid input receives its own fail-closed result. Every result reports authorization=NONE, mutation_attempted=false, and safe_to_repair=false. A self-reported confirmed_absent database-process value is not independent verification; if that state is unknown, the result is blocked. No result establishes live recovery or authorizes action.

The checked-in base Compose file defines PostgreSQL, frontend, and nginx healthchecks. Frontend and nginx use bounded loopback HTTP probes; their Alpine runtime images provide BusyBox `wget`. A failed healthcheck marks a container unhealthy but does not itself trigger the `unless-stopped` restart policy; the probes also leave nginx's `service_started` dependency conditions unchanged. The effective production override may add health behavior (the backend currently has an effective production healthcheck), so assess the reviewed effective service observations rather than inferring runtime health from the base file alone. Backend/frontend/nginx changes to the protected Compose contract require separate review. This preflight is intentionally not wired into the CD dispatcher, and it does not change deployment behavior or protected Compose contracts.

## Incident recovery acceptance

Live recovery remains a separate owner-run operational task. It must use the approved operational channel, preserve the named database volume and protected storage, independently establish old database process access state, check all four services and application routes, verify the existing CD lock and cross-project impact, and retain rollback identities. An offline snapshot is not a substitute for those receipts.

## Disposable CI recovery rehearsal

The production-compose job runs an opt-in recovery rehearsal on its dedicated GitHub-hosted Ubuntu VM after the candidate images are built. The existing smoke script keeps its normal local smoke behavior; the destructive rehearsal branch runs only when the workflow supplies its fixed opt-in value. It refuses to start unless the runner identifies as GitHub-hosted, the run identity is numeric, no containers are already running, no production project containers exist, and the unique per-run Compose project has no pre-existing volumes or network. It rejects caller-selected Compose paths, projects, or URLs.

The rehearsal uses the fixed Compose file and project name, synthetic local storage, a project-owned Docker bridge so the existing loopback port-publishing smoke can reach the services, and loopback application URLs. R2 variables are cleared. It records container IDs, immutable image IDs, restart policies, Compose labels, mount identities, and both named-volume identities. It writes one synthetic database marker, kills only the synthetic frontend and PostgreSQL containers, then requires automatic restart with the same IDs/images, healthy PostgreSQL, HTTP recovery, and the marker still present. It then restarts Docker only on that disposable GitHub-hosted VM and checks the same identities, named volumes, project bridge, HTTP endpoints, and marker again. It emits only pass/fail status and never prints marker contents.

The CI run demonstrates ordinary container-crash and Docker-daemon-restart behavior for this isolated Compose candidate. It does not demonstrate KAGOYA host reboot recovery, production data integrity, backup restoration, R2 continuity, or shared-host impact. A failed guard or recovery assertion stops the CI job; it does not authorize or initiate production repair. Do not run the rehearsal locally, on KAGOYA, or on a shared Docker host.
