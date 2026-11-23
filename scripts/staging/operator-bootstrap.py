#!/usr/bin/env -S /usr/bin/python3 -I -B
"""Installable one-shot i5 operator entry. No install or trust adoption is implicit.

Root-owned configuration/source and the existing external public key are the
authority. No candidate module is imported as root. The admitted child runs as
hiroyuki, with a root-created sealed, PID-bound admission descriptor.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import fcntl
import hashlib
import json
import os
import pwd
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CONFIG = Path("/etc/belib-staging/operator-bootstrap.json")
RETAINED_CONFIG = Path("/etc/belib-staging/retained-upgrade.json")
INSTALL = Path("/usr/local/libexec/belib-staging/operator-bootstrap.py")
SOURCES = Path("/opt/belib-staging/sources")
STATE = Path("/var/lib/belib-staging/operator")
PREPARATIONS = Path("/home/hiroyuki/.local/state/belib-staging/retained-upgrades")
TLS_COMPLETIONS = Path("/var/lib/belib-staging/tls-serve")
PUBLIC_KEY = b"-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAw1k8oqGkMOUb8KJb6thJY5GUtENwWuLGvWrKi6oBXaA=\n-----END PUBLIC KEY-----\n"
PUBLIC_FINGERPRINT = "8f49fad277be0d1c05bf0b75246a5895a8b3489ddaaeef4f43a3e8b90e25e629"
TARGET = {"repository": "hiroyuki9614/book_library", "pull_request": 103,
          "host": "ubuntu-worker", "principal": "hiroyuki", "uid": 1000,
          "project": "belib-isolated-i5", "branch": "codex/belib-i5-isolated-staging-20261107"}
SAFE_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/home/hiroyuki", "LANG": "C"}
PRIOR_SOURCE = "6963103fd6bec332b4057457d07b890fe5c11e73"
RETAINED_DB_ID = "9ab9d141599e8b33f51a1e4e9bcd16d66e92b9d134889014d9d9bd618b42f73d"
MAX_GRANT = 16384
MAX_CONFIG = 4 * 1024 * 1024
MAX_TLS_COMPLETION = 16384
TLS_COMPLETION_SCHEMA = "belib-tls-owner-completion/v1"
TLS_COMPLETION_SCHEMA_V2 = "belib-tls-owner-completion/v2"
TLS_COMPLETION_FIELDS = {
    "schema", "owner_nonce", "plan_sha256", "prepare_receipt_sha256",
    "upgrade_receipt_nonce", "upgrade_receipt_sha256", "source_sha", "operation_id", "project",
    "previous_nginx_id", "nginx_id", "nginx_configuration_sha256", "nginx_image_id",
    "nginx_network_ids", "tls_config_sha256", "certificate_sha256", "unchanged_runtime_sha256",
    "approval_nonce", "evidence_sha256", "serve_config_sha256", "serve_etag", "completed_at", "status",
}
TLS_COMPLETION_FIELDS_V2 = TLS_COMPLETION_FIELDS | {
    "prior_generation_sha256", "prior_tls_owner_nonce", "prior_tls_completion_sha256"}


class Blocked(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Blocked("Duplicate JSON field")
        result[key] = value
    return result


def parse(raw, limit):
    if not isinstance(raw, bytes) or not raw or len(raw) > limit:
        raise Blocked("Input size is invalid")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(Blocked("Invalid JSON number")))
        if canonical(value) != raw:
            raise Blocked("Input must be canonical JSON")
        return value
    except (ValueError, UnicodeError, TypeError) as error:
        raise Blocked("Malformed JSON") from error


def trusted_path(path, *, directory=False):
    if not path.is_absolute() or path.resolve() != path:
        raise Blocked("Trusted path is not absolute and symlink-free")
    for item in [*reversed(path.parents), path]:
        info = item.lstat()
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022 or stat.S_ISLNK(info.st_mode):
            raise Blocked("Trusted path owner or mode is unsafe")
    info = path.lstat()
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise Blocked("Trusted directory is missing")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise Blocked("Trusted file is not a single regular file")


def read_regular(path, limit, *, trusted=False):
    if trusted:
        trusted_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise Blocked("Input is not a bounded regular file")
        data = os.read(fd, limit + 1)
        if len(data) > limit:
            raise Blocked("Input exceeds its limit")
        return data
    finally:
        os.close(fd)


def validate_grant(envelope_raw, source_sha):
    envelope = parse(envelope_raw, MAX_GRANT)
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
        raise Blocked("Grant envelope fields are invalid")
    decoded = []
    for name in ("payload", "signature"):
        try:
            data = base64.b64decode(envelope[name], validate=True)
            if base64.b64encode(data).decode() != envelope[name]:
                raise ValueError()
            decoded.append(data)
        except (ValueError, TypeError) as error:
            raise Blocked("Grant encoding is invalid") from error
    raw, signature = decoded
    grant = parse(raw, MAX_GRANT)
    fields = set(TARGET) | {"schema", "source_sha", "operation", "nonce", "evidence_sha256", "issued_at", "expires_at"}
    if not isinstance(grant, dict):
        raise Blocked("Grant payload must be an object")
    version2 = grant.get("schema") == "belib-staging-bootstrap-approval/v2"
    if version2 and grant.get("operation") != "upgrade-retained":
        raise Blocked("Approval v2 is restricted to retained-generation upgrades")
    if grant.get("operation") == "resume-ingress":
        fields |= {"prior_source_sha", "retained_state_sha256"}
    if grant.get("operation") == "upgrade-retained":
        fields |= {"prior_application_sha", "retained_state_sha256", "backup_sha256", "restore_attestation_sha256"}
        if version2:
            fields |= {"prior_generation_sha256"}
    if (not isinstance(grant, dict) or set(grant) != fields
            or grant.get("operation") not in {"deploy", "resume-ingress", "upgrade-retained"}
            or grant.get("schema") not in {"belib-staging-bootstrap-approval/v1", "belib-staging-bootstrap-approval/v2"}
            or grant.get("source_sha") != source_sha
            or any(grant.get(k) != v for k, v in TARGET.items())
            or any(type(grant.get(k)) is not int for k in ("uid", "pull_request", "issued_at", "expires_at"))
            or any(not isinstance(grant.get(k), str) or not re.fullmatch(r"[0-9a-f]{64}", grant[k]) for k in ("nonce", "evidence_sha256"))):
        raise Blocked("Grant does not match the fixed operation/target/source")
    now = int(time.time())
    if not (now - 300 <= grant["issued_at"] <= now + 5 and now < grant["expires_at"] <= grant["issued_at"] + 300):
        raise Blocked("Grant is expired or outside its lifetime")
    if grant["operation"] == "resume-ingress" and (
            grant["prior_source_sha"] != PRIOR_SOURCE or not isinstance(grant["retained_state_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", grant["retained_state_sha256"])):
        raise Blocked("Retained-state binding is invalid")
    if grant["operation"] == "upgrade-retained" and (
            (not version2 and grant["prior_application_sha"] != "81256268276d7ead95029849037f69ec6beec202")
            or not re.fullmatch(r"[0-9a-f]{40}", grant.get("prior_application_sha", ""))
            or any(not isinstance(grant[name], str) or not re.fullmatch(r"[0-9a-f]{64}", grant[name])
                   for name in ("retained_state_sha256", "backup_sha256", "restore_attestation_sha256"))
            or (version2 and not re.fullmatch(r"[0-9a-f]{64}", grant.get("prior_generation_sha256", "")))):
        raise Blocked("Retained upgrade prior state or evidence binding is invalid")
    if len(signature) != 64 or hashlib.sha256(PUBLIC_KEY).hexdigest() != PUBLIC_FINGERPRINT:
        raise Blocked("Signature or adopted public-key pin is invalid")
    trusted_path(Path("/usr/bin/openssl"))
    # Never use inherited TMPDIR (or tempfile's cached default): even a mode-0700
    # child can be renamed/replaced by the owner of its untrusted parent.
    trusted_path(STATE, directory=True)
    if stat.S_IMODE(STATE.stat().st_mode) != 0o700:
        raise Blocked("Operator state must be private mode 0700")
    with tempfile.TemporaryDirectory(prefix="verify-", dir=STATE) as directory:
        root = Path(directory)
        (root / "public.pem").write_bytes(PUBLIC_KEY)
        (root / "payload").write_bytes(raw)
        (root / "signature").write_bytes(signature)
        result = subprocess.run(["/usr/bin/openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(root / "public.pem"),
                                 "-rawin", "-in", str(root / "payload"), "-sigfile", str(root / "signature")],
            env={"PATH": "/usr/bin:/bin", "LANG": "C"}, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
        if result.returncode:
            raise Blocked("External grant signature is invalid")
    return grant


def verify_source(config):
    if (not isinstance(config, dict) or set(config) != {"schema", "enabled", "source_sha", "manifest"}
            or config.get("schema") != "belib-staging-operator/v1" or config.get("enabled") is not True
            or not isinstance(config.get("source_sha"), str) or not re.fullmatch(r"[0-9a-f]{40}", config["source_sha"])
            or not isinstance(config.get("manifest"), dict) or not config["manifest"]):
        raise Blocked("Installed source configuration is disabled or invalid")
    root = SOURCES / config["source_sha"]
    trusted_path(root, directory=True)
    actual = {}
    files = 0
    total_bytes = 0
    for parent, directories, names in os.walk(root, followlinks=False):
        for name in directories:
            trusted_path(Path(parent) / name, directory=True)
        for name in names:
            path = Path(parent) / name
            trusted_path(path)
            files += 1
            if files > 50000:
                raise Blocked("Installed source exceeds its file bound")
            relative = path.relative_to(root).as_posix()
            if relative.startswith(".git/"):
                continue
            contents = read_regular(path, 64 * 1024 * 1024, trusted=True)
            total_bytes += len(contents)
            if total_bytes > 512 * 1024 * 1024:
                raise Blocked("Installed source exceeds its byte bound")
            actual[relative] = {"sha256": hashlib.sha256(contents).hexdigest(), "mode": stat.S_IMODE(path.stat().st_mode)}
    if actual != config["manifest"]:
        raise Blocked("Installed source manifest does not match")
    trusted_path(Path("/usr/bin/git"))
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_NO_REPLACE_OBJECTS": "1"}
    command = ["/usr/bin/git", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "-c", f"safe.directory={root}", "-C", str(root)]
    head = subprocess.run([*command, "rev-parse", "--verify", "HEAD"], env=env, capture_output=True, text=True, timeout=10, check=True)
    dirty = subprocess.run([*command, "status", "--porcelain=v1", "--untracked-files=all"], env=env, capture_output=True, text=True, timeout=10, check=True)
    if head.stdout.strip() != config["source_sha"] or dirty.stdout:
        raise Blocked("Installed Git source is not the fixed clean revision")
    return root


def launcher_arguments(grant):
    arguments = ["--source-sha", grant["source_sha"], "--evidence-sha256", grant["evidence_sha256"],
                 "--env-file", "/home/hiroyuki/.config/belib-staging/i5.env"]
    if grant["operation"] == "resume-ingress":
        arguments += ["--prior-source-sha", grant["prior_source_sha"], "--retained-state-sha256", grant["retained_state_sha256"]]
    if grant["operation"] == "upgrade-retained":
        for name in ("prior_application_sha", "retained_state_sha256", "backup_sha256", "restore_attestation_sha256"):
            arguments += ["--" + name.replace("_", "-"), grant[name]]
        if "prior_generation_sha256" in grant:
            arguments += ["--prior-generation-sha256", grant["prior_generation_sha256"]]
    return [*arguments, grant["operation"]]


def load_retained_config(grant):
    """Only a separate root-adopted topology and evidence trust store can enable upgrade."""
    path = RETAINED_CONFIG
    if not path.exists():
        raise Blocked("Retained topology and independent evidence authorities are not adopted")
    value = parse(read_regular(path, 65536, trusted=True), 65536)
    expected_schema = "belib-retained-operator/v2" if "prior_generation_sha256" in grant else "belib-retained-operator/v1"
    config_fields={"schema","enabled","source_sha","topology","prepared_directory","authorities",
        "cutover_plan","candidate_database","source_database","migration_image"}
    if expected_schema.endswith("/v2"):
        config_fields|={"prior_project","prior_generation_sha256","environment_transition"}
    if (not isinstance(value, dict) or set(value)!=config_fields or value.get("schema") != expected_schema
            or value.get("source_sha") != grant["source_sha"] or value.get("topology") != "isolated-copy"
            or (expected_schema.endswith("/v2") and value.get("prior_generation_sha256") != grant["prior_generation_sha256"])
            or value.get("enabled") is not True):
        raise Blocked("Retained operator configuration does not enable this exact source/topology")
    return value


def verify_staging_source_access(root, fixed_entry):
    """Check the exact worker credentials can traverse and read fixed staged code."""
    allowed = {
        "scripts/staging/launch-guard.py",
        "scripts/staging/prepare-retained.py",
    }
    if fixed_entry not in allowed:
        raise Blocked("Staging source access check received an unexpected fixed entry")
    probe = (
        "import os, stat, sys\n"
        "root, entry = sys.argv[1], sys.argv[2]\n"
        "if entry not in ('scripts/staging/launch-guard.py', 'scripts/staging/prepare-retained.py'): raise SystemExit(2)\n"
        "info = os.stat(root, follow_symlinks=False)\n"
        "if not stat.S_ISDIR(info.st_mode) or not os.access(root, os.X_OK): raise SystemExit(3)\n"
        "path = os.path.join(root, entry)\n"
        "fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)\n"
        "try:\n"
        " info = os.fstat(fd)\n"
        " if not stat.S_ISREG(info.st_mode): raise SystemExit(3)\n"
        "finally: os.close(fd)\n"
    )
    try:
        result = subprocess.run(
            ["/usr/bin/python3", "-I", "-B", "-c", probe, str(root), fixed_entry],
            user=1000, group=1000, extra_groups=(),
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C"},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise Blocked(
            "Staging source access could not be verified for worker UID/GID 1000; "
            "verify source directory traversal and fixed entry permissions"
        ) from error
    if result.returncode != 0:
        raise Blocked(
            "Staging source is not readable by worker UID/GID 1000; "
            "verify source directory traversal and fixed entry permissions"
        )

def validate_generation_predecessor(active, config):
    """Require the adopted cutover source pins to be the exact active pointer."""
    plan = config.get("cutover_plan") if isinstance(config, dict) else None
    source = config.get("source_database") if isinstance(config, dict) else None
    if not isinstance(plan, dict) or not isinstance(source, dict):
        raise Blocked("Retained v2 configuration lacks predecessor resource pins")
    prior_project = active["project"]
    old = plan.get("old")
    expected_db = active["services"]["db"]
    if (plan.get("schema") != "belib-isolated-copy-cutover/v2"
            or plan.get("prior_project") != prior_project
            or plan.get("prior_application_sha") != active["source_sha"]
            or plan.get("prior_generation_sha256") != hashlib.sha256(canonical(active)).hexdigest()
            or plan.get("prior_http_port") != active["http_port"]
            or plan.get("prior_tls_loopback_port") != active["tls_loopback_port"]
            or config.get("prior_project") != prior_project
            or config.get("prior_generation_sha256") != plan.get("prior_generation_sha256")
            or source != {"id": expected_db["id"],
                          "configuration_sha256": expected_db["configuration_sha256"],
                          "network_ids": expected_db["network_ids"],
                          "volume_name": active["database_volume"],
                          "image_id": expected_db["image_id"]}
            or not isinstance(old, dict) or set(old) != {"backend", "frontend", "nginx"}):
        raise Blocked("Retained v2 configuration does not bind the exact active predecessor")
    for service, pin in old.items():
        active_pin = active["services"][service]
        if pin != {"id": active_pin["id"],
                   "configuration_sha256": active_pin["configuration_sha256"],
                   "network_ids": active_pin["network_ids"],
                   "image_id": active_pin["image_id"]}:
            raise Blocked("Retained v2 application pins differ from the active predecessor")


def consume_nonce(grant):
    trusted_path(STATE, directory=True)
    if stat.S_IMODE(STATE.stat().st_mode) != 0o700:
        raise Blocked("Replay directory must be root-only")
    path = STATE / (grant["nonce"] + ".used")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        info=path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1
                or stat.S_IMODE(info.st_mode)!=0o600):
            raise Blocked("Replay journal metadata is unsafe")
        recorded=parse(read_regular(path,4096,trusted=True),4096)
        expected={"grant_sha256":hashlib.sha256(canonical(grant)).hexdigest(),"source_sha":grant["source_sha"]}
        if recorded!=expected:
            raise Blocked("Nonce was already consumed by a different grant")
        result_path=_result_path(grant["nonce"])
        try:
            result_info=result_path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISREG(result_info.st_mode) and result_info.st_uid==0 and result_info.st_nlink==1 and stat.S_IMODE(result_info.st_mode)==0o600:
            raise Blocked("Nonce already has a durable result; use the read-only receipt query")
        raise Blocked("Existing nonce result metadata is unsafe")
    with os.fdopen(fd, "wb") as stream:
        stream.write(canonical({"grant_sha256": hashlib.sha256(canonical(grant)).hexdigest(), "source_sha": grant["source_sha"]}))
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _result_path(nonce):
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{64}", nonce):
        raise Blocked("Receipt nonce is malformed")
    trusted_path(STATE, directory=True)
    if stat.S_IMODE(STATE.stat().st_mode) != 0o700:
        raise Blocked("Receipt directory must be root-only")
    return STATE / (nonce + ".result.json")


def expected_retained_runtime(config, active_generation=None, prior_tls_completion=None):
    plan = config["cutover_plan"]
    generation = plan.get("schema") == "belib-isolated-copy-cutover/v2"
    config_fields={"schema","enabled","source_sha","topology","prepared_directory","authorities",
        "cutover_plan","candidate_database","source_database","migration_image"}
    if generation: config_fields|={"prior_project","prior_generation_sha256","environment_transition"}
    if not isinstance(config,dict) or (generation and set(config)!=config_fields):
        raise Blocked("Retained operator configuration fields do not match its version")
    if generation:
        transition=config.get("environment_transition")
        if (not isinstance(transition,dict) or set(transition)!={"prior_sha256","candidate_sha256","prior_source_sha","candidate_source_sha"}
                or any(not isinstance(transition.get(name),str) or not re.fullmatch(r"[0-9a-f]{64}",transition[name])
                       for name in ("prior_sha256","candidate_sha256"))
                or transition.get("prior_source_sha")!=plan.get("prior_application_sha")
                or transition.get("candidate_source_sha")!=config.get("source_sha")
                or config.get("prior_generation_sha256")!=plan.get("prior_generation_sha256")
                or config.get("prior_project")!=plan.get("prior_project")):
            raise Blocked("Retained environment transition does not match the adopted predecessor")
    authorities = config.get("authorities")
    if (not isinstance(authorities, dict) or set(authorities) != {"restore", "network", "roots"}
            or any(not isinstance(pin, dict) or set(pin) != {"issuer", "public_key_pem", "fingerprint"}
                   or not isinstance(pin.get("issuer"), str) or not pin["issuer"]
                   or not isinstance(pin.get("public_key_pem"), str) or not pin["public_key_pem"]
                   or not isinstance(pin.get("fingerprint"), str)
                   or not re.fullmatch(r"[0-9a-f]{64}", pin["fingerprint"])
                   for pin in authorities.values())):
        raise Blocked("Retained authority pins are malformed")
    public_plan = {"cutover_plan": plan, "candidate_database": config["candidate_database"],
                   "source_database": config["source_database"]}
    if generation:
        public_plan["environment_transition"] = config["environment_transition"]
    value = {"project": TARGET["project"] + "-upgrade-" + plan["operation_id"],
             "operation_id": plan["operation_id"], "plan": public_plan,
             "services": {"db": config["candidate_database"]["id"],
                          **{service: plan["new"][service]["id"] for service in ("backend", "frontend", "nginx")}},
             "retained": {"project": plan.get("prior_project", TARGET["project"]),
                          "db": config["source_database"].get("id", RETAINED_DB_ID),
                          "volumes": {"database": config["source_database"].get("volume_name", TARGET["project"] + "_staging-postgres-data"),
                                      "books": TARGET["project"] + "_staging-book-files"},
                          "stopped_application_ids": [plan["old"][service]["id"] for service in ("backend", "frontend", "nginx")]},
             "port": plan["port"],
             "authority_pins_sha256": hashlib.sha256(canonical(authorities)).hexdigest(),
             "plan_sha256": hashlib.sha256(canonical(public_plan)).hexdigest()}
    if generation:
        value["prior_project"] = plan["prior_project"]
        value["prior_application_sha"] = plan["prior_application_sha"]
        value["prior_generation_sha256"] = plan["prior_generation_sha256"]
        if (not isinstance(active_generation, dict) or not isinstance(prior_tls_completion, dict)
                or prior_tls_completion.get("owner_nonce") != active_generation.get("tls_owner_nonce")
                or hashlib.sha256(canonical(prior_tls_completion)).hexdigest() != active_generation.get("tls_completion_sha256")
                or prior_tls_completion.get("source_sha") != plan.get("prior_application_sha")
                or prior_tls_completion.get("project") != plan.get("prior_project")):
            raise Blocked("Prior TLS completion does not match the active-generation predecessor")
        value["prior_tls_completion"] = {"owner_nonce": prior_tls_completion["owner_nonce"],
            "completion_sha256": active_generation["tls_completion_sha256"],
            "serve_config_sha256": prior_tls_completion["serve_config_sha256"],
            "serve_etag": prior_tls_completion["serve_etag"],
            "certificate_sha256": prior_tls_completion["certificate_sha256"]}
    return value


def write_result(grant, code, retained_config=None, active_generation=None, expected_runtime=None):
    if type(code) is not int or not -128 <= code <= 255:
        raise Blocked("Executor exit status is invalid")
    result = {"schema": "belib-staging-operation-result/v1", "nonce": grant["nonce"],
              "grant_sha256": hashlib.sha256(canonical(grant)).hexdigest(),
              **{name: grant[name] for name in ("source_sha", "operation", "host", "principal", "uid", "project")},
              "exit_code": code, "completed_at": int(time.time()), "nonce_consumed": True}
    if code == 0 and grant["operation"] == "upgrade-retained" and retained_config is not None:
        # This is an expected adopted topology, NOT an observation of Docker.
        # The nonroot CD adapter independently inspects these IDs and revisions.
        if expected_runtime is None:
            prior_tls = None
            if "prior_generation_sha256" in grant:
                if (active_generation is None
                        or hashlib.sha256(canonical(active_generation)).hexdigest() != grant["prior_generation_sha256"]
                        or active_generation.get("source_sha") != grant.get("prior_application_sha")):
                    raise Blocked("Prior TLS completion selector differs from the active-generation pointer")
                prior_tls = read_tls_receipt(active_generation["tls_owner_nonce"])
            expected_runtime = expected_retained_runtime(retained_config, active_generation, prior_tls)
        result["expected_runtime"] = expected_runtime
    path = _result_path(grant["nonce"])
    # A result is immutable. Duplicate execution is already denied by the nonce
    # journal; never replace a completed result with a second claimant's result.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(canonical(result))
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return result


def read_result(nonce):
    path = _result_path(nonce)
    result = parse(read_regular(path, MAX_GRANT, trusted=True), MAX_GRANT)
    fields = {"schema", "nonce", "grant_sha256", "source_sha", "operation", "host", "principal", "uid", "project",
              "exit_code", "completed_at", "nonce_consumed"}
    if isinstance(result, dict) and result.get("operation") == "upgrade-retained" and result.get("exit_code") == 0:
        fields.add("expected_runtime")
    if (not isinstance(result, dict) or set(result) != fields
            or result.get("schema") != "belib-staging-operation-result/v1" or result.get("nonce") != nonce
            or result.get("nonce_consumed") is not True
            or any(result.get(name) != TARGET[name] for name in ("host", "principal", "uid", "project"))
            or type(result.get("uid")) is not int or type(result.get("exit_code")) is not int
            or not -128 <= result["exit_code"] <= 255 or type(result.get("completed_at")) is not int
            or not isinstance(result.get("source_sha"), str) or not re.fullmatch(r"[0-9a-f]{40}", result["source_sha"])
            or not isinstance(result.get("grant_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", result["grant_sha256"])
            or result.get("operation") not in {"deploy", "resume-ingress", "upgrade-retained"}):
        raise Blocked("Stored operation result is invalid")
    return result


def _tls_completion_path(owner_nonce):
    if not isinstance(owner_nonce, str) or not re.fullmatch(r"[0-9a-f]{64}", owner_nonce):
        raise Blocked("TLS completion nonce is malformed")
    trusted_path(TLS_COMPLETIONS, directory=True)
    if stat.S_IMODE(TLS_COMPLETIONS.stat().st_mode) != 0o700:
        raise Blocked("TLS completion directory must be root-only")
    return TLS_COMPLETIONS / (owner_nonce + ".complete.json")


def read_tls_receipt(owner_nonce):
    """Read one exact root-owned TLS completion without scanning or mutating state."""
    path = _tls_completion_path(owner_nonce)
    raw = read_regular(path, MAX_TLS_COMPLETION, trusted=True)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise Blocked("TLS completion record metadata is unsafe")
    value = parse(raw, MAX_TLS_COMPLETION)
    completion_v2 = isinstance(value, dict) and value.get("schema") == TLS_COMPLETION_SCHEMA_V2
    if (not isinstance(value, dict) or set(value) != (TLS_COMPLETION_FIELDS_V2 if completion_v2 else TLS_COMPLETION_FIELDS)
            or canonical(value) != raw or value.get("schema") != (TLS_COMPLETION_SCHEMA_V2 if completion_v2 else TLS_COMPLETION_SCHEMA)
            or value.get("owner_nonce") != owner_nonce or value.get("status") != "complete"):
        raise Blocked("TLS completion fields, encoding, or identity are invalid")
    for field in ("plan_sha256", "prepare_receipt_sha256", "upgrade_receipt_nonce", "upgrade_receipt_sha256",
                  "nginx_configuration_sha256", "tls_config_sha256", "certificate_sha256",
                  "unchanged_runtime_sha256", "approval_nonce", "evidence_sha256", "serve_config_sha256"):
        if not isinstance(value[field], str) or not re.fullmatch(r"[0-9a-f]{64}", value[field]):
            raise Blocked("TLS completion digest or nonce is malformed")
    if completion_v2 and (any(not isinstance(value.get(name), str) or not re.fullmatch(r"[0-9a-f]{64}", value[name])
            for name in ("prior_generation_sha256", "prior_tls_completion_sha256"))
            or not isinstance(value.get("prior_tls_owner_nonce"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["prior_tls_owner_nonce"])):
        raise Blocked("TLS v2 completion predecessor link is malformed")
    if (not isinstance(value["source_sha"], str) or not re.fullmatch(r"[0-9a-f]{40}", value["source_sha"])
            or not isinstance(value["operation_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", value["operation_id"])
            or value["project"] != TARGET["project"] + "-upgrade-" + value["operation_id"]):
        raise Blocked("TLS completion project/source identity is invalid")
    for field in ("previous_nginx_id", "nginx_id"):
        if not isinstance(value[field], str) or not re.fullmatch(r"[0-9a-f]{64}", value[field]):
            raise Blocked("TLS completion nginx identity is malformed")
    if value["nginx_id"] == value["previous_nginx_id"]:
        raise Blocked("TLS completion nginx transition is empty")
    if (not isinstance(value["nginx_image_id"], str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["nginx_image_id"])):
        raise Blocked("TLS completion nginx image identity is malformed")
    networks = value["nginx_network_ids"]
    expected_networks = {value["project"] + "_isolated", value["project"] + "_ingress"}
    if (not isinstance(networks, dict) or set(networks) != expected_networks
            or any(not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item)
                   for item in networks.values())):
        raise Blocked("TLS completion network identities are malformed")
    etag = value["serve_etag"]
    if (not isinstance(etag, str) or not etag or len(etag) > 256
            or any(ord(char) < 0x20 or ord(char) == 0x7f for char in etag)):
        raise Blocked("TLS completion Serve ETag is malformed")
    if type(value["completed_at"]) is not int or value["completed_at"] <= 0:
        raise Blocked("TLS completion timestamp is malformed")
    return value


def _active_generation_path():
    trusted_path(STATE, directory=True)
    if stat.S_IMODE(STATE.stat().st_mode) != 0o700:
        raise Blocked("Active-generation state directory must be root-only")
    return STATE / "active-generation.json"


def _read_active_generation(*, allow_journaled_initial_promotion=False):
    path = _active_generation_path()
    try:
        raw = read_regular(path, 128 * 1024, trusted=True)
    except FileNotFoundError:
        # A journal is durable evidence that an initial promotion (or later
        # generation CAS) began. Preparation must never interpret a missing
        # pointer beside such evidence as permission to use the literal v1
        # baseline. The promoter may inspect this state only to reconcile the
        # exact receipt-pair journal it is about to commit.
        with os.scandir(STATE) as entries:
            journaled = any(entry.name.startswith("generation-") and entry.name.endswith(".json")
                            for entry in entries)
        if not allow_journaled_initial_promotion and journaled:
            raise Blocked("Active-generation pointer is missing beside a durable promotion journal")
        return None
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600):
        raise Blocked("Active-generation pointer metadata is unsafe")
    value = parse(raw, 128 * 1024)
    if (not isinstance(value, dict) or value.get("schema") not in
            {"belib-active-generation/v1", "belib-active-generation/v2"}
            or canonical(value) != raw):
        raise Blocked("Active-generation pointer is malformed")
    validate_active_generation(value)
    journal_rows = {}
    for entry in os.scandir(STATE):
        if not entry.name.startswith("generation-") or not entry.name.endswith(".json"):
            continue
        journal_path = STATE / entry.name
        try:
            journal_raw = read_regular(journal_path, 256 * 1024, trusted=True)
            journal_info = journal_path.lstat()
        except OSError as error:
            raise Blocked("Active-generation journal chain is unavailable") from error
        if (not stat.S_ISREG(journal_info.st_mode) or journal_info.st_uid != 0
                or journal_info.st_nlink != 1 or stat.S_IMODE(journal_info.st_mode) != 0o600):
            raise Blocked("Active-generation journal metadata is unsafe")
        journal = parse(journal_raw, 256 * 1024)
        if (not isinstance(journal, dict) or canonical(journal) != journal_raw
                or journal.get("schema") not in {"belib-active-generation-journal/v1", "belib-active-generation-journal/v2"}
                or set(journal) != {"schema", "previous_sha256", "generation_sha256", "generation"}
                or not isinstance(journal.get("generation"), dict)
                or hashlib.sha256(canonical(journal["generation"])).hexdigest() != journal.get("generation_sha256")):
            raise Blocked("Active-generation journal record is malformed")
        journal_rows[journal["generation_sha256"]] = journal
    active_digest = hashlib.sha256(canonical(value)).hexdigest()
    current_journal = journal_rows.get(active_digest)
    if (current_journal is None or current_journal["generation"] != value
            or current_journal["schema"] != ("belib-active-generation-journal/v2" if value["schema"] == "belib-active-generation/v2"
                                               else "belib-active-generation-journal/v1")):
        raise Blocked("Active-generation pointer is not backed by its immutable journal")
    if value["schema"] == "belib-active-generation/v2":
        if (current_journal["previous_sha256"] != value["previous_generation_sha256"]
                or value["previous_generation_sha256"] not in journal_rows
                or value["genesis_generation_sha256"] not in journal_rows
                or value["previous_generation_sha256"] == active_digest):
            raise Blocked("Active-generation pointer has a broken immutable predecessor chain")
        cursor=value["previous_generation_sha256"]
        visited=set()
        while cursor != value["genesis_generation_sha256"]:
            if cursor in visited:
                raise Blocked("Active-generation journal chain contains a cycle")
            visited.add(cursor)
            parent=journal_rows.get(cursor)
            if (parent is None or parent.get("schema")!="belib-active-generation-journal/v2"
                    or not isinstance(parent.get("previous_sha256"),str)
                    or not re.fullmatch(r"[0-9a-f]{64}",parent["previous_sha256"])):
                raise Blocked("Active-generation journal chain skips an immutable predecessor")
            cursor=parent["previous_sha256"]
        genesis=journal_rows[value["genesis_generation_sha256"]]
        if genesis.get("schema")!="belib-active-generation-journal/v1" or genesis.get("previous_sha256") is not None:
            raise Blocked("Active-generation ancestry does not terminate at the v1 genesis")
    elif current_journal["previous_sha256"] is not None:
        raise Blocked("Genesis active-generation journal has an unexpected predecessor")
    return value


def validate_active_generation(value):
    fields = {"schema", "host", "principal", "uid", "source_sha", "operation_id", "project",
        "upgrade_receipt_nonce", "upgrade_receipt_sha256", "tls_owner_nonce", "tls_completion_sha256",
        "authority_pins_sha256", "services", "database_volume", "shared_books_volume", "http_port",
        "tls_loopback_port", "certificate_sha256"}
    version = value.get("schema") if isinstance(value, dict) else None
    if version == "belib-active-generation/v2":
        fields |= {"previous_generation_sha256", "genesis_generation_sha256", "staging_env_sha256",
                   "serve_config_sha256", "serve_etag"}
    if (not isinstance(value, dict) or set(value) != fields or version not in
            {"belib-active-generation/v1", "belib-active-generation/v2"}
            or any(value.get(name) != TARGET[name] for name in ("host", "principal", "uid"))
            or not isinstance(value.get("source_sha"), str) or not re.fullmatch(r"[0-9a-f]{40}", value["source_sha"])
            or not isinstance(value.get("operation_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", value["operation_id"])
            or value.get("project") != TARGET["project"] + "-upgrade-" + value["operation_id"]):
        raise Blocked("Active-generation identity is malformed")
    if version == "belib-active-generation/v2" and any(
            not isinstance(value.get(name), str) or not re.fullmatch(r"[0-9a-f]{64}", value[name])
            for name in ("previous_generation_sha256", "genesis_generation_sha256", "staging_env_sha256")):
        raise Blocked("Active-generation lineage digest is malformed")
    if version == "belib-active-generation/v2" and (
            not isinstance(value.get("serve_config_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["serve_config_sha256"])
            or not isinstance(value.get("serve_etag"), str) or not value["serve_etag"]
            or len(value["serve_etag"]) > 256
            or any(ord(char) < 0x20 or ord(char) == 0x7f for char in value["serve_etag"])):
        raise Blocked("Active-generation Serve config pin is malformed")
    for name in ("upgrade_receipt_nonce", "upgrade_receipt_sha256", "tls_owner_nonce",
                 "tls_completion_sha256", "authority_pins_sha256", "certificate_sha256"):
        if not isinstance(value.get(name), str) or not re.fullmatch(r"[0-9a-f]{64}", value[name]):
            raise Blocked("Active-generation receipt or configuration digest is malformed")
    project = value["project"]
    if (value.get("database_volume") != project + "_staging-postgres-data"
            or value.get("shared_books_volume") != TARGET["project"] + "_staging-book-files"
            or type(value.get("http_port")) is not int or not 1024 <= value["http_port"] <= 65535
            or value.get("tls_loopback_port") != 18443):
        raise Blocked("Active-generation resource or endpoint identity is malformed")
    services = value.get("services")
    if not isinstance(services, dict) or set(services) != {"db", "backend", "frontend", "nginx"}:
        raise Blocked("Active-generation service map is malformed")
    for service, pin in services.items():
        if (not isinstance(pin, dict) or set(pin) != {"id", "configuration_sha256", "image_id", "network_ids"}
                or not isinstance(pin.get("id"), str) or not re.fullmatch(r"[0-9a-f]{64}", pin["id"])
                or not isinstance(pin.get("configuration_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", pin["configuration_sha256"])
                or not isinstance(pin.get("image_id"), str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", pin["image_id"])
                or not isinstance(pin.get("network_ids"), dict)):
            raise Blocked("Active-generation service pin is malformed")
        expected_networks = {project + "_isolated"}
        if service == "nginx":
            expected_networks.add(project + "_ingress")
        networks = pin["network_ids"]
        if (set(networks) != expected_networks
                or any(not isinstance(network_id, str) or not re.fullmatch(r"[0-9a-f]{64}", network_id)
                       for network_id in networks.values())):
            raise Blocked("Active-generation network pin is malformed")
    return value


def _write_immutable(path, raw):
    pending = STATE / (".generation." + secrets.token_hex(16) + ".pending")
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise Blocked("Atomic no-replace generation journal publication is unavailable")
        renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameat2.restype = ctypes.c_int
        if renameat2(-100, os.fsencode(pending), -100, os.fsencode(path), 1) != 0:
            error = ctypes.get_errno()
            if error == 17:
                info = path.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                        or stat.S_IMODE(info.st_mode) != 0o600
                        or read_regular(path, 256 * 1024, trusted=True) != raw):
                    raise Blocked("Existing generation journal conflicts with this receipt pair")
                return
            raise OSError(error, os.strerror(error))
    finally:
        try:
            pending.unlink()
        except FileNotFoundError:
            pass


def _fsync_state_directory():
    directory = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _atomic_active_pointer(value):
    path = _active_generation_path()
    raw = canonical(value)
    pending = STATE / (".active-generation." + secrets.token_hex(16) + ".pending")
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
        directory = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            pending.unlink()
        except FileNotFoundError:
            pass


def _inspect_completed_generation(root, upgrade_receipt, tls_completion):
    payload = {"schema": "belib-active-generation-inspection/v1",
               "upgrade_receipt": upgrade_receipt, "tls_completion": tls_completion}
    raw = canonical(payload)
    if len(raw) > 128 * 1024:
        raise Blocked("Completed-generation evidence exceeds its size limit")
    fd = os.memfd_create("belib-active-generation", os.MFD_ALLOW_SEALING)
    try:
        offset = 0
        while offset < len(raw):
            offset += os.write(fd, raw[offset:])
        seals = (fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, seals)
        os.lseek(fd, 0, os.SEEK_SET)
        helper = root / "scripts/staging/cd-handoff-executor.py"
        trusted_path(helper)
        try:
            result = subprocess.run(["/usr/bin/python3", "-I", "-B", str(helper),
                                     "--inspect-completed-generation-fd", str(fd)],
                env=SAFE_ENV, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=180, check=False, pass_fds=(fd,), user=TARGET["uid"],
                group=TARGET["uid"], extra_groups=())
        except (OSError, subprocess.SubprocessError) as error:
            raise Blocked("Fixed owner runtime inspection could not run") from error
        if result.returncode != 0 or len(result.stdout.encode()) > 1024 * 1024:
            raise Blocked("Fixed owner runtime inspection did not verify the completed generation")
        projection = parse(result.stdout.rstrip("\n").encode(), 1024 * 1024)
        if not isinstance(projection, dict) or projection.get("schema") != "belib-active-generation-observation/v1":
            raise Blocked("Fixed owner runtime inspection returned an invalid projection")
        return projection
    finally:
        os.close(fd)


def _promote_completed(upgrade_nonce, owner_nonce, root):
    if not re.fullmatch(r"[0-9a-f]{64}", upgrade_nonce or "") or not re.fullmatch(r"[0-9a-f]{64}", owner_nonce or ""):
        raise Blocked("Completed-generation receipt selectors are malformed")
    upgrade = read_result(upgrade_nonce)
    completion = read_tls_receipt(owner_nonce)
    expected = upgrade.get("expected_runtime")
    if (upgrade.get("exit_code") != 0 or upgrade.get("operation") != "upgrade-retained"
            or not isinstance(expected, dict)
            or completion.get("upgrade_receipt_nonce") != upgrade_nonce
            or completion.get("upgrade_receipt_sha256") != hashlib.sha256(canonical(upgrade)).hexdigest()
            or completion.get("source_sha") != upgrade.get("source_sha")
            or completion.get("operation_id") != expected.get("operation_id")
            or completion.get("project") != expected.get("project")
            or completion.get("previous_nginx_id") != expected.get("services", {}).get("nginx")):
        raise Blocked("TLS completion does not link to this exact successful upgrade")
    generation_v2 = expected.get("prior_generation_sha256") is not None
    if generation_v2:
        prior_tls = expected.get("prior_tls_completion")
        if (completion.get("schema") != TLS_COMPLETION_SCHEMA_V2
                or not isinstance(prior_tls, dict)
                or completion.get("prior_generation_sha256") != expected.get("prior_generation_sha256")
                or completion.get("prior_tls_owner_nonce") != prior_tls.get("owner_nonce")
                or completion.get("prior_tls_completion_sha256") != prior_tls.get("completion_sha256")
                or completion.get("serve_config_sha256") != prior_tls.get("serve_config_sha256")
                or completion.get("serve_etag") != prior_tls.get("serve_etag")
                or completion.get("certificate_sha256") != prior_tls.get("certificate_sha256")):
            raise Blocked("TLS completion does not bind the current active predecessor")
    elif completion.get("schema") != TLS_COMPLETION_SCHEMA:
        raise Blocked("Genesis upgrade receipt cannot adopt a v2 TLS completion")
    observation = _inspect_completed_generation(root, upgrade, completion)
    if (observation.get("upgrade_receipt") != upgrade or observation.get("tls_completion") != completion
            or observation.get("expected_runtime") != expected):
        raise Blocked("Owner runtime observation differs from the selected root receipts")
    runtime = observation.get("runtime_readback")
    live = observation.get("observed_runtime")
    if (not isinstance(runtime, dict)
            or set(runtime) != {"status", "source_sha", "operation_id", "project", "completion", "observed_runtime"}
            or runtime.get("status") != "verified" or runtime.get("source_sha") != upgrade["source_sha"]
            or runtime.get("operation_id") != expected.get("operation_id")
            or runtime.get("project") != expected.get("project")
            or runtime.get("completion") != completion or runtime.get("observed_runtime") != live):
        raise Blocked("Owner runtime readback wrapper differs from the exact TLS completion")
    ids = live.get("container_ids", {}).get("candidate", {}) if isinstance(live, dict) else {}
    images = live.get("image_digests", {}).get("candidate", {}) if isinstance(live, dict) else {}
    if (ids != {"db": expected["services"]["db"], "backend": expected["services"]["backend"],
                "frontend": expected["services"]["frontend"], "nginx": completion["nginx_id"]}
            or set(images) != {"db", "backend", "frontend", "nginx"}
            or images.get("nginx") != completion.get("nginx_image_id")
            or live.get("app_image_revisions") != {"backend": upgrade["source_sha"], "frontend": upgrade["source_sha"]}):
        raise Blocked("Owner runtime IDs or source revisions differ from the root-pinned topology")
    if (observation.get("loopback_health") != {"url": f"http://127.0.0.1:{expected['port']}/health",
                                                "http_status": 200}
            or observation.get("tls_ingress") != {"status": 200,
                "certificate_sha256": completion.get("certificate_sha256")}
            or not isinstance(observation.get("staging_env_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", observation["staging_env_sha256"])):
        raise Blocked("Owner health or TLS certificate readback differs from the linked completion")
    transition = expected.get("plan", {}).get("environment_transition", {})
    if generation_v2 and observation.get("staging_env_sha256") != transition.get("candidate_sha256"):
        raise Blocked("Owner staging env readback differs from the approved commit transition")
    plan = expected["plan"]
    cutover = plan["cutover_plan"]
    services = {}
    for name in ("db", "backend", "frontend"):
        if name == "db":
            pin = plan["candidate_database"]
            networks = {expected["project"] + "_isolated": pin["network_id"]}
            config_sha = pin["configuration_sha256"]
        else:
            pin = cutover["new"][name]
            networks = pin["network_ids"]
            config_sha = pin["configuration_sha256"]
        services[name] = {"id": ids[name], "configuration_sha256": config_sha,
                          "image_id": images[name], "network_ids": networks}
    services["nginx"] = {"id": completion["nginx_id"],
        "configuration_sha256": completion["nginx_configuration_sha256"],
        "image_id": completion["nginx_image_id"], "network_ids": completion["nginx_network_ids"]}
    prior = _read_active_generation(allow_journaled_initial_promotion=True)
    generation_v2 = expected.get("prior_generation_sha256") is not None
    exact_retry = bool(prior is not None
        and prior.get("upgrade_receipt_nonce") == upgrade_nonce
        and prior.get("tls_owner_nonce") == owner_nonce
        and prior.get("upgrade_receipt_sha256") == hashlib.sha256(canonical(upgrade)).hexdigest()
        and prior.get("tls_completion_sha256") == hashlib.sha256(canonical(completion)).hexdigest())
    if generation_v2:
        prior_digest = (prior.get("previous_generation_sha256") if exact_retry else
                        hashlib.sha256(canonical(prior)).hexdigest() if prior is not None else None)
        if (prior is None
                or prior_digest != expected.get("prior_generation_sha256")
                or (exact_retry and (prior.get("source_sha") != upgrade.get("source_sha")
                    or prior.get("operation_id") != expected.get("operation_id")
                    or prior.get("project") != expected.get("project")))
                or (not exact_retry and (prior.get("source_sha") != expected.get("prior_application_sha")
                    or prior.get("project") != expected.get("prior_project")))):
            raise Blocked("Completed v2 generation does not name the current active predecessor")
    elif prior is not None and prior.get("schema") != "belib-active-generation/v1":
        raise Blocked("Legacy completed receipt cannot replace a v2 active generation")
    generation = {"schema": "belib-active-generation/v2" if generation_v2 else "belib-active-generation/v1", "host": TARGET["host"],
        "principal": TARGET["principal"], "uid": TARGET["uid"], "source_sha": upgrade["source_sha"],
        "operation_id": expected["operation_id"], "project": expected["project"],
        "upgrade_receipt_nonce": upgrade_nonce,
        "upgrade_receipt_sha256": hashlib.sha256(canonical(upgrade)).hexdigest(),
        "tls_owner_nonce": owner_nonce,
        "tls_completion_sha256": hashlib.sha256(canonical(completion)).hexdigest(),
        "authority_pins_sha256": expected["authority_pins_sha256"], "services": services,
        "database_volume": plan["candidate_database"]["volume_name"],
        "shared_books_volume": expected["retained"]["volumes"]["books"],
        "http_port": expected["port"], "tls_loopback_port": 18443,
        "certificate_sha256": completion["certificate_sha256"]}
    if generation_v2:
        if (not isinstance(completion.get("serve_config_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", completion["serve_config_sha256"])
                or not isinstance(completion.get("serve_etag"), str) or not completion["serve_etag"]):
            raise Blocked("Completed v2 generation omits its exact Serve configuration digest")
        generation["serve_config_sha256"] = completion["serve_config_sha256"]
        generation["serve_etag"] = completion["serve_etag"]
        generation["previous_generation_sha256"] = prior_digest
        generation["genesis_generation_sha256"] = (prior["genesis_generation_sha256"]
            if prior.get("schema") == "belib-active-generation/v2" else prior_digest)
        generation["staging_env_sha256"] = observation["staging_env_sha256"]
    if prior is not None:
        if canonical(prior) == canonical(generation):
            return generation
        if exact_retry:
            raise Blocked("Repeated completion receipt pair conflicts with its active generation")
        if not generation_v2:
            raise Blocked("A different active generation already exists; lineage CAS is not enabled")
    journal = STATE / ("generation-" + upgrade_nonce + "-" + owner_nonce + ".json")
    journals = {entry.name for entry in os.scandir(STATE)
                if entry.name.startswith("generation-") and entry.name.endswith(".json")}
    if (prior is None and journals and journal.name not in journals):
        raise Blocked("An unresolved generation journal blocks a different initial promotion")
    journal_raw = canonical({"schema": "belib-active-generation-journal/v2" if generation_v2 else "belib-active-generation-journal/v1",
                             "previous_sha256": (hashlib.sha256(canonical(prior)).hexdigest() if prior else None),
                             "generation_sha256": hashlib.sha256(canonical(generation)).hexdigest(),
                             "generation": generation})
    try:
        _write_immutable(journal, journal_raw)
        _fsync_state_directory()
    except FileExistsError:
        info = journal.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or read_regular(journal, 256 * 1024, trusted=True) != journal_raw):
            raise Blocked("Existing generation journal conflicts with this receipt pair")
    if canonical(_read_active_generation(allow_journaled_initial_promotion=True)) != canonical(prior):
        raise Blocked("Active-generation predecessor changed before pointer commit")
    _atomic_active_pointer(generation)
    return generation


def spawn_admitted(root, grant, retained_config=None):
    arguments = launcher_arguments(grant)
    pid = os.fork()
    if pid == 0:
        try:
            os.setsid()
            if int(time.time()) >= grant["expires_at"]:
                os._exit(124)
            admission = {"schema": "belib-staging-operator-admission/v1", "grant": grant,
                         "source_root": str(root), "pid": os.getpid(),
                         "argv_sha256": hashlib.sha256(canonical(arguments)).hexdigest()}
            if retained_config is not None:
                admission["retained_config"] = retained_config
            fd = os.memfd_create("belib-operator-admission", os.MFD_ALLOW_SEALING)
            os.fchmod(fd, 0o400)
            os.write(fd, canonical(admission))
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            os.lseek(fd, 0, os.SEEK_SET)
            os.set_inheritable(fd, True)
            os.setgroups([])
            os.setgid(1000)
            os.setuid(1000)
            os.umask(0o077)
            env = {**SAFE_ENV, "BELIB_BOOTSTRAP_FD": str(fd)}
            os.execve("/usr/bin/python3", ["/usr/bin/python3", "-I", "-B", str(root / "scripts/staging/launch-guard.py"), *arguments], env)
        except BaseException:
            os._exit(125)
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        finished, status = os.waitpid(pid, os.WNOHANG)
        if finished:
            return os.waitstatus_to_exitcode(status)
        time.sleep(0.2)
    os.killpg(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    return 124


def spawn_preparation(root, operation_id, active_generation=None):
    """An explicit root operator prepares candidates; this never authorizes cutover."""
    if not isinstance(operation_id, str) or not re.fullmatch(r"[0-9a-f]{32}", operation_id):
        raise Blocked("Preparation requires one lowercase 32-hex operation identity")
    pid = os.fork()
    if pid == 0:
        try:
            os.setsid()
            admission = {"schema": "belib-retained-preparation-admission/v1",
                         "source_root": str(root), "source_sha": root.name,
                         "operation_id": operation_id,
                         "prepared_directory": str(PREPARATIONS / operation_id),
                         "env_file": "/home/hiroyuki/.config/belib-staging/i5.env",
                         "pid": os.getpid()}
            if active_generation is not None:
                validate_active_generation(active_generation)
                admission["active_generation"] = active_generation
                admission["active_generation_sha256"] = hashlib.sha256(canonical(active_generation)).hexdigest()
            fd = os.memfd_create("belib-preparation-admission", os.MFD_ALLOW_SEALING)
            os.fchmod(fd, 0o400)
            os.write(fd, canonical(admission))
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            os.lseek(fd, 0, os.SEEK_SET)
            os.set_inheritable(fd, True)
            os.setgroups([])
            os.setgid(1000)
            os.setuid(1000)
            os.umask(0o077)
            os.execve("/usr/bin/python3", ["/usr/bin/python3", "-I", "-B",
                      str(root / "scripts/staging/prepare-retained.py")],
                      {**SAFE_ENV, "BELIB_PREPARATION_FD": str(fd)})
        except BaseException:
            os._exit(125)
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        finished, status = os.waitpid(pid, os.WNOHANG)
        if finished:
            return os.waitstatus_to_exitcode(status)
        time.sleep(0.2)
    os.killpg(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    return 124


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    request = parser.add_mutually_exclusive_group(required=True)
    request.add_argument("--grant", type=Path)
    request.add_argument("--receipt")
    request.add_argument("--tls-receipt", metavar="OWNER_NONCE")
    request.add_argument("--promote-completed", nargs=2, metavar=("UPGRADE_NONCE", "TLS_OWNER_NONCE"),
                         help="promote one linked, live-verified completed retained generation")
    request.add_argument("--prepare-retained", metavar="OPERATION_ID",
                         help="explicit operator preparation only; does not sign, adopt or activate an upgrade")
    args = parser.parse_args()
    try:
        if not hasattr(os, "memfd_create") or not hasattr(fcntl, "F_ADD_SEALS"):
            raise Blocked("Kernel sealed admission descriptors are unavailable")
        if (os.getuid() != 0 or os.geteuid() != 0 or socket.gethostname() != TARGET["host"]
                or pwd.getpwnam("hiroyuki").pw_uid != 1000 or pwd.getpwnam("hiroyuki").pw_gid != 1000
                or Path(__file__).resolve() != INSTALL or sys.executable != "/usr/bin/python3"
                or not sys.flags.isolated or not sys.flags.dont_write_bytecode):
            raise Blocked("Use only the independently installed isolated operator entry on i5")
        trusted_path(INSTALL)
        trusted_path(Path("/usr/bin/python3").resolve())
        if args.receipt is not None:
            print(canonical(read_result(args.receipt)).decode())
            return 0
        if args.tls_receipt is not None:
            print(canonical(read_tls_receipt(args.tls_receipt)).decode())
            return 0
        config = parse(read_regular(CONFIG, MAX_CONFIG, trusted=True), MAX_CONFIG)
        root = verify_source(config)
        if args.promote_completed is not None:
            trusted_path(STATE, directory=True)
            lock_fd = os.open(STATE / "project.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                generation = _promote_completed(args.promote_completed[0], args.promote_completed[1], root)
            finally:
                os.close(lock_fd)
            print(canonical(generation).decode())
            return 0
        if args.prepare_retained is not None:
            if not re.fullmatch(r"[0-9a-f]{32}", args.prepare_retained):
                raise Blocked("Preparation operation identity is invalid")
            trusted_path(STATE, directory=True)
            lock_fd = os.open(STATE / "project.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                verify_staging_source_access(root, "scripts/staging/prepare-retained.py")
                active = _read_active_generation()
                if active is not None:
                    _promote_completed(active["upgrade_receipt_nonce"], active["tls_owner_nonce"], root)
                    active = _read_active_generation()
                code = spawn_preparation(root, args.prepare_retained, active)
            finally:
                os.close(lock_fd)
            print(canonical({"schema": "belib-retained-preparation-result/v1",
                             "operation_id": args.prepare_retained,
                             "source_sha": config["source_sha"], "host": TARGET["host"],
                             "principal": TARGET["principal"], "exit_code": code,
                             "activated": False, "trust_adopted": False}).decode())
            return code if code >= 0 else 1
        grant = validate_grant(read_regular(args.grant, MAX_GRANT), config["source_sha"])
        retained_config = load_retained_config(grant) if grant["operation"] == "upgrade-retained" else None
        trusted_path(STATE, directory=True)
        lock_fd = os.open(STATE / "project.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            verify_staging_source_access(root, "scripts/staging/launch-guard.py")
            active = _read_active_generation() if grant["operation"] == "upgrade-retained" else None
            if "prior_generation_sha256" in grant:
                if (active is None or hashlib.sha256(canonical(active)).hexdigest() != grant["prior_generation_sha256"]
                        or active.get("source_sha") != grant.get("prior_application_sha")
                        or active.get("project") != retained_config.get("prior_project")):
                    raise Blocked("Approval v2 predecessor differs from the current active-generation pointer")
                validate_generation_predecessor(active, retained_config)
                if (active.get("schema") == "belib-active-generation/v2"
                        and retained_config.get("environment_transition", {}).get("prior_sha256")
                            != active.get("staging_env_sha256")):
                    raise Blocked("Approval v2 environment predecessor differs from the active-generation pointer")
            elif grant["operation"] == "upgrade-retained" and active is not None:
                raise Blocked("Legacy retained approval is restricted to the genesis baseline")
            expected_runtime = None
            if retained_config is not None:
                prior_tls = None
                if "prior_generation_sha256" in grant:
                    if (active is None
                            or hashlib.sha256(canonical(active)).hexdigest() != grant["prior_generation_sha256"]
                            or active.get("source_sha") != grant.get("prior_application_sha")):
                        raise Blocked("Prior TLS completion selector differs from the active-generation pointer")
                    prior_tls = read_tls_receipt(active["tls_owner_nonce"])
                expected_runtime = expected_retained_runtime(retained_config, active, prior_tls)
            consume_nonce(grant)
            code = spawn_admitted(root, grant, retained_config) if retained_config is not None else spawn_admitted(root, grant)
            result = write_result(grant, code, retained_config, active, expected_runtime)
        finally:
            os.close(lock_fd)
        print(canonical(result).decode())
        return code if code >= 0 else 1
    except (Blocked, OSError, ValueError, subprocess.SubprocessError) as error:
        print("BLOCKED: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
