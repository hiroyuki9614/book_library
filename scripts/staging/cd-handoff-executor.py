#!/usr/bin/env -S /usr/bin/python3 -I -B
"""Consume a reviewed staging handoff through the fixed installed operator entry.

The handoff is evidence only. The installed root-owned operator bootstrap is the
sole grant verifier and staging authority. This adapter runs as hiroyuki, calls
only its fixed sudo entry, and checks the resulting durable receipt and local
loopback health. It does not deploy from GitHub Actions or establish browser
acceptance.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import http.client
import json
import os
import pwd
import re
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Callable


REPOSITORY = "hiroyuki9614/book_library"
REPOSITORY_OWNER = "hiroyuki9614"
PULL_REQUEST = 103
BRANCH = "codex/belib-i5-isolated-staging-20261107"
HOST = "ubuntu-worker"
PRINCIPAL = "hiroyuki"
UID = 1000
PROJECT = "belib-isolated-i5"
LOCK_GROUP = "belib-staging-cd-belib-isolated-i5"
PROJECT_LOCK_PATH = Path("/tmp/belib-staging-cd-belib-isolated-i5.lock")
PRIOR_SOURCE = "6963103fd6bec332b4057457d07b890fe5c11e73"
OLD_APPLICATION = "81256268276d7ead95029849037f69ec6beec202"
RETAINED_DATABASE_ID = "9ab9d141599e8b33f51a1e4e9bcd16d66e92b9d134889014d9d9bd618b42f73d"
INSTALL = "/usr/local/libexec/belib-staging/operator-bootstrap.py"
PYTHON = "/usr/bin/python3"
SUDO = "/usr/bin/sudo"
ENV_FILE = Path("/home/hiroyuki/.config/belib-staging/i5.env")
DOCKER_CLI = "/usr/bin/docker"
DOCKER_SOCKET = Path("/run/user/1000/docker.sock")
DOCKER_ENDPOINT = f"unix://{DOCKER_SOCKET}"
ISOLATED_NETWORK = "belib-isolated-i5_isolated"
INGRESS_NETWORK = "belib-isolated-i5_ingress"
DATABASE_IMAGE = "postgres:16.14-bookworm@sha256:714b08dd70c05160db032e4321033ac4e5b6328935b2d03ed419c3a898aa8219"
NGINX_IMAGE = "nginx:1.27-alpine@sha256:62223d644fa234c3a1cc785ee14242ec47a77364226f1c811d2f669f96dc2ac8"
HEALTH_PATH = "/health"
MAX_HANDOFF_BYTES = 2 * 1024 * 1024
MAX_GRANT_BYTES = 16 * 1024
MAX_ENV_BYTES = 16 * 1024
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
OPERATIONS = {"deploy", "resume-ingress", "upgrade-retained"}
TARGET = {
    "repository": REPOSITORY,
    "pull_request": PULL_REQUEST,
    "branch": BRANCH,
    "host": HOST,
    "principal": PRINCIPAL,
    "uid": UID,
    "project": PROJECT,
}
COMMON_GRANT_FIELDS = set(TARGET) | {
    "schema", "source_sha", "operation", "nonce", "evidence_sha256",
    "issued_at", "expires_at",
}
HANDOFF_FIELDS = {
    "schema", "receipt_id", "status", "source", "evidence", "lock", "target",
    "supervisor_verification", "deployment",
}
STAGING_ENV_KEYS = {
    "BELIB_STAGE_DB_BOOTSTRAP_PASSWORD", "BELIB_STAGE_DB_APP_PASSWORD",
    "BELIB_STAGE_DB_MIGRATE_PASSWORD", "BELIB_STAGE_AUTH_SECRET",
    "BELIB_STAGE_VCS_REF", "BELIB_STAGE_PORT",
}


class ExecutorError(RuntimeError):
    """Raised when a handoff or result does not match this fixed staging target."""


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExecutorError("Input JSON contains a duplicate field")
        result[key] = value
    return result


def _parse_json(raw: bytes, limit: int, *, canonical: bool = False) -> Any:
    if not isinstance(raw, bytes) or not raw or len(raw) > limit:
        raise ExecutorError("Input file is empty or exceeds its size limit")
    try:
        value = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_pairs_no_duplicates)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                             allow_nan=False).encode("utf-8", "strict")
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        if isinstance(error, ExecutorError):
            raise
        raise ExecutorError("Input JSON is malformed") from error
    if canonical and encoded != raw:
        raise ExecutorError("Grant JSON is not canonical")
    return value


def _read_regular(path: Path, limit: int, *, owner_uid: int | None = None,
                  modes: set[int] | None = None) -> bytes:
    if not path.is_absolute():
        raise ExecutorError("Input paths must be absolute")
    try:
        before = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or (owner_uid is not None and before.st_uid != owner_uid)
                or (modes is not None and stat.S_IMODE(before.st_mode) not in modes)
                or before.st_size <= 0 or before.st_size > limit):
            raise ExecutorError("Input must be a bounded, single-link regular file with safe ownership and mode")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            opened = os.fstat(descriptor)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or opened.st_dev != before.st_dev or opened.st_ino != before.st_ino
                    or opened.st_size != before.st_size or opened.st_mtime_ns != before.st_mtime_ns):
                raise ExecutorError("Input changed while it was opened")
            chunks = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            if len(raw) != opened.st_size or len(raw) > limit:
                raise ExecutorError("Input changed or exceeded its bound while reading")
            return raw
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ExecutorError("Input file is unavailable or unsafe") from error


def _require_fields(value: Any, fields: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise ExecutorError(f"{label} fields are invalid")
    return value


def _is_sha(value: Any, pattern: re.Pattern[str] = SHA64) -> bool:
    return isinstance(value, str) and bool(pattern.fullmatch(value))


def validate_handoff(value: Any) -> tuple[str, str]:
    handoff = _require_fields(value, HANDOFF_FIELDS, "Handoff")
    if (handoff["schema"] != "belib-staging-handoff/v1"
            or handoff["status"] != "candidate-ci-passed-awaiting-existing-supervisor-verification"):
        raise ExecutorError("Handoff is not the pending candidate-evidence schema")
    source = _require_fields(handoff["source"], {"repository", "pull_request", "sha"}, "Source")
    if (source["repository"] != REPOSITORY or type(source["pull_request"]) is not int
            or source["pull_request"] != PULL_REQUEST or not _is_sha(source["sha"], SHA40)):
        raise ExecutorError("Handoff source does not match the fixed repository, PR, and revision format")

    receipt_id = handoff["receipt_id"]
    lock = _require_fields(handoff["lock"], {"github_concurrency_group", "receipt_id"}, "Lock")
    if (not isinstance(receipt_id, str) or not re.fullmatch(r"belib-staging-[0-9a-f]{64}", receipt_id)
            or lock != {"github_concurrency_group": LOCK_GROUP, "receipt_id": receipt_id}):
        raise ExecutorError("Handoff receipt or concurrency identity is invalid")

    target = _require_fields(handoff["target"], {"host", "principal", "compose_project", "transport"}, "Target")
    if target != {"host": HOST, "principal": PRINCIPAL, "compose_project": PROJECT,
                  "transport": "existing staging supervisor"}:
        raise ExecutorError("Handoff target is not the fixed i5 staging target")
    verification = _require_fields(
        handoff["supervisor_verification"], {"required", "status", "deployment_authorized"}, "Supervisor verification"
    )
    if verification != {"required": True, "status": "pending-independent-provenance-verification",
                       "deployment_authorized": False}:
        raise ExecutorError("Handoff must remain pending independent supervisor verification")
    deployment = _require_fields(handoff["deployment"], {"started", "asserted", "authorized"}, "Deployment")
    if deployment != {"started": False, "asserted": False, "authorized": False}:
        raise ExecutorError("Candidate handoff cannot claim deployment")

    evidence = _require_fields(handoff["evidence"], {
        "staging_ci", "release_ci", "workflow_provenance", "independent_review",
        "owner_request_for_supervisor_review",
    }, "Evidence")
    for name, workflow_path, required_class in (
        ("staging_ci", ".github/workflows/staging-ci.yml", "candidate-source-ci-only"),
        ("release_ci", ".github/workflows/release-ci.yml", "authoritative-workflow-definition-with-candidate-source"),
    ):
        ci = evidence[name]
        _require_fields(ci, {"trust_class", "workflow_path", "run_id", "run_attempt", "run_head_sha",
                             "source_sha", "run_url", "required_jobs"}, name)
        if (ci["trust_class"] != required_class or ci["workflow_path"] != workflow_path
                or type(ci["run_id"]) is not int or ci["run_id"] <= 0
                or type(ci["run_attempt"]) is not int or ci["run_attempt"] <= 0
                or not _is_sha(ci["run_head_sha"], SHA40) or ci["source_sha"] != source["sha"]
                or ci["run_url"] != f"https://github.com/{REPOSITORY}/actions/runs/{ci['run_id']}"
                or not isinstance(ci["required_jobs"], list) or not ci["required_jobs"]
                or any(not isinstance(job, str) or not job for job in ci["required_jobs"])):
            raise ExecutorError(f"Handoff {name} evidence does not bind the exact candidate")

    provenance = _require_fields(evidence["workflow_provenance"], {
        "authority_ref", "authority_sha", "cd_workflow", "candidate_workflows", "status",
    }, "Workflow provenance")
    if (provenance["authority_ref"] != "refs/heads/main"
            or not _is_sha(provenance["authority_sha"], SHA40)
            or provenance["status"] != "candidate-definitions-identical-to-authoritative-main"):
        raise ExecutorError("Handoff workflow provenance is invalid")
    cd = _require_fields(provenance["cd_workflow"], {"path", "ref", "sha", "blob_sha"}, "CD workflow provenance")
    if (cd != {"path": ".github/workflows/staging-cd.yml",
               "ref": f"{REPOSITORY}/.github/workflows/staging-cd.yml@refs/heads/main",
               "sha": provenance["authority_sha"], "blob_sha": cd.get("blob_sha")}
            or not _is_sha(cd["blob_sha"], SHA40)):
        raise ExecutorError("Handoff CD workflow is not pinned to the authoritative main revision")
    candidate_workflows = provenance["candidate_workflows"]
    if not isinstance(candidate_workflows, dict) or set(candidate_workflows) != {
        ".github/workflows/staging-ci.yml", ".github/workflows/release-ci.yml"
    }:
        raise ExecutorError("Handoff candidate workflow provenance is incomplete")
    for item in candidate_workflows.values():
        _require_fields(item, {"trusted_blob_sha", "candidate_blob_sha", "matches_authoritative_main"},
                         "Candidate workflow provenance")
        if (not _is_sha(item["trusted_blob_sha"], SHA40)
                or item["candidate_blob_sha"] != item["trusted_blob_sha"]
                or item["matches_authoritative_main"] is not True):
            raise ExecutorError("Candidate workflow differs from the authoritative main definition")
    review = _require_fields(evidence["independent_review"], {
        "login", "review_id", "author_association", "commit_sha", "submitted_at",
    }, "Independent review")
    if (not isinstance(review["login"], str) or not review["login"]
            or review["login"].casefold() == REPOSITORY_OWNER.casefold()
            or type(review["review_id"]) is not int or review["review_id"] <= 0
            or review["author_association"] not in {"COLLABORATOR", "OWNER"}
            or review["commit_sha"] != source["sha"]
            or not isinstance(review["submitted_at"], str) or not review["submitted_at"].endswith("Z")):
        raise ExecutorError("Handoff independent review does not approve the exact candidate")
    owner_request = _require_fields(evidence["owner_request_for_supervisor_review"], {
        "login", "actions_run_id", "actions_run_attempt",
    }, "Owner request")
    if (owner_request["login"].casefold() != REPOSITORY_OWNER.casefold()
            or type(owner_request["actions_run_id"]) is not int or owner_request["actions_run_id"] <= 0
            or type(owner_request["actions_run_attempt"]) is not int or owner_request["actions_run_attempt"] <= 0):
        raise ExecutorError("Handoff owner review request identity is invalid")
    return source["sha"], receipt_id


def decode_grant(raw: bytes, *, expected_source_sha: str, now: int) -> tuple[dict, str]:
    envelope = _parse_json(raw, MAX_GRANT_BYTES, canonical=True)
    envelope = _require_fields(envelope, {"payload", "signature"}, "Grant envelope")
    if not isinstance(envelope["payload"], str) or not isinstance(envelope["signature"], str):
        raise ExecutorError("Grant envelope encoding is invalid")
    try:
        payload_raw = base64.b64decode(envelope["payload"], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
        if (base64.b64encode(payload_raw).decode("ascii") != envelope["payload"]
                or base64.b64encode(signature).decode("ascii") != envelope["signature"]
                or len(signature) != 64):
            raise ValueError("noncanonical grant encoding")
    except (ValueError, TypeError) as error:
        raise ExecutorError("Grant envelope encoding is invalid") from error
    grant = _parse_json(payload_raw, MAX_GRANT_BYTES, canonical=True)
    if not isinstance(grant, dict):
        raise ExecutorError("Grant payload must be an object")
    operation = grant.get("operation")
    expected_fields = set(COMMON_GRANT_FIELDS)
    if operation == "resume-ingress":
        expected_fields |= {"prior_source_sha", "retained_state_sha256"}
    elif operation == "upgrade-retained":
        expected_fields |= {"prior_application_sha", "retained_state_sha256", "backup_sha256",
                            "restore_attestation_sha256"}
    if (operation not in OPERATIONS or set(grant) != expected_fields
            or grant.get("schema") != "belib-staging-bootstrap-approval/v1"
            or any(grant.get(key) != value for key, value in TARGET.items())
            or grant.get("source_sha") != expected_source_sha
            or not _is_sha(grant.get("source_sha"), SHA40)
            or not _is_sha(grant.get("nonce")) or not _is_sha(grant.get("evidence_sha256"))
            or type(grant.get("pull_request")) is not int or type(grant.get("uid")) is not int
            or type(grant.get("issued_at")) is not int or type(grant.get("expires_at")) is not int):
        raise ExecutorError("Grant target, source, or operation does not match the handoff")
    if operation == "resume-ingress" and (
        grant["prior_source_sha"] != PRIOR_SOURCE or not _is_sha(grant["retained_state_sha256"])
    ):
        raise ExecutorError("Resume grant prior-state binding is invalid")
    if operation == "upgrade-retained" and (
        grant["prior_application_sha"] != OLD_APPLICATION
        or any(not _is_sha(grant[field]) for field in (
            "retained_state_sha256", "backup_sha256", "restore_attestation_sha256"
        ))
    ):
        raise ExecutorError("Retained-upgrade grant evidence binding is invalid")
    if not (now - 300 <= grant["issued_at"] <= now + 5
            and now < grant["expires_at"] <= grant["issued_at"] + 300):
        raise ExecutorError("Grant is expired or outside its bounded lifetime")
    # This digest binds the verified operator receipt to the exact decoded grant.
    # Signature validation remains exclusively in the installed root-owned bootstrap.
    return grant, hashlib.sha256(payload_raw).hexdigest()


RESULT_FIELDS = {
    "schema", "nonce", "grant_sha256", "source_sha", "operation", "host", "principal",
    "uid", "project", "exit_code", "completed_at", "nonce_consumed",
}


def validate_expected_runtime(value: Any) -> dict:
    expected = _require_fields(value, {"project", "services", "retained", "port", "plan_sha256",
                                       "operation_id", "plan"},
                               "Expected retained runtime")
    project = expected.get("project")
    operation_id = expected.get("operation_id")
    if (not isinstance(operation_id, str) or not re.fullmatch(r"[0-9a-f]{32}", operation_id)
            or project != PROJECT + "-upgrade-" + operation_id):
        raise ExecutorError("Expected upgrade project is not operation-scoped")
    services = _require_fields(expected.get("services"), {"db", "backend", "frontend", "nginx"},
                               "Expected upgrade services")
    retained = _require_fields(expected.get("retained"), {"project", "db", "stopped_application_ids", "volumes"},
                               "Expected retained services")
    plan = _require_fields(expected.get("plan"), {"cutover_plan", "candidate_database", "source_database"}, "Expected cutover plan")
    cutover = _require_fields(plan.get("cutover_plan"), {"schema", "source_sha", "operation_id", "port", "old", "new"},
                              "Cutover plan")
    candidate_db = _require_fields(plan.get("candidate_database"),
                                   {"id", "configuration_sha256", "volume_name", "network_id"},
                                   "Candidate database plan")
    source_db = _require_fields(plan.get("source_database"),
                                {"id", "configuration_sha256", "network_ids", "volume_name"},
                                "Retained source database plan")
    if (any(not _is_sha(item) for item in services.values())
            or retained.get("project") != PROJECT or retained.get("db") != RETAINED_DATABASE_ID
            or not isinstance(retained.get("stopped_application_ids"), list)
            or len(retained["stopped_application_ids"]) != 3
            or any(not _is_sha(item) for item in retained["stopped_application_ids"])
            or len(set(retained["stopped_application_ids"])) != 3
            or type(expected.get("port")) is not int or not 1024 <= expected["port"] <= 65535
            or not _is_sha(expected.get("plan_sha256"))
            or cutover.get("schema") != "belib-isolated-copy-cutover/v1"
            or cutover.get("operation_id") != operation_id or cutover.get("port") != expected["port"]
            or not isinstance(retained.get("volumes"), dict)
            or retained["volumes"] != {"database": "belib-isolated-i5_staging-postgres-data",
                                        "books": "belib-isolated-i5_staging-book-files"}
            or candidate_db.get("id") != services.get("db")
            or candidate_db.get("volume_name") != project + "_staging-postgres-data"
            or not _is_sha(candidate_db.get("configuration_sha256"))
            or not _is_sha(candidate_db.get("network_id"))
            or source_db.get("id") != retained.get("db")
            or source_db.get("volume_name") != PROJECT + "_staging-postgres-data"
            or not _is_sha(source_db.get("configuration_sha256"))
            or not isinstance(source_db.get("network_ids"), dict)
            or set(source_db["network_ids"]) != {PROJECT + "_isolated"}
            or not _is_sha(source_db["network_ids"].get(PROJECT + "_isolated"))):
        raise ExecutorError("Expected retained runtime pins are malformed")
    ids = list(services.values()) + [retained["db"], *retained["stopped_application_ids"]]
    if len(set(ids)) != 8:
        raise ExecutorError("Old and candidate runtime container identities overlap")
    if (not isinstance(cutover.get("old"), dict) or set(cutover["old"]) != {"backend", "frontend", "nginx"}
            or not isinstance(cutover.get("new"), dict) or set(cutover["new"]) != {"backend", "frontend", "nginx"}):
        raise ExecutorError("Cutover plan must pin exactly the old and new application services")
    for side, expected_ids in (("old", dict(zip(("backend", "frontend", "nginx"), retained["stopped_application_ids"]))),
                               ("new", {name: services[name] for name in ("backend", "frontend", "nginx")})):
        for service, pin in cutover[side].items():
            pin = _require_fields(pin, {"id", "configuration_sha256", "network_ids"},
                                 f"Cutover {side} {service}")
            if (pin.get("id") != expected_ids[service] or not _is_sha(pin.get("configuration_sha256"))
                    or not isinstance(pin.get("network_ids"), dict) or not pin["network_ids"]
                    or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name)
                           or not _is_sha(network_id) for name, network_id in pin["network_ids"].items())):
                raise ExecutorError("Cutover service identity or network pins are invalid")
    new_isolated = project + "_isolated"
    new_ingress = project + "_ingress"
    old_isolated = PROJECT + "_isolated"
    old_ingress = PROJECT + "_ingress"
    if (set(cutover["new"]["nginx"]["network_ids"])
            != {new_isolated, new_ingress}):
        raise ExecutorError("Candidate nginx networks do not match the isolated-copy layout")
    if any(set(cutover["new"][service]["network_ids"]) != {new_isolated}
           for service in ("backend", "frontend")):
        raise ExecutorError("Candidate application services must remain on the isolated copy network")
    if (set(cutover["old"]["nginx"]["network_ids"]) != {old_isolated, old_ingress}
            or any(set(cutover["old"][service]["network_ids"]) != {old_isolated}
                   for service in ("backend", "frontend"))
            or candidate_db["network_id"] != cutover["new"]["backend"]["network_ids"].get(new_isolated)
            or source_db["network_ids"][old_isolated] != cutover["old"]["backend"]["network_ids"].get(old_isolated)):
        raise ExecutorError("Retained or candidate network plan differs from the fixed topology")
    if hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                 allow_nan=False).encode("utf-8")).hexdigest() != expected["plan_sha256"]:
        raise ExecutorError("Expected retained runtime plan digest is invalid")
    return expected


def validate_bootstrap_result(value: Any, grant: dict, grant_sha256: str) -> dict:
    if not isinstance(value, dict):
        raise ExecutorError("Operator result must be an object")
    result_fields = set(RESULT_FIELDS)
    if grant.get("operation") == "upgrade-retained" and value.get("exit_code") == 0:
        result_fields.add("expected_runtime")
    result = _require_fields(value, result_fields, "Operator result")
    expected = {
        "schema": "belib-staging-operation-result/v1",
        "nonce": grant["nonce"],
        "grant_sha256": grant_sha256,
        "source_sha": grant["source_sha"],
        "operation": grant["operation"],
        "host": HOST,
        "principal": PRINCIPAL,
        "uid": UID,
        "project": PROJECT,
        "nonce_consumed": True,
    }
    if any(result.get(key) != expected_value for key, expected_value in expected.items()):
        raise ExecutorError("Installed operator receipt does not match the exact admitted grant and host")
    if (type(result.get("exit_code")) is not int or result["exit_code"] < -128 or result["exit_code"] > 255
            or type(result.get("completed_at")) is not int or result["completed_at"] <= 0):
        raise ExecutorError("Installed operator receipt result fields are invalid")
    if "expected_runtime" in result:
        expected_runtime = validate_expected_runtime(result["expected_runtime"])
        if expected_runtime["plan"]["cutover_plan"]["source_sha"] != grant["source_sha"]:
            raise ExecutorError("Expected runtime plan source differs from the signed grant")
    return result


def _last_json_line(raw: str) -> Any:
    for line in reversed(raw.splitlines()):
        if not line.strip():
            continue
        try:
            return json.loads(line, object_pairs_hook=_pairs_no_duplicates)
        except (json.JSONDecodeError, ExecutorError):
            continue
    raise ExecutorError("Installed operator did not return its structured receipt")


def _bootstrap_command(args: list[str]) -> list[str]:
    return [SUDO, "-n", "--", PYTHON, "-I", "-B", INSTALL, *args]


def _staging_port(path: Path, *, owner_uid: int) -> tuple[int, str]:
    raw = _read_regular(path, MAX_ENV_BYTES, owner_uid=owner_uid, modes={0o400, 0o600})
    try:
        lines = raw.decode("utf-8", "strict").split("\n")
    except UnicodeError as error:
        raise ExecutorError("The fixed staging environment file is malformed") from error
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) != len(STAGING_ENV_KEYS) or any(not line or "\r" in line for line in lines):
        raise ExecutorError("The fixed staging environment file must contain exactly six staging keys")
    values = {}
    for line in lines:
        if line.count("=") != 1:
            raise ExecutorError("The fixed staging environment file is malformed")
        name, value = line.split("=", 1)
        if name not in STAGING_ENV_KEYS or name in values or not value:
            raise ExecutorError("The fixed staging environment file has unsupported or duplicate keys")
        values[name] = value
    if set(values) != STAGING_ENV_KEYS:
        raise ExecutorError("The fixed staging environment file is incomplete")
    for key in ("BELIB_STAGE_DB_BOOTSTRAP_PASSWORD", "BELIB_STAGE_DB_APP_PASSWORD",
                "BELIB_STAGE_DB_MIGRATE_PASSWORD", "BELIB_STAGE_AUTH_SECRET"):
        if not SHA64.fullmatch(values[key]):
            raise ExecutorError("The fixed staging environment file has invalid credential formats")
    port_text = values["BELIB_STAGE_PORT"]
    if (not re.fullmatch(r"[1-9][0-9]{3,4}", port_text)
            or not 1024 <= int(port_text) <= 65535
            or not SHA40.fullmatch(values["BELIB_STAGE_VCS_REF"])):
        raise ExecutorError("The fixed staging environment port or source revision is invalid")
    return int(port_text), hashlib.sha256(raw).hexdigest()


def loopback_health_probe(port: int, *, connection_factory: Callable[..., Any] = http.client.HTTPConnection) -> dict:
    connection = connection_factory("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", HEALTH_PATH, headers={"Host": "127.0.0.1"})
        response = connection.getresponse()
        status = response.status
        response.read(1024)
    except OSError as error:
        raise ExecutorError("Loopback staging health endpoint is unavailable") from error
    finally:
        connection.close()
    if status != 200:
        raise ExecutorError("Loopback staging health endpoint did not return HTTP 200")
    return {"url": f"http://127.0.0.1:{port}{HEALTH_PATH}", "http_status": status}


def _verify_local_docker_endpoint() -> None:
    """Verify only the existing user's rootless socket and pinned system CLI."""
    root_fd = run_fd = runtime_fd = -1
    try:
        root_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        run_fd = os.open("run", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd)
        runtime_parent = os.fstat(run_fd)
        if (not stat.S_ISDIR(runtime_parent.st_mode) or runtime_parent.st_uid != 0
                or stat.S_IMODE(runtime_parent.st_mode) & 0o022):
            raise ExecutorError("The rootless Docker runtime parent has unsafe identity")
        runtime_fd = os.open("user/1000", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                             dir_fd=run_fd)
        os.close(run_fd)
        run_fd = -1
        os.close(root_fd)
        root_fd = -1
        runtime_info = os.fstat(runtime_fd)
        socket_info = os.stat("docker.sock", dir_fd=runtime_fd, follow_symlinks=False)
        cli_path = Path(DOCKER_CLI)
        cli_info = cli_path.lstat()
        for parent in (Path("/"), Path("/usr"), Path("/usr/bin")):
            metadata = parent.lstat()
            if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0
                    or stat.S_IMODE(metadata.st_mode) & 0o022):
                raise ExecutorError("The system Docker CLI path has unsafe parent identity")
    except OSError as error:
        raise ExecutorError("The fixed rootless Docker endpoint or CLI is unavailable") from error
    finally:
        for descriptor in (runtime_fd, run_fd, root_fd):
            if descriptor >= 0:
                os.close(descriptor)
    if (not stat.S_ISDIR(runtime_info.st_mode) or runtime_info.st_uid != UID
            or stat.S_IMODE(runtime_info.st_mode) != 0o700 or stat.S_ISLNK(socket_info.st_mode)
            or not stat.S_ISSOCK(socket_info.st_mode) or socket_info.st_uid != UID
            or not stat.S_ISREG(cli_info.st_mode) or cli_info.st_uid != 0
            or stat.S_IMODE(cli_info.st_mode) & 0o022 or not cli_info.st_mode & 0o111):
        raise ExecutorError("The fixed rootless Docker endpoint or system CLI has unsafe identity")


