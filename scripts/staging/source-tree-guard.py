#!/usr/bin/env python3
"""Fail closed when checked-out staging build inputs differ from one Git commit."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import ctypes
import errno
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


GIT = "/usr/bin/git"
CONTEXTS = (b"backend", b"frontend")
DOCKERIGNORE_FILES = (b"backend/.dockerignore", b"frontend/.dockerignore")
DOCKERFILE_INPUTS = (
    b"scripts/staging/Dockerfile.backend.staging",
    b"scripts/staging/Dockerfile.frontend.staging",
)
SNAPSHOT_REVISION_FILE = b".belib-staging-source-sha"
SNAPSHOT_MANIFEST_FILE = b".belib-staging-manifest.json"
SNAPSHOT_ROOT = Path("/home/hiroyuki/.local/state/belib-staging/snapshots")
SNAPSHOT_SUPPORT_FILES = (
    b".github/workflows/staging-ci.yml",
    b"compose.staging.yaml",
    b"nginx/nginx.conf",
    b"scripts/staging/10-belib-staging-roles.sh",
    b"scripts/staging/ci-isolation-guard.py",
    b"scripts/staging/nginx-default.conf",
    *DOCKERFILE_INPUTS,
)
SNAPSHOT_TLS_FILES = frozenset((
    b"scripts/staging/compose-tls.yaml",
    b"scripts/staging/nginx-tls.conf",
    b"scripts/staging/nginx-tls-default.conf",
    b"scripts/staging/nginx-tls-locations.conf",
    b"scripts/staging/tls-ingress.py",
))


class SourceTreeBlocked(Exception):
    pass


def git(root: Path, *args: str) -> bytes:
    env = {
        "HOME": "/nonexistent",
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "LC_ALL": "C",
    }
    try:
        result = subprocess.run(
            [
                GIT,
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                *args,
            ],
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError as exc:
        raise SourceTreeBlocked("Trusted Git could not inspect the selected source commit") from exc
    if result.returncode != 0:
        raise SourceTreeBlocked("Trusted Git could not inspect the selected source commit")
    return result.stdout


def parse_tree(root: Path, revision: str) -> dict[bytes, tuple[bytes, bytes]]:
    raw = git(root, "ls-tree", "-rz", "--full-tree", revision)
    entries: dict[bytes, tuple[bytes, bytes]] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path = record.split(b"\t", 1)
            mode, kind, oid = metadata.split(b" ")
        except ValueError as exc:
            raise SourceTreeBlocked("Git tree metadata is malformed") from exc
        if kind not in (b"blob", b"commit") or path in entries:
            raise SourceTreeBlocked("Git tree contains an unsupported or duplicate source entry")
        entries[path] = (mode, oid)
    if not entries:
        raise SourceTreeBlocked("Selected Git commit has no source tree")
    return entries


def verify_index(root: Path, tree: dict[bytes, tuple[bytes, bytes]]) -> None:
    staged: dict[bytes, tuple[bytes, bytes]] = {}
    raw = git(root, "ls-files", "--stage", "-z", "--full-name")
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path = record.split(b"\t", 1)
            mode, oid, stage = metadata.split(b" ")
        except ValueError as exc:
            raise SourceTreeBlocked("Git index metadata is malformed") from exc
        if stage != b"0" or path in staged:
            raise SourceTreeBlocked("Git index contains an unresolved or duplicate entry")
        staged[path] = (mode, oid)
    if staged != tree:
        raise SourceTreeBlocked("Git index does not match the selected source commit")

    tagged = git(root, "ls-files", "-v", "-z", "--full-name")
    for record in tagged.split(b"\0"):
        if not record:
            continue
        if len(record) < 3 or record[1:2] != b" ":
            raise SourceTreeBlocked("Git index flags could not be verified")
        tag = record[:1]
        if tag != b"H":
            raise SourceTreeBlocked("Git index contains assume-unchanged or skip-worktree flags")


def hash_regular_file(path: bytes, object_format: str, collect_limit: int = 0) -> tuple[bytes, int, bytes | None]:
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise SourceTreeBlocked("A tracked source path is missing or unreadable") from exc
    if not stat.S_ISREG(before.st_mode):
        raise SourceTreeBlocked("A tracked source path is not a regular file")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise SourceTreeBlocked("A tracked source path could not be opened safely") from exc
    digest = hashlib.new(object_format)
    collected = bytearray() if collect_limit else None
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise SourceTreeBlocked("A tracked source path changed while it was inspected")
        digest.update(b"blob " + str(opened.st_size).encode("ascii") + b"\0")
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            if collected is not None:
                collected.extend(chunk)
                if len(collected) > collect_limit:
                    raise SourceTreeBlocked("A Docker ignore file is too large")
    finally:
        os.close(fd)
    return digest.hexdigest().encode("ascii"), opened.st_mode, bytes(collected) if collected is not None else None


def hash_blob(content: bytes, object_format: str) -> bytes:
    digest = hashlib.new(object_format)
    digest.update(b"blob " + str(len(content)).encode("ascii") + b"\0" + content)
    return digest.hexdigest().encode("ascii")


def path_components_are_directories(root_bytes: bytes, relative: bytes) -> None:
    components = relative.split(b"/")[:-1]
    current = root_bytes
    for component in components:
        current = os.path.join(current, component)
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise SourceTreeBlocked("A tracked source directory is missing or unreadable") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise SourceTreeBlocked("A tracked source path has a symlink or non-directory parent")


def verify_worktree(root: Path, tree: dict[bytes, tuple[bytes, bytes]], object_format: str) -> dict[bytes, bytes]:
    root_bytes = os.fsencode(root)
    dockerignore_content: dict[bytes, bytes] = {}
    for relative, (mode, oid) in tree.items():
        full_path = os.path.join(root_bytes, relative)
        path_components_are_directories(root_bytes, relative)
        try:
            info = os.lstat(full_path)
        except OSError as exc:
            raise SourceTreeBlocked("A tracked source path is missing or unreadable") from exc

        if mode == b"160000":
            if relative.startswith((b"backend/", b"frontend/")):
                raise SourceTreeBlocked("A Docker build context contains an unsupported Git submodule")
            if not stat.S_ISDIR(info.st_mode):
                raise SourceTreeBlocked("A tracked Git submodule path is not a directory")
            continue

        if mode == b"120000":
            if (
                relative.startswith((b"backend/", b"frontend/"))
                or relative in DOCKERIGNORE_FILES
                or relative in DOCKERFILE_INPUTS
            ):
                raise SourceTreeBlocked("A Docker build input contains a symlink")
            if not stat.S_ISLNK(info.st_mode):
                raise SourceTreeBlocked("A tracked symlink differs from the selected source commit")
            try:
                target = os.readlink(full_path)
            except OSError as exc:
                raise SourceTreeBlocked("A tracked symlink could not be inspected") from exc
            if hash_blob(target, object_format) != oid:
                raise SourceTreeBlocked("A tracked symlink differs from the selected source commit")
            continue

        if mode not in (b"100644", b"100755"):
            raise SourceTreeBlocked("Git tree contains an unsupported file mode")
        collect_limit = 64 * 1024 if relative in DOCKERIGNORE_FILES else 0
        actual_oid, actual_mode, collected = hash_regular_file(full_path, object_format, collect_limit)
        is_build_input = relative.startswith((b"backend/", b"frontend/")) or relative in DOCKERFILE_INPUTS
        actual_permissions = stat.S_IMODE(actual_mode)
        mode_mismatch = (
            actual_permissions not in ((0o700, 0o755) if mode == b"100755" else (0o600, 0o644))
            if is_build_input
            else bool(actual_mode & 0o111) != (mode == b"100755")
        )
        if actual_oid != oid or mode_mismatch:
            raise SourceTreeBlocked("Tracked source bytes or mode differ from the selected source commit")
        if relative in DOCKERIGNORE_FILES:
            if collected is None:
                raise SourceTreeBlocked("A Docker ignore file could not be read")
            dockerignore_content[relative] = collected

    return dockerignore_content


def dockerignore_patterns(content: bytes) -> tuple[str, ...]:
    try:
        text = content.decode("ascii")
    except UnicodeDecodeError as exc:
        raise SourceTreeBlocked("Docker ignore rules contain unsupported text") from exc
    patterns: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!") or "/" in line or "\\" in line or line in (".", ".."):
            raise SourceTreeBlocked("Docker ignore rules use unsupported syntax")
        if not re.fullmatch(r"[A-Za-z0-9._*-]+", line):
            raise SourceTreeBlocked("Docker ignore rules use unsupported syntax")
        patterns.append(line)
    if not patterns:
        raise SourceTreeBlocked("Docker ignore rules are missing")
    return tuple(patterns)


def ignored(relative: bytes, patterns: tuple[str, ...]) -> bool:
    try:
        components = relative.decode("utf-8", "surrogateescape").split("/")
    except UnicodeDecodeError as exc:  # pragma: no cover - surrogateescape is total for filesystem bytes
        raise SourceTreeBlocked("Docker context path could not be decoded") from exc
    return any(fnmatch.fnmatchcase(component, pattern) for component in components for pattern in patterns)


def verify_context(root: Path, prefix: bytes, tree_paths: set[bytes], patterns: tuple[str, ...]) -> int:
    root_bytes = os.fsencode(root)
    context_root = os.path.join(root_bytes, prefix)
    try:
        info = os.lstat(context_root)
    except OSError as exc:
        raise SourceTreeBlocked("A Docker build context is missing") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise SourceTreeBlocked("A Docker build context is not a real directory")
    if stat.S_IMODE(info.st_mode) & 0o7022:
        raise SourceTreeBlocked("A Docker build context directory has unsafe permissions")

    expected_directories: set[bytes] = {prefix}
    for path in tree_paths:
        if path.startswith(prefix + b"/"):
            parts = path.split(b"/")[:-1]
            for length in range(1, len(parts) + 1):
                expected_directories.add(b"/".join(parts[:length]))

    seen_files = 0
    for current, directories, files in os.walk(context_root, topdown=True, followlinks=False):
        current_rel = os.path.relpath(current, root_bytes)
        retained_directories: list[bytes] = []
        for name in directories:
            relative = os.path.join(current_rel, name).replace(os.sep.encode(), b"/")
            if ignored(relative, patterns):
                continue
            full_path = os.path.join(current, name)
            try:
                entry = os.lstat(full_path)
            except OSError as exc:
                raise SourceTreeBlocked("A Docker build context changed during inspection") from exc
            if stat.S_ISLNK(entry.st_mode):
                if relative not in tree_paths:
                    raise SourceTreeBlocked("An ignored-by-Git path would enter a Docker build context")
                raise SourceTreeBlocked("A Docker build input contains a symlink")
            if not stat.S_ISDIR(entry.st_mode):
                raise SourceTreeBlocked("A Docker build context contains an unsupported filesystem entry")
            if stat.S_IMODE(entry.st_mode) & 0o7022:
                raise SourceTreeBlocked("A Docker build context directory has unsafe permissions")
            if relative not in expected_directories:
                raise SourceTreeBlocked("An ignored-by-Git path would enter a Docker build context")
            retained_directories.append(name)
        directories[:] = retained_directories

        for name in files:
            relative = os.path.join(current_rel, name).replace(os.sep.encode(), b"/")
            if ignored(relative, patterns):
                continue
            full_path = os.path.join(current, name)
            try:
                entry = os.lstat(full_path)
            except OSError as exc:
                raise SourceTreeBlocked("A Docker build context changed during inspection") from exc
            if not stat.S_ISREG(entry.st_mode):
                if relative in tree_paths and stat.S_ISLNK(entry.st_mode):
                    raise SourceTreeBlocked("A Docker build input contains a symlink")
                raise SourceTreeBlocked("A Docker build context contains an unsupported filesystem entry")
            if relative not in tree_paths:
                raise SourceTreeBlocked("An ignored-by-Git path would enter a Docker build context")
            seen_files += 1

    for path in tree_paths:
        if path.startswith(prefix + b"/") and not ignored(path[len(prefix) + 1 :], patterns):
            full_path = os.path.join(root_bytes, path)
            try:
                info = os.lstat(full_path)
            except OSError as exc:
                raise SourceTreeBlocked("A tracked Docker build input is missing") from exc
            if not stat.S_ISREG(info.st_mode):
                raise SourceTreeBlocked("A tracked Docker build input is not a regular file")
    return seen_files


def verify_source(root: Path, revision: str) -> tuple[int, int]:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise SourceTreeBlocked("Source revision must be a full lowercase Git SHA")
    actual = git(root, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii", "strict").strip()
    if actual != revision:
        raise SourceTreeBlocked("Checked-out Git HEAD differs from the staging revision")
    object_format = git(root, "rev-parse", "--show-object-format").decode("ascii", "strict").strip()
    if object_format not in ("sha1", "sha256"):
        raise SourceTreeBlocked("Git object format is unsupported")

    tree = parse_tree(root, revision)
    verify_index(root, tree)
    dockerignore_content = verify_worktree(root, tree, object_format)

    tree_paths = set(tree)
    context_file_count = 0
    for prefix, ignore_file in zip(CONTEXTS, DOCKERIGNORE_FILES, strict=True):
        if ignore_file not in dockerignore_content or len(dockerignore_content[ignore_file]) > 64 * 1024:
            raise SourceTreeBlocked("Docker ignore rules are missing or too large")
        context_file_count += verify_context(root, prefix, tree_paths, dockerignore_patterns(dockerignore_content[ignore_file]))
    return len(tree), context_file_count


def _snapshot_entries(
    root: Path,
    tree: dict[bytes, tuple[bytes, bytes]],
) -> dict[bytes, tuple[bytes, bytes]]:
    selected: dict[bytes, tuple[bytes, bytes]] = {}
    for path in SNAPSHOT_SUPPORT_FILES:
        entry = tree.get(path)
        if entry is None:
            raise SourceTreeBlocked("A required staging Compose source file is missing from the selected commit")
        selected[path] = entry

    tls_entries = SNAPSHOT_TLS_FILES.intersection(tree)
    if tls_entries and tls_entries != SNAPSHOT_TLS_FILES:
        raise SourceTreeBlocked("The optional staging TLS source bundle is incomplete")
    for path in tls_entries:
        selected[path] = tree[path]

    for prefix, ignore_file in zip(CONTEXTS, DOCKERIGNORE_FILES, strict=True):
        ignore_entry = tree.get(ignore_file)
        if ignore_entry is None or ignore_entry[0] not in (b"100644", b"100755"):
            raise SourceTreeBlocked("A Docker ignore file is missing from the selected commit")
        ignore_contents = git(root, "cat-file", "blob", ignore_entry[1].decode("ascii"))
        patterns = dockerignore_patterns(ignore_contents)
        for path, entry in tree.items():
            if path.startswith(prefix + b"/") and not ignored(path[len(prefix) + 1 :], patterns):
                selected[path] = entry

    for path, (mode, _) in selected.items():
        if mode not in (b"100644", b"100755"):
            raise SourceTreeBlocked("A staging snapshot input is not a regular Git file")
        if path.startswith(b"/") or any(part in (b"", b".", b"..") for part in path.split(b"/")):
            raise SourceTreeBlocked("A staging snapshot input has an unsafe Git path")
    return selected


def require_tls_bundle(tree: dict[bytes, tuple[bytes, bytes]]) -> dict[bytes, tuple[bytes, bytes]]:
    """Require the complete optional TLS source bundle for TLS-only operations."""
    present = SNAPSHOT_TLS_FILES.intersection(tree)
    if present != SNAPSHOT_TLS_FILES:
        raise SourceTreeBlocked("The selected source commit does not contain the complete TLS bundle")
    return {path: tree[path] for path in SNAPSHOT_TLS_FILES}


@contextmanager
def sha_derived_build_snapshot(root: Path, revision: str) -> Iterator[Path]:
    """Materialize exact committed inputs to a durable per-SHA path used by live bind mounts."""
    verify_source(root, revision)
    tree = parse_tree(root, revision)
    object_format = git(root, "rev-parse", "--show-object-format").decode("ascii", "strict").strip()
    if object_format not in ("sha1", "sha256"):
        raise SourceTreeBlocked("Git object format is unsupported")
    entries = _snapshot_entries(root, tree)

    snapshot_root = SNAPSHOT_ROOT
    _ensure_private_snapshot_root(snapshot_root, os.getuid())
    snapshot = snapshot_root / revision
    if snapshot.exists() or snapshot.is_symlink():
        _verify_persistent_snapshot(snapshot, revision, entries, object_format)
        yield snapshot
        return
    temporary = tempfile.TemporaryDirectory(prefix=".building-", dir=snapshot_root)
    building = Path(temporary.name)
    os.chmod(building, 0o700)
    snapshot = building
    snapshot_bytes = os.fsencode(snapshot)
    try:
        for relative, (mode, oid) in sorted(entries.items()):
            parent = snapshot_bytes
            for component in relative.split(b"/")[:-1]:
                parent = os.path.join(parent, component)
                if not os.path.exists(parent):
                    os.mkdir(parent, 0o755)
                    os.chmod(parent, 0o755)

            content = git(root, "cat-file", "blob", oid.decode("ascii"))
            if hash_blob(content, object_format).decode("ascii") != oid.decode("ascii"):
                raise SourceTreeBlocked("A staging snapshot blob differs from its selected Git object")
            full_path = os.path.join(snapshot_bytes, relative)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(full_path, flags, 0o600)
            except OSError as exc:
                raise SourceTreeBlocked("A staging snapshot file could not be created safely") from exc
            try:
                remaining = memoryview(content)
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0:
                        raise SourceTreeBlocked("A staging snapshot file could not be written completely")
                    remaining = remaining[written:]
            finally:
                os.close(fd)
            os.chmod(full_path, 0o755 if mode == b"100755" else 0o644)

        snapshot_bytes_by_path = os.fsencode(snapshot)
        for relative, (expected_mode, expected_oid) in entries.items():
            path = os.path.join(snapshot_bytes_by_path, relative)
            actual_oid, actual_mode, _ = hash_regular_file(path, object_format)
            if actual_oid != expected_oid or bool(actual_mode & 0o111) != (expected_mode == b"100755"):
                raise SourceTreeBlocked("A staging snapshot differs from its selected Git tree")

        for prefix, ignore_file in zip(CONTEXTS, DOCKERIGNORE_FILES, strict=True):
            content = (snapshot / os.fsdecode(ignore_file)).read_bytes()
            patterns = dockerignore_patterns(content)
            verify_context(snapshot, prefix, set(entries), patterns)

        manifest = {
            "schema": "belib-staging-snapshot/v1",
            "source_sha": revision,
            "files": [
                {"path": os.fsdecode(path), "mode": mode.decode("ascii"), "blob": oid.decode("ascii")}
                for path, (mode, oid) in sorted(entries.items())
            ],
        }
        manifest_path = os.path.join(snapshot_bytes, SNAPSHOT_MANIFEST_FILE)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        manifest_fd = -1
        try:
            manifest_fd = os.open(manifest_path, flags, 0o444)
            content = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
            offset = 0
            while offset < len(content):
                count = os.write(manifest_fd, content[offset:])
                if count <= 0:
                    raise SourceTreeBlocked("The verified staging snapshot manifest could not be written completely")
                offset += count
            os.fchmod(manifest_fd, 0o444)
        except OSError as exc:
            raise SourceTreeBlocked("The verified staging snapshot manifest could not be recorded") from exc
        finally:
            if manifest_fd >= 0:
                os.close(manifest_fd)

        revision_path = os.path.join(snapshot_bytes, SNAPSHOT_REVISION_FILE)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            revision_fd = os.open(revision_path, flags, 0o444)
        except OSError as exc:
            raise SourceTreeBlocked("The verified staging snapshot identity could not be recorded") from exc
        try:
            contents = (revision + "\n").encode("ascii")
            offset = 0
            while offset < len(contents):
                written = os.write(revision_fd, contents[offset:])
                if written <= 0:
                    raise SourceTreeBlocked("The verified staging snapshot identity could not be recorded")
                offset += written
            os.fchmod(revision_fd, 0o444)
        finally:
            os.close(revision_fd)

        target = snapshot_root / revision
        try:
            _rename_directory_noreplace(snapshot, target)
        except OSError as exc:
            raise SourceTreeBlocked("The durable staging snapshot could not be published without replacement") from exc
        temporary.cleanup()
        snapshot = target
        _verify_persistent_snapshot(snapshot, revision, entries, object_format)
        yield snapshot
    finally:
        temporary.cleanup()


def _ensure_private_snapshot_root(path: Path, uid: int) -> None:
    if path != SNAPSHOT_ROOT:
        raise SourceTreeBlocked("The staging snapshot root is not the fixed private path")
    current = Path("/")
    for part in path.parts[1:]:
        current /= part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            try:
                os.mkdir(current, 0o700)
            except OSError as exc:
                raise SourceTreeBlocked("The private staging snapshot directory could not be created") from exc
            info = os.lstat(current)
        mode = stat.S_IMODE(info.st_mode)
        sticky_private_tmp = info.st_uid == 0 and mode == 0o1777
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid not in (0, uid)
            or (mode & 0o022 and not sticky_private_tmp)
        ):
            raise SourceTreeBlocked("The staging snapshot path contains an unsafe component")
    info = os.lstat(path)
    if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
        raise SourceTreeBlocked("The staging snapshot root must be private and owned by the operator")


def _rename_directory_noreplace(source: Path, destination: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "atomic no-replace rename is unavailable")
    renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    renameat2.restype = ctypes.c_int
    result = renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _verify_persistent_snapshot(snapshot: Path, revision: str, entries, object_format: str) -> None:
    info = os.lstat(snapshot)
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise SourceTreeBlocked("The persistent staging snapshot has unsafe ownership or permissions")
    expected = {os.fsdecode(path) for path in entries} | {
        SNAPSHOT_REVISION_FILE.decode("ascii"), SNAPSHOT_MANIFEST_FILE.decode("ascii")
    }
    expected_dirs = {
        "/".join(os.fsdecode(path).split("/")[:index])
        for path in entries
        for index in range(1, len(os.fsdecode(path).split("/")))
    }
    manifest_path = snapshot / SNAPSHOT_MANIFEST_FILE.decode("ascii")
    try:
        manifest = json.loads(_read_snapshot_metadata(manifest_path, maximum=8 * 1024 * 1024).decode("utf-8", "strict"))
    except (OSError, ValueError) as exc:
        raise SourceTreeBlocked("The persistent staging snapshot manifest is invalid") from exc
    expected_manifest = {
        "schema": "belib-staging-snapshot/v1", "source_sha": revision,
        "files": [{"path": os.fsdecode(path), "mode": mode.decode("ascii"), "blob": oid.decode("ascii")} for path, (mode, oid) in sorted(entries.items())],
    }
    if manifest != expected_manifest:
        raise SourceTreeBlocked("The persistent staging snapshot manifest differs from the selected source")
    for marker in (SNAPSHOT_REVISION_FILE, SNAPSHOT_MANIFEST_FILE):
        marker_info = os.lstat(snapshot / marker.decode("ascii"))
        if not stat.S_ISREG(marker_info.st_mode) or stat.S_ISLNK(marker_info.st_mode) or marker_info.st_uid != os.getuid() or stat.S_IMODE(marker_info.st_mode) != 0o444:
            raise SourceTreeBlocked("The persistent staging snapshot metadata has unsafe permissions")
    if _read_snapshot_metadata(snapshot / SNAPSHOT_REVISION_FILE.decode("ascii"), maximum=64) != f"{revision}\n".encode("ascii"):
        raise SourceTreeBlocked("The persistent staging snapshot revision marker changed")
    found = set()
    found_dirs = set()
    for current, dirs, files in os.walk(snapshot, followlinks=False):
        for name in dirs:
            path = Path(current) / name
            if path.is_symlink() or not path.is_dir():
                raise SourceTreeBlocked("The persistent staging snapshot contains an unsafe directory")
            relative_dir = path.relative_to(snapshot).as_posix()
            directory_info = path.stat()
            if relative_dir not in expected_dirs or directory_info.st_uid != os.getuid() or stat.S_IMODE(directory_info.st_mode) != 0o755:
                raise SourceTreeBlocked("The persistent staging snapshot contains an unexpected directory")
            found_dirs.add(relative_dir)
        for name in files:
            path = Path(current) / name
            relative = path.relative_to(snapshot).as_posix()
            if path.is_symlink() or not path.is_file() or relative not in expected:
                raise SourceTreeBlocked("The persistent staging snapshot contains an unexpected path")
            found.add(relative)
    if found != expected or found_dirs != expected_dirs:
        raise SourceTreeBlocked("The persistent staging snapshot is incomplete")
    for path, (mode, oid) in entries.items():
        full = os.path.join(os.fsencode(snapshot), path)
        actual_oid, actual_mode, _ = hash_regular_file(full, object_format)
        metadata = os.lstat(full)
        required_mode = 0o755 if mode == b"100755" else 0o644
        if actual_oid != oid or metadata.st_uid != os.getuid() or stat.S_IMODE(actual_mode) != required_mode:
            raise SourceTreeBlocked("The persistent staging snapshot bytes or modes changed")


def _read_snapshot_metadata(path: Path, *, maximum: int) -> bytes:
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or before.st_uid != os.getuid() or stat.S_IMODE(before.st_mode) != 0o444 or before.st_size > maximum:
            raise SourceTreeBlocked("The persistent staging snapshot metadata is unsafe")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise SourceTreeBlocked("The persistent staging snapshot metadata is unavailable") from exc
    try:
        after = os.fstat(fd)
        if (after.st_dev, after.st_ino, after.st_size) != (before.st_dev, before.st_ino, before.st_size):
            raise SourceTreeBlocked("The persistent staging snapshot metadata changed")
        data = bytearray()
        while len(data) <= maximum:
            chunk = os.read(fd, min(65536, maximum + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) != before.st_size:
            raise SourceTreeBlocked("The persistent staging snapshot metadata changed while read")
        return bytes(data)
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    root = Path(args.repo_root)
    try:
        tracked_count, context_count = verify_source(root, args.source_sha)
    except (SourceTreeBlocked, OSError, UnicodeError, ValueError) as exc:
        reason = str(exc) if isinstance(exc, SourceTreeBlocked) else "Could not verify exact Git source inputs"
        print(f"BLOCKED: {reason}", file=sys.stderr)
        return 1
    print(
        "SOURCE_CONTEXT_PASS "
        f"revision={args.source_sha} tracked_files={tracked_count} effective_context_files={context_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
