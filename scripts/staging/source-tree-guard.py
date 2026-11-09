#!/usr/bin/env python3
"""Fail closed when checked-out staging build inputs differ from one Git commit."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
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
SNAPSHOT_SUPPORT_FILES = (
    b"compose.staging.yaml",
    b"nginx/nginx.conf",
    b"scripts/staging/10-belib-staging-roles.sh",
    b"scripts/staging/ci-isolation-guard.py",
    b"scripts/staging/nginx-default.conf",
    *DOCKERFILE_INPUTS,
)


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


@contextmanager
def sha_derived_build_snapshot(root: Path, revision: str) -> Iterator[Path]:
    """Materialize only committed, effective staging inputs into a private source tree."""
    verify_source(root, revision)
    tree = parse_tree(root, revision)
    object_format = git(root, "rev-parse", "--show-object-format").decode("ascii", "strict").strip()
    if object_format not in ("sha1", "sha256"):
        raise SourceTreeBlocked("Git object format is unsupported")
    entries = _snapshot_entries(root, tree)

    temporary = tempfile.TemporaryDirectory(prefix="belib-staging-source-", dir="/tmp")
    snapshot = Path(temporary.name)
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

        yield snapshot
    finally:
        temporary.cleanup()


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
