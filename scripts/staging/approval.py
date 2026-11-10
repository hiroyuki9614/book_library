#!/usr/bin/env python3
"""Strict, one-operation Ed25519 approval transport for the i5 staging launcher."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAw1k8oqGkMOUb8KJb6thJY5GUtENwWuLGvWrKi6oBXaA=
-----END PUBLIC KEY-----
"""
PUBLIC_KEY_SHA256 = "8f49fad277be0d1c05bf0b75246a5895a8b3489ddaaeef4f43a3e8b90e25e629"
STATE_ROOT = Path("/home/hiroyuki/.local/state/belib-staging/approval-challenges")
TARGET = {
    "repository": "hiroyuki9614/book_library",
    "pull_request": 103,
    "host": "ubuntu-worker",
    "principal": "hiroyuki",
    "uid": 1000,
    "project": "belib-isolated-i5",
    "branch": "codex/belib-i5-isolated-staging-20261107",
}
MAX_ENVELOPE_BYTES = 16 * 1024
MAX_GRANT_SECONDS = 300
POLL_SECONDS = 300
TLS_OPERATION = "tls-ingress"
TLS_SERVE_ETAG = "031c8cd8bbc73f27c4b6a503142d1dd0a0bc0e0f1536a497041a4faa0cd1de5e"


class ApprovalError(Exception):
    pass


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _pairs_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ApprovalError("Approval JSON contains a duplicate field")
        result[key] = value
    return result


def strict_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_pairs_no_duplicates)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ApprovalError("Approval JSON is malformed") from error


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def unb64(value: Any) -> bytes:
    if not isinstance(value, str) or len(value) > MAX_ENVELOPE_BYTES * 2 or not re.fullmatch(r"[A-Za-z0-9+/]*={0,2}", value):
        raise ApprovalError("Approval envelope encoding is invalid")
    try:
        result = base64.b64decode(value, validate=True)
    except ValueError as error:
        raise ApprovalError("Approval envelope encoding is invalid") from error
    if b64(result) != value:
        raise ApprovalError("Approval envelope encoding is not canonical")
    return result


def expected_payload(
    source_sha: str, operation: str, nonce: str, issued_at: int, evidence_sha256: str,
    prior_source_sha: str | None = None, retained_state_sha256: str | None = None,
    tls_binding: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload = {
        "branch": TARGET["branch"],
        "expires_at": issued_at + MAX_GRANT_SECONDS,
        "evidence_sha256": evidence_sha256,
        "host": TARGET["host"],
        "issued_at": issued_at,
        "nonce": nonce,
        "operation": operation,
        "principal": TARGET["principal"],
        "project": TARGET["project"],
        "pull_request": TARGET["pull_request"],
        "repository": TARGET["repository"],
        "schema": "belib-staging-bootstrap-approval/v1",
        "source_sha": source_sha,
        "uid": TARGET["uid"],
    }
    if operation == "resume-ingress":
        payload["prior_source_sha"] = prior_source_sha
        payload["retained_state_sha256"] = retained_state_sha256
    elif operation == TLS_OPERATION:
        if tls_binding is None:
            raise ApprovalError("TLS approval requires a fresh retained-state binding")
        payload.update(tls_binding)
    return payload


def validate_payload(
    payload: Any, source_sha: str, operation: str, nonce: str, evidence_sha256: str, now: int,
    prior_source_sha: str | None = None, retained_state_sha256: str | None = None,
    tls_binding: dict[str, str] | None = None,
) -> None:
    if operation not in ("deploy", "resume-ingress", TLS_OPERATION):
        raise ApprovalError("Approval operation is not allowlisted")
    expected_fields = set(expected_payload(source_sha, operation, nonce, 0, evidence_sha256,
                                           prior_source_sha, retained_state_sha256, tls_binding))
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ApprovalError("Approval payload fields do not match the fixed schema")
    for name in ("pull_request", "uid", "issued_at", "expires_at"):
        if type(payload[name]) is not int:
            raise ApprovalError("Approval time and identity fields must be integers")
    for name, value in TARGET.items():
        if payload[name] != value:
            raise ApprovalError("Approval target does not match this staging host")
    if (payload["schema"] != "belib-staging-bootstrap-approval/v1" or payload["source_sha"] != source_sha
            or payload["operation"] != operation or payload["nonce"] != nonce
            or payload["evidence_sha256"] != evidence_sha256):
        raise ApprovalError("Approval does not match the current challenge")
    if (not re.fullmatch(r"[0-9a-f]{40}", source_sha) or not re.fullmatch(r"[0-9a-f]{64}", nonce)
            or not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256)):
        raise ApprovalError("Challenge identity is malformed")
    if operation == "resume-ingress" and (
        not re.fullmatch(r"[0-9a-f]{40}", prior_source_sha or "")
        or not re.fullmatch(r"[0-9a-f]{64}", retained_state_sha256 or "")
        or payload["prior_source_sha"] != prior_source_sha
        or payload["retained_state_sha256"] != retained_state_sha256
    ):
        raise ApprovalError("Resume approval does not match the retained source and state")
    if operation == TLS_OPERATION:
        if tls_binding is None or set(tls_binding) != {
            "prior_application_sha", "retained_state_sha256", "serve_config_sha256", "serve_etag",
            "certificate_sha256", "tls_config_sha256", "owner_plan_sha256", "tls_state_sha256",
        }:
            raise ApprovalError("TLS approval requires the exact custom-ingress state tuple")
        if (not re.fullmatch(r"[0-9a-f]{40}", tls_binding["prior_application_sha"])
                or any(not re.fullmatch(r"[0-9a-f]{64}", tls_binding[name]) for name in (
                    "retained_state_sha256", "serve_config_sha256", "serve_etag", "certificate_sha256", "tls_config_sha256", "owner_plan_sha256", "tls_state_sha256"
                )) or tls_binding["serve_etag"] != TLS_SERVE_ETAG):
            raise ApprovalError("TLS state binding is malformed")
        if any(payload[name] != value for name, value in tls_binding.items()):
            raise ApprovalError("TLS approval does not match the current retained state")
    issued, expires = payload["issued_at"], payload["expires_at"]
    if issued > now + 5 or issued < now - MAX_GRANT_SECONDS or expires < now or expires <= issued or expires - issued > MAX_GRANT_SECONDS:
        raise ApprovalError("Approval is expired or outside the bounded grant lifetime")


