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
            "frontend/dist",
            "nginx",
            "scripts/staging",
        ):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        dockerignore = "node_modules\ndist\n.git\n.env\n.env.*\nstorage\ncoverage\n*.log\n"
        (self.root / "backend/.dockerignore").write_text(dockerignore, encoding="ascii")
        (self.root / "frontend/.dockerignore").write_text(dockerignore, encoding="ascii")
        (self.root / "backend/source.ts").write_text("export const staging = true;\n", encoding="ascii")
        (self.root / "frontend/public/source.js").write_text("window.staging = true;\n", encoding="ascii")
        (self.root / "frontend/dist/production.js").write_text("window.productionBuild = true;\n", encoding="ascii")
        (self.root / ".gitignore").write_text("/frontend/test-results/\n", encoding="ascii")
        (self.root / "compose.staging.yaml").write_text("name: staging-fixture\n", encoding="ascii")
        (self.root / "nginx/nginx.conf").write_text("events {}\n", encoding="ascii")
        (self.root / "scripts/staging/10-belib-staging-roles.sh").write_text("#!/bin/sh\n", encoding="ascii")
        (self.root / "scripts/staging/ci-isolation-guard.py").write_text("# test fixture\n", encoding="ascii")
        (self.root / "scripts/staging/nginx-default.conf").write_text("server {}\n", encoding="ascii")
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

    def test_sha_snapshot_survives_checkout_mutation_and_excludes_artifacts(self) -> None:
        with guard.sha_derived_build_snapshot(self.root, self.revision) as snapshot:
            snapshot_source = snapshot / "backend/source.ts"
            self.assertEqual(snapshot_source.read_text(encoding="ascii"), "export const staging = true;\n")
            self.assertEqual(snapshot_source.stat().st_mode & 0o777, 0o644)
            self.assertFalse((snapshot / "frontend/dist/production.js").exists())

            (self.root / "backend/source.ts").write_text("export const staging = false;\n", encoding="ascii")
            playwright_results = self.root / "frontend/test-results/results.json"
            playwright_results.parent.mkdir(parents=True)
            playwright_results.write_text("unreviewed browser output\n", encoding="ascii")
            self.assertEqual(self.git("check-ignore", "-q", "frontend/test-results/results.json", check=False).returncode, 0)

            (self.root / "frontend/dist/production.js").write_text("post-admission asset\n", encoding="ascii")
            self.assertEqual(snapshot_source.read_text(encoding="ascii"), "export const staging = true;\n")
            self.assertFalse((snapshot / "frontend/test-results/results.json").exists())
            self.assertFalse((snapshot / "frontend/dist/production.js").exists())


if __name__ == "__main__":
    unittest.main()
