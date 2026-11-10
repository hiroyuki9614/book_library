#!/usr/bin/env python3
"""Offline tests for the owner-pinned rootless nginx TLS prepare CLI."""
import importlib.util
import contextlib
import copy
import json
import os
import subprocess
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("tls_prepare", Path(__file__).with_name("tls-prepare.py"))
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)
TLS_SPEC = importlib.util.spec_from_file_location("tls_ingress_test", Path(__file__).with_name("tls-ingress.py"))
tls = importlib.util.module_from_spec(TLS_SPEC)
TLS_SPEC.loader.exec_module(tls)
cli.TLS = tls
GUARD_SPEC = importlib.util.spec_from_file_location("launch_guard_test", Path(__file__).with_name("launch-guard.py"))
guard_real = importlib.util.module_from_spec(GUARD_SPEC)
GUARD_SPEC.loader.exec_module(guard_real)


def retained():
    return {
        "schema": "belib-staging-retained-state/v1", "project": tls.PROJECT,
        "prior_source_sha": "6963103fd6bec332b4057457d07b890fe5c11e73",
        "containers": [{"service": s, "id": "9ab9d141599e8b33f51a1e4e9bcd16d66e92b9d134889014d9d9bd618b42f73d" if s == "db" else s * 64}
                       for s in ("db", "backend", "frontend", "nginx")],
        "volumes": [{"name": x} for x in sorted(("belib-isolated-i5_staging-postgres-data", "belib-isolated-i5_staging-book-files"))],
    }


def retained_full(expected_db_image="postgres:16@sha256:" + "a" * 64):
    project = tls.PROJECT
    prior = "6963103fd6bec332b4057457d07b890fe5c11e73"
    snapshot = guard_real.SNAPSHOT_ROOT
    ids = {"db": "9ab9d141599e8b33f51a1e4e9bcd16d66e92b9d134889014d9d9bd618b42f73d",
           "backend": "b" * 64, "frontend": "c" * 64, "nginx": "d" * 64}
    image_ids = {"db": "sha256:" + "1" * 64, "backend": cli.APP_IMAGES["backend"],
                 "frontend": cli.APP_IMAGES["frontend"], "nginx": "sha256:" + "2" * 64}
    image_refs = {"db": expected_db_image, "backend": "belib-backend@sha256:" + "3" * 64,
                  "frontend": "belib-frontend@sha256:" + "4" * 64,
                  "nginx": "nginx:1.27-alpine@sha256:" + "5" * 64}
    volume_mount = lambda name, dest: {"type": "volume", "name": name, "source": "", "destination": dest, "read_write": True}
    bind_mount = lambda src, dest: {"type": "bind", "name": "", "source": str(src), "destination": dest, "read_write": False}
    containers = [
        {"id": ids["db"], "service": "db", "project": project, "oneoff": "false", "state": "running",
         "image_id": image_ids["db"], "image_ref": image_refs["db"], "networks": [project + "_isolated"],
         "mounts": [volume_mount(project + "_staging-postgres-data", "/var/lib/postgresql/data"),
                    bind_mount(snapshot / prior / "scripts/staging/10-belib-staging-roles.sh", "/docker-entrypoint-initdb.d/10-belib-staging-roles.sh")]},
        {"id": ids["backend"], "service": "backend", "project": project, "oneoff": "false", "state": "running",
         "image_id": image_ids["backend"], "image_ref": image_refs["backend"], "networks": [project + "_isolated"],
         "mounts": [volume_mount(project + "_staging-book-files", "/home/node")]},
        {"id": ids["frontend"], "service": "frontend", "project": project, "oneoff": "false", "state": "running",
         "image_id": image_ids["frontend"], "image_ref": image_refs["frontend"], "networks": [project + "_isolated"], "mounts": []},
        {"id": ids["nginx"], "service": "nginx", "project": project, "oneoff": "false", "state": "running",
         "image_id": image_ids["nginx"], "image_ref": image_refs["nginx"],
         "networks": [project + "_ingress", project + "_isolated"],
         "mounts": [bind_mount(snapshot / cli.APP812 / "nginx/nginx.conf", "/etc/nginx/nginx.conf"),
                    bind_mount(snapshot / cli.APP812 / "scripts/staging/nginx-default.conf", "/etc/nginx/conf.d/default.conf")]},
    ]
    volumes = []
    for name in (project + "_staging-postgres-data", project + "_staging-book-files"):
        volumes.append({"name": name, "driver": "local", "scope": "local", "created_at": "2026-01-01T00:00:00Z",
            "mountpoint": "/var/lib/docker/volumes/" + name, "labels": {"com.docker.compose.project": project,
            "com.docker.compose.volume": name.removeprefix(project + "_")}, "options": {}})
    networks = [
        {"id": "6" * 64, "name": project + "_isolated", "driver": "bridge", "internal": True,
         "labels": {"com.docker.compose.project": project, "com.docker.compose.network": "isolated"}},
        {"id": "7" * 64, "name": project + "_ingress", "driver": "bridge", "internal": False,
         "labels": {"com.docker.compose.project": project, "com.docker.compose.network": "ingress"}},
    ]
    return {"schema": "belib-staging-retained-state/v1", "project": project, "prior_source_sha": prior,
            "containers": containers, "volumes": volumes, "networks": networks}


