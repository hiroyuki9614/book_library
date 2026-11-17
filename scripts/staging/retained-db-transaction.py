#!/usr/bin/env python3
"""Retained PostgreSQL upgrade transaction used by an admitted staging executor.

This module has no CLI, trust store, grant issuer, Docker discovery, or launch
authority. The independently installed executor must authenticate the operation,
freeze the exact resource identities and hold its project lock before calling it.
The operations object owns only those already-admitted resources. In particular,
``verify_restore`` must restore the dump into a separate disposable PostgreSQL
instance and compare data/integrity; checking a digest alone is insufficient.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Protocol


class UpgradeError(RuntimeError):
    pass


class Operations(Protocol):
    # backup() must fsync its immutable dump before returning the digest;
    # restore_backup() must verify those exact bytes and never reseed.
    def quiesce(self) -> None: ...
    def backup(self) -> str: ...
    def verify_restore(self, backup_sha256: str) -> None: ...
    def migrate(self) -> None: ...
    def verify_upgraded(self) -> None: ...
    def activate_read_only(self) -> None: ...
    def verify_active(self) -> None: ...
    def release_writes(self) -> None: ...
    def restore_backup(self, backup_sha256: str) -> None: ...
    def verify_previous(self) -> None: ...
    def resume_previous_read_only(self) -> None: ...


PHASES = (
    "prepared", "quiesce_started", "quiesced", "backup_started", "backup_created",
    "restore_check_started", "restore_verified", "migration_started", "migrated",
    "upgrade_check_started", "upgrade_verified", "activation_started", "activated",
    "active_check_started", "commit_decided", "completed", "rollback_started", "rolled_back",
    "resume_previous_started", "abort_decided", "aborted", "recovery_failed",
)
MUTATION_PHASES = frozenset(PHASES[7:14]) | {"rollback_started"}
BACKUP_REQUIRED = frozenset(PHASES[4:18])
BACKUP_FORBIDDEN = frozenset(PHASES[:4])


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise UpgradeError("Journal contains duplicate fields")
        value[key] = item
    return value


def _validate_phase_backup(value):
    phase, backup = value["phase"], value["backup_sha256"]
    if not isinstance(phase, str) or phase not in PHASES:
        raise UpgradeError("Journal phase is invalid")
    if phase in BACKUP_REQUIRED:
        _digest(backup)
    elif backup is not None:
        _digest(backup)
    if phase in BACKUP_FORBIDDEN and backup is not None:
        raise UpgradeError("Journal backup exists before creation")


def _digest(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise UpgradeError("Backup digest is invalid")
    return value


class Journal:
    """One existing project lock protects this private, durable operation journal."""

    def __init__(self, root: Path, identity: dict):
        # Identity is selected by the trusted caller, never inferred from a file.
        if set(identity) != {"operation_id", "source_sha", "retained_state_sha256"}:
            raise UpgradeError("Transaction identity fields are invalid")
        if not re.fullmatch(r"[0-9a-f]{32}", identity.get("operation_id", "")):
            raise UpgradeError("Operation identity is invalid")
        if not re.fullmatch(r"[0-9a-f]{40}", identity.get("source_sha", "")):
            raise UpgradeError("Source revision is invalid")
        _digest(identity.get("retained_state_sha256", ""))
        info = root.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700 or root.resolve() != root):
            raise UpgradeError("Journal directory must be private and symlink-free")
        self.root = root
        self.path = root / (identity["operation_id"] + ".json")
        self.identity = dict(identity)
        self.value = {"schema": "belib-retained-db-transaction/v1", **identity,
                      "phase": "prepared", "backup_sha256": None}
        if self.path.exists() or self.path.is_symlink():
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                        or info.st_size > 4096):
                    raise UpgradeError("Journal file is unsafe")
                raw = os.read(fd, 4097)
            finally:
                os.close(fd)
            try:
                value = json.loads(raw, object_pairs_hook=_unique_pairs)
            except (ValueError, UnicodeError) as error:
                raise UpgradeError("Journal is malformed") from error
            if (not isinstance(value, dict) or set(value) != set(self.value)
                    or any(value.get(k) != v for k, v in identity.items())
                    or value.get("schema") != self.value["schema"]
                    or not isinstance(value.get("phase"), str) or value.get("phase") not in PHASES):
                raise UpgradeError("Journal belongs to a different or invalid transaction")
            _validate_phase_backup(value)
            self.value = value
        else:
            self.write("prepared")

    def write(self, phase: str, backup_sha256: str | None = None) -> None:
        if phase not in PHASES:
            raise UpgradeError("Unknown transaction phase")
        value = {**self.value, "phase": phase}
        if backup_sha256 is not None:
            value["backup_sha256"] = _digest(backup_sha256)
        _validate_phase_backup(value)
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        temporary = self.path.with_suffix(".pending")
        # A crash may leave our previous temporary file. Never follow a symlink.
        if temporary.exists() or temporary.is_symlink():
            info = temporary.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise UpgradeError("Pending journal is unsafe")
            temporary.unlink()
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary.exists():
                temporary.unlink()
        self.value = value


def _recover(journal: Journal, operations: Operations) -> None:
    phase = journal.value["phase"]
    if phase == "recovery_failed":
        raise UpgradeError("Failed recovery needs explicit operator reconciliation")
    try:
        if phase in MUTATION_PHASES:
            backup_sha = _digest(journal.value["backup_sha256"])
            journal.write("rollback_started")
            # Quiesce again: activation might have succeeded before interruption.
            operations.quiesce()
            operations.restore_backup(backup_sha)
            operations.verify_previous()
            journal.write("rolled_back")
        operations.verify_previous()
        journal.write("resume_previous_started")
        operations.resume_previous_read_only()
        operations.verify_previous()
        journal.write("abort_decided")
        operations.release_writes()
        journal.write("aborted")
    except Exception as error:
        # Even a resume/release method can fail after making the old app visible.
        # Best-effort re-quiesce is mandatory; never pretend a failed barrier is safe.
        try:
            operations.quiesce()
        except Exception as quiesce_error:
            error.add_note("Re-quiesce also failed: " + type(quiesce_error).__name__)
        journal.write("recovery_failed")
        raise UpgradeError("Recovery failed; keep application quiesced and preserve backup") from error


def _publish_committed(journal: Journal, operations: Operations) -> dict:
    # The commit decision is durable before write admission. A release that
    # succeeds then raises may already have acknowledged writes: NEVER roll back.
    operations.verify_active()
    operations.release_writes()
    journal.write("completed")
    return dict(journal.value)


def run_admitted_transaction(journal: Journal, operations: Operations) -> dict:
    """Execute once, or reconcile an interrupted operation without re-migrating.

    Admission, project lock, immutable source, host/principal and exact resource
    binding are obligations of the installed caller. This function never marks
    its result as independent approval or deployment admission. A recovered
    attempt is terminal; another upgrade needs a fresh admitted operation ID.
    """
    phase = journal.value["phase"]
    if phase == "completed":
        operations.verify_active()
        return dict(journal.value)
    if phase == "commit_decided":
        return _publish_committed(journal, operations)
    if phase == "aborted":
        return dict(journal.value)
    if phase != "prepared":
        _recover(journal, operations)
        return dict(journal.value)
    try:
        journal.write("quiesce_started")
        operations.quiesce()
        journal.write("quiesced")
        journal.write("backup_started")
        backup_sha = _digest(operations.backup())
        journal.write("backup_created", backup_sha)
        journal.write("restore_check_started")
        operations.verify_restore(backup_sha)
        journal.write("restore_verified")
        journal.write("migration_started")
        operations.migrate()
        journal.write("migrated")
        journal.write("upgrade_check_started")
        operations.verify_upgraded()
        journal.write("upgrade_verified")
        journal.write("activation_started")
        operations.activate_read_only()
        journal.write("activated")
        journal.write("active_check_started")
        operations.verify_active()
        journal.write("commit_decided")
    except Exception as error:
        _recover(journal, operations)
        raise UpgradeError("Upgrade aborted; previous database/application restored") from error
    # Outside the rollback handler: publication failure after commit cannot erase
    # writes. Retry the same operation to reconcile its committed application.
    return _publish_committed(journal, operations)