def verify_envelope(
    envelope: Any, source_sha: str, operation: str, nonce: str, evidence_sha256: str, *,
    now: int | None = None, prior_source_sha: str | None = None,
    retained_state_sha256: str | None = None,
    tls_binding: dict[str, str] | None = None,
) -> str:
    if hashlib.sha256(PUBLIC_KEY_PEM).hexdigest() != PUBLIC_KEY_SHA256:
        raise ApprovalError("Embedded approval public key fingerprint mismatch")
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
        raise ApprovalError("Approval envelope fields do not match the fixed schema")
    payload_raw, signature = unb64(envelope["payload"]), unb64(envelope["signature"])
    if len(payload_raw) > MAX_ENVELOPE_BYTES or len(signature) != 64:
        raise ApprovalError("Approval envelope exceeds its fixed bounds")
    payload = strict_json(payload_raw)
    if canonical_json(payload) != payload_raw:
        raise ApprovalError("Approval payload is not canonical JSON")
    validate_payload(payload, source_sha, operation, nonce, evidence_sha256,
                     int(time.time()) if now is None else now, prior_source_sha, retained_state_sha256, tls_binding)
    _verify_trusted_openssl()
    with tempfile.TemporaryDirectory(prefix="belib-approval-verify-", dir="/tmp") as temporary:
        key_path = Path(temporary) / "public.pem"
        message_path = Path(temporary) / "payload"
        sig_path = Path(temporary) / "signature"
        key_path.write_bytes(PUBLIC_KEY_PEM)
        message_path.write_bytes(payload_raw)
        sig_path.write_bytes(signature)
        os.chmod(key_path, 0o600)
        verified = subprocess.run(
            ["/usr/bin/openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(key_path), "-rawin", "-in", str(message_path), "-sigfile", str(sig_path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"}, timeout=5, check=False,
        )
    if verified.returncode != 0:
        raise ApprovalError("External staging approval signature is invalid")
    return nonce


def _verify_trusted_openssl() -> None:
    for path in (Path("/usr"), Path("/usr/bin"), Path("/usr/bin/openssl")):
        try:
            info = os.lstat(path)
        except OSError as error:
            raise ApprovalError("Trusted /usr/bin/openssl is unavailable") from error
        if path.name == "openssl":
            safe = stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not stat.S_IMODE(info.st_mode) & 0o7022 and bool(stat.S_IMODE(info.st_mode) & 0o111)
        else:
            safe = stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not stat.S_IMODE(info.st_mode) & 0o7022
        if not safe:
            raise ApprovalError("Trusted /usr/bin/openssl path is unsafe")
    try:
        temporary_root = os.lstat("/tmp")
    except OSError as error:
        raise ApprovalError("Trusted temporary storage is unavailable") from error
    if not stat.S_ISDIR(temporary_root.st_mode) or temporary_root.st_uid != 0 or stat.S_IMODE(temporary_root.st_mode) != 0o1777:
        raise ApprovalError("Trusted temporary storage must be root-owned sticky /tmp")