def serve():
    return json.dumps({"TCP": {"443": {"HTTPS": True}}, "Web": {
        "fedora-worker.tail63cd2d.ts.net:443": {"Handlers": {tls.BRI_PATH: {"Path": tls.BRI_FILE}}}},
        }, separators=(",", ":")).encode()


class FakeEffects:
    source_sha = "a" * 40
    tls_config_sha256 = "b" * 64
    certificate_sha256 = tls.EXPECTED_CERT_SHA256
    approval_nonce = "d" * 64
    evidence_sha256 = "e" * 64
    def __init__(self):
        self.calls = []
        self.current = "old-nginx"
        self.failure = None
        self.third_party = False
    def syntax_check(self): self.calls.append("syntax")
    def recreate_nginx(self):
        self.calls.append("recreate")
        self.current = "new-nginx"
        if self.third_party: self.current = "foreign-nginx"
        if self.failure == "recreate": raise cli.PrepareError("recreate failed")
    def manifest(self):
        self.calls.append("manifest")
        return retained()
    def nginx_identity(self): return self.current
    def assert_retained_equal(self, before, after, *, allow_nginx_change):
        self.calls.append("retained-check")
        if self.failure == "retained": raise cli.PrepareError("retained state changed")
    def probe_http(self):
        self.calls.append("http")
        if self.failure == "http": raise cli.PrepareError("HTTP down")
    def probe_https(self):
        self.calls.append("https")
        if self.failure in ("https", "server-down-before-unknown"): raise cli.PrepareError("TLS server down")
    def probe_unknown_sni(self):
        self.calls.append("unknown-sni")
        if self.failure == "unknown-sni": raise cli.PrepareError("unknown SNI passed or server down")
    def owns_nginx_change(self): return self.current == "new-nginx"
    def restore_nginx(self):
        self.calls.append("rollback")
        if self.current != "new-nginx": raise cli.PrepareError("foreign nginx")
        self.current = "old-nginx"


