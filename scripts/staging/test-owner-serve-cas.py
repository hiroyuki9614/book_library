#!/usr/bin/python3
"""Offline end-to-end tests for the digest-pinned standalone Serve transaction."""

import contextlib
import hashlib
import importlib.util
import json
import os
import signal
import stat
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("owner_serve_cas", Path(__file__).with_name("owner-serve-cas.py"))
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)
RealLocalAPI = owner.LocalAPI
RealTlsRequest = owner.tls_request
RealOperationLock = owner.operation_lock
RealOwnerIdentity = owner._check_owner_identity


def baseline():
    return json.dumps({"TCP": {"443": {"HTTPS": True}}, "Web": {
        "fedora-worker.tail63cd2d.ts.net:443": {"Handlers": {
            owner.BRI: {"Path": owner.BRI_FILE}}}},}, separators=(",", ":")).encode()


def new_plan(now=None):
    now = int(owner.time.time()) if now is None else now
    original = baseline()
    return {"schema": owner.PLAN_SCHEMA, "disposition": owner.PLAN_DISPOSITION, "source_sha": "a" * 40,
            "prior_application_sha": owner.PRIOR_APP_SHA,
            "retained_state_sha256": "b" * 64, "serve_config_sha256": owner.digest(original),
            "serve_etag": owner.digest(original), "certificate_sha256": "c" * 64,
            "tls_config_sha256": "d" * 64, "nonce": "e" * 64, "expires_at": now + 1200}


class FakeClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class FakeAPI:
    def __init__(self, raw=None):
        self.raw = raw or baseline()
        self.etag = owner.digest(self.raw)
        self.posts = 0
        self.deny_at = {}
        self.ambiguous_after = set()
        self.foreign_on_deny = set()
        self.read_failures = 0
        self.get_count = 0
        self.before_post_failure = None
        self.events = []

    def status(self):
        return None

    def get(self):
        self.get_count += 1
        if self.before_post_failure and self.get_count == 2:
            raw, error = self.before_post_failure
            self.raw = raw
            self.etag = hashlib.sha256(raw + b"foreign").hexdigest()
            raise error
        if self.read_failures:
            self.read_failures -= 1
            raise owner.TransportFailure("test read failure")
        return self.raw, self.etag

    def set(self, raw, etag, attempt, before_write=None):
        if before_write is not None:
            before_write()
        attempt.post_attempted = True
        self.posts += 1
        self.events.append(("POST", self.posts, raw))
        if etag != self.etag:
            raise owner.HTTPFailure(412)
        if self.posts in self.deny_at:
            status = self.deny_at[self.posts]
            if self.posts in self.foreign_on_deny:
                # Simulate a different writer producing the exact desired payload.
                self.raw = raw
                self.etag = hashlib.sha256(raw + b"foreign").hexdigest()
            raise owner.HTTPFailure(status)
        self.raw = raw
        self.etag = hashlib.sha256(raw + str(self.posts).encode()).hexdigest()
        attempt.post_accepted = True
        if self.posts in self.ambiguous_after:
            raise owner.TransportFailure("simulated lost response after apply")


class OwnerServeTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.used_nonces = set()
        self.probes = []
        self.counter = 0

        def journal_start(plan, original, etag, plan_sha):
            if plan["nonce"] in self.used_nonces:
                raise owner.Stop("nonce replay")
            self.used_nonces.add(plan["nonce"])
            return (os.open(os.devnull, os.O_RDONLY), Path("/offline/journal"))

        def event(journal, event, **fields):
            self.api.events.append(("journal", event, fields))

        def tls(port, hostname, path, **kwargs):
            self.probes.append(("tls", port, hostname, path, kwargs))

        def unknown(port, connect_host="127.0.0.1"):
            self.probes.append(("unknown", port, connect_host))

        def no_sni(port, connect_host="127.0.0.1"):
            self.probes.append(("no_sni", port, connect_host))

        def legacy_sni(port, connect_host="127.0.0.1"):
            self.probes.append(("legacy_sni", port, connect_host))

        def self_sni(port, connect_host="127.0.0.1"):
            self.probes.append(("self_sni", port, connect_host))

        patches = [
            patch.object(owner, "_check_owner_identity"),
            patch.object(owner, "operation_lock", contextlib.nullcontext),
            patch.object(owner, "LocalAPI", return_value=self.api),
            patch.object(owner, "journal_start", side_effect=journal_start),
            patch.object(owner, "journal_event", side_effect=event),
            patch.object(owner, "http_18083", side_effect=lambda: self.probes.append(("http", 18083))),
            patch.object(owner, "tls_request", side_effect=tls),
            patch.object(owner, "tls_unknown_rejected", side_effect=unknown),
            patch.object(owner, "tls_no_sni_rejected", side_effect=no_sni),
            patch.object(owner, "tls_legacy_sni_rejected", side_effect=legacy_sni),
            patch.object(owner, "tls_self_sni_rejected", side_effect=self_sni),
        ]
        for item in patches:
            started = item.start()
            self.addCleanup(item.stop)
            if item is patches[4]:
                self.journal_mock = started

    def execute(self, plan=None):
        if plan is None:
            self.counter += 1
            plan = new_plan()
            plan["nonce"] = f"{self.counter:064x}"
        owner.LocalAPI.return_value = self.api
        self.api.events = self.probes
        raw = owner.canonical(plan)
        return owner.run(raw, owner.digest(raw))

    def test_retirement_replaces_exact_legacy_handler_with_custom_tls_and_rejects_other_sni(self):
        result = self.execute()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(owner.config(self.api.raw),
                         {"TCP": {"443": {"TCPForward": "127.0.0.1:18443"}}, "Web": {}})
        self.assertEqual(self.api.posts, 2)
        events = self.api.events
        intent = next(i for i, item in enumerate(events) if item[0] == "journal" and item[1] == "retirement-intent")
        post = next(i for i, item in enumerate(events) if item[0] == "POST")
        self.assertLess(intent, post)
        phases = [item[2]["phase"] for item in events if item[0] == "journal" and item[1] == "retirement-intent"]
        self.assertEqual(phases, ["remove-old-https", "add-custom-tls"])
        self.assertIn(("legacy_sni", 18443, "127.0.0.1"), self.probes)
        self.assertIn(("self_sni", 18443, "127.0.0.1"), self.probes)
        self.assertIn(("unknown", 18443, "127.0.0.1"), self.probes)
        self.assertIn(("no_sni", 18443, "127.0.0.1"), self.probes)
        self.assertIn(("legacy_sni", 443, owner.SELF_IP), self.probes)
        self.assertIn(("self_sni", 443, owner.SELF_IP), self.probes)
        self.assertIn(("unknown", 443, owner.SELF_IP), self.probes)
        self.assertIn(("no_sni", 443, owner.SELF_IP), self.probes)
        positives = [i for i, p in enumerate(self.probes) if p[0] == "tls" and p[1] == 443 and p[2] == owner.CUSTOM]
        negative = next(i for i, p in enumerate(self.probes) if p[0] == "legacy_sni" and p[1] == 443)
        self.assertLess(positives[0], negative)
        self.assertGreater(positives[-1], negative)

    def test_legacy_sni_acceptance_on_candidate_ingress_blocks_before_nonce_and_write(self):
        self.api = FakeAPI()
        self.used_nonces.clear()
        def accepted_legacy_sni(port, connect_host="127.0.0.1"):
            if port == 18443:
                raise owner.Stop("legacy SNI unexpectedly completed TLS")
        with patch.object(owner, "tls_legacy_sni_rejected", side_effect=accepted_legacy_sni):
            with self.assertRaisesRegex(owner.Stop, "unexpectedly completed"):
                self.execute()
        self.assertEqual(self.api.posts, 0)
        self.assertEqual(self.used_nonces, set())
        self.assertEqual(owner.canonical(owner.config(self.api.raw)), owner.canonical(owner.config(baseline())))

    def test_post_retirement_probe_failure_rolls_back_owned_config_and_burns_nonce(self):
        plan = new_plan()
        def reject_post_retirement(port, connect_host="127.0.0.1"):
            if port == 443:
                raise owner.Stop("legacy SNI handshake accepted after retirement")
            self.probes.append(("legacy_sni", port, connect_host))
        with patch.object(owner, "tls_legacy_sni_rejected", side_effect=reject_post_retirement):
            with self.assertRaisesRegex(owner.Stop, "accepted after retirement"):
                self.execute(plan)
        self.assertEqual(self.api.posts, 4)  # two forward steps, then remove owned forward and restore baseline
        self.assertEqual(self.used_nonces, {plan["nonce"]})
        self.assertEqual(owner.canonical(owner.config(self.api.raw)), owner.canonical(owner.config(baseline())))

    def test_plan_expiry_during_journal_blocks_write_and_after_commit_rolls_back(self):
        plan = new_plan(now=1000)
        clock = FakeClock(1000)
        mark_nonce = owner.journal_start.side_effect
        def expire_during_journal(*args):
            result = mark_nonce(*args)
            clock.now = plan["expires_at"] + 1
            return result
        with patch.object(owner.time, "time", new=clock), \
             patch.object(owner, "journal_start", side_effect=expire_during_journal), \
             patch.object(owner, "tls_request", return_value=None):
            with self.assertRaisesRegex(owner.Stop, "expired"):
                self.execute(plan)
        self.assertEqual(self.api.posts, 0)
        self.assertEqual(self.used_nonces, {plan["nonce"]})
        self.assertEqual(owner.canonical(owner.config(self.api.raw)), owner.canonical(owner.config(baseline())))

        self.api = FakeAPI()
        self.used_nonces.clear()
        plan = new_plan(now=1000)
        plan["nonce"] = "f" * 64
        clock.now = 1000
        original_event = self.journal_mock.side_effect
        def expire_after_retirement(journal, event, **fields):
            original_event(journal, event, **fields)
            if event == "retirement-committed" and fields.get("phase") == "add-custom-tls":
                clock.now = plan["expires_at"] + 1
        with patch.object(owner.time, "time", new=clock), patch.object(owner, "tls_request", return_value=None):
            self.journal_mock.side_effect = expire_after_retirement
            try:
                with self.assertRaisesRegex(owner.Stop, "expired"):
                    self.execute(plan)
            finally:
                self.journal_mock.side_effect = original_event
        self.assertEqual(self.api.posts, 4)
        self.assertEqual(owner.canonical(owner.config(self.api.raw)), owner.canonical(owner.config(baseline())))

    def test_expiry_after_cas_get_is_checked_at_actual_post_boundary(self):
        plan = new_plan(now=1000)
        clock = FakeClock(1000)
        original_get = self.api.get
        def expire_after_get():
            result = original_get()
            if self.api.get_count == 2:
                clock.now = plan["expires_at"] + 1
            return result
        with patch.object(owner.time, "time", new=clock), \
             patch.object(self.api, "get", side_effect=expire_after_get), \
             patch.object(owner, "tls_request", return_value=None):
            with self.assertRaisesRegex(owner.Stop, "expired"):
                self.execute(plan)
        self.assertEqual(self.api.posts, 0)
        self.assertEqual(self.used_nonces, {plan["nonce"]})
        self.assertEqual(owner.canonical(owner.config(self.api.raw)), owner.canonical(owner.config(baseline())))

        self.api = FakeAPI()
        self.used_nonces.clear()
        plan = new_plan(now=1000)
        plan["nonce"] = "f" * 64
        clock.now = 1000
        original_get = self.api.get
        def expire_before_second_post():
            result = original_get()
            if self.api.get_count == 4:  # second CAS GET, after the clearing transition committed
                clock.now = plan["expires_at"] + 1
            return result
        with patch.object(owner.time, "time", new=clock), \
             patch.object(self.api, "get", side_effect=expire_before_second_post), \
             patch.object(owner, "tls_request", return_value=None):
            with self.assertRaisesRegex(owner.Stop, "expired"):
                self.execute(plan)
        self.assertEqual(self.api.posts, 2)  # clear, then exact rollback; expired forward was never posted
        self.assertEqual(owner.canonical(owner.config(self.api.raw)), owner.canonical(owner.config(baseline())))

    def test_plan_digest_canonicality_expiry_and_wrong_host_fail_before_api(self):
        raw = owner.canonical(new_plan())
        with self.assertRaises(owner.Stop):
            owner.run(raw, "0" * 64)
        malformed = json.dumps({**new_plan(), "unexpected": True}, separators=(",", ":")).encode()
        with self.assertRaises(owner.Stop):
            owner.run(malformed, owner.digest(malformed))
        old_preserve_packet = dict(new_plan(), schema="belib-tls-owner-plan/v1")
        old_raw = owner.canonical(old_preserve_packet)
        with self.assertRaises(owner.Stop):
            owner.run(old_raw, owner.digest(old_raw))
        wrong_disposition = dict(new_plan(), disposition="preserve-legacy-handler")
        wrong_raw = owner.canonical(wrong_disposition)
        with self.assertRaises(owner.Stop):
            owner.run(wrong_raw, owner.digest(wrong_raw))
        expired = new_plan(now=100)
        expired["expires_at"] = 101
        with patch.object(owner.time, "time", return_value=10000):
            with self.assertRaises(owner.Stop):
                owner.run(owner.canonical(expired), owner.digest(owner.canonical(expired)))
        far_future = new_plan()
        far_future["expires_at"] = int(owner.time.time()) + 1801
        with self.assertRaises(owner.Stop):
            owner.run(owner.canonical(far_future), owner.digest(owner.canonical(far_future)))
        with patch.object(owner, "_check_owner_identity", side_effect=owner.Stop("wrong host")):
            with self.assertRaises(owner.Stop):
                owner.run(raw, owner.digest(raw))
        self.assertEqual(self.api.posts, 0)

    def test_plan_expiring_during_readiness_never_consumes_nonce_or_posts(self):
        plan = new_plan(now=1000)
        with patch.object(owner.time, "time", side_effect=[1000, 2201]):
            with self.assertRaisesRegex(owner.Stop, "expired"):
                self.execute(plan)
        self.assertEqual(self.api.posts, 0)
        self.assertEqual(self.used_nonces, set())

    def test_owner_identity_rejects_wrong_host_and_nonroot(self):
        with patch.object(owner.os, "geteuid", return_value=0), patch.object(owner.os, "getuid", return_value=0), \
             patch.object(owner.socket, "gethostname", return_value="other-host"):
            with self.assertRaises(owner.Stop):
                RealOwnerIdentity()
        with patch.object(owner.os, "geteuid", return_value=1000), patch.object(owner.os, "getuid", return_value=1000), \
             patch.object(owner.socket, "gethostname", return_value="ubuntu-worker"):
            with self.assertRaises(owner.Stop):
                RealOwnerIdentity()

    def test_exact_baseline_rejects_extra_scopes_funnel_and_sibling_handlers(self):
        config = owner.config(baseline())
        variants = []
        variants.append({**config, "AllowFunnel": {"443": True}})
        variants.append({**config, "Web": {**config["Web"], "other:443": {"Handlers": {}}}})
        variants.append({**config, "TCP": {**config["TCP"], "8443": {"HTTPS": True}}})
        for item in variants:
            with self.subTest(item=item), self.assertRaises(owner.Stop):
                owner.baseline(owner.canonical(item))

    def test_definite_401_403_412_never_adopt_a_foreign_matching_desired(self):
        original = baseline()
        desired = [owner.remove_legacy_handler(original), owner.retire_legacy_handler(original)]
        for status in (401, 403, 412):
            for phase_post in (1, 2):
                with self.subTest(status=status, phase_post=phase_post):
                    self.api = FakeAPI()
                    self.api.deny_at = {phase_post: status}
                    self.api.foreign_on_deny = {phase_post}
                    with self.assertRaises(owner.Stop):
                        self.execute()
                    self.assertEqual(self.api.posts, phase_post)
                    self.assertEqual(owner.canonical(owner.config(self.api.raw)),
                                     owner.canonical(owner.config(desired[phase_post - 1])))

    def test_definite_denials_at_both_steps_restore_only_owned_state(self):
        for phase_post in (1, 2):
            with self.subTest(phase_post=phase_post):
                self.api = FakeAPI()
                self.api.deny_at = {phase_post: 403}
                with self.assertRaises(owner.HTTPFailure):
                    self.execute()
                self.assertEqual(owner.canonical(owner.config(self.api.raw)), owner.canonical(owner.config(baseline())))
                self.assertEqual(self.api.posts, 1 if phase_post == 1 else 3)

    def test_ambiguous_success_at_each_step_is_reconciled_without_retry(self):
        for phase_post in (1, 2):
            with self.subTest(phase_post=phase_post):
                self.api = FakeAPI()
                self.api.ambiguous_after = {phase_post}
                self.assertEqual(self.execute()["status"], "complete")
                self.assertEqual(self.api.posts, 2)

    def test_pre_post_get_failure_never_adopts_foreign_matching_desired(self):
        desired = owner.retire_legacy_handler(baseline())
        self.api.before_post_failure = (desired, owner.TransportFailure("pre-send GET failed"))
        with self.assertRaises(owner.TransportFailure):
            self.execute()
        self.assertEqual(self.api.posts, 0)
        self.assertEqual(owner.canonical(owner.config(self.api.raw)), owner.canonical(owner.config(desired)))

    def test_signal_during_pre_post_get_does_not_adopt_or_rollback(self):
        desired = owner.retire_legacy_handler(baseline())
        self.api.before_post_failure = (desired, owner.OperationInterrupted("before POST"))
        with self.assertRaises(owner.Stop):
            self.execute()
        self.assertEqual(self.api.posts, 0)
        self.assertEqual(owner.canonical(owner.config(self.api.raw)), owner.canonical(owner.config(desired)))

    def test_ambiguous_foreign_race_is_not_overwritten(self):
        self.api.ambiguous_after = {1}
        def race(raw, etag, attempt, before_write=None):
            if before_write is not None:
                before_write()
            attempt.post_attempted = True
            self.api.posts += 1
            self.api.raw = owner.canonical({**owner.config(self.api.raw), "Foreign": {"x": 1}})
            self.api.etag = owner.digest(self.api.raw)
            raise owner.TransportFailure("ambiguous foreign race")
        self.api.set = race
        with self.assertRaises(owner.Stop):
            self.execute()
        self.assertIn("Foreign", json.loads(self.api.raw))
        self.assertEqual(self.api.posts, 1)

    def test_interrupt_after_retirement_commit_performs_bounded_rollback(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sig):
                self.api = FakeAPI()
                original_event = self.journal_mock.side_effect
                def interrupt_on_b(journal, event, **fields):
                    original_event(journal, event, **fields)
                    if event == "retirement-committed":
                        signal.raise_signal(sig)
                self.journal_mock.side_effect = interrupt_on_b
                with self.assertRaises(owner.Stop):
                    self.execute()
                self.assertEqual(owner.canonical(owner.config(self.api.raw)), owner.canonical(owner.config(baseline())))
                self.journal_mock.side_effect = original_event

    def test_post_retirement_probe_failure_rolls_back_exact_owned_state(self):
        original_tls = owner.tls_request
        calls = 0
        def fail_after_c(port, hostname, path, **kwargs):
            nonlocal calls
            calls += 1
            if port == 443 and hostname == owner.CUSTOM and path == "/health":
                raise owner.Stop("post-retirement acceptance failure")
            return original_tls(port, hostname, path, **kwargs)
        with patch.object(owner, "tls_request", side_effect=fail_after_c):
            with self.assertRaises(owner.Stop):
                self.execute()
        self.assertEqual(owner.canonical(owner.config(self.api.raw)), owner.canonical(owner.config(baseline())))

    def test_negative_unknown_and_no_sni_probes_reject_any_successful_handshake(self):
        class FakeSocket:
            def close(self): pass
        class Context:
            def __init__(self, successful): self.successful, self.server_names = successful, []
            def wrap_socket(self, raw, *, server_hostname):
                self.server_names.append(server_hostname)
                if self.successful:
                    return FakeWrapped()
                raise owner.ssl.SSLError("handshake rejected")
        class FakeWrapped:
            def __enter__(self): return self
            def __exit__(self, *_): return False
        for sni in ("unknown.invalid", None, owner.LEGACY_BRI_HOST, owner.SELF):
            context = Context(True)
            with patch.object(owner.socket, "create_connection", return_value=FakeSocket()), \
                 patch.object(owner.ssl, "_create_unverified_context", return_value=context):
                with self.assertRaisesRegex(owner.Stop, "completed a handshake"):
                    owner._negative_tls_rejected(18443, "127.0.0.1", sni)
            self.assertEqual(context.server_names, [sni])
            rejected = Context(False)
            with patch.object(owner.socket, "create_connection", return_value=FakeSocket()), \
                 patch.object(owner.ssl, "_create_unverified_context", return_value=rejected):
                owner._negative_tls_rejected(18443, "127.0.0.1", sni)
            self.assertEqual(rejected.server_names, [sni])

    def test_tailscale_full_build_is_pinned(self):
        expected = json.dumps({"Version": owner.TAILSCALE_BUILD,
                               "Self": {"DNSName": owner.SELF + ".", "TailscaleIPs": [owner.SELF_IP]}}).encode()
        with patch.object(RealLocalAPI, "request", return_value=(expected, "")):
            RealLocalAPI().status()
        wrong = expected.replace(owner.TAILSCALE_BUILD.encode(), b"1.104.5")
        with patch.object(RealLocalAPI, "request", return_value=(wrong, "")):
            with self.assertRaises(owner.Stop):
                RealLocalAPI().status()

    def test_positive_tls_uses_fixed_ca_store_and_fixed_connect_address(self):
        class RawSocket:
            def close(self): pass
        leaf = b"synthetic DER cert"
        class Secure:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def getpeercert(self, binary_form=False): return leaf
            def sendall(self, _): pass
        class Context:
            def wrap_socket(self, raw, *, server_hostname):
                self.server_hostname = server_hostname
                return Secure()
        class Response:
            status = 200
            def __init__(self, _): pass
            def begin(self): pass
            def read(self, _): return b"ok"
        context = Context()
        with patch.object(owner.ssl, "create_default_context", return_value=context) as make_context, \
             patch.object(owner.socket, "create_connection", return_value=RawSocket()) as connect, \
             patch.object(owner.http.client, "HTTPResponse", Response):
            RealTlsRequest(443, owner.CUSTOM, "/health", connect_host=owner.SELF_IP,
                           fingerprint=owner.digest(leaf))
        make_context.assert_called_once_with(cafile=owner.CA_FILE)
        connect.assert_called_once_with((owner.SELF_IP, 443), timeout=4)
        self.assertEqual(context.server_hostname, owner.CUSTOM)

    def test_optional_bounded_response_body_hash_check_still_rejects_mismatch(self):
        class RawSocket:
            def close(self): pass
        class Secure:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def sendall(self, _): pass
        class Context:
            def wrap_socket(self, raw, *, server_hostname):
                return Secure()
        class Response:
            status = 200
            body = b"verified original asset fixture"
            def __init__(self, _): pass
            def begin(self): pass
            def read(self, limit):
                self.limit = limit
                return self.body

        body_hash = hashlib.sha256(Response.body).hexdigest()
        with patch.object(owner.ssl, "create_default_context", return_value=Context()), \
             patch.object(owner.socket, "create_connection", return_value=RawSocket()), \
             patch.object(owner.http.client, "HTTPResponse", Response):
            self.assertIsNone(RealTlsRequest(443, owner.CUSTOM, "/fixture", connect_host=owner.SELF_IP,
                                             expected_body_sha256=body_hash))
            Response.body = b"different response bytes"
            with self.assertRaisesRegex(owner.Stop, "response bytes"):
                RealTlsRequest(443, owner.CUSTOM, "/fixture", connect_host=owner.SELF_IP,
                               expected_body_sha256=body_hash)

            Response.body = b"x" * (owner.MAX_TLS_RESPONSE_BYTES + 1)
            with self.assertRaisesRegex(owner.Stop, "fixed bound"):
                RealTlsRequest(443, owner.CUSTOM, "/fixture", connect_host=owner.SELF_IP,
                               expected_body_sha256=body_hash)

    def test_tls_handshake_and_transport_exceptions_fail_closed(self):
        with patch.object(owner.socket, "create_connection", side_effect=OSError("listener unavailable")):
            with self.assertRaisesRegex(owner.Stop, "TLS/HTTP readiness probe failed"):
                RealTlsRequest(443, owner.CUSTOM, "/health", connect_host=owner.SELF_IP)

        class RawSocket:
            def close(self): pass
        class BadContext:
            def wrap_socket(self, raw, *, server_hostname):
                raise owner.ssl.SSLCertVerificationError("wrong legacy hostname certificate")

        with patch.object(owner.ssl, "create_default_context", return_value=BadContext()), \
             patch.object(owner.socket, "create_connection", return_value=RawSocket()):
            with self.assertRaisesRegex(owner.Stop, "TLS/HTTP readiness probe failed"):
                RealTlsRequest(443, owner.CUSTOM, "/health", connect_host=owner.SELF_IP)

    def test_nonce_replay_is_rejected_before_any_serve_write(self):
        plan = new_plan()
        self.execute(plan)
        self.api = FakeAPI()
        self.api.raw = baseline()
        self.api.etag = owner.digest(self.api.raw)
        with self.assertRaises(owner.Stop):
            self.execute(plan)
        self.assertEqual(self.api.posts, 0)

    def test_lock_contention_fails_closed_before_serve_write(self):
        @contextlib.contextmanager
        def locked():
            raise owner.Stop("lock held")
            yield
        with patch.object(owner, "operation_lock", locked):
            with self.assertRaises(owner.Stop):
                self.execute()
        self.assertEqual(self.api.posts, 0)

    def test_fixed_flock_is_exclusive_and_rejects_symlink_lock(self):
        with tempfile.TemporaryDirectory(prefix="owner-lock-test-") as directory:
            root = Path(directory)
            fake_stat = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_nlink=1)
            lock_calls = 0
            def fake_flock(*_):
                nonlocal lock_calls
                lock_calls += 1
                if lock_calls == 2:
                    raise BlockingIOError
            with patch.object(owner, "JOURNAL", root), patch.object(owner, "_check_dir"), \
                 patch.object(owner.os, "fstat", return_value=fake_stat), \
                 patch.object(owner.fcntl, "flock", side_effect=fake_flock):
                with RealOperationLock():
                    pass
                with self.assertRaisesRegex(owner.Stop, "Another owner Serve operation"):
                    with RealOperationLock():
                        self.fail("second lock unexpectedly acquired")
                lock_path = root / "operation.lock"
                lock_path.unlink()
                target = root / "target"
                target.write_text("x")
                lock_path.symlink_to(target)
                with self.assertRaisesRegex(owner.Stop, "fixed Serve operation lock"):
                    with RealOperationLock():
                        pass

    def test_direct_cli_is_disabled(self):
        with self.assertRaisesRegex(owner.Stop, "digest-pinned"):
            owner.main()


if __name__ == "__main__":
    unittest.main()
