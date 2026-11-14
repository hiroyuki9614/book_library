#!/usr/bin/env -S /usr/bin/python3 -I -B
"""Installable one-shot i5 operator entry. No install or trust adoption is implicit.

Root-owned configuration/source and the existing external public key are the
authority. No candidate module is imported as root. The admitted child runs as
hiroyuki, with a root-created sealed, PID-bound admission descriptor.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import pwd
import re
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
    if grant.get("operation") == "resume-ingress":
        fields |= {"prior_source_sha", "retained_state_sha256"}
    if grant.get("operation") == "upgrade-retained":
        fields |= {"prior_application_sha", "retained_state_sha256", "backup_sha256", "restore_attestation_sha256"}
    if (not isinstance(grant, dict) or set(grant) != fields
            or grant.get("operation") not in {"deploy", "resume-ingress", "upgrade-retained"}
            or grant.get("schema") != "belib-staging-bootstrap-approval/v1"
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
            grant["prior_application_sha"] != "81256268276d7ead95029849037f69ec6beec202"
            or any(not isinstance(grant[name], str) or not re.fullmatch(r"[0-9a-f]{64}", grant[name])
                   for name in ("retained_state_sha256", "backup_sha256", "restore_attestation_sha256"))):
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
    return [*arguments, grant["operation"]]


def load_retained_config(grant):
    """Only a separate root-adopted topology and evidence trust store can enable upgrade."""
    path = RETAINED_CONFIG
    if not path.exists():
        raise Blocked("Retained topology and independent evidence authorities are not adopted")
    value = parse(read_regular(path, 65536, trusted=True), 65536)
    if (not isinstance(value, dict) or value.get("schema") != "belib-retained-operator/v1"
            or value.get("source_sha") != grant["source_sha"] or value.get("topology") != "isolated-copy"
            or value.get("enabled") is not True):
        raise Blocked("Retained operator configuration does not enable this exact source/topology")
    return value


def consume_nonce(grant):
    trusted_path(STATE, directory=True)
    if stat.S_IMODE(STATE.stat().st_mode) != 0o700:
        raise Blocked("Replay directory must be root-only")
    path = STATE / (grant["nonce"] + ".used")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
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


def expected_retained_runtime(config):
    plan = config["cutover_plan"]
    public_plan = {"cutover_plan": plan, "candidate_database": config["candidate_database"],
                   "source_database": config["source_database"]}
    value = {"project": TARGET["project"] + "-upgrade-" + plan["operation_id"],
             "operation_id": plan["operation_id"], "plan": public_plan,
             "services": {"db": config["candidate_database"]["id"],
                          **{service: plan["new"][service]["id"] for service in ("backend", "frontend", "nginx")}},
             "retained": {"project": TARGET["project"],
                          "db": RETAINED_DB_ID,
                          "volumes": {"database": TARGET["project"] + "_staging-postgres-data",
                                      "books": TARGET["project"] + "_staging-book-files"},
                          "stopped_application_ids": [plan["old"][service]["id"] for service in ("backend", "frontend", "nginx")]},
             "port": plan["port"],
             "plan_sha256": hashlib.sha256(canonical(public_plan)).hexdigest()}
    return value


def write_result(grant, code, retained_config=None):
    if type(code) is not int or not -128 <= code <= 255:
        raise Blocked("Executor exit status is invalid")
    result = {"schema": "belib-staging-operation-result/v1", "nonce": grant["nonce"],
              "grant_sha256": hashlib.sha256(canonical(grant)).hexdigest(),
              **{name: grant[name] for name in ("source_sha", "operation", "host", "principal", "uid", "project")},
              "exit_code": code, "completed_at": int(time.time()), "nonce_consumed": True}
    if code == 0 and grant["operation"] == "upgrade-retained" and retained_config is not None:
        # This is an expected adopted topology, NOT an observation of Docker.
        # The nonroot CD adapter independently inspects these IDs and revisions.
        result["expected_runtime"] = expected_retained_runtime(retained_config)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    request = parser.add_mutually_exclusive_group(required=True)
    request.add_argument("--grant", type=Path)
    request.add_argument("--receipt")
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
        config = parse(read_regular(CONFIG, MAX_CONFIG, trusted=True), MAX_CONFIG)
        root = verify_source(config)
        grant = validate_grant(read_regular(args.grant, MAX_GRANT), config["source_sha"])
        retained_config = load_retained_config(grant) if grant["operation"] == "upgrade-retained" else None
        trusted_path(STATE, directory=True)
        lock_fd = os.open(STATE / "project.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            consume_nonce(grant)
            code = spawn_admitted(root, grant, retained_config) if retained_config is not None else spawn_admitted(root, grant)
            result = write_result(grant, code, retained_config)
        finally:
            os.close(lock_fd)
        print(canonical(result).decode())
        return code if code >= 0 else 1
    except (Blocked, OSError, ValueError, subprocess.SubprocessError) as error:
        print("BLOCKED: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
