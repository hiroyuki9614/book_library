"""Pure, non-authorizing BeLib runtime snapshot validator.

The input is untrusted operator-supplied JSON. This module performs no host,
Docker, OCI, process, network, or mutation operations.
"""

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT = "belib-production"
NETWORK = "belib-production_default"
DB_VOLUME = "belib-production_postgres-data"
BOOK_VOLUME = "belib-production_book-files"
SERVICES = ("db", "backend", "frontend", "nginx")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")
EXPECTED_SOURCE = "hiroyuki9614/book_library"
PROMPT_ID = "63ef369e-efce-4546-b643-8d27302dc801"


def _is_bool(value):
    return type(value) is bool


def assess(snapshot, expected_head):
    """Validate exact identities and return an allowlisted advisory result."""
    issues = []
    if not isinstance(snapshot, dict):
        snapshot = {}
        issues.append("SNAPSHOT_NOT_OBJECT")

    def require(condition, reason):
        if not condition:
            issues.append(reason)

    require(snapshot.get("schema_version") == 2, "SCHEMA_INVALID")
    require(isinstance(expected_head, str) and bool(SHA40.fullmatch(expected_head)), "EXPECTED_HEAD_INVALID")

    source = snapshot.get("source")
    if not isinstance(source, dict):
        source = {}
        issues.append("SOURCE_INVALID")
    require(source.get("repository") == EXPECTED_SOURCE, "SOURCE_REPOSITORY_MISMATCH")
    require(source.get("head") == expected_head, "SOURCE_HEAD_MISMATCH")
    require(source.get("work_prompt_document_id") == PROMPT_ID, "WORK_PROMPT_ID_MISMATCH")

    runtime = snapshot.get("runtime")
    if not isinstance(runtime, dict):
        runtime = {}
        issues.append("RUNTIME_INVALID")
    for key, reason in (
        ("rootless", "ROOTLESS_UNVERIFIED"),
        ("shared_inventory_complete", "SHARED_INVENTORY_INCOMPLETE"),
        ("namespace_error", "NAMESPACE_ERROR"),
    ):
        require(_is_bool(runtime.get(key)), f"{reason}_UNKNOWN")
    require(runtime.get("rootless") is True, "ROOTLESS_UNVERIFIED")
    require(
        runtime.get("error_class") in {
            "none", "duplicate_container_id", "docker_task_runc_mismatch", "namespace_error"
        },
        "RUNTIME_ERROR_CLASS_INVALID",
    )
    require(runtime.get("error_class") == "none", "RUNTIME_ERROR_REPORTED")
    require(runtime.get("shared_inventory_complete") is True, "SHARED_INVENTORY_INCOMPLETE")
    require(runtime.get("namespace_error") is False, "NAMESPACE_ERROR")
    require(runtime.get("other_project_impact") == "none", "OTHER_PROJECT_IMPACT")
    require(
        runtime.get("old_db_process_access") == "confirmed_absent",
        "OLD_DB_PROCESS_ACCESS_UNKNOWN",
    )

    services = snapshot.get("services")
    if not isinstance(services, dict):
        services = {}
        issues.append("SERVICE_INVENTORY_INVALID")
    require(set(services) == set(SERVICES), "SERVICE_INVENTORY_MISMATCH")
    ids = []
    for name in SERVICES:
        item = services.get(name)
        if not isinstance(item, dict):
            issues.append(f"SERVICE_{name.upper()}_INVALID")
            continue
        require(item.get("project") == PROJECT, f"SERVICE_{name.upper()}_PROJECT_MISMATCH")
        require(item.get("service") == name, f"SERVICE_{name.upper()}_IDENTITY_MISMATCH")
        container_id = item.get("container_id")
        require(
            isinstance(container_id, str) and bool(CONTAINER_ID.fullmatch(container_id)),
            f"SERVICE_{name.upper()}_CONTAINER_ID_INVALID",
        )
        if isinstance(container_id, str) and CONTAINER_ID.fullmatch(container_id):
            ids.append(container_id)
        states = [item.get(key) for key in ("docker_state", "task_state", "runc_state")]
        allowed_states = {"running", "exited", "created", "paused", "restarting", "dead", "removing", "unknown"}
        for key, state in zip(("docker_state", "task_state", "runc_state"), states):
            require(isinstance(state, str) and state in allowed_states, f"SERVICE_{name.upper()}_{key.upper()}_INVALID")
        require(states[0] == states[1] == states[2] and states[0] != "unknown", "OCI_STATE_MISMATCH")
        require(item.get("docker_state") == "running", f"SERVICE_{name.upper()}_NOT_RUNNING")
        require(
            isinstance(item.get("health"), str)
            and item.get("health") in {"healthy", "unhealthy", "starting", "not_configured", "none"},
            f"SERVICE_{name.upper()}_HEALTH_INVALID",
        )
        require(item.get("health") == "healthy", f"SERVICE_{name.upper()}_NOT_HEALTHY")
        require(item.get("restart") == "unless-stopped", f"SERVICE_{name.upper()}_RESTART_MISMATCH")
        expected_image_id = item.get("expected_image_id")
        observed_image_id = item.get("observed_image_id")
        require(
            isinstance(expected_image_id, str) and bool(IMAGE_ID.fullmatch(expected_image_id))
            and isinstance(observed_image_id, str) and bool(IMAGE_ID.fullmatch(observed_image_id)),
            f"SERVICE_{name.upper()}_IMAGE_ID_INVALID",
        )
        require(expected_image_id == observed_image_id, f"SERVICE_{name.upper()}_IMAGE_ID_MISMATCH")
        require(item.get("networks") == [NETWORK], f"SERVICE_{name.upper()}_NETWORK_MISMATCH")
        expected_volumes = [DB_VOLUME] if name == "db" else ([BOOK_VOLUME] if name == "backend" else [])
        require(item.get("volumes") == expected_volumes, f"SERVICE_{name.upper()}_VOLUME_MISMATCH")
    require(len(ids) == len(set(ids)), "DUPLICATE_CONTAINER_ID")

    volumes = snapshot.get("volumes")
    if not isinstance(volumes, dict):
        volumes = {}
        issues.append("VOLUME_INVENTORY_INVALID")
    require(set(volumes) == {"postgres", "book_files"}, "VOLUME_INVENTORY_MISMATCH")
    for key, name in (("postgres", DB_VOLUME), ("book_files", BOOK_VOLUME)):
        volume = volumes.get(key)
        if not isinstance(volume, dict):
            issues.append(f"VOLUME_{key.upper()}_INVALID")
            continue
        require(volume.get("name") == name, f"VOLUME_{key.upper()}_NAME_MISMATCH")
        require(volume.get("exists") is True, f"VOLUME_{key.upper()}_MISSING")
        expected_identity = volume.get("expected_identity")
        observed_identity = volume.get("observed_identity")
        require(
            isinstance(expected_identity, str) and bool(re.fullmatch(r"[0-9a-f]{64}", expected_identity))
            and isinstance(observed_identity, str) and bool(re.fullmatch(r"[0-9a-f]{64}", observed_identity)),
            f"VOLUME_{key.upper()}_IDENTITY_INVALID",
        )
        require(expected_identity == observed_identity, f"VOLUME_{key.upper()}_IDENTITY_MISMATCH")

    # Unknown or extra fields are rejected so arbitrary paths, commands, or secrets
    # cannot be accidentally reflected or accepted as evidence.
    expected_keys = {"schema_version", "source", "runtime", "services", "volumes"}
    require(set(snapshot) == expected_keys, "SNAPSHOT_FIELDS_INVALID")
    require(
        isinstance(source, dict)
        and set(source) == {"repository", "head", "work_prompt_document_id"},
        "SOURCE_FIELDS_INVALID",
    )
    require(
        isinstance(runtime, dict)
        and set(runtime)
        == {
            "rootless", "shared_inventory_complete", "namespace_error",
            "other_project_impact", "old_db_process_access", "error_class",
        },
        "RUNTIME_FIELDS_INVALID",
    )
    require(
        isinstance(volumes, dict) and set(volumes) == {"postgres", "book_files"}
        and all(isinstance(v, dict) and set(v) == {"name", "exists", "expected_identity", "observed_identity"} for v in volumes.values()),
        "VOLUME_FIELDS_INVALID",
    )
    for name in SERVICES:
        item = services.get(name)
        require(
            isinstance(item, dict)
            and set(item)
            == {
                "project", "service", "container_id", "docker_state", "task_state", "runc_state",
                "health", "restart", "expected_image_id", "observed_image_id", "networks", "volumes",
            },
            f"SERVICE_{name.upper()}_FIELDS_INVALID",
        )

    shared_issue = any(
        reason in {
            "ROOTLESS_UNVERIFIED", "OCI_STATE_MISMATCH", "SHARED_INVENTORY_INCOMPLETE",
            "NAMESPACE_ERROR", "OTHER_PROJECT_IMPACT", "OLD_DB_PROCESS_ACCESS_UNKNOWN",
            "DUPLICATE_CONTAINER_ID", "RUNTIME_ERROR_REPORTED", "OCI_STATE_MISMATCH",
        }
        or reason.endswith(("_PROJECT_MISMATCH", "_IDENTITY_MISMATCH", "_NETWORK_MISMATCH", "_VOLUME_MISMATCH", "_RESTART_MISMATCH", "_IMAGE_ID_INVALID", "_IMAGE_ID_MISMATCH", "_NAME_MISMATCH", "_MISSING"))
        for reason in issues
    )
    invalid_input = any(
        reason in {
            "SNAPSHOT_NOT_OBJECT", "SCHEMA_INVALID", "EXPECTED_HEAD_INVALID",
            "SOURCE_INVALID", "SOURCE_REPOSITORY_MISMATCH", "SOURCE_HEAD_MISMATCH",
            "WORK_PROMPT_ID_MISMATCH", "RUNTIME_INVALID", "SERVICE_INVENTORY_INVALID",
            "SERVICE_INVENTORY_MISMATCH", "VOLUME_INVENTORY_INVALID", "VOLUME_INVENTORY_MISMATCH", "SNAPSHOT_FIELDS_INVALID",
            "SOURCE_FIELDS_INVALID", "RUNTIME_FIELDS_INVALID", "VOLUME_FIELDS_INVALID", "RUNTIME_ERROR_CLASS_INVALID",
        }
        or reason.endswith(("_FIELDS_INVALID", "_CONTAINER_ID_INVALID", "_STATE_INVALID", "_HEALTH_INVALID", "_IMAGE_ID_INVALID"))
        for reason in issues
    )
    if invalid_input:
        decision = "PREFLIGHT_EVIDENCE_INVALID"
    elif shared_issue:
        decision = "SHARED_RUNTIME_RECOVERY_REQUIRED"
    elif any(reason.endswith("_NOT_RUNNING") for reason in issues):
        decision = "NORMAL_CONTAINER_STOPPED"
    elif any(reason.endswith("_NOT_HEALTHY") for reason in issues):
        decision = "SERVICE_UNHEALTHY"
    elif issues:
        decision = "PREFLIGHT_EVIDENCE_INVALID"
    else:
        decision = "READY_FOR_OWNER_REVIEW"

    # Snapshot claims are never treated as independent authentication or approval.
    return {
        "schema_version": 2,
        "decision": decision,
        "authorization": "NONE",
        "mutation_attempted": False,
        "safe_to_repair": False,
        "expected_head": expected_head if isinstance(expected_head, str) and SHA40.fullmatch(expected_head) else None,
        "reason_codes": sorted(set(issues)),
        "note": "Untrusted snapshot only; readiness is not evidence authentication or owner authorization.",
    }


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid arguments") from None