def _docker_json(command: list[str], *, runner: Callable[..., Any]) -> Any:
    environment = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "DOCKER_CONFIG": "/nonexistent",
                   "DOCKER_HOST": DOCKER_ENDPOINT, "LANG": "C"}
    try:
        result = runner([DOCKER_CLI, *command], env=environment, stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                        timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise ExecutorError("Read-only rootless Docker inventory failed") from error
    if result.returncode != 0:
        raise ExecutorError("Read-only rootless Docker inventory was rejected")
    try:
        return json.loads(result.stdout, object_pairs_hook=_pairs_no_duplicates)
    except (json.JSONDecodeError, TypeError, ExecutorError) as error:
        raise ExecutorError("Read-only rootless Docker inventory is malformed") from error


def _published_container_ids(port: int, *, runner: Callable[..., Any]) -> set[str]:
    result = runner([DOCKER_CLI, "--host", DOCKER_ENDPOINT, "ps", "--no-trunc",
                     "--filter", f"publish={port}", "--format", "{{.ID}}"],
                    env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "DOCKER_CONFIG": "/nonexistent",
                         "DOCKER_HOST": DOCKER_ENDPOINT, "LANG": "C"},
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, timeout=20, check=False)
    if result.returncode != 0:
        raise ExecutorError("Could not enumerate containers publishing the staging port")
    ids = [line for line in result.stdout.splitlines() if line]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in ids):
        raise ExecutorError("Published-port container inventory is malformed")
    return set(ids)


