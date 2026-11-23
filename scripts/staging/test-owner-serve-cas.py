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


def tailscale_wire(raw):
    """Model Go json omitempty on empty Serve config maps without inventing its ETag."""
    value = json.loads(raw)
    return json.dumps({key: child for key, child in value.items() if child},
                      sort_keys=True, separators=(",", ":")).encode()


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
                self.raw = tailscale_wire(raw)
                self.etag = hashlib.sha256(raw + b"foreign").hexdigest()
            raise owner.HTTPFailure(status)
        self.raw = tailscale_wire(raw)
        # Model the ETag as an opaque server token, independent of normalized config.
        self.etag = f"{self.posts + 100:064x}"
        self.events.append(("WIRE", self.posts, self.raw))
        attempt.post_accepted = True
        if self.posts in self.ambiguous_after:
            raise owner.TransportFailure("simulated lost response after apply")


class OwnerServeTests(unittest.TestCase):
    def setUp(self):
        self.journal_temp = tempfile.TemporaryDirectory(prefix="owner-serve-state-test-")
        self.addCleanup(self.journal_temp.cleanup)
        isolated_journal = Path(self.journal_temp.name)
        isolated_journal.chmod(0o700)
        journal_patch = patch.object(owner, "JOURNAL", isolated_journal)
        journal_patch.start()
        self.addCleanup(journal_patch.stop)
        self.api = FakeAPI()
        self.used_nonces = set()
        self.probes = []
        self.counter = 0

        def journal_start(plan, original, etag, plan_sha, adoption=None):
            if plan["nonce"] in self.used_nonces:
                raise owner.Stop("nonce replay")
            self.used_nonces.add(plan["nonce"])
            self.adoption = adoption
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

    def v2_inputs(self):
        source = "a" * 40
        operation_id = "1" * 32
        project = "belib-isolated-i5-upgrade-" + operation_id
        old_nginx, initial_nginx, tls_nginx = "2" * 64, "3" * 64, "4" * 64
        new_ids = {"db": "8" * 64, "backend": "9" * 64, "frontend": "a" * 64, "nginx": initial_nginx}
        old_ids = {"backend": "b" * 64, "frontend": "c" * 64, "nginx": old_nginx}
        old_isolated, old_ingress = "d" * 64, "e" * 64
        new_isolated, new_ingress = "f" * 64, "1" * 64
        old_networks = {"belib-isolated-i5_isolated": old_isolated,
                        "belib-isolated-i5_ingress": old_ingress}
        new_nginx_networks = {project + "_isolated": new_isolated,
                              project + "_ingress": new_ingress}
        def pin(container_id, networks):
            return {"id": container_id, "configuration_sha256": hashlib.sha256(container_id.encode()).hexdigest(),
                    "network_ids": networks}
        runtime_plan = {
            "cutover_plan": {"schema": "belib-isolated-copy-cutover/v1", "source_sha": source,
                             "operation_id": operation_id, "port": 18083,
                             "old": {"backend": pin(old_ids["backend"], {"belib-isolated-i5_isolated": old_isolated}),
                                     "frontend": pin(old_ids["frontend"], {"belib-isolated-i5_isolated": old_isolated}),
                                     "nginx": pin(old_nginx, old_networks)},
                             "new": {"backend": pin(new_ids["backend"], {project + "_isolated": new_isolated}),
                                     "frontend": pin(new_ids["frontend"], {project + "_isolated": new_isolated}),
                                     "nginx": pin(initial_nginx, new_nginx_networks)}},
            "candidate_database": {"id": new_ids["db"], "configuration_sha256": "a" * 64,
                                   "volume_name": project + "_staging-postgres-data", "network_id": new_isolated},
            "source_database": {"id": owner.UPGRADE_RETAINED_DATABASE_ID,
                                "configuration_sha256": "b" * 64,
                                "network_ids": {"belib-isolated-i5_isolated": old_isolated},
                                "volume_name": "belib-isolated-i5_staging-postgres-data"},
        }
        runtime = {"project": project, "operation_id": operation_id,
                   "services": new_ids,
                   "retained": {"project": "belib-isolated-i5", "db": owner.UPGRADE_RETAINED_DATABASE_ID,
                                "stopped_application_ids": list(old_ids.values()),
                                "volumes": {"database": "belib-isolated-i5_staging-postgres-data",
                                            "books": "belib-isolated-i5_staging-book-files"}},
                   "port": 18083, "plan": runtime_plan,
                   "plan_sha256": hashlib.sha256(owner.canonical(runtime_plan)).hexdigest()}
        stable = json.loads(json.dumps(runtime))
        del stable["services"]["nginx"]
        del stable["plan"]["cutover_plan"]["new"]["nginx"]["id"]
        del stable["plan"]["cutover_plan"]["new"]["nginx"]["configuration_sha256"]
        del stable["plan_sha256"]
        unchanged = owner.digest(owner.canonical(stable))
        upgrade_nonce = "b" * 64
        root_result = {
            "schema": "belib-staging-operation-result/v1", "nonce": upgrade_nonce,
            "grant_sha256": "c" * 64, "source_sha": source, "operation": "upgrade-retained",
            "host": "ubuntu-worker", "principal": "hiroyuki", "uid": 1000,
            "project": "belib-isolated-i5", "exit_code": 0, "completed_at": 100,
            "nonce_consumed": True, "expected_runtime": runtime,
        }
        plan = new_plan()
        plan.update({"schema": owner.PLAN_SCHEMA_V2, "source_sha": source,
                     "prior_application_sha": source, "nonce": "d" * 64,
                     "upgrade_receipt_nonce": upgrade_nonce,
                     "upgrade_receipt_sha256": owner.digest(owner.canonical(root_result)),
                     "operation_id": operation_id, "project": project})
        plan_raw = owner.canonical(plan)
        plan_sha = owner.digest(plan_raw)
        prepared = {
            "schema": owner.PREPARE_SCHEMA_V2, "plan_sha256": plan_sha,
            "retained_state_sha256": plan["retained_state_sha256"], "nginx_id": tls_nginx,
            "source_sha": source, "tls_config_sha256": plan["tls_config_sha256"],
            "certificate_sha256": plan["certificate_sha256"], "approval_nonce": "e" * 64,
            "evidence_sha256": "f" * 64, "prepared": True, "owner_nonce": plan["nonce"],
            "upgrade_receipt_nonce": upgrade_nonce, "upgrade_receipt_sha256": plan["upgrade_receipt_sha256"],
            "operation_id": operation_id, "project": project, "previous_nginx_id": initial_nginx,
            "nginx_configuration_sha256": "a" * 64,
            "nginx_image_id": "sha256:" + "8" * 64,
            "nginx_network_ids": new_nginx_networks, "unchanged_runtime_sha256": unchanged,
        }
        prepared_raw = owner.canonical(prepared)
        return plan, plan_raw, prepared, prepared_raw, root_result

    def test_v2_adopts_exact_root_receipt_and_publishes_linked_completion(self):
        plan, plan_raw, prepared, prepared_raw, root_result = self.v2_inputs()
        with tempfile.TemporaryDirectory(prefix="owner-v2-state-") as temporary:
            journal = Path(temporary)
            journal.chmod(0o700)
            with patch.object(owner, "JOURNAL", journal), \
                    patch.object(owner, "UPGRADE_RESULTS", Path("/offline/operator")), \
                    patch.object(owner, "_safe_root_path"), \
                    patch.object(owner, "_read_root_regular", return_value=owner.canonical(root_result)), \
                    patch.object(owner, "_read_completion", return_value=None), \
                    patch.object(owner, "_publish_completion", side_effect=lambda value: value) as publish:
                result = owner.run(plan_raw, owner.digest(plan_raw), prepared_raw, owner.digest(prepared_raw))
        self.assertEqual(result["schema"], owner.COMPLETION_SCHEMA)
        self.assertEqual(result["owner_nonce"], plan["nonce"])
        self.assertEqual(result["nginx_id"], prepared["nginx_id"])
        self.assertEqual(result["previous_nginx_id"], "3" * 64)
        self.assertEqual(self.api.posts, 2)
        self.assertEqual(self.adoption["prepare_receipt_sha256"], owner.digest(prepared_raw))
        publish.assert_called_once()

    def test_v2_rejects_wrong_source_and_prepare_link_before_serve_effect(self):
        plan, plan_raw, prepared, prepared_raw, root_result = self.v2_inputs()
        for mutate in (lambda p: p.update(prior_application_sha="9" * 40),
                       lambda p: p.update(upgrade_receipt_sha256="0" * 64)):
            with self.subTest(mutate=mutate):
                changed = dict(plan)
                mutate(changed)
                raw = owner.canonical(changed)
                with patch.object(owner, "UPGRADE_RESULTS", Path("/offline/operator")), \
                        patch.object(owner, "_safe_root_path"), \
                        patch.object(owner, "_read_root_regular", return_value=owner.canonical(root_result)), \
                        self.assertRaises(owner.Stop):
                    owner.run(raw, owner.digest(raw), prepared_raw, owner.digest(prepared_raw))
        self.assertEqual(self.api.posts, 0)

    def test_completion_publication_uses_no_replace_and_leaves_complete_nlink_one_on_response_loss(self):
        plan, plan_raw, prepared, prepared_raw, _ = self.v2_inputs()
        serve_result = {"serve_config_sha256": "a" * 64, "serve_etag": "opaque-etag"}
        completion = owner._completion_value(plan, owner.digest(plan_raw), owner.digest(prepared_raw),
                                             prepared, serve_result)
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory)
            journal.chmod(0o700)
            path = journal / (plan["nonce"] + ".complete.json")
            real_open, real_fstat = owner.os.open, owner.os.fstat
            def fstat_root(fd):
                info = real_fstat(fd)
                return type("Info", (), {"st_mode": info.st_mode, "st_uid": 0,
                                          "st_nlink": info.st_nlink})()
            def lose_after_publish(name, flags, *args, **kwargs):
                if flags & getattr(os, "O_DIRECTORY", 0):
                    raise OSError("simulated response loss after atomic publication")
                return real_open(name, flags, *args, **kwargs)
            with patch.object(owner, "JOURNAL", journal), patch.object(owner, "ensure_journal_dir"), \
                    patch.object(owner, "_read_completion", side_effect=[None]), \
                    patch.object(owner.os, "fstat", side_effect=fstat_root), \
                    patch.object(owner.os, "open", side_effect=lose_after_publish):
                with self.assertRaisesRegex(owner.Stop, "Atomic immutable TLS completion"):
                    owner._publish_completion(completion)
            self.assertTrue(path.is_file())
            self.assertEqual(path.stat().st_nlink, 1)
            self.assertEqual(path.read_bytes(), owner.canonical(completion))

    def test_v2_recovery_ignores_only_incomplete_final_journal_line_and_never_reposts(self):
        plan, plan_raw, prepared, prepared_raw, _ = self.v2_inputs()
        original = baseline()
        final = owner.activate_custom_tls(owner.remove_legacy_handler(original))
        final_etag = "9" * 64
        adoption = {"prepare_receipt_sha256": owner.digest(prepared_raw),
                    "upgrade_receipt_nonce": plan["upgrade_receipt_nonce"],
                    "upgrade_receipt_sha256": plan["upgrade_receipt_sha256"],
                    "source_sha": plan["source_sha"], "operation_id": plan["operation_id"],
                    "project": plan["project"], "previous_nginx_id": prepared["previous_nginx_id"],
                    "nginx_id": prepared["nginx_id"],
                    "nginx_configuration_sha256": prepared["nginx_configuration_sha256"],
                    "nginx_image_id": prepared["nginx_image_id"],
                    "nginx_network_ids": prepared["nginx_network_ids"],
                    "unchanged_runtime_sha256": prepared["unchanged_runtime_sha256"]}
        initial = {"schema": "belib-tls-owner-journal/v1", "plan_sha256": owner.digest(plan_raw),
                   "original_config_b64": __import__("base64").b64encode(original).decode("ascii"),
                   "original_sha256": owner.digest(original), "original_etag": plan["serve_etag"],
                   "event": "prepared", "adoption": adoption}
        committed = {"event": "retirement-committed", "phase": "add-custom-tls",
                     "config_sha256": owner.digest(final), "etag": final_etag}
        journal_raw = owner.canonical(initial) + b"\n" + owner.canonical(committed) + b"\n" + b'{"event":'
        self.api.raw = final
        self.api.etag = final_etag
        with patch.object(owner, "_read_root_regular", return_value=journal_raw):
            result = owner._recover_v2_completion(plan, owner.digest(plan_raw),
                                                  owner.digest(prepared_raw), prepared)
        self.assertEqual(result, {"status": "complete", "serve_config_sha256": owner.digest(final),
                                  "serve_etag": final_etag})
        self.assertEqual(self.api.posts, 0)
        torn_commit = owner.canonical(initial) + b"\n" + owner.canonical(committed)
        with patch.object(owner, "_read_root_regular", return_value=torn_commit), \
                self.assertRaisesRegex(owner.Stop, "no durable final Serve commit"):
            owner._recover_v2_completion(plan, owner.digest(plan_raw), owner.digest(prepared_raw), prepared)
            with patch.object(owner, "JOURNAL", journal), patch.object(owner, "ensure_journal_dir"), \
                    patch.object(owner, "_read_completion", return_value=completion):
                self.assertEqual(owner._publish_completion(completion), completion)
                conflict = {**completion, "serve_etag": "foreign-etag"}
                with self.assertRaisesRegex(owner.Stop, "conflicting immutable"):
                    owner._publish_completion(conflict)
            self.assertEqual(path.read_bytes(), owner.canonical(completion))

    def test_retirement_replaces_exact_legacy_handler_with_custom_tls_and_rejects_other_sni(self):
        result = self.execute()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(owner.config(self.api.raw),
                         {"TCP": {"443": {"TCPForward": "127.0.0.1:18443"}}, "Web": {}})
        self.assertEqual(self.api.posts, 2)
        wire_states = [item[2] for item in self.api.events if item[0] == "WIRE"]
        self.assertEqual(wire_states, [
            b"{}", b'{"TCP":{"443":{"TCPForward":"127.0.0.1:18443"}}}',
        ])
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
        wire_states = [item[2] for item in self.api.events if item[0] == "WIRE"]
        self.assertIn(b"{}", wire_states)
        self.assertIn(b'{"TCP":{"443":{"TCPForward":"127.0.0.1:18443"}}}', wire_states)
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

    def test_omitted_serve_maps_normalize_but_null_and_unknown_scopes_reject(self):
        self.assertEqual(owner.config(b"{}"), {"TCP": {}, "Web": {}})
        self.assertEqual(owner.config(b'{"TCP":{"443":{"TCPForward":"127.0.0.1:18443"}}}'),
                         {"TCP": {"443": {"TCPForward": "127.0.0.1:18443"}}, "Web": {}})
        for raw in (b'{"TCP":null}', b'{"Web":[]}', b'{"Other":{}}'):
            with self.subTest(raw=raw), self.assertRaises(owner.Stop):
                owner.config(raw)
        with self.assertRaises(owner.Stop):
            owner.baseline(b"{}")

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

    def test_v3_stable_serve_path_only_reads_exact_active_mapping_and_probes(self):
        raw = json.dumps({"TCP": {"443": {"TCPForward": "127.0.0.1:18443"}}, "Web": {}},
            sort_keys=True, separators=(",", ":")).encode()
        prior = {"owner_nonce": "1"*64, "completion_sha256": "2"*64,
            "serve_config_sha256": owner.digest(raw), "serve_etag": "3"*64,
            "certificate_sha256": "4"*64}
        runtime = {"prior_generation_sha256": "5"*64, "prior_tls_completion": prior,
            "project": "belib-isolated-i5-upgrade-"+"6"*32, "operation_id": "6"*32,
            "services": {"nginx": "7"*64}, "port": 18083}
        plan = {"schema": owner.PLAN_SCHEMA_V3, "disposition": "reuse-exact-active-custom-tls-ingress",
            "source_sha": "8"*40, "prior_application_sha": "8"*40,
            "retained_state_sha256": "9"*64, "serve_config_sha256": prior["serve_config_sha256"],
            "serve_etag": prior["serve_etag"], "certificate_sha256": prior["certificate_sha256"],
            "tls_config_sha256": "a"*64, "nonce": "b"*64, "expires_at": 1000,
            "upgrade_receipt_nonce": "c"*64, "upgrade_receipt_sha256": "d"*64,
            "operation_id": runtime["operation_id"], "project": runtime["project"],
            "prior_generation_sha256": runtime["prior_generation_sha256"],
            "prior_tls_owner_nonce": prior["owner_nonce"],
            "prior_tls_completion_sha256": prior["completion_sha256"]}
        prepared = {"unchanged_runtime_sha256": "e"*64, "previous_nginx_id": runtime["services"]["nginx"],
            "nginx_id": "f"*64, "nginx_configuration_sha256": "1"*64}
        class ReadOnlyApi:
            def __init__(self): self.reads = 0; self.statuses = 0
            def status(self): self.statuses += 1
            def get(self): self.reads += 1; return raw, prior["serve_etag"]
            def set(self, *_args): raise AssertionError("v3 path must never write Serve")
        api = ReadOnlyApi()
        with patch.object(owner, "LocalAPI", return_value=api), \
                patch.object(owner, "_root_unchanged_digest_from_upgrade", return_value=("e"*64, runtime)), \
                patch.object(owner, "_http_health") as http, \
                patch.object(owner, "tls_request") as positive, \
                patch.object(owner, "tls_unknown_rejected") as unknown, \
                patch.object(owner, "tls_no_sni_rejected") as no_sni, \
                patch.object(owner, "tls_legacy_sni_rejected") as legacy, \
                patch.object(owner, "tls_self_sni_rejected") as self_sni:
            result = owner._verify_stable_active_serve(plan, prepared, "f"*64)
        self.assertEqual(result["status"], "complete")
        self.assertEqual((api.statuses, api.reads), (2, 2))
        http.assert_called_once_with(18083)
        self.assertEqual(positive.call_count, 2)
        unknown.assert_called_once(); no_sni.assert_called_once(); legacy.assert_called_once(); self_sni.assert_called_once()

    def test_expired_v3_plan_cannot_publish_first_completion(self):
        now = int(owner.time.time())
        plan = {"schema": owner.PLAN_SCHEMA_V3, "disposition": "reuse-exact-active-custom-tls-ingress",
            "source_sha": "a"*40, "prior_application_sha": "a"*40,
            "retained_state_sha256": "b"*64, "serve_config_sha256": "c"*64,
            "serve_etag": "d"*64, "certificate_sha256": "e"*64,
            "tls_config_sha256": "f"*64, "nonce": "1"*64, "expires_at": now-1,
            "upgrade_receipt_nonce": "2"*64, "upgrade_receipt_sha256": "3"*64,
            "operation_id": "4"*32, "project": "belib-isolated-i5-upgrade-"+"4"*32,
            "prior_generation_sha256": "5"*64, "prior_tls_owner_nonce": "6"*64,
            "prior_tls_completion_sha256": "7"*64}
        raw = owner.canonical(plan)
        prepared = {"approval_nonce": "8"*64, "evidence_sha256": "9"*64,
            "previous_nginx_id": "a"*64, "nginx_id": "b"*64,
            "nginx_configuration_sha256": "c"*64, "nginx_image_id": "sha256:"+"d"*64,
            "nginx_network_ids": {plan["project"]+"_isolated":"e"*64, plan["project"]+"_ingress":"f"*64},
            "unchanged_runtime_sha256":"0"*64}
        with patch.object(owner, "_validate_prepare_v2", return_value=prepared), \
                patch.object(owner, "_read_completion", return_value=None), \
                patch.object(owner, "_verify_stable_active_serve") as verify:
            with self.assertRaisesRegex(owner.Stop, "Expired or out-of-window TLS v3"):
                owner.run(raw, owner.digest(raw), b"{}", owner.digest(b"{}"))
        verify.assert_not_called()

    def test_direct_cli_is_disabled(self):
        with self.assertRaisesRegex(owner.Stop, "digest-pinned"):
            owner.main()


if __name__ == "__main__":
    unittest.main()
