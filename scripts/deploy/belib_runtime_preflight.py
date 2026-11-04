"""Pure, non-authorizing BeLib incident evidence check; performs no host operations.

This tool consumes a *sanitized, operator-supplied* JSON snapshot. Its output is
advisory only: even READY_FOR_REVIEW does not grant permission to repair.
"""

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT = "belib-production"
DB_VOLUME = "belib-production_postgres-data"
SERVICES = ("db", "backend", "frontend", "nginx")
SHA40 = re.compile(r"^[0-9a-f]{40}$")


def assess(evidence, expected_head):
    """Return an allowlisted, secret-free advisory result from untrusted input."""
    reasons = []

    def require(condition, code):
        if not condition and code not in reasons:
            reasons.append(code)

    def obj(container, key):
        value = container.get(key) if isinstance(container, dict) else None
        return value if isinstance(value, dict) else {}

    require(isinstance(evidence, dict), "EVIDENCE_NOT_OBJECT")
    data = evidence if isinstance(evidence, dict) else {}
    require(data.get("schema_version") == 1, "SCHEMA_INVALID")
    require(bool(SHA40.fullmatch(expected_head or "")), "EXPECTED_HEAD_INVALID")

    source = obj(data, "source")
    require(source.get("repository") == "hiroyuki9614/book_library", "SOURCE_REPOSITORY_UNKNOWN")
    require(source.get("head") == expected_head, "SOURCE_HEAD_MISMATCH")
    require(source.get("work_prompt_document_id") == "63ef369e-efce-4546-b643-8d27302dc801", "WORK_PROMPT_ID_MISMATCH")
    # This flag is an operator statement, not independent GitHub authentication.
    require(source.get("github_identity_readback") is True, "SOURCE_READBACK_UNVERIFIED")

    operator = obj(data, "operator")
    require(operator.get("principal") == "agent" and operator.get("uid") == 1002, "WRONG_RUNTIME_OWNER")
    require(operator.get("host") == "v133-18-43-217-vir", "HOST_IDENTITY_UNVERIFIED")

    runtime = obj(data, "runtime")
    require(runtime.get("rootless") is True, "ROOTLESS_UNVERIFIED")
    require(runtime.get("docker_service_enabled") is True, "DOCKER_BOOT_UNVERIFIED")
    require(runtime.get("linger_enabled") is True, "LINGER_UNVERIFIED")
    require(runtime.get("runc_container_state_consistent") is True, "RUNC_STATE_AMBIGUOUS")
    require(runtime.get("postgres_old_process_confirmed_absent") is True, "OLD_DB_PROCESS_UNVERIFIED")
    require(runtime.get("shared_docker_impact_inventory_complete") is True, "OTHER_PROJECT_IMPACT_UNVERIFIED")
    require(runtime.get("memory_pressure_root_cause_addressed") is True, "OOM_RECURRENCE_UNRESOLVED")

    services = obj(data, "services")
    require(set(services) == set(SERVICES), "SERVICE_INVENTORY_MISMATCH")
    for service in SERVICES:
        state = obj(services, service)
        require(state.get("restart") == "unless-stopped", f"RESTART_POLICY_{service.upper()}_UNVERIFIED")
        require(state.get("image_id_retained") is True, f"IMAGE_{service.upper()}_UNVERIFIED")

    volume = obj(data, "postgres_volume")
    require(volume.get("name") == DB_VOLUME and volume.get("exists") is True, "POSTGRES_VOLUME_UNVERIFIED")
    require(volume.get("identity_pinned") is True, "POSTGRES_VOLUME_IDENTITY_UNVERIFIED")
    require(volume.get("data_integrity_verified") is True, "POSTGRES_DATA_UNVERIFIED")

    backup = obj(data, "backup")
    require(backup.get("postgres_volume") == DB_VOLUME, "BACKUP_VOLUME_MISMATCH")
    require(backup.get("checksum_verified") is True, "BACKUP_CHECKSUM_UNVERIFIED")
    require(backup.get("restore_drill_verified") is True, "BACKUP_RESTORE_UNVERIFIED")

    protected_storage = obj(data, "protected_storage")
    require(protected_storage.get("r2_objects_unchanged_verified") is True, "R2_CONTINUITY_UNVERIFIED")
    require(protected_storage.get("private_access_verified") is True, "R2_PRIVACY_UNVERIFIED")

    coordination = obj(data, "coordination")
    require(coordination.get("other_projects_considered") is True, "CROSS_PROJECT_IMPACT_UNVERIFIED")
    require(coordination.get("owner_approved_cross_project_effects") is True, "CROSS_PROJECT_APPROVAL_MISSING")
    require(coordination.get("existing_cd_lock_respected") is True, "CD_LOCK_UNVERIFIED")
    require(coordination.get("rollback_rehearsed") is True, "ROLLBACK_UNVERIFIED")

    return {
        "schema_version": 1,
        "decision": "BLOCKED" if reasons else "READY_FOR_OWNER_REVIEW",
        "authorization": "NONE",
        "mutation_attempted": False,
        "safe_to_repair": False,
        "expected_head": expected_head if SHA40.fullmatch(expected_head or "") else None,
        "reason_codes": sorted(reasons),
        "note": "Untrusted evidence checklist only; separate authenticated owner authorization is mandatory.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path, help="Sanitized JSON, never secrets")
    parser.add_argument("--expected-head", required=True, help="40-character reviewed book_library commit")
    args = parser.parse_args(argv)
    try:
        if args.evidence.is_symlink() or not args.evidence.is_file():
            raise ValueError("evidence must be a regular non-symlink file")
        if args.evidence.stat().st_size > 64 * 1024:
            raise ValueError("evidence too large")
        data = json.loads(args.evidence.read_text(encoding="utf-8"))
        report = assess(data, args.expected_head)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        report = {
            "schema_version": 1,
            "decision": "PREFLIGHT_ERROR",
            "authorization": "NONE",
            "mutation_attempted": False,
            "safe_to_repair": False,
            "reason_codes": ["EVIDENCE_UNREADABLE_OR_INVALID"],
        }
    print(json.dumps(report, sort_keys=True, ensure_ascii=True))
    return {"READY_FOR_OWNER_REVIEW": 0, "BLOCKED": 20, "PREFLIGHT_ERROR": 30}[report["decision"]]


if __name__ == "__main__":
    sys.exit(main())
