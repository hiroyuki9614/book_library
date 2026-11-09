#!/usr/bin/env python3
"""Adversarial offline tests for exact staging Docker build inputs."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


GUARD_PATH = Path(__file__).with_name("source-tree-guard.py")
SPEC = importlib.util.spec_from_file_location("source_tree_guard", GUARD_PATH)
assert SPEC is not None and SPEC.loader is not None
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

GIT_ENV = {
    "HOME": "/nonexistent",
    "PATH": "/usr/bin:/bin",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_OPTIONAL_LOCKS": "0",
    "LC_ALL": "C",
}


class SourceTreeGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="belib-source-guard-test-")
        self.root = Path(self.temporary.name) / "repo"
        for directory in (
            "backend",
            "frontend/public",
            "scripts/staging",
        ):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        (self.root / "backend/.dockerignore").write_text("node_modules\n", encoding="ascii")
        (self.root / "frontend/.dockerignore").write_text("node_modules\n", encoding="ascii")
        (self.root / "backend/source.ts").write_text("export const staging = true;\n", encoding="ascii")
        (self.root / "frontend/public/source.js").write_text("window.staging = true;\n", encoding="ascii")
        (self.root / "scripts/staging/Dockerfile.backend.staging").write_text(
            "FROM node:22-bookworm@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
            encoding="ascii",
        )
        (self.root / "scripts/staging/Dockerfile.frontend.staging").write_text(
            "FROM nginxinc/nginx-unprivileged:1.27-alpine@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n",
            encoding="ascii",
        )
        self.revision = self.commit()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def git(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/usr/bin/git", "-C", str(self.root), *arguments],
            env=GIT_ENV,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=check,
        )

    def commit(self) -> str:
        if not (self.root / ".git").exists():
            self.git("init", "-q")
        self.git("add", "-A")
        self.git(
            "-c",
            "user.name=BeLib staging test",
            "-c",
            "user.email=belib-staging-test@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgSign=false",
            "commit",
            "-qm",
            "fixture",
        )
        return self.git("rev-parse", "HEAD").stdout.strip()

    def test_external_compose_dockerfile_symlink_is_rejected(self) -> None:
        dockerfile = self.root / "scripts/staging/Dockerfile.backend.staging"
        outside = Path(self.temporary.name) / "outside.dockerfile"
        outside.write_text("FROM scratch\n", encoding="ascii")
        dockerfile.unlink()
        dockerfile.symlink_to(outside)
        revision = self.commit()

        with self.assertRaisesRegex(guard.SourceTreeBlocked, "Docker build input contains a symlink"):
            guard.verify_source(self.root, revision)

    def test_noncanonical_build_input_mode_is_rejected(self) -> None:
        dockerfile = self.root / "scripts/staging/Dockerfile.backend.staging"
        dockerfile.chmod(0o666)

        with self.assertRaisesRegex(guard.SourceTreeBlocked, "bytes or mode"):
            guard.verify_source(self.root, self.revision)

    def test_writable_context_directory_is_rejected(self) -> None:
        (self.root / "backend").chmod(0o777)

        with self.assertRaisesRegex(guard.SourceTreeBlocked, "unsafe permissions"):
            guard.verify_source(self.root, self.revision)

    def test_git_ignored_dirty_build_input_is_rejected(self) -> None:
        (self.root / ".gitignore").write_text("/frontend/public/playwright-results.js\n", encoding="ascii")
        self.revision = self.commit()
        injected = self.root / "frontend/public/playwright-results.js"
        injected.write_text("window.unreviewed = true;\n", encoding="ascii")

        with self.assertRaisesRegex(guard.SourceTreeBlocked, "ignored-by-Git path"):
            guard.verify_source(self.root, self.revision)

    def test_assume_unchanged_index_flag_is_rejected(self) -> None:
        self.git("update-index", "--assume-unchanged", "backend/source.ts")

        with self.assertRaisesRegex(guard.SourceTreeBlocked, "assume-unchanged or skip-worktree"):
            guard.verify_source(self.root, self.revision)


if __name__ == "__main__":
    unittest.main()
