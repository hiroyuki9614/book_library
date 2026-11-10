#!/usr/bin/env python3
"""Offline negative and boundary checks for retained-upgrade.py."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MODULE_PATH = Path(__file__).with_name("retained-upgrade.py")
SPEC = importlib.util.spec_from_file_location("retained_upgrade", MODULE_PATH)
upgrade = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(upgrade)


class RetainedUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch.object(upgrade.time, "time", return_value=1100)
        self.mock_time = self.clock.start()
        self.temp = tempfile.TemporaryDirectory(prefix="belib-retained-upgrade-test-")
        self.root = Path(self.temp.name)
        self.backup_path = self.root / "synthetic-backup.dump"
        self.backup_path.write_bytes(b"synthetic backup bytes, not database data")
        self.backup_path.chmod(0o600)
        self.backup_sha = hashlib.sha256(self.backup_path.read_bytes()).hexdigest()
        self.repository_root = self.root / "repository"
        self.repository_root.mkdir()
        self.volume_root = self.root / "retained-volume"
        self.volume_root.mkdir()
        self.db_root = self.root / "retained-db"
        self.db_root.mkdir()
        self.books_root = self.root / "retained-books"
        self.books_root.mkdir()
        self.source_sha = "d" * 40
        self.query_report = b"synthetic integrity query report\n"
        self.network_ids = ["e" * 64, "f" * 64]
        self.stage_pin = {
            "repository": upgrade.EXPECTED_REPOSITORY,
            "pull_request": upgrade.EXPECTED_PULL_REQUEST,
            "branch": upgrade.EXPECTED_BRANCH,
            "host": upgrade.EXPECTED_HOST,
            "principal": upgrade.EXPECTED_PRINCIPAL,
            "uid": upgrade.EXPECTED_UID,
            "project": upgrade.EXPECTED_PROJECT,
        }
        self.network_issuer = "independent-network-inventory-test"
        self.network_private_key = Ed25519PrivateKey.generate()
        self.network_public_pem, self.network_fingerprint = self.pin(self.network_private_key)
        self.network_inventory = self.sign_with(self.network_private_key, {
            "schema": "belib-retained-upgrade-network-inventory/v1",
            "evidence_issuer": self.network_issuer,
            **self.stage_pin,
            "prior_application_sha": upgrade.OLD_APPLICATION_SHA,
            "network_ids": self.network_ids,
            "issued_at": 1000,
            "expires_at": 1200,
        })
        self.root_issuer = "independent-backup-roots-test"
        self.root_private_key = Ed25519PrivateKey.generate()
        self.root_public_pem, self.root_fingerprint = self.pin(self.root_private_key)
        self.root_contract = self.sign_with(self.root_private_key, {
            "schema": "belib-retained-upgrade-backup-roots/v1",
            "evidence_issuer": self.root_issuer,
            **self.stage_pin,
            "repository_root": str(self.repository_root),
            "retained_db_root": str(self.db_root),
            "retained_books_root": str(self.books_root),
            "issued_at": 1000,
            "expires_at": 1200,
        })
        self.issuer = "independent-restore-verifier-test"
        self.private_key = Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key()
        self.public_pem = self.public_key.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        raw_public = self.public_key.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.fingerprint = hashlib.sha256(raw_public).hexdigest()
        self.manifest = {
            "schema": "belib-retained-upgrade-manifest/v2",
            "repository": upgrade.EXPECTED_REPOSITORY,
            "pull_request": upgrade.EXPECTED_PULL_REQUEST,
            "branch": upgrade.EXPECTED_BRANCH,
            "host": upgrade.EXPECTED_HOST,
            "principal": upgrade.EXPECTED_PRINCIPAL,
            "uid": upgrade.EXPECTED_UID,
            "project": upgrade.EXPECTED_PROJECT,
            "source_sha": self.source_sha,
            "prior_application_sha": upgrade.OLD_APPLICATION_SHA,
            "prior_backend_image_digest": upgrade.OLD_BACKEND_IMAGE,
            "prior_frontend_image_digest": upgrade.OLD_FRONTEND_IMAGE,
            "database_lineage": upgrade.OLD_DATABASE_LINEAGE,
            "db_container_id": upgrade.OLD_DB_CONTAINER_ID,
            "db_image_digest": upgrade.OLD_DB_IMAGE,
            "volumes": [
                {"name": name, "identity_sha256": str(i) * 64}
                for i, name in enumerate(upgrade.RETAINED_VOLUMES, start=1)
            ],
            "network_ids": self.network_ids,
            "book_files": [
                {"path": "books/a.epub", "size": 10, "sha256": "1" * 64},
                {"path": "books/b.pdf", "size": 20, "sha256": "2" * 64},
            ],
        }
        self.manifest_sha = self.manifest_digest(self.manifest)
        self.payload = {
            "schema": "belib-retained-upgrade-restore-attestation/v1",
            "evidence_issuer": self.issuer,
            "backup_sha256": self.backup_sha,
            "manifest_sha256": self.manifest_sha,
            "postgresql_major_version": 16,
            "restore_target_identity": {"disposable": True, "identity": "isolated-test-restore-01"},
            "integrity_query_sha256": hashlib.sha256(self.query_report).hexdigest(),
            "restore_success": True,
            "issued_at": 1000,
            "expires_at": 1200,
        }
        self.envelope = self.sign(self.payload)

    def tearDown(self):
        self.temp.cleanup()
        self.clock.stop()

    def sign(self, payload):
        signature = self.private_key.sign(upgrade.canonical_json(payload))
        return {"payload": payload, "signature": base64.b64encode(signature).decode("ascii")}

    @staticmethod
    def sign_with(private_key, payload):
        signature = private_key.sign(upgrade.canonical_json(payload))
        return {"payload": payload, "signature": base64.b64encode(signature).decode("ascii")}

    @staticmethod
    def pin(private_key):
        public_key = private_key.public_key()
        pem = public_key.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return pem, hashlib.sha256(raw).hexdigest()

    def manifest_digest(self, manifest):
        return upgrade.validate_manifest(
            manifest, self.source_sha, trusted_network_inventory=self.network_inventory,
            trusted_network_inventory_issuer=self.network_issuer,
            network_inventory_public_key_pem=self.network_public_pem,
            trusted_network_inventory_fingerprint=self.network_fingerprint)

    def preflight_args(self):
        return {
            "trusted_root_contract": self.root_contract,
            "trusted_root_contract_issuer": self.root_issuer,
            "root_contract_public_key_pem": self.root_public_pem,
            "trusted_root_contract_fingerprint": self.root_fingerprint,
            "trusted_network_inventory": self.network_inventory,
            "trusted_network_inventory_issuer": self.network_issuer,
            "network_inventory_public_key_pem": self.network_public_pem,
            "trusted_network_inventory_fingerprint": self.network_fingerprint,
        }

    def validate_backup(self, backup_path=None, **changes):
        args = {
            "uid": os.getuid(),
            "trusted_root_contract": self.root_contract,
            "trusted_root_contract_issuer": self.root_issuer,
            "root_contract_public_key_pem": self.root_public_pem,
            "trusted_root_contract_fingerprint": self.root_fingerprint,
        }
        args.update(changes)
        return upgrade.validate_backup_artifact(backup_path or self.backup_path, **args)

    def verify(self, **changes):
        args = {
            "backup_sha256": self.backup_sha,
            "manifest_sha256": self.manifest_sha,
            "query_report": self.query_report,
            "trusted_issuer": self.issuer,
            "verifier_public_key_pem": self.public_pem,
            "trusted_verifier_fingerprint": self.fingerprint,
        }
        args.update(changes)
        return upgrade.validate_restore_attestation(self.envelope, **args)

    def test_valid_evidence_stays_source_only(self):
        result = upgrade._preflight_with_test_fixtures(
            operation="upgrade-retained", source_sha=self.source_sha, manifest=self.manifest,
            attestation=self.envelope, backup_path=self.backup_path, uid=os.getuid(),
            **self.preflight_args(),
            query_report=self.query_report, trusted_issuer=self.issuer,
            verifier_public_key_pem=self.public_pem,
            trusted_verifier_fingerprint=self.fingerprint)
        self.assertEqual(result["status"], "NOT_DEPLOYABLE_SOURCE_ONLY")
        self.assertFalse(result["deployable"])
        self.assertEqual(result["manifest_sha256"], self.manifest_sha)
        self.assertEqual(len(result["blockers"]), 3)
        self.assertIn("Independent PostgreSQL restore verification", result["blockers"][0])

    def test_preflight_hashes_actual_backup_and_rejects_insecure_or_in_repo_artifact(self):
        self.backup_path.write_bytes(b"tampered synthetic backup")
        self.backup_path.chmod(0o600)
        with self.assertRaisesRegex(upgrade.UpgradePreflightError, "digest does not match"):
            upgrade._preflight_with_test_fixtures(
                operation="upgrade-retained", source_sha=self.source_sha, manifest=self.manifest,
                attestation=self.envelope, backup_path=self.backup_path, uid=os.getuid(),
                **self.preflight_args(),
                query_report=self.query_report, trusted_issuer=self.issuer,
                verifier_public_key_pem=self.public_pem,
                trusted_verifier_fingerprint=self.fingerprint)
        self.backup_path.write_bytes(b"synthetic backup bytes, not database data")
        self.backup_path.chmod(0o644)
        with self.assertRaisesRegex(upgrade.UpgradePreflightError, "mode-0600"):
            self.validate_backup()
        inside = self.repository_root / "backup.dump"
        inside.write_bytes(b"synthetic")
        inside.chmod(0o600)
        with self.assertRaisesRegex(upgrade.UpgradePreflightError, "outside source"):
            self.validate_backup(inside)

    def test_duplicate_malformed_and_oversize_json_rejected(self):
        for raw in (b'{"x":1,"x":2}', b'{broken', b' ' * (upgrade.MAX_JSON_BYTES + 1)):
            with self.subTest(raw=raw[:16]), self.assertRaises(upgrade.UpgradePreflightError):
                upgrade.strict_json(raw)
        duplicate_manifest = b'{"project":"belib-isolated-i5","project":"attacker"}'
        with self.assertRaises(upgrade.UpgradePreflightError):
            self.manifest_digest(duplicate_manifest)

    def test_manifest_requires_exact_fields_and_expected_source(self):
        for changed in (
            {**self.manifest, "unexpected": True},
            {key: value for key, value in self.manifest.items() if key != "project"},
            {**self.manifest, "project": "other-project"},
            {**self.manifest, "source_sha": "c" * 40},
            {**self.manifest, "prior_backend_image_digest": "sha256:" + "0" * 64},
            {**self.manifest, "prior_frontend_image_digest": "sha256:" + "0" * 64},
            {**self.manifest, "db_image_digest": "postgres:latest"},
        ):
            with self.subTest(changed=changed.keys()), self.assertRaises(upgrade.UpgradePreflightError):
                self.manifest_digest(changed)

    def test_manifest_network_pair_requires_independent_exact_pin(self):
        for changed_ids in (
            ["a" * 64, "b" * 64],
            ["e" * 64, "f" * 64, "0" * 64],
            ["e" * 64, "e" * 64],
        ):
            with self.subTest(network_ids=changed_ids), self.assertRaises(upgrade.UpgradePreflightError):
                self.manifest_digest({**self.manifest, "network_ids": changed_ids})
        for changed_inventory in (
            {**self.network_inventory["payload"], "network_ids": ["a" * 64, "b" * 64]},
            {**self.network_inventory["payload"], "network_ids": ["e" * 64]},
            {**self.network_inventory["payload"], "project": "other-project"},
            {**self.network_inventory["payload"], "prior_application_sha": "0" * 40},
        ):
            pin = self.sign_with(self.network_private_key, changed_inventory)
            with self.subTest(pin=changed_inventory), self.assertRaises(upgrade.UpgradePreflightError):
                upgrade.validate_manifest(
                    self.manifest, self.source_sha, trusted_network_inventory=pin,
                    trusted_network_inventory_issuer=self.network_issuer,
                    network_inventory_public_key_pem=self.network_public_pem,
                    trusted_network_inventory_fingerprint=self.network_fingerprint)
        with self.assertRaisesRegex(upgrade.UpgradePreflightError, "Caller-provided network-inventory fixture pins are missing"):
            upgrade.validate_manifest(
                self.manifest, self.source_sha, trusted_network_inventory=None,
                trusted_network_inventory_issuer=None, network_inventory_public_key_pem=None,
                trusted_network_inventory_fingerprint=None)
        with self.assertRaisesRegex(upgrade.UpgradePreflightError, "supplied fingerprint"):
            upgrade.validate_manifest(
                self.manifest, self.source_sha, trusted_network_inventory=self.network_inventory,
                trusted_network_inventory_issuer=self.network_issuer,
                network_inventory_public_key_pem=self.network_public_pem,
                trusted_network_inventory_fingerprint="0" * 64)

    def test_backup_requires_signed_complete_exhaustive_roots(self):
        for protected_dir in (self.repository_root, self.db_root, self.books_root):
            path = protected_dir / "would-be-backup.dump"
            path.write_bytes(b"synthetic backup bytes")
            path.chmod(0o600)
            with self.subTest(root=protected_dir), self.assertRaisesRegex(
                    upgrade.UpgradePreflightError, "outside source and retained-volume paths"):
                self.validate_backup(path)
        with self.assertRaisesRegex(upgrade.UpgradePreflightError, "Caller-provided backup-root contract fixture pins are missing"):
            self.validate_backup(trusted_root_contract=None, trusted_root_contract_issuer=None)
        incomplete = self.sign_with(self.root_private_key, {
            **self.root_contract["payload"], "retained_books_root": "",
        })
        with self.assertRaisesRegex(upgrade.UpgradePreflightError, "missing or non-absolute"):
            self.validate_backup(trusted_root_contract=incomplete)
        extra_field = self.sign_with(self.root_private_key, {
            **self.root_contract["payload"], "other_root": str(self.root),
        })
        with self.assertRaisesRegex(upgrade.UpgradePreflightError, "fields are missing or unexpected"):
            self.validate_backup(trusted_root_contract=extra_field)
        wrong_stage = self.sign_with(self.root_private_key, {
            **self.root_contract["payload"], "host": "other-host",
        })
        with self.assertRaisesRegex(upgrade.UpgradePreflightError, "exact expected staging identity"):
            self.validate_backup(trusted_root_contract=wrong_stage)
        with self.assertRaisesRegex(upgrade.UpgradePreflightError, "supplied fingerprint"):
            self.validate_backup(trusted_root_contract_fingerprint="0" * 64)
        nested_root = self.repository_root / "retained-books"
        nested_root.mkdir()
        overlapping = self.sign_with(self.root_private_key, {
            **self.root_contract["payload"], "retained_books_root": str(nested_root),
        })
        with self.assertRaisesRegex(upgrade.UpgradePreflightError, "non-overlapping"):
            self.validate_backup(trusted_root_contract=overlapping)

    def test_manifest_rejects_wrong_db_container_and_volume_identity(self):
        changed = {**self.manifest, "db_container_id": "0" * 64}
        with self.assertRaises(upgrade.UpgradePreflightError):
            self.manifest_digest(changed)
        changed = {**self.manifest, "volumes": [
            {"name": upgrade.RETAINED_VOLUMES[0], "identity_sha256": "0" * 64},
            {"name": "unexpected-volume", "identity_sha256": "1" * 64},
        ]}
        with self.assertRaises(upgrade.UpgradePreflightError):
            self.manifest_digest(changed)

    def test_manifest_rejects_traversal_unsorted_and_duplicate_book_paths(self):
        for files in (
            [{"path": "../secret", "size": 1, "sha256": "1" * 64}],
            list(reversed(self.manifest["book_files"])),
            [self.manifest["book_files"][0], self.manifest["book_files"][0]],
            [{"path": "/absolute.epub", "size": 1, "sha256": "1" * 64}],
        ):
            with self.subTest(files=files), self.assertRaises(upgrade.UpgradePreflightError):
                self.manifest_digest({**self.manifest, "book_files": files})

    def test_attestation_rejects_missing_or_mismatched_trust(self):
        for args in (
            {"verifier_public_key_pem": None},
            {"trusted_verifier_fingerprint": None},
            {"trusted_verifier_fingerprint": "0" * 64},
            {"trusted_issuer": "different-issuer"},
        ):
            with self.subTest(args=args), self.assertRaises(upgrade.UpgradePreflightError):
                self.verify(**args)

    def test_attestation_rejects_forged_or_missing_signature(self):
        forged = {**self.envelope, "signature": base64.b64encode(b"x" * 64).decode("ascii")}
        with self.assertRaises(upgrade.UpgradePreflightError):
            upgrade.validate_restore_attestation(
                forged, backup_sha256=self.backup_sha, manifest_sha256=self.manifest_sha,
                query_report=self.query_report, trusted_issuer=self.issuer,
                verifier_public_key_pem=self.public_pem,
                trusted_verifier_fingerprint=self.fingerprint)
        missing = {"payload": self.payload}
        with self.assertRaises(upgrade.UpgradePreflightError):
            upgrade.validate_restore_attestation(
                missing, backup_sha256=self.backup_sha, manifest_sha256=self.manifest_sha,
                query_report=self.query_report, trusted_issuer=self.issuer,
                verifier_public_key_pem=self.public_pem,
                trusted_verifier_fingerprint=self.fingerprint)

    def test_attestation_rejects_expiry_and_input_mismatch(self):
        self.mock_time.return_value = 1300
        with self.assertRaisesRegex(upgrade.UpgradePreflightError, "expired"):
            self.verify()
        self.mock_time.return_value = 1100
        for args in (
            {"backup_sha256": "0" * 64},
            {"manifest_sha256": "0" * 64},
            {"query_report": b"tampered report"},
        ):
            with self.subTest(args=args), self.assertRaises(upgrade.UpgradePreflightError):
                self.verify(**args)

    def test_public_preflight_checks_all_expiries_and_fails_closed_without_external_pins(self):
        network_expired = self.sign_with(self.network_private_key, {
            **self.network_inventory["payload"], "issued_at": 700, "expires_at": 900,
        })
        roots_expired = self.sign_with(self.root_private_key, {
            **self.root_contract["payload"], "issued_at": 700, "expires_at": 900,
        })
        attestation_expired = self.sign({
            **self.payload, "issued_at": 700, "expires_at": 900,
        })
        common = {
            "operation": "upgrade-retained", "source_sha": self.source_sha,
            "manifest": self.manifest, "network_inventory": self.network_inventory,
            "backup_root_contract": self.root_contract, "attestation": self.envelope,
            "backup_path": self.backup_path, "uid": os.getuid(),
            "query_report": self.query_report,
        }
        for key, envelope, label in (
            ("network_inventory", network_expired, "Network inventory"),
            ("backup_root_contract", roots_expired, "Backup root contract"),
            ("attestation", attestation_expired, "Restore attestation"),
        ):
            with self.subTest(evidence=key):
                args = {**common, key: envelope}
                with self.assertRaisesRegex(upgrade.UpgradePreflightError, label + " is expired"):
                    upgrade.preflight(**args)
        with self.assertRaisesRegex(upgrade.UpgradePreflightError, "BLOCKED: external independent pin provision"):
            upgrade.preflight(**common)
        with self.assertRaises(TypeError):
            upgrade.preflight(**common, now=1000)

    def test_attestation_rejects_false_restore_and_extra_fields(self):
        for payload in (
            {**self.payload, "restore_success": False},
            {**self.payload, "unexpected": "field"},
        ):
            envelope = self.sign(payload)
            with self.assertRaises(upgrade.UpgradePreflightError):
                upgrade.validate_restore_attestation(
                    envelope, backup_sha256=self.backup_sha, manifest_sha256=self.manifest_sha,
                    query_report=self.query_report, trusted_issuer=self.issuer,
                    verifier_public_key_pem=self.public_pem,
                    trusted_verifier_fingerprint=self.fingerprint)


if __name__ == "__main__":
    unittest.main(verbosity=2)