def collect_runtime_readback(
    source_sha: str,
    port: int,
    *,
    expected_runtime: dict | None = None,
    runner: Callable[..., Any] = subprocess.run,
    endpoint_check: Callable[[], None] = _verify_local_docker_endpoint,
) -> dict:
    """Read running project container/image identity without Docker mutations."""
    if not _is_sha(source_sha, SHA40) or type(port) is not int or not 1024 <= port <= 65535:
        raise ExecutorError("Runtime readback source or port is invalid")
    if expected_runtime is not None:
        expected = validate_expected_runtime(expected_runtime)
        if expected["port"] != port or expected["plan"]["cutover_plan"]["source_sha"] != source_sha:
            raise ExecutorError("Expected retained runtime differs from admitted source or staging port")
        return _collect_upgrade_runtime_readback(expected, source_sha, port, runner=runner,
                                                 endpoint_check=endpoint_check)
    endpoint_check()
    raw_ids = runner([DOCKER_CLI, "--host", DOCKER_ENDPOINT, "ps", "--all", "--no-trunc",
                      "--filter", f"label=com.docker.compose.project={PROJECT}", "--format", "{{.ID}}"],
                     env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "DOCKER_CONFIG": "/nonexistent",
                          "DOCKER_HOST": DOCKER_ENDPOINT, "LANG": "C"},
                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                     text=True, timeout=20, check=False)
    if raw_ids.returncode != 0:
        raise ExecutorError("Could not enumerate the exact staging project containers")
    container_ids = [line for line in raw_ids.stdout.splitlines() if line]
    if (len(container_ids) != 4 or len(set(container_ids)) != 4
            or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in container_ids)):
        raise ExecutorError("Staging project does not have exactly four uniquely identified services")
    containers: dict[str, dict] = {}
    images: dict[str, dict] = {}
    for container_id in container_ids:
        container = _docker_json(["inspect", "--format", "{{json .}}", container_id], runner=runner)
        if not isinstance(container, dict) or container.get("Id") != container_id:
            raise ExecutorError("Staging container readback identity is invalid")
        config = container.get("Config")
        labels = config.get("Labels") if isinstance(config, dict) else None
        if not isinstance(labels, dict) or labels.get("com.docker.compose.project") != PROJECT:
            raise ExecutorError("Staging container is outside the fixed Compose project")
        service = labels.get("com.docker.compose.service")
        if service not in {"db", "backend", "frontend", "nginx"} or service in containers:
            raise ExecutorError("Staging project contains an unexpected or duplicate service")
        if str(labels.get("com.docker.compose.oneoff", "false")).lower() != "false":
            raise ExecutorError("One-off staging containers are not part of the running project")
        state = container.get("State")
        if not isinstance(state, dict) or state.get("Status") != "running":
            raise ExecutorError("Every admitted staging service must be running")
        network_settings = container.get("NetworkSettings")
        network_map = network_settings.get("Networks") if isinstance(network_settings, dict) else None
        expected_networks = ({ISOLATED_NETWORK, INGRESS_NETWORK} if service == "nginx" else {ISOLATED_NETWORK})
        if not isinstance(network_map, dict) or set(network_map) != expected_networks:
            raise ExecutorError("Staging service network attachments differ from the fixed layout")
        health_state = state.get("Health")
        if service == "db" and (not isinstance(health_state, dict) or health_state.get("Status") != "healthy"):
            raise ExecutorError("Staging database is not healthy")
        if service == "nginx":
            bindings = container.get("HostConfig", {}).get("PortBindings", {})
            if bindings.get("8080/tcp") != [{"HostIp": "127.0.0.1", "HostPort": str(port)}]:
                raise ExecutorError("Staging nginx does not have the exact loopback port binding")
        image_id = container.get("Image")
        if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise ExecutorError("Staging service image ID is malformed")
        image = _docker_json(["image", "inspect", "--format", "{{json .}}", image_id], runner=runner)
        if not isinstance(image, dict) or image.get("Id") != image_id:
            raise ExecutorError("Staging image readback differs from the running container")
        image_config = image.get("Config")
        image_labels = image_config.get("Labels") if isinstance(image_config, dict) else None
        if service in {"backend", "frontend"}:
            revision = image_labels.get("org.opencontainers.image.revision") if isinstance(image_labels, dict) else None
            if revision != source_sha:
                raise ExecutorError("Running application image revision does not match the admitted source")
        elif service == "db":
            if config.get("Image") != DATABASE_IMAGE or not any(
                isinstance(digest, str) and digest.endswith("@" + DATABASE_IMAGE.split("@", 1)[1])
                for digest in image.get("RepoDigests", [])
            ):
                raise ExecutorError("Running staging database image is not the pinned immutable digest")
        elif service == "nginx":
            if config.get("Image") != NGINX_IMAGE or not any(
                isinstance(digest, str) and digest.endswith("@" + NGINX_IMAGE.split("@", 1)[1])
                for digest in image.get("RepoDigests", [])
            ):
                raise ExecutorError("Running staging nginx image is not the pinned immutable digest")
        containers[service] = container
        images[service] = image
    if set(containers) != {"db", "backend", "frontend", "nginx"}:
        raise ExecutorError("Staging service inventory is incomplete")
    if _published_container_ids(port, runner=runner) != {containers["nginx"]["Id"]}:
        raise ExecutorError("Unexpected containers publish the fixed staging loopback port")
    return {
        "status": "verified",
        "source_sha": source_sha,
        "container_ids": {service: containers[service]["Id"] for service in sorted(containers)},
        "image_digests": {service: images[service]["Id"] for service in sorted(images)},
        "app_image_revisions": {
            service: images[service]["Config"]["Labels"]["org.opencontainers.image.revision"]
            for service in ("backend", "frontend")
        },
        "nginx_loopback_binding": {"host_ip": "127.0.0.1", "host_port": port, "container_port": "8080/tcp"},
    }


