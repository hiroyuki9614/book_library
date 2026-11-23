from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_PATH = REPO_ROOT / "scripts/staging/operator_bootstrap_reference.py"
LAUNCH_GUARD_PATH = REPO_ROOT / "scripts/staging/launch-guard.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Could not load {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reference = load_module(REFERENCE_PATH, "operator_bootstrap_reference_test_subject")


class OperatorBootstrapReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="belib-reference-test-")
        self.root = Path(self.temporary.name)
        self.private_key = self.root / "ephemeral-private.pem"
        self.public_key = self.root / "ephemeral-public.pem"
        subprocess.run(
            ["/usr/bin/openssl", "genpkey", "-algorithm", "ED25519", "-out", str(self.private_key)],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["/usr/bin/openssl", "pkey", "-in", str(self.private_key), "-pubout", "-out", str(self.public_key)],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.public_key_pem = self.public_key.read_bytes()
        self.source_root = self.root / "readonly-snapshot"
        self.source_root.mkdir(mode=0o700)
        self.source_bytes = b"candidate source fixture\n"
        source_file = self.source_root / "candidate.py"
        source_file.write_bytes(self.source_bytes)
        source_file.chmod(0o444)
        self.source_root.chmod(0o555)
        self.manifest = (("candidate.py", hashlib.sha256(self.source_bytes).hexdigest()),)
        self.state_dir = self.root / "protected-state"
        self.state_dir.mkdir(mode=0o700)
        self.state_dir.chmod(0o700)
        # Synthetic offline-clock fixture; no time override enters the verifier API.
        clock_patch = mock.patch.object(reference, "_time_now", return_value=1000)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)

    def tearDown(self) -> None:
        self.source_root.chmod(0o700)
        candidate = self.source_root / "candidate.py"
        if candidate.exists() and not candidate.is_symlink():
            candidate.chmod(0o600)
        self.temporary.cleanup()

    def make_envelope(self, updates: dict[str, object] | None = None, *, expires_at: int = 1300) -> bytes:
        grant: dict[str, object] = {
            "schema": reference.SCHEMA,
            "repository": reference.REPOSITORY,
            "pull_request": reference.PULL_REQUEST,
            "source_sha": "9813b9bc5ec9a5cc313ab8843517666d2611a3df",
            "operation": "deploy",
            "evidence_sha256": "cd" * 32,
            "host": reference.HOST,
            "principal": reference.PRINCIPAL,
            "uid": reference.UID,
            "project": reference.PROJECT,
            "expires_at": expires_at,
            "nonce": "ab" * 16,
        }
        if updates:
            grant.update(updates)
        message = reference.canonical_json(grant)
        message_path = self.root / "ephemeral-grant.json"
        signature_path = self.root / "ephemeral-grant.sig"
        message_path.write_bytes(message)
        subprocess.run(
            [
                "/usr/bin/openssl", "pkeyutl", "-sign", "-inkey", str(self.private_key),
                "-rawin", "-in", str(message_path), "-out", str(signature_path),
            ],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        envelope = {"grant": grant, "signature": base64.b64encode(signature_path.read_bytes()).decode("ascii")}
        return reference.canonical_json(envelope)

    def verify(self, envelope: bytes, **overrides):
        arguments = {
            "expected_source_sha": "9813b9bc5ec9a5cc313ab8843517666d2611a3df",
            "expected_evidence_sha256": "cd" * 32,
            "expected_operation": "deploy",
            "verified_public_key_pem": self.public_key_pem,
            "source_root": self.source_root,
            "expected_manifest": self.manifest,
            "state_dir": self.state_dir,
        }
        arguments.update(overrides)
        return reference.verify_and_consume_grant(envelope, **arguments)

    def test_valid_bound_grant_is_verified_and_consumed(self) -> None:
        grant = self.verify(self.make_envelope())
        self.assertEqual(grant["repository"], "hiroyuki9614/book_library")
        self.assertEqual(grant["pull_request"], 103)
        self.assertTrue((self.state_dir / f"{grant['nonce']}.used").is_file())

    def test_wrong_signature_is_rejected(self) -> None:
        envelope = json.loads(self.make_envelope())
        signature = bytearray(base64.b64decode(envelope["signature"]))
        signature[0] ^= 1
        envelope["signature"] = base64.b64encode(signature).decode("ascii")
        with self.assertRaises(reference.BootstrapReferenceError):
            self.verify(reference.canonical_json(envelope))

    def test_duplicate_and_noncanonical_json_are_rejected(self) -> None:
        with self.assertRaises(reference.BootstrapReferenceError):
            self.verify(b'{"grant":{},"grant":{},"signature":""}')
        valid = self.make_envelope()
        with self.assertRaises(reference.BootstrapReferenceError):
            self.verify(b" " + valid)

    def test_sha_evidence_operation_and_fixed_target_mismatches_are_rejected(self) -> None:
        wrong_source = "a" * 40
        wrong_evidence = "ef" * 32
        cases = (
            ({"source_sha": wrong_source}, {}),
            ({"evidence_sha256": wrong_evidence}, {}),
            ({}, {"expected_evidence_sha256": wrong_evidence}),
            ({"operation": "resume-ingress"}, {}),
            ({"host": "other-host"}, {}),
            ({"uid": 1001}, {}),
            ({"project": "other-project"}, {}),
        )
        for updates, overrides in cases:
            with self.subTest(updates=updates):
                with self.assertRaises(reference.BootstrapReferenceError):
                    self.verify(self.make_envelope(updates), **overrides)

    def test_noncanonical_evidence_digest_is_rejected(self) -> None:
        for digest in ("CD" * 32, "a" * 63, "g" * 64):
            with self.subTest(digest=digest):
                with self.assertRaises(reference.BootstrapReferenceError):
                    self.verify(self.make_envelope({"evidence_sha256": digest}))
        with self.assertRaises(reference.BootstrapReferenceError):
            self.verify(self.make_envelope(), expected_evidence_sha256="CD" * 32)

    def test_caller_cannot_override_the_verifier_clock(self) -> None:
        with self.assertRaises(TypeError):
            self.verify(self.make_envelope(), now=1000)
        self.assertFalse((self.state_dir / f'{"ab" * 16}.used').exists())

    def test_expired_grant_is_rejected(self) -> None:
        with self.assertRaises(reference.BootstrapReferenceError):
            self.verify(self.make_envelope(expires_at=999))

    def test_expiry_during_signature_verification_is_rechecked(self) -> None:
        envelope = self.make_envelope(expires_at=1001)
        nonce_marker = self.state_dir / f'{"ab" * 16}.used'
        with mock.patch.object(reference, "_time_now", side_effect=[1000, 1001]), \
             mock.patch.object(reference, "verify_source_manifest") as verify_manifest:
            with self.assertRaises(reference.BootstrapReferenceError):
                self.verify(envelope)
        verify_manifest.assert_not_called()
        self.assertFalse(nonce_marker.exists())

    def test_expiry_after_manifest_verification_is_rechecked(self) -> None:
        envelope = self.make_envelope(expires_at=1001)
        nonce_marker = self.state_dir / f'{"ab" * 16}.used'
        with mock.patch.object(reference, "_time_now", side_effect=[1000, 1000, 1001]):
            with self.assertRaises(reference.BootstrapReferenceError):
                self.verify(envelope)
        self.assertFalse(nonce_marker.exists())

    def test_expiry_after_nonce_consumption_is_rechecked_before_success(self) -> None:
        envelope = self.make_envelope(expires_at=1001)
        nonce_marker = self.state_dir / f'{"ab" * 16}.used'
        with mock.patch.object(reference, "_time_now", side_effect=[1000, 1000, 1000, 1001]):
            with self.assertRaises(reference.BootstrapReferenceError):
                self.verify(envelope)
        self.assertTrue(nonce_marker.is_file())

    def test_nonce_replay_is_rejected(self) -> None:
        envelope = self.make_envelope()
        self.verify(envelope)
        with self.assertRaises(reference.BootstrapReferenceError):
            self.verify(envelope)

    def test_concurrent_same_nonce_has_exactly_one_success(self) -> None:
        envelope = self.make_envelope()

        def verify_once(_index: int) -> bool:
            try:
                self.verify(envelope)
            except reference.BootstrapReferenceError:
                return False
            return True

        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(verify_once, range(8)))
        self.assertEqual(outcomes.count(True), 1)
        self.assertEqual(outcomes.count(False), 7)

    def test_tampered_source_is_rejected(self) -> None:
        source_file = self.source_root / "candidate.py"
        self.source_root.chmod(0o755)
        source_file.chmod(0o644)
        source_file.write_bytes(b"tampered source\n")
        source_file.chmod(0o444)
        self.source_root.chmod(0o555)
        with self.assertRaises(reference.BootstrapReferenceError):
            self.verify(self.make_envelope())

    def test_symlink_in_source_snapshot_is_rejected(self) -> None:
        source_file = self.source_root / "candidate.py"
        self.source_root.chmod(0o755)
        source_file.unlink()
        target = self.root / "outside-target"
        target.write_bytes(self.source_bytes)
        source_file.symlink_to(target)
        self.source_root.chmod(0o555)
        with self.assertRaises(reference.BootstrapReferenceError):
            self.verify(self.make_envelope())

    def test_hard_link_alias_in_source_snapshot_is_rejected(self) -> None:
        source_file = self.source_root / "candidate.py"
        outside_alias = self.root / "outside-alias"
        self.source_root.chmod(0o755)
        os.link(source_file, outside_alias)
        self.source_root.chmod(0o555)
        with self.assertRaises(reference.BootstrapReferenceError):
            reference.verify_source_manifest(self.source_root, self.manifest)

    def test_writable_source_snapshot_is_rejected(self) -> None:
        (self.source_root / "candidate.py").chmod(0o644)
        with self.assertRaises(reference.BootstrapReferenceError):
            self.verify(self.make_envelope())

    def test_unavailable_replay_state_is_rejected(self) -> None:
        with self.assertRaises(reference.BootstrapReferenceError):
            self.verify(self.make_envelope(), state_dir=self.root / "missing-state")