class TlsPrepareTests(unittest.TestCase):
    @staticmethod
    def _manifest_tls(baseline, build_root, state):
        observed = copy.deepcopy(baseline)
        nginx = next(row for row in observed["containers"] if row["service"] == "nginx")
        nginx["id"] = "f" * 64
        nginx["mounts"] = [
            {"type": "bind", "name": "", "source": str(build_root / "scripts/staging/nginx-tls.conf"),
             "destination": "/etc/nginx/nginx.conf", "read_write": False},
            {"type": "bind", "name": "", "source": str(build_root / "scripts/staging/nginx-tls-default.conf"),
             "destination": "/etc/nginx/conf.d/default.conf", "read_write": False},
            {"type": "bind", "name": "", "source": str(build_root / "scripts/staging/nginx-tls-locations.conf"),
             "destination": "/etc/nginx/conf.d/belib-proxy-locations.conf", "read_write": False},
            {"type": "bind", "name": "", "source": str(tls.CERT_PATH),
             "destination": "/run/belib-tls/fullchain.pem", "read_write": False},
            {"type": "bind", "name": "", "source": str(tls.KEY_PATH),
             "destination": "/run/belib-tls/privkey.pem", "read_write": False},
        ]
        return observed

    def test_plan_is_canonical_fixed_schema_and_bounded(self):
        raw = cli.create_plan("a" * 40, retained(), serve(), tls.EXPECTED_SERVE_ETAG,
                              tls.EXPECTED_CERT_SHA256, "c" * 64, now=100, nonce="d" * 64)
        plan = cli.validate_plan(raw, cli.digest(raw), now=101)
        self.assertEqual(plan["expires_at"], 1900)
        owner = cli._module("owner_cas_contract_test", Path(__file__).with_name("owner-serve-cas.py"))
        self.assertEqual(set(plan), owner.PLAN_KEYS)
        self.assertEqual(plan["schema"], "belib-tls-owner-plan/v1")
        self.assertEqual(owner.validate_plan(plan, now=101), plan)
        self.assertEqual(set(plan), {"schema", "source_sha", "prior_application_sha", "retained_state_sha256",
            "serve_config_sha256", "serve_etag", "certificate_sha256", "tls_config_sha256", "nonce", "expires_at"})
        with self.assertRaisesRegex(cli.PrepareError, "digest"):
            cli.validate_plan(raw, "e" * 64, now=101)
        with self.assertRaisesRegex(cli.PrepareError, "expired"):
            cli.validate_plan(raw, cli.digest(raw), now=1901)
        with self.assertRaises(tls.TlsIngressError):
            cli.create_plan("a" * 40, {**retained(), "prior_source_sha": "6" * 40}, serve(), "b" * 64,
                            tls.EXPECTED_CERT_SHA256, "c" * 64, now=100)

    def test_running_696_lineage_812_manifest_uses_full_validator_and_retains_every_network(self):
        expected_db = "postgres:16@sha256:" + "a" * 64
        manifest = retained_full(expected_db)
        calls = {}
        class Guard:
            def build_retained_manifest(self, prior, environment, **kwargs):
                calls["build"] = (prior, kwargs)
                return copy.deepcopy(manifest)
            def validate_retained_manifest(self, actual, prior, **kwargs):
                calls["validate"] = (prior, kwargs)
                guard_real.validate_retained_manifest(actual, prior, **kwargs)
        observed = cli.retained_live_manifest(Guard(), {}, ["docker"], expected_db)
        self.assertEqual(observed, manifest)
        self.assertFalse(calls["build"][1]["validate"])
        self.assertEqual(calls["validate"][1], {"allow_running": True, "allow_ingress": True,
            "current_source_sha": cli.APP812, "expected_db_image": expected_db})
        self.assertEqual({row["name"] for row in observed["networks"]},
                         {tls.PROJECT + "_isolated", tls.PROJECT + "_ingress"})

    def test_source_bundle_digest_includes_all_tls_entrypoints_and_configs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / "compose.staging.yaml", root / "scripts/staging/tls-prepare.py",
                root / "scripts/staging/tls-ingress.py", root / "scripts/staging/owner-serve-cas.py",
                root / "scripts/staging/owner-command.py", root / "scripts/staging/nginx-default.conf",
                *(root / item for item in tls.TLS_CONFIG_PATHS)]
            for index, path in enumerate(paths):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"source fixture {index}\n")
            first = cli.bundle_digest(root)
            (root / "scripts/staging/owner-serve-cas.py").write_text("changed owner entrypoint\n")
            self.assertNotEqual(cli.bundle_digest(root), first)

    def test_missing_owner_ready_and_missing_adapter_never_mutate(self):
        fx = FakeEffects()
        raw = cli.create_plan("a" * 40, retained(), serve(), tls.EXPECTED_SERVE_ETAG, tls.EXPECTED_CERT_SHA256, "c" * 64, now=int(time.time()))
        with self.assertRaisesRegex(cli.PrepareError, "owner-ready"):
            cli.prepare(raw, cli.digest(raw), evidence_sha256="f" * 64, owner_ready=False, adapters=lambda *_: fx.recreate_nginx())
        with self.assertRaisesRegex(cli.PrepareError, "verified owner loader"):
            cli.prepare(raw, cli.digest(raw), evidence_sha256="f" * 64, owner_ready=True)
        self.assertEqual(fx.calls, [])
        self.assertEqual(fx.current, "old-nginx")

    def test_missing_signed_grant_is_rejected_before_any_transaction_effect(self):
        fx = FakeEffects()
        fx.approval_nonce = ""
        with self.assertRaisesRegex(cli.PrepareError, "Fresh signed approval"):
            cli.transaction(before_manifest=retained(), expected_manifest_sha=cli.digest(cli.canonical(retained())),
                plan_sha="c" * 64, nginx_identity="old-nginx", effects=fx)
        self.assertEqual(fx.calls, [])

    def test_transaction_success_recreates_only_nginx_and_probes_unknown_sni(self):
        fx = FakeEffects()
        receipt = cli.transaction(before_manifest=retained(), expected_manifest_sha=cli.digest(cli.canonical(retained())),
            plan_sha="c" * 64, nginx_identity="old-nginx", effects=fx)
        self.assertTrue(receipt["prepared"])
        self.assertEqual(fx.calls, ["syntax", "recreate", "manifest", "retained-check", "http", "https",
                                    "unknown-sni", "https", "manifest", "retained-check"])
        self.assertEqual(fx.current, "new-nginx")

    def test_failure_after_mutation_rolls_back_only_owned_nginx(self):
        for failure in ("recreate", "retained", "http", "https", "unknown-sni"):
            fx = FakeEffects(); fx.failure = failure
            with self.subTest(failure=failure), self.assertRaises(cli.PrepareError):
                cli.transaction(before_manifest=retained(), expected_manifest_sha=cli.digest(cli.canonical(retained())),
                    plan_sha="c" * 64, nginx_identity="old-nginx", effects=fx)
            self.assertEqual(fx.current, "old-nginx")
            self.assertEqual(fx.calls[-1], "rollback")

    def test_unexpected_nginx_owner_change_is_never_overwritten(self):
        fx = FakeEffects(); fx.third_party = True
        with self.assertRaisesRegex(cli.PrepareError, "ownership is uncertain"):
            cli.transaction(before_manifest=retained(), expected_manifest_sha=cli.digest(cli.canonical(retained())),
                plan_sha="c" * 64, nginx_identity="old-nginx", effects=fx)
        self.assertEqual(fx.current, "foreign-nginx")
        self.assertNotIn("rollback", fx.calls)

    def test_tls_server_down_cannot_be_mistaken_for_unknown_sni_rejection(self):
        fx = FakeEffects(); fx.failure = "server-down-before-unknown"
        with self.assertRaises(cli.PrepareError):
            cli.transaction(before_manifest=retained(), expected_manifest_sha=cli.digest(cli.canonical(retained())),
                plan_sha="c" * 64, nginx_identity="old-nginx", effects=fx)
        self.assertNotIn("unknown-sni", fx.calls)
        self.assertEqual(fx.calls[-1], "rollback")

    def test_concrete_syntax_check_uses_pinned_image_snapshot_mounts_and_hardened_shape(self):
        effects = object.__new__(cli.RuntimeEffects)
        with tempfile.TemporaryDirectory() as directory:
            effects.build_root = Path(directory)
            effects.docker_prefix = ["/usr/bin/docker", "--host", "unix://rootless.sock"]
            effects.docker_env = {"PATH": "/usr/bin:/bin"}
            effects._manifest_before = {"containers": [{"service": "nginx",
                "image_ref": "nginx:1.27-alpine@sha256:" + "a" * 64}]}
            seen = {}
            def run(command, **kwargs):
                seen["command"] = command
                return subprocess.CompletedProcess(command, 0, "", "")
            with patch.object(cli, "_run", side_effect=run):
                effects.syntax_check()
            command = seen["command"]
            for required in ("--pull", "never", "--read-only", "--cap-drop", "ALL", "--cap-add", "CHOWN",
                "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777",
                "/var/cache/nginx:rw,nosuid,nodev,mode=0755", "backend:127.0.0.1", "frontend:127.0.0.1",
                "ubuntu-worker.tail63cd2d.ts.net:100.109.171.70"):
                self.assertIn(required, command)
            self.assertIn(f"type=bind,src={effects.build_root / 'scripts/staging/nginx-tls.conf'},dst=/etc/nginx/nginx.conf,readonly", command)

    def test_concrete_recreate_reacquires_nginx_id_even_when_command_throws(self):
        effects = object.__new__(cli.RuntimeEffects)
        effects._prior_nginx = effects._new_nginx_id = None
        effects.docker_prefix = ["docker", "--host", "unix://sock"]
        effects.compose = ["docker", "compose"]
        effects.build_root = Path("/snapshot")
        effects.docker_env = {}
        effects.env_fd = 9
        identities = iter(("d" * 64, "f" * 64))
        effects.nginx_identity = lambda: next(identities)
        with patch.object(cli, "_run", side_effect=cli.PrepareError("ambiguous response")):
            with self.assertRaises(cli.PrepareError):
                effects.recreate_nginx()
        self.assertEqual(effects._prior_nginx, "d" * 64)
        self.assertEqual(effects._new_nginx_id, "f" * 64)

    def test_concrete_owned_rollback_checks_restored_manifest_config_identity_and_http(self):
        expected_db = "postgres:16@sha256:" + "a" * 64
        before = retained_full(expected_db)
        effects = object.__new__(cli.RuntimeEffects)
        effects._manifest_before = before
        effects._new_nginx_id = "f" * 64
        effects._expected_db_image = expected_db
        effects.base_compose = ["docker", "compose", "-p", tls.PROJECT]
        effects.build_root = Path("/snapshot")
        effects.docker_env = {}
        effects.env_fd = 9
        effects.manifest = lambda: copy.deepcopy(before)
        effects.nginx_identity = lambda: next(row["id"] for row in before["containers"] if row["service"] == "nginx")
        effects.owns_nginx_change = lambda: True
        effects._http = lambda _url: 200
        seen = {}
        def run(command, **kwargs):
            seen["command"] = command
            return subprocess.CompletedProcess(command, 0, "", "")
        previous_guard = cli.GUARD
        cli.GUARD = types.SimpleNamespace(validate_retained_manifest=lambda manifest, prior, **kwargs:
            guard_real.validate_retained_manifest(manifest, prior, **kwargs))
        try:
            with patch.object(cli, "_run", side_effect=run):
                effects.restore_nginx()
        finally:
            cli.GUARD = previous_guard
        command = seen["command"]
        for item in ("--no-deps", "--no-build", "--pull", "never", "nginx"):
            self.assertIn(item, command)
        self.assertNotIn("--build", command)

    def test_unknown_and_absent_sni_must_raise_tls_alert_and_server_down_is_error(self):
        effects = object.__new__(cli.RuntimeEffects)
        calls = []
        class FakeContext:
            check_hostname = None
            verify_mode = None
            def wrap_socket(self, raw, *, server_hostname):
                calls.append(server_hostname)
                raise cli.ssl.SSLError("unrecognized_name")
        class Raw:
            def __enter__(self): return self
            def __exit__(self, *_args): pass
        with patch.object(cli.ssl, "SSLContext", return_value=FakeContext()), \
             patch.object(cli.socket, "create_connection", return_value=Raw()):
            effects.probe_unknown_sni()
        self.assertEqual(calls, ["unknown.invalid", None])
        with patch.object(cli.socket, "create_connection", side_effect=ConnectionRefusedError):
            with self.assertRaisesRegex(cli.PrepareError, "could not reach"):
                effects.probe_unknown_sni()

    def test_positive_tls_probe_connects_loopback_and_uses_custom_sni(self):
        effects = object.__new__(cli.RuntimeEffects)
        effects.build_root = Path("/snapshot")
        effects.certificate_sha256 = cli.digest(b"leaf cert")
        class Conn:
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def getpeercert(self, binary_form): return b"leaf cert"
        conn = Conn()
        # Use a real context manager wrapper around the socket mock.
        class Raw:
            def __enter__(self): return self
            def __exit__(self, *_args): pass
        context = types.SimpleNamespace(check_hostname=None,
            wrap_socket=lambda sock, *, server_hostname: (self.assertEqual(server_hostname, "stg-bl.hiroyuki9614.com") or conn))
        class Request:
            def __init__(self, *args, **kwargs): self.sock = None
            def request(self, *args, **kwargs): pass
            def getresponse(self): return types.SimpleNamespace(status=200, read=lambda _n: b"")
            def close(self): pass
        with patch.object(cli.ssl, "create_default_context", return_value=context), \
             patch.object(cli.socket, "create_connection", return_value=Raw()) as connect, \
             patch.object(cli.http.client, "HTTPSConnection", Request):
            effects.probe_https()
        connect.assert_called_once_with(("127.0.0.1", 18443), timeout=8)

    def test_stale_vcs_ref_is_never_rewritten_by_the_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / "i5.env"
            env.write_text("BELIB_STAGE_VCS_REF=81256268276d7ead95029849037f69ec6beec202\n", encoding="ascii")
            before = env.read_bytes()
            with patch.object(cli, "validate_identity"), patch.object(cli, "verify_cli_source"), patch.object(cli, "collect_plan_inputs",
                 side_effect=cli.PrepareError("mock readonly collector stop")), patch.object(cli, "_load_helpers"):
                self.assertEqual(cli.main(["plan", "--source-sha", "a" * 40]), 2)
            self.assertEqual(env.read_bytes(), before)

    def test_prepare_cli_requires_explicit_owner_ready_and_pinned_plan(self):
        with patch.object(cli, "validate_identity"), patch.object(cli, "verify_cli_source"), patch.object(cli, "_load_helpers"):
            self.assertEqual(cli.main(["prepare", "--source-sha", "a" * 40]), 2)

    def test_shipped_cli_routes_owner_ready_prepare_to_pinned_transaction(self):
        previous_guard = cli.GUARD
        cli.GUARD = types.SimpleNamespace(verify_python_runtime=lambda: None, reject_caller_environment=lambda _env: None)
        receipt = {"schema": "belib-staging-tls-prepare-receipt/v1", "prepared": True}
        raw = cli.create_plan("a" * 40, retained(), serve(), tls.EXPECTED_SERVE_ETAG,
                              tls.EXPECTED_CERT_SHA256, "c" * 64, now=int(time.time()))
        plan_sha = cli.digest(raw)
        with patch.object(cli, "validate_identity"), patch.object(cli, "verify_cli_source"), patch.object(cli, "_load_helpers"), \
             patch.object(cli, "_secure_read", return_value=raw) as read, \
             patch.object(cli, "prepare_live", return_value=receipt) as prepare_live:
            result = cli.main(["prepare", "--source-sha", "a" * 40, "--owner-ready",
                               "--plan-sha256", plan_sha, "--evidence-sha256", "d" * 64])
        cli.GUARD = previous_guard
        self.assertEqual(result, 0)
        read.assert_called_once_with(cli.PLAN_PATH)
        prepare_live.assert_called_once_with(raw, plan_sha, "d" * 64)

    def test_plan_path_matches_owner_packager(self):
        package = cli._module("owner_package_contract", Path(__file__).with_name("owner-command.py"))
        self.assertEqual(cli.PLAN_PATH, package.PLAN_PATH)

    def test_runtime_contract_keeps_old_schema_and_rejects_changed_base(self):
        seen = []
        def git(_root, _op, spec):
            seen.append(spec)
            return b"base"
        sg = types.SimpleNamespace(git=git)
        guard = types.SimpleNamespace(
            verify_resume_snapshot_compatibility=lambda _root, live, prior:
                ("db-image" if live == cli.APP812 and prior.startswith("6963103") else self.fail("wrong DB source")),
            load_source_tree_guard=lambda _root: sg)
        with patch.object(cli, "GUARD", guard):
            self.assertEqual(cli.verify_tls_runtime_contract(Path("/source"), "a" * 40), "db-image")
            self.assertFalse(any("prisma" in x for x in seen))
            sg.git = lambda _root, _op, spec: b"new" if spec.startswith("a" * 40) else b"old"
            with self.assertRaises(cli.PrepareError):
                cli.verify_tls_runtime_contract(Path("/source"), "a" * 40)

    def test_prepare_admits_before_actual_guard_seed_loading(self):
        raw = cli.create_plan("a" * 40, retained(), serve(), tls.EXPECTED_SERVE_ETAG,
                              tls.EXPECTED_CERT_SHA256, "c" * 64)
        with patch.object(cli, "GUARD", guard_real), \
             patch.object(guard_real, "ADMITTED_SOURCE_ROOT", None), \
             patch.object(guard_real, "_admission_source_root", return_value=Path("/fixed-admission")), \
             patch.object(guard_real.importlib.util, "spec_from_file_location", side_effect=RuntimeError("admitted module boundary")):
            with self.assertRaisesRegex(RuntimeError, "admitted module boundary"):
                cli.prepare_live(raw, cli.digest(raw), "d" * 64)
            self.assertEqual(guard_real.ADMITTED_SOURCE_ROOT, Path("/fixed-admission"))

    def test_shipped_cli_prepare_runs_live_orchestration_through_mocked_boundaries(self):
        expected_db = "postgres:16@sha256:" + "a" * 64
        baseline = retained_full(expected_db)
        state = {"mutated": False}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "admitted"
            staging = source / "scripts/staging"
            staging.mkdir(parents=True)
            for relative in ("compose.staging.yaml", "scripts/staging/tls-prepare.py", "scripts/staging/tls-ingress.py",
                "scripts/staging/owner-serve-cas.py", "scripts/staging/owner-command.py", "scripts/staging/nginx-default.conf",
                "scripts/staging/compose-tls.yaml", "scripts/staging/nginx-tls.conf",
                "scripts/staging/nginx-tls-default.conf", "scripts/staging/nginx-tls-locations.conf"):
                path = source / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("offline fixture\n")
            build_root, rollback_root = root / "build", root / "rollback"
            build_root.mkdir(); rollback_root.mkdir()
            class Seed:
                class SeedWrapperError(Exception): pass
                def validate_build_snapshot(self, *_args): pass
                def validate_staging_admission(self, *_args):
                    return os.open(os.devnull, os.O_RDONLY), {"BELIB_STAGE_PORT": "8083", "BELIB_STAGE_VCS_REF": cli.APP812}
            seed = Seed()
            class SourceGuard:
                def git(self, *_args): return b"same-base-blob"
                @contextlib.contextmanager
                def sha_derived_build_snapshot(self, _source, revision):
                    yield build_root if revision == "a" * 40 else rollback_root
            source_guard = SourceGuard()
            class Approval:
                def request_and_wait(self, source_sha, operation, evidence_sha, **kwargs):
                    self.call = (source_sha, operation, evidence_sha, kwargs)
                    return "d" * 64
            approval = Approval()
            class Serve:
                def get(self): return serve(), tls.EXPECTED_SERVE_ETAG
            guard = types.SimpleNamespace(
                EXPECTED_REPO_ROOT=root, LaunchBlocked=Exception, verify_python_runtime=lambda: None,
                reject_caller_environment=lambda _env: None, load_seed_module=lambda: seed,
                _admission_source_root=lambda _expected, _sha: source,
                load_source_tree_guard=lambda _root: source_guard,
                project_lock=lambda: contextlib.nullcontext(),
                verify_resume_snapshot_compatibility=lambda *_args: expected_db,
                build_retained_manifest=lambda *_args, **_kwargs: copy.deepcopy(
                    self._manifest_tls(baseline, build_root, state) if state["mutated"] else baseline),
                validate_retained_manifest=lambda actual, prior, **kwargs: guard_real.validate_retained_manifest(actual, prior, **kwargs),
                _docker_read=lambda *_args: json.dumps({"org.opencontainers.image.revision": cli.APP812}),
                load_approval_module=lambda: approval,
            )
            def command(command, **kwargs):
                if "ps" in command and "-aq" in command:
                    return subprocess.CompletedProcess(command, 0, ("f" if state["mutated"] else "d") * 64 + "\n", "")
                if "compose" in command and "up" in command:
                    state["mutated"] = True
                if command and command[0] == "/usr/bin/curl":
                    return subprocess.CompletedProcess(command, 0, "200", "")
                return subprocess.CompletedProcess(command, 0, "", "")
            previous_guard = cli.GUARD
            cli.GUARD = guard
            raw = cli.create_plan("a" * 40, baseline, serve(), tls.EXPECTED_SERVE_ETAG,
                tls.EXPECTED_CERT_SHA256, "c" * 64, now=int(time.time()))
            plan_sha = cli.digest(raw)
            with patch.object(cli, "validate_identity"), patch.object(cli, "verify_cli_source"), \
                 patch.object(cli, "_load_helpers"), patch.object(cli, "_secure_read", return_value=raw), \
                 patch.object(cli, "_read_only_rootless", return_value=({"PATH": "/usr/bin:/bin"}, "sock", ["/usr/bin/docker", "--host", "unix://sock"])), \
                 patch.object(cli, "_compose_model", return_value={}), \
                 patch.object(tls, "validate_tls_compose_overlay"), patch.object(tls, "TailscaleServeClient", return_value=Serve()), \
                 patch.object(tls, "inspect_certificate", return_value=tls.EXPECTED_CERT_SHA256), \
                 patch.object(cli, "bundle_digest", return_value="c" * 64), patch.object(cli, "_run", side_effect=command), \
                 patch.object(cli.RuntimeEffects, "probe_https"), patch.object(cli.RuntimeEffects, "probe_unknown_sni"):
                result = cli.main(["prepare", "--source-sha", "a" * 40, "--owner-ready", "--plan-sha256", plan_sha,
                                   "--evidence-sha256", "e" * 64])
            cli.GUARD = previous_guard
            self.assertEqual(result, 0)
            self.assertTrue(state["mutated"])
            self.assertEqual(approval.call[1], "tls-ingress")
            self.assertEqual(approval.call[3]["tls_binding"]["owner_plan_sha256"], plan_sha)


if __name__ == "__main__":
    unittest.main()