def _collect_upgrade_runtime_readback(expected: dict, source_sha: str, port: int, *,
                                      runner: Callable[..., Any], endpoint_check: Callable[[], None]) -> dict:
    endpoint_check()
    plan = expected["plan"]
    cutover = plan["cutover_plan"]
    candidate_db = plan["candidate_database"]
    source_db = plan["source_database"]
    new_project = expected["project"]
    old_project = expected["retained"]["project"]
    new_services = expected["services"]
    old_services = {"db": expected["retained"]["db"], **dict(zip(
        ("backend", "frontend", "nginx"), expected["retained"]["stopped_application_ids"]
    ))}

    def project_ids(project: str) -> list[str]:
        result = runner([DOCKER_CLI, "--host", DOCKER_ENDPOINT, "ps", "--all", "--no-trunc",
                         "--filter", f"label=com.docker.compose.project={project}", "--format", "{{.ID}}"],
                        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "DOCKER_CONFIG": "/nonexistent",
                             "DOCKER_HOST": DOCKER_ENDPOINT, "LANG": "C"},
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                        text=True, timeout=20, check=False)
        if result.returncode != 0:
            raise ExecutorError("Could not enumerate the fixed retained/candidate projects")
        ids = [line for line in result.stdout.splitlines() if line]
        if (len(ids) != 4 or len(set(ids)) != 4
                or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in ids)):
            raise ExecutorError("Retained/candidate project has missing or unexpected containers")
        return ids

    actual_new_ids = project_ids(new_project)
    actual_old_ids = project_ids(old_project)
    if set(actual_new_ids) != set(new_services.values()) or set(actual_old_ids) != set(old_services.values()):
        raise ExecutorError("Retained/candidate project container IDs differ from the adopted cutover plan")
    # `docker ps` reports active publishers only. The stopped retained nginx
    # remains independently pinned and checked below through project inventory,
    # exact container inspection, exited state, and its HostConfig binding.
    if _published_container_ids(port, runner=runner) != {expected["services"]["nginx"]}:
        raise ExecutorError("Unexpected containers publish the fixed staging loopback port")

    def inspect_container(container_id: str) -> dict:
        value = _docker_json(["inspect", "--format", "{{json .}}", container_id], runner=runner)
        if not isinstance(value, dict) or value.get("Id") != container_id:
            raise ExecutorError("Retained/candidate container inspection identity is invalid")
        return value

    def inspect_image(image_id: str) -> dict:
        value = _docker_json(["image", "inspect", "--format", "{{json .}}", image_id], runner=runner)
        if not isinstance(value, dict) or value.get("Id") != image_id:
            raise ExecutorError("Retained/candidate image inspection identity is invalid")
        return value

    network_cache: dict[tuple[str, str], dict] = {}

    def verify_network(project: str, name: str, expected_id: str, container_network_id: str) -> None:
        if container_network_id != expected_id:
            raise ExecutorError("Container network attachment differs from the adopted cutover plan")
        cache_key = (project, name)
        if cache_key not in network_cache:
            value = _docker_json(["network", "inspect", "--format", "{{json .}}", name], runner=runner)
            if not isinstance(value, dict):
                raise ExecutorError("Retained/candidate network inspection is malformed")
            labels = value.get("Labels") or {}
            expected_network_label = name.removeprefix(project + "_")
            if (value.get("Id") != expected_id or value.get("Name") != name
                    or value.get("Driver") != "bridge"
                    or labels.get("com.docker.compose.project") != project
                    or labels.get("com.docker.compose.network") != expected_network_label
                    or value.get("Internal") != (expected_network_label == "isolated")):
                raise ExecutorError("Retained/candidate network identity or isolation differs from the plan")
            network_cache[cache_key] = value

    containers = {old_project: {}, new_project: {}}
    images = {old_project: {}, new_project: {}}
    expected_sets = {
        (old_project, "db"): {"id": old_services["db"],
                               "configuration_sha256": source_db["configuration_sha256"],
                               "network_ids": source_db["network_ids"]},
        (new_project, "db"): {"id": new_services["db"],
                               "configuration_sha256": candidate_db["configuration_sha256"],
                               "network_ids": {new_project + "_isolated": candidate_db["network_id"]}},
    }
    for side, project, service_pins, active_services in (
        ("old", old_project, cutover["old"], {"db"}),
        ("new", new_project, cutover["new"], {"db", "backend", "frontend", "nginx"}),
    ):
        for service in ("backend", "frontend", "nginx"):
            pin = service_pins[service]
            expected_sets[(project, service)] = pin
        for service in ("db", "backend", "frontend", "nginx"):
            pin = expected_sets[(project, service)]
            container_id = pin["id"]
            container = inspect_container(container_id)
            labels = container.get("Config", {}).get("Labels") or {}
            if (labels.get("com.docker.compose.project") != project
                    or labels.get("com.docker.compose.service") != service
                    or str(labels.get("com.docker.compose.oneoff", "false")).lower() != "false"):
                raise ExecutorError("Retained/candidate container project or service labels are invalid")
            state = container.get("State")
            if not isinstance(state, dict):
                raise ExecutorError("Retained/candidate container state is missing")
            running = state.get("Status") == "running"
            if service in active_services and not running:
                raise ExecutorError("Expected retained/candidate service is not running")
            if service not in active_services and (running or state.get("Status") != "exited"):
                raise ExecutorError("Old application services must remain stopped after cutover")
            if service == "db" and (not isinstance(state.get("Health"), dict)
                                     or state["Health"].get("Status") != "healthy"):
                raise ExecutorError("Retained/candidate database health is not confirmed")
            config = container.get("Config")
            if not isinstance(config, dict) or not isinstance(config.get("Image"), str):
                raise ExecutorError("Retained/candidate container image reference is unavailable")
            if pin["configuration_sha256"] is not None and _configuration_digest(container) != pin["configuration_sha256"]:
                raise ExecutorError("Retained/candidate container configuration differs from the adopted plan")
            image_id = container.get("Image")
            if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
                raise ExecutorError("Retained/candidate image ID is malformed")
            image = inspect_image(image_id)
            if service in {"backend", "frontend"}:
                expected_revision = source_sha if side == "new" else OLD_APPLICATION
                image_labels = image.get("Config", {}).get("Labels") or {}
                if image_labels.get("org.opencontainers.image.revision") != expected_revision:
                    raise ExecutorError("Retained/candidate application OCI revision differs from its authorized SHA")
            elif service == "db":
                if (config["Image"] != DATABASE_IMAGE or not any(
                    isinstance(digest, str) and digest.endswith("@" + DATABASE_IMAGE.split("@", 1)[1])
                    for digest in image.get("RepoDigests", [])
                )):
                    raise ExecutorError("Retained/candidate database image digest is not the fixed PostgreSQL pin")
            elif service == "nginx":
                if (config["Image"] != NGINX_IMAGE or not any(
                    isinstance(digest, str) and digest.endswith("@" + NGINX_IMAGE.split("@", 1)[1])
                    for digest in image.get("RepoDigests", [])
                )):
                    raise ExecutorError("Retained/candidate nginx image digest is not the fixed pin")
            network_map = container.get("NetworkSettings", {}).get("Networks")
            expected_networks = pin["network_ids"]
            if (not isinstance(network_map, dict) or set(network_map) != set(expected_networks)
                    or any(not isinstance(item, dict) for item in network_map.values())):
                raise ExecutorError("Retained/candidate service network set differs from its plan")
            for network_name, network in network_map.items():
                network_id = expected_networks[network_name]
                if not isinstance(network_id, str) or not _is_sha(network_id):
                    raise ExecutorError("Retained/candidate network ID is malformed")
                verify_network(project, network_name, network_id, network.get("NetworkID", ""))
            mounts = container.get("Mounts")
            if not isinstance(mounts, list):
                raise ExecutorError("Retained/candidate mount inventory is malformed")
            volume_mounts = [mount for mount in mounts if isinstance(mount, dict) and mount.get("Type") == "volume"]
            if service == "db":
                expected_volume = (old_project + "_staging-postgres-data" if side == "old"
                                   else candidate_db["volume_name"])
                if len(volume_mounts) != 1 or volume_mounts[0].get("Name") != expected_volume or volume_mounts[0].get("Destination") != "/var/lib/postgresql/data":
                    raise ExecutorError("Retained/candidate DB volume differs from its approved identity")
            elif service == "backend":
                if (len(volume_mounts) != 1 or volume_mounts[0].get("Name") != expected["retained"]["volumes"]["books"]
                        or volume_mounts[0].get("Destination") != "/home/node"):
                    raise ExecutorError("Candidate application no longer uses the retained book volume")
            elif service in {"frontend", "nginx"} and volume_mounts:
                raise ExecutorError("Retained/candidate frontend or nginx has unexpected named volumes")
            if service == "nginx":
                bindings = container.get("HostConfig", {}).get("PortBindings", {})
                if bindings.get("8080/tcp") != [{"HostIp": "127.0.0.1", "HostPort": str(port)}]:
                    raise ExecutorError("Retained/candidate nginx port binding is not the exact loopback endpoint")
            containers[project][service] = container
            images[project][service] = image

    return {
        "status": "verified",
        "source_sha": source_sha,
        "topology": "isolated-copy",
        "operation_id": expected["operation_id"],
        "plan_sha256": expected["plan_sha256"],
        "container_ids": {
            "candidate": {name: containers[new_project][name]["Id"] for name in ("db", "backend", "frontend", "nginx")},
            "retained": {name: containers[old_project][name]["Id"] for name in ("db", "backend", "frontend", "nginx")},
        },
        "image_digests": {
            "candidate": {name: images[new_project][name]["Id"] for name in ("db", "backend", "frontend", "nginx")},
            "retained": {name: images[old_project][name]["Id"] for name in ("db", "backend", "frontend", "nginx")},
        },
        "app_image_revisions": {"backend": source_sha, "frontend": source_sha},
        "retained_app_states": {name: containers[old_project][name]["State"]["Status"]
                                 for name in ("backend", "frontend", "nginx")},
        "nginx_loopback_binding": {"host_ip": "127.0.0.1", "host_port": port, "container_port": "8080/tcp"},
    }


