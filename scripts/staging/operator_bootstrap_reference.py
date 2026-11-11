"""UNTRUSTED offline reference for a future operator-installed bootstrap.

This candidate-controlled file is not itself a trust root. A separately
installed verifier must independently pin and review its own bytes and inject
the already verified public key and immutable source manifest. This module
never imports or launches candidate code and must not enable runtime access.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


REPOSITORY = "hiroyuki9614/book_library"
PULL_REQUEST = 103
HOST = "ubuntu-worker"
PRINCIPAL = "hiroyuki"
UID = 1000
PROJECT = "belib-isolated-i5"
OPERATIONS = frozenset({"deploy", "resume-ingress", "tls-ingress"})
SCHEMA = "belib-operator-bootstrap-reference/v1"
MAX_GRANT_BYTES = 16 * 1024
MAX_GRANT_SECONDS = 300
_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_NONCE = re.compile(r"[0-9a-f]{32}\Z")
_GRANT_FIELDS = frozenset({
    "schema", "repository", "pull_request", "source_sha", "operation", "evidence_sha256",
    "host", "principal", "uid", "project", "expires_at", "nonce",
})


class BootstrapReferenceError(ValueError):
    """The untrusted reference verifier rejected its inputs."""


def _time_now() -> int:
    """Read the verifier clock; replaceable only by offline tests."""
    return int(time.time())


def _current_time() -> int:
    current_time = _time_now()
    if type(current_time) is not int:
        raise BootstrapReferenceError("Verification time is invalid")
    return current_time


def _require_unexpired(expires_at: int) -> None:
    if expires_at <= _current_time():
        raise BootstrapReferenceError("Grant tuple or expiry is invalid")


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError) as error:
        raise BootstrapReferenceError("JSON value is not canonicalizable") from error


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BootstrapReferenceError("JSON contains a duplicate field")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise BootstrapReferenceError("JSON contains a non-standard number")


def _parse_canonical_envelope(raw: bytes) -> dict[str, Any]:
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_GRANT_BYTES:
        raise BootstrapReferenceError("Grant envelope size is invalid")
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_constant,
        )
    except BootstrapReferenceError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise BootstrapReferenceError("Grant envelope is malformed") from error
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise BootstrapReferenceError("Grant envelope is not canonical JSON")
    if set(value) != {"grant", "signature"} or not isinstance(value["grant"], dict):
        raise BootstrapReferenceError("Grant envelope fields are invalid")
    return value


def _validate_manifest(manifest: tuple[tuple[str, str], ...]) -> dict[str, str]:
    if type(manifest) is not tuple or not manifest:
        raise BootstrapReferenceError("Expected manifest must be a nonempty immutable tuple")
    checked: dict[str, str] = {}
    for item in manifest:
        if type(item) is not tuple or len(item) != 2:
            raise BootstrapReferenceError("Expected manifest entries must be immutable pairs")
        relative_path, expected_hash = item
        if not isinstance(relative_path, str) or not relative_path or "\\" in relative_path:
            raise BootstrapReferenceError("Expected manifest path is invalid")
        parts = relative_path.split("/")
        if any(part in ("", ".", "..") for part in parts) or relative_path.startswith("/"):
            raise BootstrapReferenceError("Expected manifest path is not normalized")
        try:
            relative_path.encode("utf-8", "strict")
        except UnicodeError as error:
            raise BootstrapReferenceError("Expected manifest path is invalid") from error
        if not isinstance(expected_hash, str) or not _SHA256.fullmatch(expected_hash):
            raise BootstrapReferenceError("Expected manifest hash is invalid")
        if relative_path in checked:
            raise BootstrapReferenceError("Expected manifest contains a duplicate path")
        checked[relative_path] = expected_hash
    return checked


def _open_absolute_directory(path: Path) -> int:
    if not isinstance(path, Path) or not path.is_absolute():
        raise BootstrapReferenceError("Directory path must be absolute")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    current = os.open("/", flags)
    try:
        for part in path.parts[1:]:
            if part in ("", ".", ".."):
                raise BootstrapReferenceError("Directory path is not normalized")
            next_fd = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = next_fd
        return current
    except OSError as error:
        os.close(current)
        raise BootstrapReferenceError("Directory could not be safely opened") from error
    except BaseException:
        os.close(current)
        raise


def _readonly_directory(st: os.stat_result) -> None:
    if not stat.S_ISDIR(st.st_mode) or stat.S_IMODE(st.st_mode) & 0o222:
        raise BootstrapReferenceError("Source snapshot contains a writable directory")


def _hash_snapshot(root_fd: int) -> dict[str, str]:
    hashes: dict[str, str] = {}
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC

    def visit(directory_fd: int, prefix: str) -> None:
        _readonly_directory(os.fstat(directory_fd))
        for name in os.listdir(directory_fd):
            relative = f"{prefix}/{name}" if prefix else name
            entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISDIR(entry.st_mode):
                child_fd = os.open(name, directory_flags, dir_fd=directory_fd)
                try:
                    opened = os.fstat(child_fd)
                    if (opened.st_dev, opened.st_ino) != (entry.st_dev, entry.st_ino):
                        raise BootstrapReferenceError("Source directory changed during verification")
                    visit(child_fd, relative)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(entry.st_mode):
                if stat.S_IMODE(entry.st_mode) & 0o222:
                    raise BootstrapReferenceError("Source snapshot contains a writable file")
                file_fd = os.open(name, file_flags, dir_fd=directory_fd)
                try:
                    opened = os.fstat(file_fd)
                    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (entry.st_dev, entry.st_ino):
                        raise BootstrapReferenceError("Source file changed during verification")
                    digest = hashlib.sha256()
                    while True:
                        chunk = os.read(file_fd, 64 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                    hashes[relative] = digest.hexdigest()
                finally:
                    os.close(file_fd)
            else:
                raise BootstrapReferenceError("Source snapshot contains a symlink or special file")

    visit(root_fd, "")
    return hashes


def verify_source_manifest(
    source_root: Path, expected_manifest: tuple[tuple[str, str], ...]
) -> None:
    """Verify an externally supplied complete file manifest in a read-only tree.

    Mode-bit checks are a reference safeguard, not an OS immutability guarantee.
    """
    expected = _validate_manifest(expected_manifest)
    root_fd = _open_absolute_directory(source_root)
    try:
        actual = _hash_snapshot(root_fd)
    finally:
        os.close(root_fd)
    if actual != expected:
        raise BootstrapReferenceError("Source file manifest does not match")


def _verify_signature(public_key_pem: bytes, message: bytes, signature: bytes) -> None:
    if not isinstance(public_key_pem, bytes) or not public_key_pem or len(public_key_pem) > 4096:
        raise BootstrapReferenceError("Caller-supplied verified public key is invalid")
    try:
        with tempfile.TemporaryDirectory(prefix="belib-bootstrap-reference-") as temporary:
            root = Path(temporary)
            key_path = root / "public.pem"
            message_path = root / "grant.json"
            signature_path = root / "grant.sig"
            key_path.write_bytes(public_key_pem)
            message_path.write_bytes(message)
            signature_path.write_bytes(signature)
            result = subprocess.run(
                [
                    "/usr/bin/openssl", "pkeyutl", "-verify", "-pubin",
                    "-inkey", str(key_path), "-rawin", "-in", str(message_path),
                    "-sigfile", str(signature_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin", "LANG": "C"},
                timeout=5,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BootstrapReferenceError("Ed25519 verifier is unavailable") from error
    if result.returncode != 0:
        raise BootstrapReferenceError("Grant signature is invalid")


def _consume_nonce(state_dir: Path, nonce: str) -> None:
    directory_fd = _open_absolute_directory(state_dir)
    try:
        directory_stat = os.fstat(directory_fd)
        if (
            directory_stat.st_uid != os.getuid()
            or stat.S_IMODE(directory_stat.st_mode) != 0o700
        ):
            raise BootstrapReferenceError("Replay state directory owner or mode is unsafe")
        try:
            fd = os.open(
                f"{nonce}.used",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=directory_fd,
            )
        except FileExistsError as error:
            raise BootstrapReferenceError("Grant nonce was already consumed") from error
        try:
            created = os.fstat(fd)
            if (
                not stat.S_ISREG(created.st_mode)
                or created.st_uid != os.getuid()
                or stat.S_IMODE(created.st_mode) != 0o600
                or created.st_nlink != 1
            ):
                raise BootstrapReferenceError("Replay marker is unsafe")
            os.write(fd, b"consumed\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(directory_fd)
    except OSError as error:
        raise BootstrapReferenceError("Replay state is unavailable") from error
    finally:
        os.close(directory_fd)


def verify_and_consume_grant(
    envelope: bytes,
    *,
    expected_source_sha: str,
    expected_evidence_sha256: str,
    expected_operation: str,
    verified_public_key_pem: bytes,
    source_root: Path,
    expected_manifest: tuple[tuple[str, str], ...],
    state_dir: Path,
) -> dict[str, Any]:
    """Verify a bounded tuple without caller-controlled time; not runtime authority."""
    if not isinstance(expected_source_sha, str) or not _SHA40.fullmatch(expected_source_sha):
        raise BootstrapReferenceError("Expected source SHA is invalid")
    if not isinstance(expected_evidence_sha256, str) or not _SHA256.fullmatch(expected_evidence_sha256):
        raise BootstrapReferenceError("Expected evidence SHA256 is invalid")
    if expected_operation not in OPERATIONS:
        raise BootstrapReferenceError("Expected operation is not allowed")
    current_time = _current_time()

    parsed = _parse_canonical_envelope(envelope)
    grant = parsed["grant"]
    if set(grant) != _GRANT_FIELDS:
        raise BootstrapReferenceError("Grant fields are invalid")
    if (
        grant["schema"] != SCHEMA
        or grant["repository"] != REPOSITORY
        or type(grant["pull_request"]) is not int
        or grant["pull_request"] != PULL_REQUEST
        or grant["source_sha"] != expected_source_sha
        or not isinstance(grant["source_sha"], str)
        or not _SHA40.fullmatch(grant["source_sha"])
        or grant["operation"] != expected_operation
        or grant["operation"] not in OPERATIONS
        or not isinstance(grant["evidence_sha256"], str)
        or not _SHA256.fullmatch(grant["evidence_sha256"])
        or grant["evidence_sha256"] != expected_evidence_sha256
        or grant["host"] != HOST
        or grant["principal"] != PRINCIPAL
        or type(grant["uid"]) is not int
        or grant["uid"] != UID
        or grant["project"] != PROJECT
        or type(grant["expires_at"]) is not int
        or grant["expires_at"] <= current_time
        or grant["expires_at"] > current_time + MAX_GRANT_SECONDS
        or not isinstance(grant["nonce"], str)
        or not _NONCE.fullmatch(grant["nonce"])
    ):
        raise BootstrapReferenceError("Grant tuple or expiry is invalid")

    encoded_signature = parsed["signature"]
    if not isinstance(encoded_signature, str) or not re.fullmatch(r"[A-Za-z0-9+/]{86}==", encoded_signature):
        raise BootstrapReferenceError("Grant signature encoding is invalid")
    try:
        signature = base64.b64decode(encoded_signature, validate=True)
    except ValueError as error:
        raise BootstrapReferenceError("Grant signature encoding is invalid") from error
    if len(signature) != 64 or base64.b64encode(signature).decode("ascii") != encoded_signature:
        raise BootstrapReferenceError("Grant signature encoding is not canonical")

    _verify_signature(verified_public_key_pem, canonical_json(grant), signature)
    _require_unexpired(grant["expires_at"])
    verify_source_manifest(source_root, expected_manifest)
    _require_unexpired(grant["expires_at"])
    result = dict(grant)
    _consume_nonce(state_dir, grant["nonce"])
    _require_unexpired(grant["expires_at"])
    return result
