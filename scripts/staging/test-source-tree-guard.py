#!/usr/bin/env python3
"""Adversarial offline tests for exact staging Docker build inputs."""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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
        guard.SNAPSHOT_ROOT = Path(self.temporary.name) / "snapshots"
        def validate_test_snapshot_root(path: Path, uid: int) -> None:
            if path != guard.SNAPSHOT_ROOT:
                raise guard.SourceTreeBlocked("Unexpected test snapshot path")
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = os.lstat(path)
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
                raise guard.SourceTreeBlocked("Test snapshot root is not private")
        self.snapshot_path_patch = patch.object(guard, "_ensure_private_snapshot_root", side_effect=validate_test_snapshot_root)
        self.snapshot_path_patch.start()
        self.addCleanup(self.snapshot_path_patch.stop)
        self.root = Path(self.temporary.name) / "repo"
        for directory in (
            "backend",
            "frontend/public",
            "frontend/dist",
            "nginx",
            "scripts/staging",
            ".github/workflows",
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
        (self.root / ".github/workflows/staging-ci.yml").write_bytes(
            (GUARD_PATH.parents[2] / ".github/workflows/staging-ci.yml").read_bytes()
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

    def add_tls_bundle(self, paths=None) -> str:
        selected = guard.SNAPSHOT_TLS_FILES if paths is None else paths
        for index, relative in enumerate(sorted(selected)):
            target = self.root / relative.decode("ascii")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"tls fixture {index}\n", encoding="ascii")
        self.revision = self.commit()
        return self.revision

    def test_historical_tree_without_tls_keeps_base_snapshot_requirements(self) -> None:
        tree = guard.parse_tree(self.root, self.revision)
        selected = guard._snapshot_entries(self.root, tree)
        self.assertFalse(guard.SNAPSHOT_TLS_FILES.intersection(selected))
        with self.assertRaisesRegex(guard.SourceTreeBlocked, "complete TLS bundle"):
            guard.require_tls_bundle(tree)

    def test_partial_tls_tree_is_rejected(self) -> None:
        one_tls_file = {sorted(guard.SNAPSHOT_TLS_FILES)[0]}
        self.add_tls_bundle(one_tls_file)
        tree = guard.parse_tree(self.root, self.revision)
        with self.assertRaisesRegex(guard.SourceTreeBlocked, "bundle is incomplete"):
            guard._snapshot_entries(self.root, tree)

    def test_full_tls_tree_is_snapshotted_and_available_to_tls_operation(self) -> None:
        self.add_tls_bundle()
        tree = guard.parse_tree(self.root, self.revision)
        selected = guard._snapshot_entries(self.root, tree)
        required = guard.require_tls_bundle(tree)
        self.assertEqual(set(required), guard.SNAPSHOT_TLS_FILES)
        self.assertTrue(guard.SNAPSHOT_TLS_FILES.issubset(selected))

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
        snapshot_uid = Path(guard.SNAPSHOT_ROOT.parent).stat().st_uid
        real_lstat = os.lstat
        def safe_lstat(path, *args, **kwargs):
            if path == Path("/tmp"):
                return type("Stat", (), {"st_mode": stat.S_IFDIR | 0o1777, "st_uid": 0})()
            return real_lstat(path, *args, **kwargs)
        with patch.object(guard.os, "getuid", return_value=snapshot_uid), patch.object(guard.os, "lstat", side_effect=safe_lstat), guard.sha_derived_build_snapshot(self.root, self.revision) as snapshot:
            snapshot_source = snapshot / "backend/source.ts"
            self.assertEqual(snapshot_source.read_text(encoding="ascii"), "export const staging = true;\n")
            self.assertEqual(snapshot_source.stat().st_mode & 0o777, 0o644)
            revision_marker = snapshot / guard.SNAPSHOT_REVISION_FILE.decode("ascii")
            self.assertEqual(revision_marker.read_text(encoding="ascii"), f"{self.revision}\n")
            self.assertEqual(revision_marker.stat().st_mode & 0o777, 0o444)
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


    def test_snapshot_preserves_workflow_required_by_live_compose_guard(self) -> None:
        ci_spec = importlib.util.spec_from_file_location("snapshot_ci", GUARD_PATH.with_name("ci-isolation-guard.py"))
        ci = importlib.util.module_from_spec(ci_spec)
        ci_spec.loader.exec_module(ci)
        with guard.sha_derived_build_snapshot(self.root, self.revision) as snapshot:
            workflow = snapshot / ".github/workflows/staging-ci.yml"
            self.assertEqual(workflow.read_bytes(), (self.root / ".github/workflows/staging-ci.yml").read_bytes())
            ci._validate_staging_ci_image(snapshot)

    def test_persistent_snapshot_reuse_and_corruption_rejection(self) -> None:
        snapshot_uid = Path(guard.SNAPSHOT_ROOT.parent).stat().st_uid
        real_lstat = os.lstat
        def safe_lstat(path, *args, **kwargs):
            if path == Path("/tmp"):
                return type("Stat", (), {"st_mode": stat.S_IFDIR | 0o1777, "st_uid": 0})()
            return real_lstat(path, *args, **kwargs)
        with patch.object(guard.os, "getuid", return_value=snapshot_uid), patch.object(guard.os, "lstat", side_effect=safe_lstat), guard.sha_derived_build_snapshot(self.root, self.revision) as snapshot:
            self.assertEqual(snapshot, guard.SNAPSHOT_ROOT / self.revision)
        with patch.object(guard.os, "getuid", return_value=snapshot_uid), patch.object(guard.os, "lstat", side_effect=safe_lstat), guard.sha_derived_build_snapshot(self.root, self.revision) as reused:
            self.assertEqual(reused, snapshot)
        (snapshot / "backend/source.ts").write_text("tampered\n", encoding="ascii")
        with self.assertRaisesRegex(guard.SourceTreeBlocked, "bytes or modes changed"):
            with patch.object(guard.os, "getuid", return_value=snapshot_uid), patch.object(guard.os, "lstat", side_effect=safe_lstat), guard.sha_derived_build_snapshot(self.root, self.revision):
                pass


if __name__ == "__main__":
    unittest.main()
