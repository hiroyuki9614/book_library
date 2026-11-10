#!/usr/bin/env python3
"""Offline tests for the signed one-time staging approval channel."""

import importlib.util
import json
import os
import stat
import hashlib
import io
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PATH = Path(__file__).with_name("approval.py")
SPEC = importlib.util.spec_from_file_location("staging_approval", PATH)
approval = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(approval)


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.private = self.root / "private.pem"
        self.public = self.root / "public.pem"
        subprocess.run(["/usr/bin/openssl", "genpkey", "-algorithm", "ED25519", "-out", str(self.private)], check=True)
        subprocess.run(["/usr/bin/openssl", "pkey", "-in", str(self.private), "-pubout", "-out", str(self.public)], check=True)
        approval.PUBLIC_KEY_PEM = self.public.read_bytes()
        approval.PUBLIC_KEY_SHA256 = hashlib.sha256(approval.PUBLIC_KEY_PEM).hexdigest()

    def tearDown(self):
        self.temp.cleanup()

    def envelope(self, **changes):
        now = int(time.time())
        nonce = "c" * 64
        payload = approval.expected_payload("a" * 40, "deploy", nonce, now, "d" * 64)
        payload.update(changes)
        raw = approval.canonical_json(payload)
        message = self.root / "message"
        signature = self.root / "signature"
        message.write_bytes(raw)
        subprocess.run(["/usr/bin/openssl", "pkeyutl", "-sign", "-inkey", str(self.private), "-rawin", "-in", str(message), "-out", str(signature)], check=True)
        return {"payload": approval.b64(raw), "signature": approval.b64(signature.read_bytes())}

    def tls_envelope(self, binding, **changes):
        now = int(time.time())
        payload = approval.expected_payload("a" * 40, "tls-ingress", "c" * 64, now, "d" * 64,
                                            tls_binding=binding)
        payload.update(changes)
        raw = approval.canonical_json(payload)
        message = self.root / "tls-message"
        signature = self.root / "tls-signature"
        message.write_bytes(raw)
        subprocess.run(["/usr/bin/openssl", "pkeyutl", "-sign", "-inkey", str(self.private), "-rawin",
                        "-in", str(message), "-out", str(signature)], check=True)
        return {"payload": approval.b64(raw), "signature": approval.b64(signature.read_bytes())}

    def test_valid_external_signature_accepts_exact_tuple(self):
        with patch.object(approval, "_verify_trusted_openssl"):
            self.assertEqual(approval.verify_envelope(self.envelope(), "a" * 40, "deploy", "c" * 64, "d" * 64), "c" * 64)

    def test_embedded_production_key_fingerprint_is_pinned(self):
        self.assertEqual(hashlib.sha256(approval.PUBLIC_KEY_PEM).hexdigest(), approval.PUBLIC_KEY_SHA256)

    def test_forgery_payload_tamper_and_unexpected_fields_reject(self):
        env = self.envelope()
        env["signature"] = approval.b64(b"x" * 64)
        with patch.object(approval, "_verify_trusted_openssl"):
            with self.assertRaisesRegex(approval.ApprovalError, "signature is invalid"):
                approval.verify_envelope(env, "a" * 40, "deploy", "c" * 64, "d" * 64)
        env = self.envelope()
        payload = json.loads(approval.unb64(env["payload"]))
        payload["host"] = "production"
        env["payload"] = approval.b64(approval.canonical_json(payload))
        with self.assertRaises(approval.ApprovalError):
            approval.verify_envelope(env, "a" * 40, "deploy", "c" * 64, "d" * 64)
        payload["unexpected"] = True
        with self.assertRaises(approval.ApprovalError):
                approval.validate_payload(payload, "a" * 40, "deploy", "c" * 64, "d" * 64, int(time.time()))

    def test_wrong_nonce_sha_operation_and_expiry_reject(self):
        cases = [
            {"nonce": "other"}, {"source_sha": "b" * 40}, {"operation": "seed"},
            {"expires_at": int(time.time()) - 1}, {"issued_at": int(time.time()) + 30},
            {"expires_at": int(time.time()) + 900},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(approval.ApprovalError):
                approval.verify_envelope(self.envelope(**changes), "a" * 40, "deploy", "c" * 64, "d" * 64)

    def test_wrong_host_principal_uid_project_and_pr_reject(self):
        for changes in (
            {"host": "other-host"}, {"principal": "other"}, {"uid": 1001},
            {"project": "production"}, {"pull_request": 104},
        ):
            with self.subTest(changes=changes), self.assertRaises(approval.ApprovalError):
                approval.verify_envelope(self.envelope(**changes), "a" * 40, "deploy", "c" * 64, "d" * 64)

    def test_wrong_external_evidence_digest_and_branch_reject(self):
        for changes in ({"evidence_sha256": "e" * 64}, {"branch": "codex/wrong"}):
            with self.subTest(changes=changes), self.assertRaises(approval.ApprovalError):
                approval.verify_envelope(self.envelope(**changes), "a" * 40, "deploy", "c" * 64, "d" * 64)

    def test_resume_approval_is_a_distinct_fixed_schema_and_binds_retained_state(self):
        now = int(time.time())
        prior, retained = "6" * 40, "f" * 64
        payload = approval.expected_payload("a" * 40, "resume-ingress", "c" * 64, now, "d" * 64, prior, retained)
        approval.validate_payload(payload, "a" * 40, "resume-ingress", "c" * 64, "d" * 64,
                                  now, prior, retained)
        for changes, operation, wanted_prior, wanted_digest in (
            ({"operation": "deploy"}, "resume-ingress", prior, retained),
            ({}, "resume-ingress", "7" * 40, retained),
            ({}, "resume-ingress", prior, "e" * 64),
            ({"prior_source_sha": "7" * 40}, "resume-ingress", prior, retained),
            ({"retained_state_sha256": "e" * 64}, "resume-ingress", prior, retained),
        ):
            changed = dict(payload)
            changed.update(changes)
            with self.subTest(changes=changes, operation=operation), self.assertRaises(approval.ApprovalError):
                approval.validate_payload(changed, "a" * 40, operation, "c" * 64, "d" * 64,
                                          now, wanted_prior, wanted_digest)

    def test_tls_grant_is_a_distinct_one_use_operation_bound_to_all_runtime_state(self):
        binding = {
            "prior_application_sha": "8" * 40,
            "retained_state_sha256": "1" * 64,
            "serve_config_sha256": "2" * 64,
            "serve_etag": approval.TLS_SERVE_ETAG,
            "certificate_sha256": "4" * 64,
            "tls_config_sha256": "5" * 64,
            "owner_plan_sha256": "7" * 64,
            "tls_state_sha256": "6" * 64,
        }
        envelope = self.tls_envelope(binding)
        with patch.object(approval, "_verify_trusted_openssl"):
            self.assertEqual(approval.verify_envelope(
                envelope, "a" * 40, "tls-ingress", "c" * 64, "d" * 64, tls_binding=binding), "c" * 64)
        for name, value in (("serve_etag", "6" * 64), ("certificate_sha256", "7" * 64),
                            ("owner_plan_sha256", "8" * 64),
                            ("project", "production")):
            with self.subTest(name=name), self.assertRaises(approval.ApprovalError):
                self.verify_tls_tamper(binding, name, value)
        with self.assertRaises(approval.ApprovalError):
            approval.request_and_wait("a" * 40, "tls-ingress", "d" * 64, uid=os.getuid(),
                                      output=io.StringIO(), timeout=0)

    def verify_tls_tamper(self, binding, name, value):
        envelope = self.tls_envelope(binding, **{name: value})
        with patch.object(approval, "_verify_trusted_openssl"):
            approval.verify_envelope(envelope, "a" * 40, "tls-ingress", "c" * 64, "d" * 64,
                                     tls_binding=binding)

    def test_deploy_token_cannot_be_requested_with_resume_fields(self):
        with self.assertRaisesRegex(approval.ApprovalError, "cannot carry retained-state"):
            approval.request_and_wait("a" * 40, "deploy", "d" * 64, uid=os.getuid(),
                                      output=io.StringIO(), timeout=0,
                                      prior_source_sha="6" * 40, retained_state_sha256="f" * 64)

    def test_challenge_timeout_and_unsafe_response_fail_closed(self):
        state_root = self.root / "state"
        state_root.mkdir(mode=0o700)
        approval.STATE_ROOT = state_root
        test_uid = state_root.stat().st_uid
        real_lstat = os.lstat
        def safe_lstat(path):
            if path == Path("/tmp"):
                return type("Stat", (), {"st_mode": stat.S_IFDIR | 0o1777, "st_uid": 0})()
            return real_lstat(path)
        with patch.object(approval.os, "getuid", return_value=test_uid), patch.object(approval.os, "lstat", side_effect=safe_lstat):
            with self.assertRaisesRegex(approval.ApprovalError, "Timed out"):
                approval.request_and_wait("a" * 40, "deploy", "d" * 64, uid=test_uid, output=io.StringIO(), timeout=0)

        outside = self.root / "outside"
        outside.write_text("not a receipt")
        class SymlinkResponse:
            def write(self, value):
                challenge = json.loads(value)
                Path(challenge["response_path"]).symlink_to(outside)
            def flush(self):
                pass
        with patch.object(approval.os, "getuid", return_value=test_uid), patch.object(approval.os, "lstat", side_effect=safe_lstat):
            with self.assertRaisesRegex(approval.ApprovalError, "unsafe"):
                approval.request_and_wait("a" * 40, "deploy", "d" * 64, uid=test_uid, output=SymlinkResponse(), timeout=1)

    def test_duplicate_json_keys_reject(self):
        with self.assertRaises(approval.ApprovalError):
            approval.strict_json(b'{"payload":"a","payload":"b","signature":"c"}')


if __name__ == "__main__":
    unittest.main()