def _configuration_digest(container: dict) -> str:
    stable = {name: container.get(name) for name in ("Id", "Image", "Config", "HostConfig", "Mounts")}
    host_config = stable.get("HostConfig")
    if isinstance(host_config, dict):
        # Moby resolves these fields when a stopped container starts. Ignore
        # only those runtime values while retaining every other HostConfig pin.
        host_config = dict(host_config)
        host_config.pop("OomScoreAdj", None)
        host_config.pop("OomKillDisable", None)
        stable["HostConfig"] = host_config
    stable["network_names"] = sorted(container.get("NetworkSettings", {}).get("Networks", {}))
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@contextmanager
def existing_project_readback_lock():
    """Hold the launcher's existing owner lock without creating or changing it."""
    try:
        descriptor = os.open(PROJECT_LOCK_PATH, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as error:
        raise ExecutorError("The existing staging project lock is unavailable") from error
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != UID
                or stat.S_IMODE(metadata.st_mode) != 0o600):
            raise ExecutorError("The existing staging project lock has an unexpected identity")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ExecutorError("Another staging operation holds the project lock") from error
        yield
    finally:
        os.close(descriptor)


def execute_handoff(
    handoff_path: Path,
    grant_path: Path,
    *,
    runner: Callable[..., Any] = subprocess.run,
    health_probe: Callable[[int], dict] = loopback_health_probe,
    runtime_readback: Callable[..., dict] = collect_runtime_readback,
    readback_lock: Callable[[], Any] = existing_project_readback_lock,
    env_file_path: Path = ENV_FILE,
    now: int | None = None,
    hostname: str | None = None,
    uid: int | None = None,
    username: str | None = None,
    file_owner_uid: int | None = None,
) -> dict:
    """Consume one handoff through sudo's exact installed bootstrap entry."""
    actual_host = socket.gethostname() if hostname is None else hostname
    actual_uid = os.getuid() if uid is None else uid
    if username is None:
        try:
            actual_user = pwd.getpwuid(actual_uid).pw_name
        except KeyError as error:
            raise ExecutorError("Local operator identity is unavailable") from error
    else:
        actual_user = username
    if actual_host != HOST or actual_uid != UID or actual_user != PRINCIPAL:
        raise ExecutorError("Run only as the fixed owner on the staging host")

    owner_uid = UID if file_owner_uid is None else file_owner_uid
    handoff_raw = _read_regular(handoff_path, MAX_HANDOFF_BYTES, owner_uid=owner_uid,
                                modes={0o400, 0o600, 0o644})
    source_sha, handoff_id = validate_handoff(_parse_json(handoff_raw, MAX_HANDOFF_BYTES))
    grant_raw = _read_regular(grant_path, MAX_GRANT_BYTES, owner_uid=owner_uid, modes={0o400, 0o600})
    grant, grant_sha = decode_grant(grant_raw, expected_source_sha=source_sha,
                                    now=int(time.time()) if now is None else now)
    stage_port, env_digest = _staging_port(env_file_path, owner_uid=owner_uid)

    # The fixed root-owned entry alone verifies the Ed25519 signature and consumes
    # the nonce. No handoff field grants authority, and no arbitrary command path
    # or operation arguments can be supplied by this caller.
    executed = runner(_bootstrap_command(["--grant", str(grant_path)]),
                      env={"PATH": "/usr/bin:/bin", "HOME": "/home/hiroyuki", "LANG": "C"},
                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                      text=True, timeout=1830, check=False)
    if executed.returncode not in (0, 1):
        raise ExecutorError("Installed operator command did not return a bounded result")
    execute_receipt = None
    if executed.stdout:
        try:
            execute_receipt = _last_json_line(executed.stdout)
        except ExecutorError:
            # stdout can be cut off after the durable result was written; the
            # read-only nonce query below remains the completion source of truth.
            execute_receipt = None
        if execute_receipt is not None:
            validate_bootstrap_result(execute_receipt, grant, grant_sha)

    # Independently read the durable nonce record. The query is read-only and
    # cannot replay or restart the operation; it is the authoritative completion
    # evidence if child output was interrupted or the command returned nonzero.
    queried = runner(_bootstrap_command(["--receipt", grant["nonce"]]),
                     env={"PATH": "/usr/bin:/bin", "HOME": "/home/hiroyuki", "LANG": "C"},
                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     text=True, timeout=10, check=False)
    if queried.returncode != 0:
        raise ExecutorError("Installed operator has no durable receipt for the consumed nonce")
    durable = validate_bootstrap_result(_last_json_line(queried.stdout), grant, grant_sha)
    if execute_receipt is not None and execute_receipt != durable:
        raise ExecutorError("Operator execution output differs from its durable receipt")

    local_health = None
    status = "OPERATION_FAILED"
    observed_runtime = {"status": "not-queried", "source_sha": None,
                        "container_ids": None, "image_digests": None}
    if durable["exit_code"] == 0:
        try:
            with readback_lock():
                final_port, final_env_digest = _staging_port(env_file_path, owner_uid=owner_uid)
                if final_port != stage_port or final_env_digest != env_digest:
                    raise ExecutorError("Staging environment changed during the admitted operation")
                local_health = health_probe(stage_port)
                if local_health != {"url": f"http://127.0.0.1:{stage_port}{HEALTH_PATH}", "http_status": 200}:
                    raise ExecutorError("Loopback health probe returned an unexpected result")
                status = "HEALTH_PASS_REVISION_UNCONFIRMED"
                expected_runtime = durable.get("expected_runtime")
                observed_runtime = runtime_readback(source_sha, stage_port,
                                                    expected_runtime=expected_runtime)
                if expected_runtime is None:
                    expected_container_shape = {"db", "backend", "frontend", "nginx"}
                    expected_images = expected_container_shape
                else:
                    expected_container_shape = {"candidate", "retained"}
                    expected_images = expected_container_shape
                if (not isinstance(observed_runtime, dict) or observed_runtime.get("status") != "verified"
                        or observed_runtime.get("source_sha") != source_sha
                        or not isinstance(observed_runtime.get("container_ids"), dict)
                        or set(observed_runtime["container_ids"]) != expected_container_shape
                        or not isinstance(observed_runtime.get("image_digests"), dict)
                        or set(observed_runtime["image_digests"]) != expected_images
                        or observed_runtime.get("app_image_revisions") != {"backend": source_sha, "frontend": source_sha}
                        or observed_runtime.get("nginx_loopback_binding") != {
                            "host_ip": "127.0.0.1", "host_port": stage_port, "container_port": "8080/tcp"
                        }):
                    raise ExecutorError("Runtime readback did not prove the admitted running revision")
                status = "LOCAL_DEPLOYMENT_CONFIRMED"
        except ExecutorError:
            if local_health is None:
                local_health = {"url": f"http://127.0.0.1:{stage_port}{HEALTH_PATH}", "http_status": None}
            if observed_runtime.get("status") != "verified":
                observed_runtime = {"status": "unconfirmed", "source_sha": None,
                                    "container_ids": None, "image_digests": None}
            status = ("HEALTH_PASS_REVISION_UNCONFIRMED" if local_health["http_status"] == 200
                      else "LOOPBACK_HEALTH_FAILED_REVISION_UNCONFIRMED")
    return {
        "schema": "belib-staging-cd-execution/v1",
        "handoff_receipt_id": handoff_id,
        "admitted_source_sha": source_sha,
        "operation": grant["operation"],
        "host": HOST,
        "principal": PRINCIPAL,
        "project": PROJECT,
        "status": status,
        "operator_receipt": durable,
        "loopback_health": local_health,
        "running_revision_readback": observed_runtime,
        "browser_acceptance": "not-performed",
        "https_ingress_acceptance": "not-performed",
    }


def write_result(path: Path, result: dict) -> None:
    if not path.is_absolute():
        raise ExecutorError("Result path must be absolute")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(json.dumps(result, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n")
            output.flush()
            os.fsync(output.fileno())
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except OSError as error:
        raise ExecutorError("Could not create the one-time execution result") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", required=True, type=Path,
                        help="Downloaded belib-staging-handoff/v1 JSON from the existing supervisor workflow")
    parser.add_argument("--grant", required=True, type=Path,
                        help="Existing supervisor's signed one-use operator grant")
    parser.add_argument("--result", required=True, type=Path,
                        help="New absolute path for this adapter's local execution receipt")
    args = parser.parse_args(argv)
    try:
        result = execute_handoff(args.handoff, args.grant)
        write_result(args.result, result)
    except (ExecutorError, OSError, subprocess.SubprocessError, ValueError) as error:
        print(f"Staging local execution blocked: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return execution_exit_code(result)


def execution_exit_code(result: dict) -> int:
    """Only an observed runtime revision and exact local health are a pass."""
    return 0 if result.get("status") == "LOCAL_DEPLOYMENT_CONFIRMED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
