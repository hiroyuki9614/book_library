#!/usr/bin/env python3
"""Offline source checks for a proposed retained upgrade.

No independent authority pins are provisioned here. Public ``preflight`` therefore
fails closed; the private fixture path exists only for synthetic unit tests.
Neither path authorizes or performs an upgrade.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Any

STATUS = "NOT_DEPLOYABLE_SOURCE_ONLY"
EXPECTED_PROJECT = "belib-isolated-i5"
EXPECTED_REPOSITORY = "hiroyuki9614/book_library"
EXPECTED_PULL_REQUEST = 103
EXPECTED_BRANCH = "codex/belib-i5-isolated-staging-20261107"
EXPECTED_HOST = "ubuntu-worker"
EXPECTED_PRINCIPAL = "hiroyuki"
EXPECTED_UID = 1000
OLD_APPLICATION_SHA = "81256268276d7ead95029849037f69ec6beec202"
OLD_DATABASE_LINEAGE = "6963103fd6bec332b4057457d07b890fe5c11e73"
OLD_DB_CONTAINER_ID = "9ab9d141599e8b33f51a1e4e9bcd16d66e92b9d134889014d9d9bd618b42f73d"
OLD_BACKEND_IMAGE = "sha256:df3725ef307ebd11eb353e17aecb340c8b909c1451f9032d28fb5cb285a1218c"
OLD_FRONTEND_IMAGE = "sha256:1ec82024965c504fe2c65d9e5cb3f0d3937b7bbaf4cc11ff843d2ae27d95382c"
OLD_DB_IMAGE = "postgres:16.14-bookworm@sha256:714b08dd70c05160db032e4321033ac4e5b6328935b2d03ed419c3a898aa8219"
RETAINED_VOLUMES = (
    "belib-isolated-i5_staging-book-files",
    "belib-isolated-i5_staging-postgres-data",
)
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_QUERY_REPORT_BYTES = 1024 * 1024
MAX_BACKUP_BYTES = 64 * 1024 * 1024 * 1024
MAX_GRANT_SECONDS = 300
HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
HEX_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
VOLUME_ID = re.compile(r"[0-9a-f]{64}\Z")
SAFE_ISSUER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
MANIFEST_FIELDS = {
    "schema", "repository", "pull_request", "branch", "host", "principal", "uid",
    "project", "source_sha", "prior_application_sha", "prior_backend_image_digest",
    "prior_frontend_image_digest", "database_lineage", "db_container_id",
    "db_image_digest", "volumes", "network_ids", "book_files",
}
ATTESTATION_FIELDS = {
    "schema", "evidence_issuer", "backup_sha256", "manifest_sha256",
    "postgresql_major_version", "restore_target_identity", "integrity_query_sha256",
    "restore_success", "issued_at", "expires_at",
}
NETWORK_INVENTORY_FIELDS = {
    "schema", "evidence_issuer", "repository", "pull_request", "branch", "host",
    "principal", "uid", "project", "prior_application_sha", "network_ids",
    "issued_at", "expires_at",
}
BACKUP_ROOT_CONTRACT_FIELDS = {
    "schema", "evidence_issuer", "repository", "pull_request", "branch", "host",
    "principal", "uid", "project", "repository_root", "retained_db_root",
    "retained_books_root", "issued_at", "expires_at",
}


class UpgradePreflightError(Exception):
    """Raised when retained-upgrade evidence is malformed or untrusted."""


def _validate_stage_identity(payload: dict, label: str) -> None:
    expected = {
        "repository": EXPECTED_REPOSITORY,
        "pull_request": EXPECTED_PULL_REQUEST,
        "branch": EXPECTED_BRANCH,
        "host": EXPECTED_HOST,
        "principal": EXPECTED_PRINCIPAL,
        "uid": EXPECTED_UID,
        "project": EXPECTED_PROJECT,
    }
    if any(payload.get(name) != value for name, value in expected.items()):
        raise UpgradePreflightError(f"{label} does not bind the exact expected staging identity")
    if type(payload["pull_request"]) is not int or type(payload["uid"]) is not int:
        raise UpgradePreflightError(f"{label} numeric staging identity fields must be integers")


def _pairs_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpgradePreflightError("Evidence JSON contains a duplicate field")
        result[key] = value
    return result


def strict_json(raw: bytes | str) -> Any:
    """Parse bounded UTF-8 JSON while rejecting duplicate keys at every depth."""
    if isinstance(raw, str):
        try:
            encoded = raw.encode("utf-8", "strict")
        except UnicodeError as error:
            raise UpgradePreflightError("Evidence JSON is malformed") from error
    elif isinstance(raw, bytes):
        encoded = raw
    else:
        raise UpgradePreflightError("Evidence JSON must be bytes or text")
    if len(encoded) > MAX_JSON_BYTES:
        raise UpgradePreflightError("Evidence JSON exceeds the size limit")
    try:
        return json.loads(encoded.decode("utf-8", "strict"), object_pairs_hook=_pairs_no_duplicates)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise UpgradePreflightError("Evidence JSON is malformed") from error


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError) as error:
        raise UpgradePreflightError("Evidence is not canonical JSON data") from error


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _canonical_object(value: Any, label: str) -> Any:
    if isinstance(value, (bytes, str)):
        try:
            raw = value.encode("utf-8", "strict") if isinstance(value, str) else value
        except UnicodeError as error:
            raise UpgradePreflightError(f"{label} JSON is malformed") from error
        parsed = strict_json(raw)
        if canonical_json(parsed) != raw:
            raise UpgradePreflightError(f"{label} JSON is not canonical")
        return parsed
    return value


def _trusted_signed_payload(
    envelope: Any, *, fields: set[str], schema: str, trusted_issuer: str,
    public_key_pem: bytes | None, trusted_fingerprint: str | None,
    label: str,
) -> dict:
    envelope = _canonical_object(envelope, label + " envelope")
    if (not isinstance(trusted_issuer, str) or not SAFE_ISSUER.fullmatch(trusted_issuer)
            or public_key_pem is None or trusted_fingerprint is None):
        raise UpgradePreflightError(f"Caller-provided {label.lower()} issuer/key fixture values are missing")
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
        raise UpgradePreflightError(f"{label} envelope fields are invalid")
    payload = _exact_fields(envelope["payload"], fields, label)
    if payload["schema"] != schema or payload["evidence_issuer"] != trusted_issuer:
        raise UpgradePreflightError(f"{label} schema or caller-provided issuer does not match")
    issued, expires = payload["issued_at"], payload["expires_at"]
    current = int(time.time())
    if (type(issued) is not int or type(expires) is not int or type(current) is not int
            or issued > current + 5 or issued < 0 or expires <= current
            or expires <= issued or expires - issued > MAX_GRANT_SECONDS):
        raise UpgradePreflightError(f"{label} is expired or outside its short validity")
    key = _trusted_ed25519_key(public_key_pem, trusted_fingerprint)
    try:
        signature_text = envelope["signature"]
        signature = base64.b64decode(signature_text, validate=True)
        if (not isinstance(signature_text, str) or len(signature) != 64
                or base64.b64encode(signature).decode("ascii") != signature_text):
            raise ValueError("noncanonical signature")
        key.verify(signature, canonical_json(payload))
    except Exception as error:
        raise UpgradePreflightError(f"{label} signature is missing or invalid") from error
    return payload


def _trusted_backup_roots(
    envelope: Any, *, trusted_issuer: str, public_key_pem: bytes | None,
    trusted_fingerprint: str | None,
) -> tuple[Path, ...]:
    """Resolve the complete signed repo, retained DB, and retained books exclusion set."""
    payload = _trusted_signed_payload(
        envelope, fields=BACKUP_ROOT_CONTRACT_FIELDS,
        schema="belib-retained-upgrade-backup-roots/v1", trusted_issuer=trusted_issuer,
        public_key_pem=public_key_pem, trusted_fingerprint=trusted_fingerprint,
        label="Backup root contract",
    )
    _validate_stage_identity(payload, "Backup root contract")
    roots = []
    for field in ("repository_root", "retained_db_root", "retained_books_root"):
        value = payload[field]
        if not isinstance(value, str) or not value or not Path(value).is_absolute():
            raise UpgradePreflightError("Backup root contract contains a missing or non-absolute protected root")
        try:
            supplied = Path(value)
            resolved = supplied.resolve(strict=True)
            if supplied != Path(os.path.abspath(supplied)) or supplied != resolved or not resolved.is_dir():
                raise UpgradePreflightError("Backup root contract contains an unverifiable protected root")
        except OSError as error:
            raise UpgradePreflightError("Backup root contract contains an unavailable protected root") from error
        roots.append(resolved)
    if len(set(roots)) != 3 or any(
            left in right.parents or right in left.parents
            for index, left in enumerate(roots) for right in roots[index + 1:]):
        raise UpgradePreflightError("Backup root contract must pin distinct, non-overlapping repository, DB, and books roots")
    return tuple(roots)


def validate_backup_artifact(
    backup_path: str | os.PathLike[str], *, uid: int,
    trusted_root_contract: Any, trusted_root_contract_issuer: str | None,
    root_contract_public_key_pem: bytes | None, trusted_root_contract_fingerprint: str | None,
    extra_exclusion_roots: tuple[Path, ...] = (),
) -> str:
    """Fixture-level backup check; caller-supplied pins do not establish independent trust."""
    path = Path(backup_path)
    if not path.is_absolute():
        raise UpgradePreflightError("Backup path must be absolute")
    try:
        resolved = path.resolve(strict=True)
        if Path(os.path.abspath(path)) != resolved:
            raise UpgradePreflightError("Backup path may not contain symlinked components")
        if trusted_root_contract_issuer is None:
            raise UpgradePreflightError("Caller-provided backup-root contract fixture pins are missing")
        forbidden_roots = _trusted_backup_roots(
            trusted_root_contract, trusted_issuer=trusted_root_contract_issuer,
            public_key_pem=root_contract_public_key_pem,
            trusted_fingerprint=trusted_root_contract_fingerprint,
        )
        for root in extra_exclusion_roots:
            try:
                resolved_root = Path(root).resolve(strict=True)
            except (OSError, TypeError) as error:
                raise UpgradePreflightError("An optional backup exclusion root is unverifiable") from error
            if not resolved_root.is_dir():
                raise UpgradePreflightError("An optional backup exclusion root is not a directory")
            forbidden_roots += (resolved_root,)
        if any(resolved == root or root in resolved.parents for root in forbidden_roots):
            raise UpgradePreflightError("Backup must be stored outside source and retained-volume paths")
        before = os.stat(resolved, follow_symlinks=False)
    except UpgradePreflightError:
        raise
    except OSError as error:
        raise UpgradePreflightError("Backup artifact is unavailable") from error
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != uid or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1 or before.st_size <= 0 or before.st_size > MAX_BACKUP_BYTES):
        raise UpgradePreflightError("Backup must be a bounded nonempty, singly linked mode-0600 regular file owned by the operator")
    digest = hashlib.sha256()
    fd = None
    dir_fd = None
    try:
        dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        dir_fd = os.open(resolved.parent, dir_flags)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(resolved.name, flags, dir_fd=dir_fd)
        with os.fdopen(fd, "rb") as stream:
            fd = None
            opened = os.fstat(stream.fileno())
            identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != identity:
                raise UpgradePreflightError("Backup changed while it was opened")
            total = 0
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(chunk)
                if total > MAX_BACKUP_BYTES:
                    raise UpgradePreflightError("Backup artifact exceeds the size limit")
                digest.update(chunk)
            after = os.fstat(stream.fileno())
            if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != identity or total != before.st_size:
                raise UpgradePreflightError("Backup changed while its digest was read")
    except UpgradePreflightError:
        raise
    except OSError as error:
        raise UpgradePreflightError("Backup artifact could not be read safely") from error
    finally:
        if fd is not None:
            os.close(fd)
        if dir_fd is not None:
            os.close(dir_fd)
    return digest.hexdigest()


def _exact_fields(value: Any, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise UpgradePreflightError(f"{label} fields are missing or unexpected")
    return value


def _sha(value: Any, label: str, pattern=HEX_SHA256) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise UpgradePreflightError(f"{label} is malformed")
    return value


def _safe_book_path(value: Any) -> str:
    if (not isinstance(value, str) or not value or len(value.encode("utf-8", "ignore")) > 1024
            or value.startswith("/") or "\\" in value or "\x00" in value):
        raise UpgradePreflightError("Book inventory path is unsafe")
    parts = value.split("/")
    if any(part in ("", ".", "..") or part.startswith(".") for part in parts):
        raise UpgradePreflightError("Book inventory path is unsafe")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise UpgradePreflightError("Book inventory path is unsafe")
    return value


def validate_manifest(
    manifest: Any, expected_source_sha: str, *, trusted_network_inventory: Any,
    trusted_network_inventory_issuer: str | None, network_inventory_public_key_pem: bytes | None,
    trusted_network_inventory_fingerprint: str | None,
) -> str:
    """Fixture-level manifest check; caller-supplied pins do not establish authority."""
    _sha(expected_source_sha, "Expected candidate source SHA", HEX_SHA1)
    manifest = _canonical_object(manifest, "Manifest")
    item = _exact_fields(manifest, MANIFEST_FIELDS, "Manifest")
    fixed = {
        "schema": "belib-retained-upgrade-manifest/v2",
        "repository": EXPECTED_REPOSITORY,
        "pull_request": EXPECTED_PULL_REQUEST,
        "branch": EXPECTED_BRANCH,
        "host": EXPECTED_HOST,
        "principal": EXPECTED_PRINCIPAL,
        "uid": EXPECTED_UID,
        "project": EXPECTED_PROJECT,
        "source_sha": expected_source_sha,
        "prior_application_sha": OLD_APPLICATION_SHA,
        "prior_backend_image_digest": OLD_BACKEND_IMAGE,
        "prior_frontend_image_digest": OLD_FRONTEND_IMAGE,
        "database_lineage": OLD_DATABASE_LINEAGE,
        "db_container_id": OLD_DB_CONTAINER_ID,
        "db_image_digest": OLD_DB_IMAGE,
    }
    if any(item.get(name) != value for name, value in fixed.items()):
        raise UpgradePreflightError("Manifest staging, source, OCI, or retained DB identity mismatch")
    if any(type(item[name]) is not int for name in ("pull_request", "uid")):
        raise UpgradePreflightError("Manifest numeric identity fields must be integers")
    volumes = item["volumes"]
    if (not isinstance(volumes, list) or len(volumes) != 2
            or [v.get("name") if isinstance(v, dict) else None for v in volumes] != list(RETAINED_VOLUMES)):
        raise UpgradePreflightError("Manifest must name both retained volumes in canonical order")
    for volume in volumes:
        _exact_fields(volume, {"name", "identity_sha256"}, "Volume identity")
        _sha(volume["identity_sha256"], "Volume identity digest")
    if trusted_network_inventory_issuer is None:
        raise UpgradePreflightError("Caller-provided network-inventory fixture pins are missing")
    network_pin = _trusted_signed_payload(
        trusted_network_inventory, fields=NETWORK_INVENTORY_FIELDS,
        schema="belib-retained-upgrade-network-inventory/v1",
        trusted_issuer=trusted_network_inventory_issuer,
        public_key_pem=network_inventory_public_key_pem,
        trusted_fingerprint=trusted_network_inventory_fingerprint,
        label="Network inventory",
    )
    _validate_stage_identity(network_pin, "Network inventory")
    if network_pin["prior_application_sha"] != OLD_APPLICATION_SHA:
        raise UpgradePreflightError("Network inventory does not bind the exact prior application")
    expected_networks = network_pin["network_ids"]
    if (not isinstance(expected_networks, list) or len(expected_networks) != 2
            or expected_networks != sorted(expected_networks)
            or expected_networks[0] == expected_networks[1]):
        raise UpgradePreflightError("Pinned network inventory must contain the exact sorted distinct network pair")
    for network in expected_networks:
        _sha(network, "Pinned network identity")
    networks = item["network_ids"]
    if (not isinstance(networks, list) or len(networks) != 2 or networks != sorted(networks)
            or networks != expected_networks):
        raise UpgradePreflightError("Manifest network identities do not match the caller-provided fixture pair")
    files = item["book_files"]
    if not isinstance(files, list) or len(files) > 100_000:
        raise UpgradePreflightError("Book inventory is malformed or too large")
    prior_path = None
    for entry in files:
        _exact_fields(entry, {"path", "size", "sha256"}, "Book file inventory entry")
        path = _safe_book_path(entry["path"])
        if prior_path is not None and path <= prior_path:
            raise UpgradePreflightError("Book inventory paths must be strictly sorted and unique")
        prior_path = path
        if type(entry["size"]) is not int or entry["size"] < 0:
            raise UpgradePreflightError("Book inventory size is malformed")
        _sha(entry["sha256"], "Book file digest")
    return canonical_sha256(item)


def _trusted_ed25519_key(public_key_pem: bytes, trusted_fingerprint: str):
    """Load a fixture Ed25519 key; matching its fingerprint does not establish authority."""
    _sha(trusted_fingerprint, "Fixture verifier fingerprint")
    if not isinstance(public_key_pem, bytes) or len(public_key_pem) > 16 * 1024:
        raise UpgradePreflightError("Fixture verifier key is missing or malformed")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        loaded = serialization.load_pem_public_key(public_key_pem)
        if not isinstance(loaded, Ed25519PublicKey):
            raise ValueError("not Ed25519")
        raw = loaded.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    except (ImportError, ValueError, TypeError) as error:
        raise UpgradePreflightError("Fixture verifier key is missing or malformed") from error
    if hashlib.sha256(raw).hexdigest() != trusted_fingerprint:
        raise UpgradePreflightError("Fixture verifier key does not match its supplied fingerprint")
    return loaded


def validate_restore_attestation(
    envelope: Any, *, backup_sha256: str, manifest_sha256: str, query_report: bytes,
    trusted_issuer: str, verifier_public_key_pem: bytes | None,
    trusted_verifier_fingerprint: str | None,
) -> str:
    """Fixture-level signature check; caller-supplied pins do not establish authority."""
    envelope = _canonical_object(envelope, "Restore attestation envelope")
    _sha(backup_sha256, "Backup digest")
    _sha(manifest_sha256, "Manifest digest")
    if not isinstance(query_report, bytes) or len(query_report) > MAX_QUERY_REPORT_BYTES:
        raise UpgradePreflightError("Integrity query report is missing or exceeds the size limit")
    if not isinstance(trusted_issuer, str) or not SAFE_ISSUER.fullmatch(trusted_issuer):
        raise UpgradePreflightError("Fixture verifier issuer is missing or malformed")
    if verifier_public_key_pem is None or trusted_verifier_fingerprint is None:
        raise UpgradePreflightError("Caller-provided restore-verifier fixture pins are missing")
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
        raise UpgradePreflightError("Restore attestation envelope fields are invalid")
    payload = _exact_fields(envelope["payload"], ATTESTATION_FIELDS, "Restore attestation")
    if payload["schema"] != "belib-retained-upgrade-restore-attestation/v1":
        raise UpgradePreflightError("Restore attestation schema is invalid")
    if payload["evidence_issuer"] != trusted_issuer:
        raise UpgradePreflightError("Restore attestation issuer does not match the caller-provided fixture")
    if payload["backup_sha256"] != backup_sha256 or payload["manifest_sha256"] != manifest_sha256:
        raise UpgradePreflightError("Restore attestation backup or manifest digest does not match")
    if payload["integrity_query_sha256"] != hashlib.sha256(query_report).hexdigest():
        raise UpgradePreflightError("Integrity query report digest does not match the attestation")
    if type(payload["postgresql_major_version"]) is not int or payload["postgresql_major_version"] != 16:
        raise UpgradePreflightError("Restore PostgreSQL major version is unsupported")
    target = payload["restore_target_identity"]
    _exact_fields(target, {"disposable", "identity"}, "Restore target identity")
    if target["disposable"] is not True or not isinstance(target["identity"], str) or not target["identity"]:
        raise UpgradePreflightError("Restore target is not identified as a disposable isolated instance")
    if type(payload["restore_success"]) is not bool or payload["restore_success"] is not True:
        raise UpgradePreflightError("Restore did not report an actual successful restore")
    issued, expires = payload["issued_at"], payload["expires_at"]
    if type(issued) is not int or type(expires) is not int:
        raise UpgradePreflightError("Restore attestation validity times must be integers")
    current = int(time.time())
    if (type(current) is not int or issued > current + 5 or issued < 0 or expires <= current
            or expires <= issued or expires - issued > MAX_GRANT_SECONDS):
        raise UpgradePreflightError("Restore attestation is expired or outside its short validity")
    key = _trusted_ed25519_key(verifier_public_key_pem, trusted_verifier_fingerprint)
    try:
        signature = base64.b64decode(envelope["signature"], validate=True)
        if (not isinstance(envelope["signature"], str) or len(signature) != 64
                or base64.b64encode(signature).decode("ascii") != envelope["signature"]):
            raise ValueError("noncanonical signature")
        key.verify(signature, canonical_json(payload))
    except Exception as error:
        raise UpgradePreflightError("Restore attestation signature is missing or invalid") from error
    return canonical_sha256(payload)


def _preflight_with_test_fixtures(*, operation: str, source_sha: str, manifest: Any, attestation: Any,
              backup_path: str | os.PathLike[str], uid: int,
              trusted_root_contract: Any, trusted_root_contract_issuer: str | None,
              root_contract_public_key_pem: bytes | None,
              trusted_root_contract_fingerprint: str | None,
              extra_exclusion_roots: tuple[Path, ...] = (),
              trusted_network_inventory: Any, trusted_network_inventory_issuer: str | None,
              network_inventory_public_key_pem: bytes | None,
              trusted_network_inventory_fingerprint: str | None,
              query_report: bytes, verifier_public_key_pem: bytes | None,
              trusted_verifier_fingerprint: str | None, trusted_issuer: str | None) -> dict[str, Any]:
    """Private synthetic-fixture helper; caller-provided pins prove no independent authority."""
    if operation != "upgrade-retained":
        raise UpgradePreflightError("Only the distinct upgrade-retained operation is recognized")
    manifest_sha = validate_manifest(
        manifest, source_sha, trusted_network_inventory=trusted_network_inventory,
        trusted_network_inventory_issuer=trusted_network_inventory_issuer,
        network_inventory_public_key_pem=network_inventory_public_key_pem,
        trusted_network_inventory_fingerprint=trusted_network_inventory_fingerprint,
    )
    backup_sha = validate_backup_artifact(
        backup_path, uid=uid, trusted_root_contract=trusted_root_contract,
        trusted_root_contract_issuer=trusted_root_contract_issuer,
        root_contract_public_key_pem=root_contract_public_key_pem,
        trusted_root_contract_fingerprint=trusted_root_contract_fingerprint,
        extra_exclusion_roots=extra_exclusion_roots,
    )
    if trusted_issuer is None:
        raise UpgradePreflightError("Caller-provided restore-verifier fixture pins are missing")
    attestation_sha = validate_restore_attestation(
        attestation, backup_sha256=backup_sha, manifest_sha256=manifest_sha,
        query_report=query_report, trusted_issuer=trusted_issuer,
        verifier_public_key_pem=verifier_public_key_pem,
        trusted_verifier_fingerprint=trusted_verifier_fingerprint,
    )
    return {
        "status": STATUS,
        "operation": operation,
        "source_sha": source_sha,
        "manifest_sha256": manifest_sha,
        "backup_sha256": backup_sha,
        "restore_attestation_sha256": attestation_sha,
        "deployable": False,
        "blockers": [
            "Independent PostgreSQL restore verification has not been performed by this source-only work.",
            "No approved retained-database rollback topology exists.",
            "No upgrade-retained operation exists in the launch guard or signed approval allowlist.",
        ],
    }


def _rehearse_transaction_with_test_fixtures(*, journal_root: Path, operation_id: str,
                                           operations: Any, **evidence: Any) -> dict[str, Any]:
    """Exercise the transaction after synthetic evidence checks, never live admission.

    This is the source-only fixture route. The public preflight remains separate
    until independently installed trust pins and the fixed live adapter exist.
    ``operations`` must refer only to test-owned disposable resources.
    """
    checked = _preflight_with_test_fixtures(**evidence)
    path = Path(__file__).with_name("retained-db-transaction.py")
    spec = importlib.util.spec_from_file_location("belib_retained_db_transaction", path)
    if spec is None or spec.loader is None:
        raise UpgradePreflightError("Retained transaction implementation is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class BoundFixtureOperations:
        def __getattr__(self, name):
            return getattr(operations, name)

        def backup(self):
            digest = operations.backup()
            if digest != checked["backup_sha256"]:
                raise UpgradePreflightError("Transaction backup differs from verified fixture evidence")
            return digest

    journal = module.Journal(journal_root, {
        "operation_id": operation_id,
        "source_sha": checked["source_sha"],
        "retained_state_sha256": checked["manifest_sha256"],
    })
    receipt = module.run_admitted_transaction(journal, BoundFixtureOperations())
    return {**checked, "transaction_rehearsal": receipt, "deployable": False}


def preflight(*, operation: str, source_sha: str, manifest: Any,
              network_inventory: Any, backup_root_contract: Any,
              attestation: Any, backup_path: str | os.PathLike[str], uid: int,
              query_report: bytes) -> dict[str, Any]:
    """Check evidence expiry using system UTC time, then fail closed without external pins.

    Issuer names, keys, and fingerprints embedded in or supplied alongside evidence
    are not authority. Provisioning an independent trust source is BLOCKED outside
    this source-only scope, so this entry point never returns a positive result.
    """
    del operation, source_sha, manifest, backup_path, uid, query_report
    current = int(time.time())
    for label, envelope in (("Network inventory", network_inventory),
                            ("Backup root contract", backup_root_contract),
                            ("Restore attestation", attestation)):
        parsed = _canonical_object(envelope, label + " envelope")
        if not isinstance(parsed, dict) or not isinstance(parsed.get("payload"), dict):
            raise UpgradePreflightError(f"{label} envelope fields are invalid")
        issued = parsed["payload"].get("issued_at")
        expires = parsed["payload"].get("expires_at")
        if (type(issued) is not int or type(expires) is not int or issued < 0
                or issued > current + 5 or expires <= current or expires <= issued
                or expires - issued > MAX_GRANT_SECONDS):
            raise UpgradePreflightError(f"{label} is expired or outside its short validity")
    raise UpgradePreflightError(
        "BLOCKED: external independent pin provision is not configured; caller-supplied "
        "issuer, key, and fingerprint values do not establish trust"
    )