def _safe_state_root(uid: int) -> None:
    root = STATE_ROOT
    current = Path("/")
    for part in root.parts[1:]:
        current /= part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            try:
                os.mkdir(current, 0o700)
            except OSError as error:
                raise ApprovalError("Approval state path could not be created safely") from error
            info = os.lstat(current)
        mode = stat.S_IMODE(info.st_mode)
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid not in (0, uid)
            or (mode & 0o022 and not (info.st_uid == 0 and mode == 0o1777))
        ):
            raise ApprovalError("Approval state path contains an unsafe component")
    info = os.lstat(root)
    if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
        raise ApprovalError("Approval state directory must be private and owned by the operator")


def request_and_wait(
    source_sha: str, operation: str, evidence_sha256: str, *, uid: int, output,
    timeout: int = POLL_SECONDS, prior_source_sha: str | None = None,
    retained_state_sha256: str | None = None,
    tls_binding: dict[str, str] | None = None,
) -> str:
    if operation not in ("deploy", "resume-ingress", TLS_OPERATION):
        raise ApprovalError("Approval operation is not allowlisted")
    if operation == "deploy" and (prior_source_sha is not None or retained_state_sha256 is not None):
        raise ApprovalError("Deploy approval cannot carry retained-state resume fields")
    if operation == "resume-ingress" and (
        not re.fullmatch(r"[0-9a-f]{40}", prior_source_sha or "")
        or not re.fullmatch(r"[0-9a-f]{64}", retained_state_sha256 or "")
    ):
        raise ApprovalError("Resume approval requires the exact prior source and retained-state digest")
    if operation == TLS_OPERATION:
        if prior_source_sha is not None or retained_state_sha256 is not None:
            raise ApprovalError("TLS approval uses its distinct retained-state binding")
        if tls_binding is None:
            raise ApprovalError("TLS approval requires its distinct retained-state binding")
        # Reuse the fixed-schema validator before creating a challenge.
        payload = expected_payload(source_sha, operation, "0" * 64, 0, evidence_sha256, tls_binding=tls_binding)
        validate_payload(payload, source_sha, operation, "0" * 64, evidence_sha256, int(time.time()),
                         tls_binding=tls_binding)
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ApprovalError("Source revision must be a full lowercase Git SHA")
    if not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256):
        raise ApprovalError("External evidence digest must be 64 lowercase hexadecimal characters")
    _safe_state_root(uid)
    nonce = secrets.token_hex(32)
    challenge_dir = STATE_ROOT / ("challenge-" + nonce)
    os.mkdir(challenge_dir, 0o700)
    response_path = challenge_dir / "response.json"
    challenge = {
        "schema": "belib-staging-bootstrap-challenge/v1",
        "source_sha": source_sha,
        "evidence_sha256": evidence_sha256,
        **TARGET,
        "operation": operation,
        "nonce": nonce,
        "response_path": str(response_path),
        "created_at": int(time.time()),
    }
    if operation == "resume-ingress":
        challenge["prior_source_sha"] = prior_source_sha
        challenge["retained_state_sha256"] = retained_state_sha256
    elif operation == TLS_OPERATION:
        challenge.update(tls_binding or {})
    try:
        output.write(canonical_json(challenge).decode("utf-8") + "\n")
        output.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
                fd = os.open(response_path, flags)
            except FileNotFoundError:
                time.sleep(min(0.2, max(0, deadline - time.monotonic())))
                continue
            except OSError as error:
                raise ApprovalError("Approval response path is unsafe") from error
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > MAX_ENVELOPE_BYTES:
                    raise ApprovalError("Approval response must be a bounded mode-0600 regular file")
                chunks = bytearray()
                while len(chunks) <= MAX_ENVELOPE_BYTES:
                    chunk = os.read(fd, min(4096, MAX_ENVELOPE_BYTES + 1 - len(chunks)))
                    if not chunk:
                        break
                    chunks.extend(chunk)
                after = os.fstat(fd)
                if len(chunks) != info.st_size or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns):
                    raise ApprovalError("Approval response changed while it was read")
                raw = bytes(chunks)
            finally:
                os.close(fd)
            verify_envelope(strict_json(raw), source_sha, operation, nonce, evidence_sha256,
                            prior_source_sha=prior_source_sha, retained_state_sha256=retained_state_sha256,
                            tls_binding=tls_binding)
            return nonce
        raise ApprovalError("Timed out waiting for the one-time external approval")
    finally:
        try:
            response_path.unlink(missing_ok=True)
            challenge_dir.rmdir()
        except OSError:
            pass
