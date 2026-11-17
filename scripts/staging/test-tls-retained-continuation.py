#!/usr/bin/env python3
"""Opt-in actual disposable preparation -> upgrade -> TLS -> owner -> CD test.

Docker/PG, generated preparation records, Compose, nginx, CA/SNI/HTTP and receipt
validators are real. Host/root metadata, signer key, unique namespaces and the
Tailscale LocalAPI are fixtures. This is not physical staging acceptance.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import ssl
import stat
import subprocess
import time
import traceback
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "scripts/staging"
OPT_IN = "BELIB_DISPOSABLE_TLS_RETAINED_CONTINUATION_TEST"
EXTRA = ("tls-prepare.py", "tls-ingress.py", "owner-command.py", "owner-serve-cas.py",
         "cd-handoff-executor.py", "approval.py", "compose-tls.yaml", "nginx-tls.conf",
         "nginx-tls-default.conf", "nginx-tls-locations.conf", "test-cd-handoff-executor.py",
         "test-cd-handoff.py", "cd-handoff.py")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class RootMetadata:
    """Only Unix root identity is synthetic; real bytes/modes/inodes are read."""
    def __init__(self, actual):
        self.actual = actual

    def __getattr__(self, name):
        return 0 if name == "st_uid" else getattr(self.actual, name)


class LostReply(BaseException):
    pass


@unittest.skipUnless(os.environ.get(OPT_IN) == "1", "explicit disposable Docker/PG/TLS opt-in required")
class RetainedTlsContinuationIntegration(unittest.TestCase):
    def setUp(self):
        self.prep_test = load(STAGE / "test-prepare-retained-integration.py", "tls_real_prepare_fixture")
        entry = self.prep_test.entry
        entry.RUNTIME = tuple(dict.fromkeys((*entry.RUNTIME, *EXTRA)))
        self.fixture = self.prep_test.PrepareFinalizeExecutorIntegration(
            "test_prepare_finalize_execute_replay_and_response_loss")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        # This existing test leaves actual prepared records and the successful
        # immutable root-upgrade fixture receipt for our next operation.
        self.fixture.test_prepare_finalize_execute_replay_and_response_loss()
        f = self.fixture
        receipts = list(f.state.glob("*.result.json"))
        self.assertEqual(len(receipts), 1)
        self.upgrade = json.loads(receipts[0].read_bytes())
        self.upgrade_bytes = receipts[0].read_bytes()
        self.upgrade_nonce = self.upgrade["nonce"]
        self.expected = self.upgrade["expected_runtime"]
        self.assertNotEqual(f.project, "belib-isolated-i5")
        self.assertEqual(self.expected["operation_id"], f.prep_operation)
        self.a = load(f.source / "scripts/staging/tls-prepare.py", "tls_actual_prepare")
        self.a._load_helpers(f.source)
        self.b = load(f.source / "scripts/staging/owner-serve-cas.py", "tls_actual_owner")
        self.cd = load(f.source / "scripts/staging/cd-handoff-executor.py", "tls_actual_cd")
        self.bootstrap = f.bootstrap
        self.certs = f.root / "certificates"
        self.certs.mkdir(mode=0o700)
        self.make_certificate()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.tls_port = listener.getsockname()[1]
        self.effects = []
        self.queries = []
        self.grants = []
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.configure()

    def make_certificate(self):
        def openssl(*args):
            subprocess.run(["/usr/bin/openssl", *map(str, args)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.ca = self.certs / "ca.pem"
        self.cert = self.certs / "fullchain.pem"
        self.key = self.certs / "privkey.pem"
        openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3",
                "-subj", "/CN=Disposable TLS Test CA",
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                "-keyout", self.certs / "ca.key", "-out", self.ca)
        openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=stg-bl.hiroyuki9614.com",
                "-keyout", self.key, "-out", self.certs / "server.csr")
        ext = self.certs / "extensions"
        ext.write_text("subjectAltName=DNS:stg-bl.hiroyuki9614.com\nextendedKeyUsage=serverAuth\n")
        openssl("x509", "-req", "-in", self.certs / "server.csr", "-CA", self.ca,
                "-CAkey", self.certs / "ca.key", "-CAcreateserial", "-days", "3", "-extfile", ext,
                "-out", self.cert)
        self.key.chmod(0o600)
        der = subprocess.check_output(["/usr/bin/openssl", "x509", "-in", str(self.cert), "-outform", "DER"])
        self.cert_sha = sha(der)

    def configure(self):
        f, a, b, cd = self.fixture, self.a, self.b, self.cd
        a.PROJECT = a.TLS.PROJECT = f.project
        a.STATE = f.root
        a.TLS_CONTEXT = f.root / "tls-work-context"
        a.PREPARED_ROOT = f.root
        a.ENV_PATH = f.env
        a.GUARD.ADMITTED_SOURCE_ROOT = f.source
        a.GUARD.PROJECT_LOCK_PATH = f.root / "tls-project.lock"
        original_project_lock = a.GUARD.project_lock
        self.stack.enter_context(patch.object(a.GUARD, "project_lock",
            side_effect=lambda: original_project_lock(path=f.root / "tls-project.lock")))
        self.bootstrap.STATE = f.state
        self.bootstrap.TLS_COMPLETIONS = f.root / "tls-root-completions"
        b.JOURNAL = self.bootstrap.TLS_COMPLETIONS
        b.JOURNAL.mkdir(mode=0o700)
        b.UPGRADE_RESULTS = f.state
        b.UPGRADE_PROJECT = f.project
        b.UPGRADE_RETAINED_DATABASE_ID = f.old_db
        b.CA_FILE = str(self.ca)
        a.TLS.CERT_PATH, a.TLS.KEY_PATH = self.cert, self.key
        a.TLS.EXPECTED_CERT_SHA256 = self.cert_sha
        production_inspector = a.TLS.inspect_certificate
        def fixture_inspector(certificate, private_key):
            return production_inspector(certificate, private_key, expected_fingerprint=self.cert_sha)
        self.stack.enter_context(patch.object(a.TLS, "inspect_certificate", side_effect=fixture_inspector))
        cd.PROJECT = f.project
        cd.TARGET = {**cd.TARGET, "project": f.project}
        cd.RETAINED_DATABASE_ID = f.old_db
        cd.OLD_APPLICATION = f.prep_module.OLD_APP_SHA
        cd.DATABASE_IMAGE = f.pg.IMAGE
        cd.TLS_LOOPBACK_PORT = self.tls_port
        cd.PROJECT_LOCK_PATH = a.GUARD.PROJECT_LOCK_PATH
        self.stack.enter_context(patch.object(a, "_v2_load_executor", return_value=cd))
        # Physical /opt root ownership is a fixture; source SHA and clean Git
        # bytes are still checked, and bind the actual root receipt/OCI labels.
        def fixture_source(source):
            self.assertEqual(source, f.source)
            actual = subprocess.check_output(["/usr/bin/git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
            self.assertEqual(actual, f.sha)
            self.assertEqual(subprocess.check_output(["/usr/bin/git", "-C", str(source), "status", "--porcelain"], text=True), "")
            return actual
        self.stack.enter_context(patch.object(a, "_v2_source_sha", side_effect=fixture_source))
        seed_loader = a.GUARD.load_seed_module
        def fixture_seed():
            module = seed_loader()
            module.COMPOSE_PROJECT = f.project
            return module
        self.stack.enter_context(patch.object(a.GUARD, "load_seed_module", side_effect=fixture_seed))
        # Fake only the external Serve API. CAS and owner transaction remain real.
        self.serve_raw = canonical({"TCP": {"443": {"HTTPS": True}}, "Web": {
            "fedora-worker.tail63cd2d.ts.net:443": {"Handlers": {b.BRI: {"Path": b.BRI_FILE}}}}})
        self.serve_etag = sha(self.serve_raw)
        self.serve_posts = 0
        fixture = self
        class Serve:
            def status(self):
                pass
            def get(self):
                return fixture.serve_raw, fixture.serve_etag
            def set(self, raw, etag, attempt, before_write=None):
                if etag != fixture.serve_etag:
                    raise b.HTTPFailure(412)
                if before_write:
                    before_write()
                attempt.post_attempted = True
                fixture.serve_posts += 1
                fixture.serve_raw = canonical({k: v for k, v in json.loads(raw).items() if v})
                fixture.serve_etag = sha(fixture.serve_raw)
                attempt.post_accepted = True
        a.TLS.EXPECTED_SERVE_ETAG = self.serve_etag
        self.stack.enter_context(patch.object(a.TLS, "TailscaleServeClient", Serve))
        self.stack.enter_context(patch.object(b, "LocalAPI", Serve))
        # Use the same real SSL methods and checks against this fixture's CA and
        # uniquely bound TLS port. No host certificate or fixed live port access.
        actual_context = ssl.create_default_context
        actual_connection = socket.create_connection
        def fixture_context(*args, **kwargs):
            kwargs["cafile"] = str(self.ca)
            return actual_context(*args, **kwargs)
        def fixture_connection(address, *args, **kwargs):
            host, port = address
            if (host, port) in {("100.109.171.70", 443), ("127.0.0.1", 18443)}:
                address = ("127.0.0.1", self.tls_port)
            elif (host, port) == ("127.0.0.1", 18083):
                address = ("127.0.0.1", f.port)
            return actual_connection(address, *args, **kwargs)
        self.stack.enter_context(patch.object(ssl, "create_default_context", side_effect=fixture_context))
        self.stack.enter_context(patch.object(socket, "create_connection", side_effect=fixture_connection))

    @contextlib.contextmanager
    def root_identity(self):
        # This test never invokes sudo. These metadata-only substitutions allow
        # production root-file readers/journal writers to inspect real fixture files.
        original_lstat, original_fstat = os.lstat, os.fstat
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(os, "lstat", side_effect=lambda *a, **k: RootMetadata(original_lstat(*a, **k))))
            stack.enter_context(patch.object(os, "fstat", side_effect=lambda *a, **k: RootMetadata(original_fstat(*a, **k))))
            stack.enter_context(patch.object(self.b, "_check_owner_identity", return_value=None))
            stack.enter_context(patch.object(self.b, "ensure_journal_dir", side_effect=lambda: self.b._check_dir(self.b.JOURNAL, mode=0o700)))
            yield

    def runner(self, command, **kwargs):
        if command[0] == self.cd.SUDO:
            self.queries.append(tuple(command))
            self.assertNotIn("--grant", command)
            with self.root_identity():
                if "--receipt" in command:
                    value = self.bootstrap.read_result(command[-1])
                elif "--tls-receipt" in command:
                    value = self.bootstrap.read_tls_receipt(command[-1])
                else:
                    self.fail("Unknown synthetic root query")
            return subprocess.CompletedProcess(command, 0, canonical(value).decode() + "\n", "")
        if "compose" in command and "up" in command:
            self.effects.append(tuple(command))
        return subprocess.run(command, **kwargs)

    def signed_grant(self, binding):
        f = self.fixture
        approval = load(f.source / "scripts/staging/approval.py", "tls_fixture_signed_approval")
        approval.TARGET = {**approval.TARGET, "project": f.project}
        approval.TLS_SERVE_ETAG = self.a.TLS.EXPECTED_SERVE_ETAG
        approval.PUBLIC_KEY_PEM = f.public.read_bytes()
        approval.PUBLIC_KEY_SHA256 = sha(approval.PUBLIC_KEY_PEM)
        nonce = uuid.uuid4().hex + uuid.uuid4().hex
        payload = approval.expected_payload(f.sha, "tls-ingress", nonce, int(time.time()), self.evidence,
                                            tls_binding=binding)
        message = f.root / "tls-grant-payload"
        signature = f.root / "tls-grant-signature"
        message.write_bytes(approval.canonical_json(payload))
        subprocess.run(["/usr/bin/openssl", "pkeyutl", "-sign", "-inkey", str(f.key), "-rawin",
                        "-in", str(message), "-out", str(signature)], check=True)
        envelope = {"payload": base64.b64encode(message.read_bytes()).decode(),
                    "signature": base64.b64encode(signature.read_bytes()).decode()}
        self.grants.append(nonce)
        return approval.verify_envelope(envelope, f.sha, "tls-ingress", nonce, self.evidence, tls_binding=binding)

    def fingerprint_non_nginx(self):
        ids = set(self.expected["services"].values()) | {self.expected["retained"]["db"],
                *self.expected["retained"]["stopped_application_ids"]}
        ids.remove(self.expected["services"]["nginx"])
        return {i: self.cd._configuration_digest(json.loads(self.fixture.docker("inspect", i))[0]) for i in ids}

    def test_actual_tls_owner_chain_reconciliation_and_drift(self):
        f, a, b, cd = self.fixture, self.a, self.b, self.cd
        self.evidence = "9" * 64
        before = self.fingerprint_non_nginx()
        db_before = f.source_port.fingerprint()
        raw, context = a.v2_create_plan(f.source, self.upgrade_nonce, runner=self.runner)
        plan = json.loads(raw)
        plan_sha = sha(raw)
        # Boundary: a modified private Compose model is rejected before any effect.
        override = f.prepared / "compose.override.json"
        original = override.read_bytes()
        changed = json.loads(original)
        changed["services"]["nginx"] = {"privileged": True}
        override.write_bytes(canonical(changed))
        try:
            with self.assertRaises(a.PrepareError):
                a._v2_validate_prepared_inputs(f.source, f.prepared, self.expected)
        finally:
            override.write_bytes(original)
        self.assertEqual(self.effects, [])
        # Exercise a real failed TLS deployment and its owned HTTP rollback,
        # then select that previous intent through the public recovery planner.
        prior_owner_nonce = plan["nonce"]
        with patch.object(a, "_v2_probe_tls", side_effect=a.PrepareError("injected post-recreate TLS failure")):
            with self.assertRaises(a.PrepareError) as tls_failure:
                a.v2_prepare_live(raw, plan_sha, self.evidence, owner_ready=True, source=f.source,
                                 runner=self.runner, approval_callback=self.signed_grant, tls_port=self.tls_port,
                                 tls_ca_file=self.ca)
        rollback_detail = "\n".join(traceback.format_exception(tls_failure.exception))
        self.assertEqual(len(self.effects), 2,
                         f"failed TLS create and owned HTTP rollback; full exception chain:\n{rollback_detail}")
        self.assertEqual(str(tls_failure.exception), "injected post-recreate TLS failure")
        self.assertEqual(len(self.grants), 1)
        self.assertEqual(self.fingerprint_non_nginx(), before)
        raw, context = a.v2_create_plan(f.source, self.upgrade_nonce, runner=self.runner,
                                      recovery_owner_nonce=prior_owner_nonce)
        plan, plan_sha = json.loads(raw), sha(raw)
        recovery = json.loads((context / "before-manifest.json").read_bytes())["observed_runtime"]
        self.assertEqual(recovery["prior_owner_nonce"], prior_owner_nonce)
        self.assertEqual(recovery["mode"], "http")
        self.assertEqual(len(self.effects), 2, "recovery planning must remain read-only")
        # Model a lost reply/process interruption after actual successful nginx
        # effects and probes, immediately before private receipt publication.
        publish = a._v2_atomic_receipt
        with patch.object(a, "_v2_atomic_receipt", side_effect=LostReply("receipt response lost")):
            with self.assertRaises(LostReply):
                a.v2_prepare_live(raw, plan_sha, self.evidence, owner_ready=True, source=f.source,
                                 runner=self.runner, approval_callback=self.signed_grant, tls_port=self.tls_port,
                                 tls_ca_file=self.ca)
        self.assertEqual(len(self.effects), 3)
        self.assertEqual(len(self.grants), 2)
        with patch.object(a.time, "time", return_value=plan["expires_at"] + 1):
            receipt = a.v2_prepare_live(raw, plan_sha, self.evidence, owner_ready=True, source=f.source,
                        runner=self.runner, approval_callback=lambda _: self.fail("read-only reconciliation requested grant"),
                        tls_port=self.tls_port, tls_ca_file=self.ca)
        self.assertEqual(len(self.effects), 3)
        self.assertEqual(self.fingerprint_non_nginx(), before)
        prepared_raw = (context / "prepare-receipt.json").read_bytes()
        self.assertEqual(json.loads(prepared_raw), receipt)
        with self.root_identity():
            completion = b.run(raw, plan_sha, prepared_raw, sha(prepared_raw))
            self.assertEqual(self.serve_posts, 2)
            repeated = b.run(raw, plan_sha, prepared_raw, sha(prepared_raw))
        self.assertEqual(completion, repeated)
        self.assertEqual(self.serve_posts, 2)
        helpers = load(f.source / "scripts/staging/test-cd-handoff-executor.py", "tls_handoff_fixture")
        handoff = helpers.make_handoff(f.sha)
        handoff["target"]["compose_project"] = f.project
        handoff_path = f.root / "tls-handoff.json"
        handoff_path.write_bytes(canonical(handoff)); handoff_path.chmod(0o600)
        def readback():
            return cd.readback_tls_handoff(handoff_path, self.upgrade_nonce, plan["nonce"],
                runner=self.runner, env_file_path=f.env)
        result = readback()
        self.assertEqual(result["status"], "LOCAL_TLS_DEPLOYMENT_CONFIRMED")
        # Existing grant expiry cannot cause replay or prevent historical readback.
        with patch.object(cd, "decode_grant", side_effect=AssertionError("grant parser on read-only path")):
            self.assertEqual(readback()["status"], "LOCAL_TLS_DEPLOYMENT_CONFIRMED")
        # Actual runtime config tampering (reversible restart policy) must fail CD;
        # restore exact old HostConfig value and prove recovery.
        candidate_db = self.expected["services"]["db"]
        info = json.loads(f.docker("inspect", candidate_db))[0]
        original_host_config = info["HostConfig"]
        original_restart = original_host_config["RestartPolicy"]
        self.assertIn(original_restart["Name"], {"no", "always", "unless-stopped", "on-failure"})
        self.assertNotEqual(original_restart, {"Name": "on-failure", "MaximumRetryCount": 1})
        f.docker("update", "--restart=on-failure:1", candidate_db)
        try:
            with self.assertRaises(cd.ExecutorError):
                readback()
        finally:
            restore_restart = original_restart["Name"]
            if restore_restart == "on-failure":
                restore_restart += ":" + str(original_restart["MaximumRetryCount"])
            f.docker("update", "--restart=" + restore_restart, candidate_db)
        restored_info = json.loads(f.docker("inspect", candidate_db))[0]
        self.assertEqual(restored_info["HostConfig"], original_host_config)
        self.assertEqual(readback()["status"], "LOCAL_TLS_DEPLOYMENT_CONFIRMED")
        self.assertEqual(self.fingerprint_non_nginx(), before)
        self.assertEqual(f.source_port.fingerprint(), db_before)
        self.assertEqual((f.state / (self.upgrade_nonce + ".result.json")).read_bytes(), self.upgrade_bytes)
        self.assertTrue(any("--tls-receipt" in q for q in self.queries))
        self.assertFalse(any("--grant" in q for q in self.queries))
        print("REAL TLS CONTINUITY PASS: prepare journal -> upgrade -> nginx -> owner -> root query -> CD; "
              "lost-reply reconciliation; immutable completion; private-model/config drift rejected")


if __name__ == "__main__":
    unittest.main(verbosity=2)