def main(argv=None):
    parser = _SafeArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path, help="Sanitized JSON snapshot")
    parser.add_argument("--expected-head", required=True, help="40-character reviewed source commit")
    try:
        args = parser.parse_args(argv)
        if args.evidence.is_symlink() or not args.evidence.is_file():
            raise ValueError
        if args.evidence.stat().st_size > 64 * 1024:
            raise ValueError
        snapshot = json.loads(args.evidence.read_text(encoding="utf-8"))
        report = assess(snapshot, args.expected_head)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        report = {
            "schema_version": 2,
            "decision": "PREFLIGHT_EVIDENCE_INVALID",
            "authorization": "NONE",
            "mutation_attempted": False,
            "safe_to_repair": False,
            "expected_head": None,
            "reason_codes": ["EVIDENCE_UNREADABLE_OR_INVALID"],
            "note": "Untrusted snapshot only; readiness is not evidence authentication or owner authorization.",
        }
    print(json.dumps(report, sort_keys=True, ensure_ascii=True))
    return {
        "READY_FOR_OWNER_REVIEW": 0,
        "NORMAL_CONTAINER_STOPPED": 10,
        "SERVICE_UNHEALTHY": 11,
        "SHARED_RUNTIME_RECOVERY_REQUIRED": 20,
        "PREFLIGHT_EVIDENCE_INVALID": 30,
    }[report["decision"]]


if __name__ == "__main__":
    sys.exit(main())