class LaunchGuardAdmissionOrderTests(unittest.TestCase):
    def test_main_blocks_before_parsing_importing_helpers_or_subprocess(self) -> None:
        launch_guard = load_module(LAUNCH_GUARD_PATH, "launch_guard_admission_order_test")
        parse = mock.patch.object(
            launch_guard.argparse.ArgumentParser,
            "parse_args",
            side_effect=AssertionError("argument parser must remain unreachable"),
        )
        approval_import = mock.patch.object(
            launch_guard, "load_approval_module",
            side_effect=AssertionError("approval helper import must remain unreachable"),
        )
        seed_import = mock.patch.object(
            launch_guard, "load_seed_module",
            side_effect=AssertionError("seed helper import must remain unreachable"),
        )
        module_import = mock.patch.object(
            launch_guard.importlib.util,
            "spec_from_file_location",
            side_effect=AssertionError("candidate module import must remain unreachable"),
        )
        subprocess_calls = [
            mock.patch.object(
                launch_guard.subprocess, name,
                side_effect=AssertionError("subprocess must remain unreachable"),
            )
            for name in ("run", "Popen", "call", "check_call", "check_output")
        ]
        with ExitStack() as stack:
            sentinels = [stack.enter_context(patcher) for patcher in (
                parse, approval_import, seed_import, module_import, *subprocess_calls
            )]
            with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
                try:
                    result = launch_guard.main(["--invalid-before-admission"])
                except launch_guard.LaunchBlocked:
                    result = 1
            self.assertNotEqual(result, 0)
            for patched in sentinels:
                patched.assert_not_called()


if __name__ == "__main__":
    unittest.main()
