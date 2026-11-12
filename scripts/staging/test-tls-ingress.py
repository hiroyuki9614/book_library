#!/usr/bin/env python3
"""Offline tests for Serve CAS transitions and the TLS-only state binding."""

import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
import copy
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("tls_ingress", Path(__file__).with_name("tls-ingress.py"))
tls = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tls)


def original_config():
    return {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {"fedora-worker.tail63cd2d.ts.net:443": {
            "Handlers": {"/bri-referee-pilot-comp.png": {"Path": "/home/hiroyuki/tailscale-share/bri-referee-pilot-comp.png"}}
        }},
    }


def original_bytes():
    return json.dumps(original_config(), indent=2).encode()


class FakeServeClient:
    def __init__(self, raw=None):
        self.raw = raw or original_bytes()
        self.etag = hashlib.sha256(self.raw).hexdigest()
        self.posts = 0
        self.fail_post_numbers = set()
        self.raise_after_apply = set()
        self.fail_get_numbers = set()
        self.gets = 0
        self.before_get = None

    def get(self):
        self.gets += 1
        if self.gets in self.fail_get_numbers:
            raise OSError("simulated unavailable readback")
        if self.before_get:
            self.before_get(self)
            self.before_get = None
        return self.raw, self.etag

    def set(self, body, etag):
        self.posts += 1
        if self.posts in self.fail_post_numbers:
            raise tls.LocalApiError(412)
        if etag != self.etag:
            raise tls.LocalApiError(412)
        self.raw = body
        self.etag = hashlib.sha256(body + str(self.posts).encode()).hexdigest()
        if self.posts in self.raise_after_apply:
            raise OSError("simulated lost LocalAPI response after commit")


