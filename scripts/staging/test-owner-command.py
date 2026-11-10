#!/usr/bin/python3
"""Offline tests for readonly digest-pinned owner command generation."""

import hashlib
import importlib.util
import json
import os
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("owner_command", Path(__file__).with_name("owner-command.py"))
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class OwnerCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="owner-package-test-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.source_sha = "a" * 40
        self.helper = self.home / ".local/state/belib-staging/snapshots" / self.source_sha / "scripts/staging/owner-serve-cas.py"
        self.helper.parent.mkdir(parents=True)
        self.helper.write_bytes(b"# pinned helper bytes\n")
        self.helper.chmod(0o444)
        self.plan_path = self.home / ".local/state/belib-staging/tls-work-context/owner-plan.json"
        self.plan_path.parent.mkdir(parents=True)
        self.plan = {"schema": "belib-tls-owner-plan/v1", "source_sha": self.source_sha,
                     "prior_application_sha": "81256268276d7ead95029849037f69ec6beec202",
                     "retained_state_sha256": "b" * 64, "serve_config_sha256": "c" * 64,
                     "serve_etag": "d" * 64, "certificate_sha256": "e" * 64,
                     "tls_config_sha256": "f" * 64, "nonce": "1" * 64,
                     "expires_at": 1900000000}
        self.plan_path.write_bytes(json.dumps(self.plan, sort_keys=True, separators=(",", ":")).encode())
        self.plan_path.chmod(0o600)
        self.helper_sha = hashlib.sha256(self.helper.read_bytes()).hexdigest()
        self.plan_sha = hashlib.sha256(self.plan_path.read_bytes()).hexdigest()
        self.patches = [patch.object(packager, "HOME", self.home), patch.object(packager, "PLAN_PATH", self.plan_path)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def command(self):
        return packager.make_loader_command(self.source_sha, self.helper_sha, self.plan_sha)

    def test_generates_one_reviewable_clean_environment_loader_command(self):
        command = self.command()
        argv = shlex.split(command)
        self.assertEqual(argv[:4], ["/usr/bin/sudo", "--", "/usr/bin/env", "-i"])
        self.assertIn("/usr/bin/python3", argv)
        self.assertIn("-I", argv)
        self.assertIn("-S", argv)
        self.assertIn("-B", argv)
        loader = argv[argv.index("-c") + 1]
        compile(loader, "<digest-pinned-owner-loader>", "exec")
        self.assertIn("os.chdir('/')", loader)
        self.assertIn("hashlib.sha256(data).hexdigest()!=sha", loader)
        self.assertIn("exec(compile(helper,HP,'exec'),ns,ns)", loader)
        self.assertIn("ns['run'](plan,PH)", loader)
        self.assertIn("print(json.dumps(ns['run'](plan,PH)", loader)
        self.assertIn(self.helper_sha, loader)
        self.assertIn(self.plan_sha, loader)
        self.assertNotIn("docker", command.lower())
        self.assertEqual(command.count("/usr/bin/python3"), 1)

    def test_caller_digest_mismatch_symlink_and_bad_mode_fail_closed(self):
        with self.assertRaises(packager.PackageError):
            packager.make_loader_command(self.source_sha, "0" * 64, self.plan_sha)
        target = self.helper.with_suffix(".real")
        self.helper.rename(target)
        self.helper.symlink_to(target)
        with self.assertRaises(packager.PackageError):
            packager.make_loader_command(self.source_sha, self.helper_sha, self.plan_sha)
        self.helper.unlink()
        target.rename(self.helper)
        self.helper.chmod(0o600)
        with self.assertRaises(packager.PackageError):
            packager.make_loader_command(self.source_sha, self.helper_sha, self.plan_sha)

    def test_plan_must_be_exact_canonical_contract_for_selected_source(self):
        changed = dict(self.plan)
        changed["ready_receipt_sha256"] = "9" * 64
        self.plan_path.write_text(json.dumps(changed, sort_keys=True, separators=(",", ":")))
        self.plan_path.chmod(0o600)
        digest = hashlib.sha256(self.plan_path.read_bytes()).hexdigest()
        with self.assertRaises(packager.PackageError):
            packager.make_loader_command(self.source_sha, self.helper_sha, digest)
        self.plan["source_sha"] = "9" * 40
        raw = json.dumps(self.plan, sort_keys=True, separators=(",", ":")).encode()
        self.plan_path.write_bytes(raw)
        self.plan_path.chmod(0o600)
        with self.assertRaises(packager.PackageError):
            packager.make_loader_command(self.source_sha, self.helper_sha, hashlib.sha256(raw).hexdigest())

    def test_paths_and_hashes_are_narrowly_validated(self):
        with self.assertRaises(packager.PackageError):
            packager.make_loader_command("../" + "a" * 37, self.helper_sha, self.plan_sha)
        with self.assertRaises(packager.PackageError):
            packager.make_loader_command(self.source_sha, "Z" * 64, self.plan_sha)


if __name__ == "__main__":
    unittest.main()