class TlsIngressTests(unittest.TestCase):
    def test_three_phase_transition_preserves_bri_and_all_unrelated_fields(self):
        original = original_bytes()
        a = json.loads(tls.plan_phase_a(original))
        self.assertEqual(a["TCP"]["443"], {"HTTPS": True})
        self.assertEqual(a["TCP"]["8443"], {"HTTPS": True})
        stale = "fedora-worker.tail63cd2d.ts.net:443"
        legacy_8443 = "fedora-worker.tail63cd2d.ts.net:8443"
        current = f"{tls.TAILNET_HOST}:8443"
        self.assertEqual(a["Web"][stale], original_config()["Web"][stale])
        self.assertEqual(a["Web"][legacy_8443], original_config()["Web"][stale])
        self.assertEqual(a["Web"][current], original_config()["Web"][stale])
        b = json.loads(tls.plan_phase_b(tls.canonical_json(a)))
        self.assertNotIn("443", b["TCP"])
        self.assertNotIn(stale, b["Web"])
        self.assertEqual(b["Web"][legacy_8443], original_config()["Web"][stale])
        self.assertIn(current, b["Web"])
        c = json.loads(tls.plan_phase_c(tls.canonical_json(b)))
        self.assertEqual(c["TCP"]["443"], {"TCPForward": "127.0.0.1:18443"})
        self.assertEqual(tls.plan_phase_b(tls.canonical_json(a)), tls.canonical_json(b))
        self.assertEqual(tls.plan_phase_c(tls.canonical_json(b)), tls.canonical_json(c))
        self.assertEqual(c["TCP"]["8443"], {"HTTPS": True})
        self.assertEqual(c["Web"][legacy_8443], original_config()["Web"][stale])

    def test_localapi_unix_host_is_the_tailscale_sock_host(self):
        connection = tls._UnixHTTPConnection(Path("/unused.sock"), 1)
        self.assertEqual(connection.host, "local-tailscaled.sock")

    def test_localapi_client_requires_the_exact_observed_build(self):
        tls.verify_tailscale_version(tls.TAILSCALE_BUILD + "\n")
        with self.assertRaises(tls.TlsIngressError):
            tls.verify_tailscale_version("1.104.5\n")

    def test_invalid_baseline_funnel_and_occupied_ports_reject(self):
        config = original_config()
        config["AllowFunnel"] = {}
        config["AllowFunnel"]["443"] = True
        with self.assertRaises(tls.TlsIngressError):
            tls.validate_serve_config(json.dumps(config).encode())
        config = original_config()
        config["TCP"]["8443"] = {"HTTPS": True}
        with self.assertRaisesRegex(tls.TlsIngressError, "Serve baseline"):
            tls.plan_phase_a(json.dumps(config).encode())
        config = original_config()
        config["Web"]["unrelated:443"] = {"Handlers": {"/": {"Proxy": "http://localhost"}}}
        with self.assertRaisesRegex(tls.TlsIngressError, "TCP 443 and one legacy Web host"):
            tls.plan_phase_a(json.dumps(config).encode())

    def test_serve_baseline_rejects_extra_scopes_handlers_and_siblings(self):
        for mutate in (
            lambda config: config.update({"UnrelatedNodeField": {"unsafe": True}}),
            lambda config: config["Web"].__setitem__("other:443", {"Handlers": {"/": {"Proxy": "http://localhost"}}}),
            lambda config: config["Web"]["fedora-worker.tail63cd2d.ts.net:443"].update({"Proxy": "http://localhost"}),
        ):
            config = original_config()
            mutate(config)
            with self.assertRaises(tls.TlsIngressError):
                tls.validate_original_serve_config(json.dumps(config).encode())

    def test_actual_two_key_serve_baseline_and_pinned_etag_are_required(self):
        self.assertEqual(set(tls.validate_original_serve_config(original_bytes())), {"TCP", "Web"})
        config = original_config()
        config["AllowFunnel"] = {}
        with self.assertRaises(tls.TlsIngressError):
            tls.validate_original_serve_config(json.dumps(config).encode())
        manifest = {
            "schema": "belib-staging-retained-state/v1", "project": tls.PROJECT,
            "prior_source_sha": "6963103fd6bec332b4057457d07b890fe5c11e73",
            "containers": [{"service": service,
                "id": "9ab9d141599e8b33f51a1e4e9bcd16d66e92b9d134889014d9d9bd618b42f73d" if service == "db" else service * 64}
                for service in ("db", "backend", "frontend", "nginx")],
            "volumes": [{"name": name} for name in ("belib-isolated-i5_staging-postgres-data", "belib-isolated-i5_staging-book-files")],
        }
        with self.assertRaises(tls.TlsIngressError):
            tls.build_tls_binding(source_sha="a" * 40, prior_application_sha=tls.PRIOR_APPLICATION_SHA,
                retained_manifest=manifest, serve_config=original_bytes(), serve_etag="b" * 64,
                certificate_sha256=tls.EXPECTED_CERT_SHA256, tls_config=b"x", owner_plan_sha256="c" * 64)

    def test_fresh_tls_approval_tuple_binds_source_serve_etag_certificate_and_resources(self):
        manifest = {
            "schema": "belib-staging-retained-state/v1",
            "project": tls.PROJECT,
            "prior_source_sha": "6963103fd6bec332b4057457d07b890fe5c11e73",
            "containers": [
                {"service": service, "id": "9ab9d141599e8b33f51a1e4e9bcd16d66e92b9d134889014d9d9bd618b42f73d" if service == "db" else service * 64}
                for service in ("db", "backend", "frontend", "nginx")
            ],
            "volumes": [{"name": name} for name in sorted(("belib-isolated-i5_staging-postgres-data", "belib-isolated-i5_staging-book-files"))],
        }
        binding = tls.build_tls_binding(
            source_sha="a" * 40, prior_application_sha=tls.PRIOR_APPLICATION_SHA,
            retained_manifest=manifest, serve_config=original_bytes(), serve_etag=tls.EXPECTED_SERVE_ETAG,
            certificate_sha256=tls.EXPECTED_CERT_SHA256, tls_config=b"nginx and compose bytes",
            owner_plan_sha256="c" * 64,
        )
        state = tls.make_tls_state(binding, "a" * 40)
        state_digest = tls.tls_state_sha256(state)
        self.assertEqual(len(state_digest), 64)
        for name, value in (("source_sha", "c" * 40), ("serve_etag", "d" * 64)):
            changed = dict(state)
            changed[name] = value
            self.assertNotEqual(tls.tls_state_sha256(changed), state_digest)
        changed = dict(state)
        changed["funnel_enabled"] = True
        with self.assertRaises(tls.TlsIngressError):
            tls.tls_state_sha256(changed)

    def test_tls_config_digest_covers_overlay_and_all_nginx_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / "compose.staging.yaml", root / "scripts/staging/nginx-default.conf",
                     *(root / item for item in tls.TLS_CONFIG_PATHS)]
            for index, path in enumerate(paths):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"config-{index}\n", encoding="ascii")
            first = tls.tls_config_digest(root)
            (root / "scripts/staging/nginx-tls-locations.conf").write_text("location /changed {}\n", encoding="ascii")
            self.assertNotEqual(tls.tls_config_digest(root), first)

    def test_compose_overlay_requires_two_normalized_loopback_tcp_ingress_ports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_paths = (
                root / "scripts/staging/nginx-tls.conf",
                root / "scripts/staging/nginx-tls-default.conf",
                root / "scripts/staging/nginx-tls-locations.conf",
            )
            for path in config_paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture\n", encoding="ascii")
            cert = root / "fullchain.pem"
            key = root / "privkey.pem"
            cert.write_text("synthetic cert path\n", encoding="ascii")
            key.write_text("synthetic key path\n", encoding="ascii")
            mounts = [
                (str(config_paths[0]), "/etc/nginx/nginx.conf"),
                (str(config_paths[1]), "/etc/nginx/conf.d/default.conf"),
                (str(config_paths[2]), "/etc/nginx/conf.d/belib-proxy-locations.conf"),
                (str(cert), "/run/belib-tls/fullchain.pem"),
                (str(key), "/run/belib-tls/privkey.pem"),
            ]
            base = {
                "services": {"nginx": {"ports": [{
                    "host_ip": "127.0.0.1", "published": "18083", "target": 8080,
                    "protocol": "tcp", "mode": "ingress",
                }], "volumes": []}, "backend": {"image": "unchanged"}},
                "networks": {"isolated": {"internal": True}}, "volumes": {},
            }
            combined = copy.deepcopy(base)
            combined["services"]["nginx"]["ports"] = [
                {"host_ip": "127.0.0.1", "published": "18083", "target": 8080,
                 "protocol": "tcp", "mode": "ingress"},
                {"host_ip": "127.0.0.1", "published": "18443", "target": 8443,
                 "protocol": "tcp", "mode": "ingress"},
            ]
            combined["services"]["nginx"]["volumes"] = [
                {"type": "bind", "source": source, "target": target, "read_only": True,
                 "bind": {"create_host_path": False}} for source, target in mounts
            ]
            combined["services"]["nginx"]["extra_hosts"] = [f"{tls.TAILNET_HOST}=100.109.171.70"]
            with patch.object(tls, "CERT_PATH", cert), patch.object(tls, "KEY_PATH", key):
                tls.validate_tls_compose_overlay(base, combined, root, 18083)
                for mutation in ("duplicate", "udp", "host"):
                    invalid = copy.deepcopy(combined)
                    if mutation == "duplicate":
                        invalid["services"]["nginx"]["ports"].append(copy.deepcopy(invalid["services"]["nginx"]["ports"][0]))
                    elif mutation == "udp":
                        invalid["services"]["nginx"]["ports"][1]["protocol"] = "udp"
                    else:
                        invalid["services"]["nginx"]["ports"][1]["mode"] = "host"
                    with self.subTest(mutation=mutation), self.assertRaises(tls.TlsIngressError):
                        tls.validate_tls_compose_overlay(base, invalid, root, 18083)

    def test_certificate_san_fingerprint_key_match_and_mode_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / "key.pem"
            cert = root / "fullchain.pem"
            subprocess.run([
                "/usr/bin/openssl", "req", "-x509", "-newkey", "ed25519", "-nodes", "-days", "30",
                "-subj", "/CN=stg-bl.hiroyuki9614.com", "-addext", "subjectAltName=DNS:stg-bl.hiroyuki9614.com",
                "-keyout", str(key), "-out", str(cert),
            ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            key.chmod(0o600)
            output = subprocess.run(["/usr/bin/openssl", "x509", "-in", str(cert), "-noout", "-fingerprint", "-sha256"],
                                    stdout=subprocess.PIPE, check=True).stdout.decode()
            fingerprint = output.split("=", 1)[1].strip().replace(":", "").lower()
            self.assertEqual(tls.inspect_certificate(cert, key, expected_fingerprint=fingerprint), fingerprint)
            key.chmod(0o640)
            with self.assertRaisesRegex(tls.TlsIngressError, "mode-0600"):
                tls.inspect_certificate(cert, key, expected_fingerprint=fingerprint)

    def test_cas_conflict_and_api_denials_are_not_retried(self):
        client = FakeServeClient()
        before = client.raw
        observed = client.get()
        client.etag = "e" * 64
        with self.assertRaisesRegex(tls.TlsIngressError, "changed outside"):
            tls._set_cas(client, observed[0], observed[1], tls.plan_phase_a(before))
        self.assertEqual(client.posts, 0)

        for denied_status in (401, 403, 412):
            class Response:
                def __init__(self): self.status = denied_status
                def read(self, limit): return b"denied"
                def getheader(self, name, default=""): return default
            class Connection:
                def __init__(self, host, *_):
                    self.host = host
                def request(self, method, path, *, body=None, headers=None): pass
                def getresponse(self): return Response()
                def close(self): pass
            with self.subTest(status=denied_status), patch.object(tls, "_UnixHTTPConnection", Connection):
                with self.assertRaises(tls.LocalApiError) as raised:
                    tls.TailscaleServeClient(Path("/unused")).request("POST", b"{}", "f" * 64)
                self.assertEqual(raised.exception.status, denied_status)
        captured = {}
        def capture(connection, host, *, timeout): captured["host"] = host
        with patch.object(tls.http.client.HTTPConnection, "__init__", capture):
            tls._UnixHTTPConnection(Path("/unused.sock"), 1)
        self.assertEqual(captured["host"], "local-tailscaled.sock")

    def test_failure_after_each_serve_phase_restores_exact_original(self):
        for failed_phase in (1, 2, 3):
            with self.subTest(failed_phase=failed_phase):
                client = FakeServeClient()
                original, original_etag = client.get()
                phases = (tls.plan_phase_a(original),)
                phases += (tls.plan_phase_b(phases[-1]),)
                phases += (tls.plan_phase_c(phases[-1]),)
                owned = []
                current, etag = original, original_etag
                try:
                    for phase_number, desired in enumerate(phases, 1):
                        current, etag = tls._set_cas(client, current, etag, desired)
                        owned.append((current, etag, phase_number))
                        if phase_number == failed_phase:
                            raise RuntimeError("simulated post-phase verification failure")
                except RuntimeError:
                    observed, observed_etag = client.get()
                    phase_number = next(number for raw, tag, number in reversed(owned)
                                        if tls._same_config(raw, observed) and tag == observed_etag)
                    tls.restore_original(client, original, original_etag, observed, observed_etag,
                                         remove_tcp443_first=(phase_number == 3))
                self.assertTrue(tls._same_config(client.raw, original))

    def test_lost_post_response_reconciles_owned_desired_state_without_retry(self):
        client = FakeServeClient()
        client.raise_after_apply.add(1)
        desired = tls.plan_phase_a(client.raw)
        updated, etag = tls._set_cas(client, client.raw, client.etag, desired)
        self.assertTrue(tls._same_config(updated, desired))
        self.assertEqual(client.posts, 1)

    def test_lost_first_readback_is_reconciled_by_read_only_retry(self):
        client = FakeServeClient()
        client.fail_get_numbers.add(2)
        desired = tls.plan_phase_a(client.raw)
        updated, _ = tls._set_cas(client, client.raw, client.etag, desired)
        self.assertTrue(tls._same_config(updated, desired))
        self.assertEqual(client.posts, 1)

    def test_post_error_with_foreign_state_is_not_overwritten(self):
        client = FakeServeClient()
        original = client.raw
        def conflicting_set(body, etag):
            foreign = json.loads(client.raw)
            foreign["ConcurrentOwnerChange"] = True
            client.raw = tls.canonical_json(foreign)
            client.etag = hashlib.sha256(client.raw).hexdigest()
            raise OSError("ambiguous response")
        client.set = conflicting_set
        with self.assertRaisesRegex(tls.TlsIngressError, "conflicts with both owned states"):
            tls._set_cas(client, original, client.etag, tls.plan_phase_a(original))
        self.assertIn(b"ConcurrentOwnerChange", client.raw)

    def test_rollback_lost_response_reconciles_exact_original(self):
        client = FakeServeClient()
        original, original_etag = client.get()
        phase_a, etag_a = tls._set_cas(client, original, original_etag, tls.plan_phase_a(original))
        client.raise_after_apply.add(2)
        tls.restore_original(client, original, original_etag, phase_a, etag_a, remove_tcp443_first=False)
        self.assertTrue(tls._same_config(client.raw, original))
        self.assertEqual(client.posts, 2)

    def test_rollback_refuses_to_overwrite_foreign_config(self):
        client = FakeServeClient()
        original = client.raw
        phase_a = tls.plan_phase_a(original)
        expected = client.etag
        current, etag = tls._set_cas(client, original, expected, phase_a)
        foreign = json.loads(current)
        foreign["OperatorChange"] = True
        client.raw = tls.canonical_json(foreign)
        client.etag = hashlib.sha256(client.raw).hexdigest()
        with self.assertRaisesRegex(tls.TlsIngressError, "Rollback refused"):
            tls.restore_original(client, original, expected, current, etag, remove_tcp443_first=False)
        self.assertIn(b"OperatorChange", client.raw)

    def test_actual_cli_version_layout_requires_exact_long_build(self):
        tls.verify_tailscale_version("1.104.5\n  long version: " + tls.TAILSCALE_BUILD + "\n")
        with self.assertRaises(tls.TlsIngressError):
            tls.verify_tailscale_version("1.104.5\n  long version: 1.104.5-other\n")
        with self.assertRaises(tls.TlsIngressError):
            tls.verify_tailscale_version("1.104.5")



if __name__ == "__main__":
    unittest.main()
