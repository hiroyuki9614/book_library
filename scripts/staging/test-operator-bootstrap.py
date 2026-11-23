#!/usr/bin/env python3
"""Offline synthetic operator tests. No trust installation or host mutation."""
import base64
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack, nullcontext, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("operator_bootstrap", Path(__file__).with_name("operator-bootstrap.py"))
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)
LAUNCH_SPEC = importlib.util.spec_from_file_location("admitted_launcher", Path(__file__).with_name("launch-guard.py"))
launch = importlib.util.module_from_spec(LAUNCH_SPEC)
LAUNCH_SPEC.loader.exec_module(launch)


class OperatorBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="belib-operator-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        state = self.root / "operator-state"
        state.mkdir(mode=0o700)
        state_patch = patch.object(bootstrap, "STATE", state)
        state_patch.start()
        self.addCleanup(state_patch.stop)
        private = self.root / "private.pem"
        public = self.root / "public.pem"
        subprocess.run(["/usr/bin/openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)], check=True)
        subprocess.run(["/usr/bin/openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)], check=True)
        self.private = private
        self.key = public.read_bytes()
        self.patches = [patch.object(bootstrap, "PUBLIC_KEY", self.key),
                        patch.object(bootstrap, "PUBLIC_FINGERPRINT", hashlib.sha256(self.key).hexdigest())]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        now = int(time.time())
        self.grant = {**bootstrap.TARGET, "schema": "belib-staging-bootstrap-approval/v1",
                      "source_sha": "a" * 40, "operation": "deploy", "nonce": "b" * 64,
                      "evidence_sha256": "c" * 64, "issued_at": now, "expires_at": now + 300}

    def main_identity(self, argv):
        stack = ExitStack()
        stack.enter_context(patch.object(bootstrap, "INSTALL", Path(bootstrap.__file__).resolve()))
        stack.enter_context(patch.object(bootstrap.os, "getuid", return_value=0))
        stack.enter_context(patch.object(bootstrap.os, "geteuid", return_value=0))
        stack.enter_context(patch.object(bootstrap.socket, "gethostname", return_value="ubuntu-worker"))
        stack.enter_context(patch.object(bootstrap.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1000, pw_gid=1000)))
        stack.enter_context(patch.object(bootstrap.sys, "executable", "/usr/bin/python3"))
        flags = SimpleNamespace(**{
            **{name: getattr(sys.flags, name) for name in dir(sys.flags)
               if isinstance(getattr(sys.flags, name), (int, bool))},
            "isolated": True, "dont_write_bytecode": True,
        })
        stack.enter_context(patch.object(bootstrap.sys, "flags", flags))
        stack.enter_context(patch.object(bootstrap.sys, "argv", ["operator-bootstrap.py", *argv]))
        stack.enter_context(patch.object(bootstrap, "trusted_path"))
        return stack

    def signed(self, grant=None):
        raw = bootstrap.canonical(grant or self.grant)
        source, signature = self.root / "payload", self.root / "signature"
        source.write_bytes(raw)
        subprocess.run(["/usr/bin/openssl", "pkeyutl", "-sign", "-inkey", str(self.private), "-rawin",
                        "-in", str(source), "-out", str(signature)], check=True)
        return bootstrap.canonical({"payload": base64.b64encode(raw).decode(),
                                    "signature": base64.b64encode(signature.read_bytes()).decode()})

    def test_expected_retained_runtime_binds_exact_authority_pins(self):
        authorities = {name: {"issuer": name + ".example", "public_key_pem": "key-" + name,
                              "fingerprint": str(index) * 64}
                       for index, name in enumerate(("restore", "network", "roots"), start=1)}
        operation = "a" * 32
        config = {"authorities": authorities, "cutover_plan": {"operation_id": operation,
            "port": 18083,
            "old": {name: {"id": str(index) * 64} for index, name in
                enumerate(("backend", "frontend", "nginx"), start=1)},
            "new": {name: {"id": str(index + 3) * 64} for index, name in
                enumerate(("backend", "frontend", "nginx"), start=1)}},
            "candidate_database": {"id": "7" * 64}, "source_database": {}}
        expected = bootstrap.expected_retained_runtime(config)
        self.assertEqual(expected["authority_pins_sha256"],
                         hashlib.sha256(bootstrap.canonical(authorities)).hexdigest())
        upgrade_grant = {**self.grant, "operation": "upgrade-retained", "nonce": "d" * 64}
        with patch.object(bootstrap, "trusted_path"):
            bootstrap.consume_nonce(upgrade_grant)
            recorded = bootstrap.write_result(upgrade_grant, 0, retained_config=config)
            loaded = bootstrap.read_result(upgrade_grant["nonce"])
        self.assertEqual(loaded, recorded)
        self.assertEqual(loaded["expected_runtime"]["authority_pins_sha256"],
                         hashlib.sha256(bootstrap.canonical(authorities)).hexdigest())
        changed = {**authorities, "roots": {**authorities["roots"], "issuer": "other.example"}}
        self.assertNotEqual(expected["authority_pins_sha256"],
                            hashlib.sha256(bootstrap.canonical(changed)).hexdigest())
        with self.assertRaisesRegex(bootstrap.Blocked, "authority pins are malformed"):
            bootstrap.expected_retained_runtime({**config, "authorities": {"restore": authorities["restore"]}})

    def _completed_generation_fixture(self):
        operation = "d" * 32
        project = bootstrap.TARGET["project"] + "-upgrade-" + operation
        source_sha = "a" * 40
        services = {name: str(index) * 64 for index, name in enumerate(("db", "backend", "frontend", "nginx"), 1)}
        expected = {"project": project, "operation_id": operation, "services": services,
            "authority_pins_sha256": "e" * 64, "port": 18083,
            "retained": {"volumes": {"books": bootstrap.TARGET["project"] + "_staging-book-files"}},
            "plan": {"candidate_database": {"id": services["db"],
                    "configuration_sha256": "1" * 64, "volume_name": project + "_staging-postgres-data",
                    "network_id": "2" * 64},
                "cutover_plan": {"new": {
                    "backend": {"configuration_sha256": "3" * 64,
                                "network_ids": {project + "_isolated": "2" * 64}},
                    "frontend": {"configuration_sha256": "4" * 64,
                                 "network_ids": {project + "_isolated": "2" * 64}}}}}}
        upgrade = {"nonce": "b" * 64, "source_sha": source_sha, "operation": "upgrade-retained",
            "exit_code": 0, "expected_runtime": expected, "host": bootstrap.TARGET["host"],
            "principal": bootstrap.TARGET["principal"], "uid": bootstrap.TARGET["uid"],
            "project": bootstrap.TARGET["project"], "nonce_consumed": True}
        completion = {"schema": bootstrap.TLS_COMPLETION_SCHEMA, "owner_nonce": "c" * 64, "upgrade_receipt_nonce": upgrade["nonce"],
            "upgrade_receipt_sha256": hashlib.sha256(bootstrap.canonical(upgrade)).hexdigest(),
            "source_sha": source_sha, "operation_id": operation, "project": project,
            "previous_nginx_id": services["nginx"], "nginx_id": "5" * 64,
            "nginx_configuration_sha256": "6" * 64, "nginx_image_id": "sha256:" + "7" * 64,
            "nginx_network_ids": {project + "_isolated": "2" * 64, project + "_ingress": "8" * 64},
            "certificate_sha256": "9" * 64, "serve_config_sha256": "a" * 64,
            "serve_etag": "b" * 64}
        live_services = {**services, "nginx": completion["nginx_id"]}
        observed = {"container_ids": {"candidate": live_services},
            "image_digests": {"candidate": {"db": "sha256:" + "1" * 64,
                "backend": "sha256:" + "2" * 64, "frontend": "sha256:" + "3" * 64,
                "nginx": completion["nginx_image_id"]}},
            "app_image_revisions": {"backend": source_sha, "frontend": source_sha}}
        return upgrade, completion, {"schema": "belib-active-generation-observation/v1",
            "upgrade_receipt": upgrade, "tls_completion": completion, "expected_runtime": expected,
            "runtime_readback":{"status":"verified","source_sha":source_sha,"operation_id":operation,
                "project":project,"completion":completion,"observed_runtime":observed},
            "observed_runtime": observed,
            "loopback_health": {"url": f"http://127.0.0.1:{expected['port']}/health", "http_status": 200},
            "tls_ingress": {"status": 200, "certificate_sha256": completion["certificate_sha256"]},
            "staging_env_sha256": "a" * 64}

    def test_completed_generation_promotion_is_linked_and_exact_retry_only(self):
        upgrade, completion, observation = self._completed_generation_fixture()
        observed_writes = []
        with patch.object(bootstrap, "read_result", return_value=upgrade), \
                patch.object(bootstrap, "read_tls_receipt", return_value=completion), \
                patch.object(bootstrap, "_inspect_completed_generation", return_value=observation), \
                patch.object(bootstrap, "_read_active_generation", return_value=None), \
                patch.object(bootstrap, "_atomic_active_pointer", side_effect=observed_writes.append), \
                patch.object(bootstrap, "trusted_path"):
            generation = bootstrap._promote_completed(upgrade["nonce"], completion["owner_nonce"], self.root)
        self.assertEqual(observed_writes, [generation])
        self.assertEqual(generation["services"]["nginx"]["id"], completion["nginx_id"])
        self.assertEqual(generation["database_volume"], upgrade["expected_runtime"]["plan"]["candidate_database"]["volume_name"])
        self.assertEqual(generation["shared_books_volume"], bootstrap.TARGET["project"] + "_staging-book-files")
        bootstrap.validate_active_generation(generation)
        with patch.object(bootstrap, "read_result", return_value=upgrade), \
                patch.object(bootstrap, "read_tls_receipt", return_value=completion), \
                patch.object(bootstrap, "_inspect_completed_generation", return_value=observation), \
                patch.object(bootstrap, "_read_active_generation", return_value=generation), \
                patch.object(bootstrap, "_atomic_active_pointer") as write_pointer, \
                patch.object(bootstrap, "trusted_path"):
            self.assertEqual(bootstrap._promote_completed(upgrade["nonce"], completion["owner_nonce"], self.root), generation)
        write_pointer.assert_not_called()
        drifted = {**observation, "observed_runtime": {**observation["observed_runtime"],
                                                        "image_digests": {"candidate": {}}}}
        drifted["runtime_readback"] = {**observation["runtime_readback"],
            "observed_runtime":drifted["observed_runtime"]}
        with patch.object(bootstrap, "read_result", return_value=upgrade), \
                patch.object(bootstrap, "read_tls_receipt", return_value=completion), \
                patch.object(bootstrap, "_inspect_completed_generation", return_value=drifted), \
                self.assertRaisesRegex(bootstrap.Blocked, "runtime IDs or source revisions"):
            bootstrap._promote_completed(upgrade["nonce"], completion["owner_nonce"], self.root)

    def test_initial_promotion_recovers_exact_pair_after_journal_before_pointer(self):
        upgrade, completion, observation = self._completed_generation_fixture()
        real_lstat = Path.lstat
        state = self.root / "operator-state"
        def root_lstat(path):
            info = real_lstat(path)
            try:
                path.relative_to(state)
            except ValueError:
                return info
            return SimpleNamespace(st_uid=0, st_mode=info.st_mode, st_nlink=info.st_nlink,
                st_size=info.st_size, st_dev=info.st_dev, st_ino=info.st_ino,
                st_mtime_ns=info.st_mtime_ns, st_ctime_ns=info.st_ctime_ns)
        with patch.object(Path, "lstat", root_lstat), \
                patch.object(bootstrap, "read_result", return_value=upgrade), \
                patch.object(bootstrap, "read_tls_receipt", return_value=completion), \
                patch.object(bootstrap, "_inspect_completed_generation", return_value=observation), \
                patch.object(bootstrap, "trusted_path"):
            atomic = bootstrap._atomic_active_pointer
            attempts = 0
            def interrupt_once(value):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise OSError("interrupted after journal")
                return atomic(value)
            with patch.object(bootstrap, "_atomic_active_pointer", side_effect=interrupt_once):
                with self.assertRaisesRegex(OSError, "interrupted after journal"):
                    bootstrap._promote_completed(upgrade["nonce"], completion["owner_nonce"], self.root)
                self.assertFalse((state / "active-generation.json").exists())
                generation = bootstrap._promote_completed(upgrade["nonce"], completion["owner_nonce"], self.root)
            self.assertEqual(bootstrap._read_active_generation(), generation)

    def test_v2_promotion_advances_only_the_exact_pointer_predecessor(self):
        first_upgrade, first_tls, first_observation=self._completed_generation_fixture()
        with patch.object(bootstrap,"read_result",return_value=first_upgrade), \
             patch.object(bootstrap,"read_tls_receipt",return_value=first_tls), \
             patch.object(bootstrap,"_inspect_completed_generation",return_value=first_observation), \
             patch.object(bootstrap,"_read_active_generation",return_value=None), \
             patch.object(bootstrap,"_atomic_active_pointer"), patch.object(bootstrap,"trusted_path"):
            predecessor=bootstrap._promote_completed(first_upgrade["nonce"],first_tls["owner_nonce"],self.root)
        predecessor_sha=hashlib.sha256(bootstrap.canonical(predecessor)).hexdigest()
        upgrade,tls,observation=self._completed_generation_fixture()
        upgrade={**upgrade,"nonce":"1"*64,"source_sha":"f"*40}
        plan=upgrade["expected_runtime"]["plan"]
        plan["cutover_plan"].update(schema="belib-isolated-copy-cutover/v2",prior_project=predecessor["project"],
            prior_application_sha=predecessor["source_sha"],prior_generation_sha256=predecessor_sha,
            prior_http_port=predecessor["http_port"],prior_tls_loopback_port=predecessor["tls_loopback_port"],
            source_sha=upgrade["source_sha"])
        plan["environment_transition"]={"prior_sha256":"a"*64,"candidate_sha256":"b"*64,
            "prior_source_sha":predecessor["source_sha"],"candidate_source_sha":upgrade["source_sha"]}
        upgrade["expected_runtime"].update(prior_project=predecessor["project"],
            prior_application_sha=predecessor["source_sha"],prior_generation_sha256=predecessor_sha)
        upgrade["expected_runtime"]["prior_tls_completion"]={
            "owner_nonce":first_tls["owner_nonce"],
            "completion_sha256":hashlib.sha256(bootstrap.canonical(first_tls)).hexdigest(),
            "serve_config_sha256":first_tls["serve_config_sha256"],
            "serve_etag":first_tls["serve_etag"],
            "certificate_sha256":first_tls["certificate_sha256"]}
        tls={**tls,"owner_nonce":"2"*64,"source_sha":upgrade["source_sha"],
            "schema":bootstrap.TLS_COMPLETION_SCHEMA_V2,
            "prior_generation_sha256":predecessor_sha,
            "prior_tls_owner_nonce":first_tls["owner_nonce"],
            "prior_tls_completion_sha256":hashlib.sha256(bootstrap.canonical(first_tls)).hexdigest(),
            "upgrade_receipt_nonce":upgrade["nonce"],"operation_id":upgrade["expected_runtime"]["operation_id"],
            "project":upgrade["expected_runtime"]["project"],"previous_nginx_id":upgrade["expected_runtime"]["services"]["nginx"],
            "upgrade_receipt_sha256":hashlib.sha256(bootstrap.canonical(upgrade)).hexdigest()}
        observation={**observation,"upgrade_receipt":upgrade,"tls_completion":tls,
            "expected_runtime":upgrade["expected_runtime"],"staging_env_sha256":"b"*64,
            "observed_runtime":{**observation["observed_runtime"],"app_image_revisions":{
                "backend":upgrade["source_sha"],"frontend":upgrade["source_sha"]}}}
        observation["runtime_readback"]={**observation["runtime_readback"],"source_sha":upgrade["source_sha"],
            "operation_id":upgrade["expected_runtime"]["operation_id"],
            "project":upgrade["expected_runtime"]["project"],"completion":tls,
            "observed_runtime":observation["observed_runtime"]}
        written=[]
        def read_active(**_kwargs): return predecessor
        with patch.object(bootstrap,"read_result",return_value=upgrade), \
             patch.object(bootstrap,"read_tls_receipt",return_value=tls), \
             patch.object(bootstrap,"_inspect_completed_generation",return_value=observation), \
             patch.object(bootstrap,"_read_active_generation",side_effect=read_active), \
             patch.object(bootstrap,"_atomic_active_pointer",side_effect=written.append), patch.object(bootstrap,"trusted_path"):
            generation=bootstrap._promote_completed(upgrade["nonce"],tls["owner_nonce"],self.root)
        self.assertEqual(generation["schema"],"belib-active-generation/v2")
        self.assertEqual(generation["previous_generation_sha256"],predecessor_sha)
        self.assertEqual(generation["genesis_generation_sha256"],predecessor_sha)
        self.assertEqual(generation["staging_env_sha256"],"b"*64)
        self.assertEqual(written,[generation])
        with patch.object(bootstrap,"trusted_path"):
            bootstrap._atomic_active_pointer(generation)
        self.assertEqual(json.loads((bootstrap.STATE/"active-generation.json").read_text()),generation)
        with patch.object(bootstrap,"read_result",return_value=upgrade), \
             patch.object(bootstrap,"read_tls_receipt",return_value=tls), \
             patch.object(bootstrap,"_inspect_completed_generation",return_value=observation), \
             patch.object(bootstrap,"_read_active_generation",return_value=generation), \
             patch.object(bootstrap,"_atomic_active_pointer") as pointer, patch.object(bootstrap,"trusted_path"):
            self.assertEqual(bootstrap._promote_completed(upgrade["nonce"],tls["owner_nonce"],self.root),generation)
        pointer.assert_not_called()
        stale_upgrade={**upgrade,"expected_runtime":{**upgrade["expected_runtime"],
            "prior_generation_sha256":"0"*64}}
        stale_tls={**tls,"upgrade_receipt_sha256":hashlib.sha256(bootstrap.canonical(stale_upgrade)).hexdigest()}
        stale_observation={**observation,"upgrade_receipt":stale_upgrade,"tls_completion":stale_tls,
            "expected_runtime":stale_upgrade["expected_runtime"]}
        stale_observation["runtime_readback"]={**observation["runtime_readback"],"completion":stale_tls}
        with patch.object(bootstrap,"read_result",return_value=stale_upgrade), \
             patch.object(bootstrap,"read_tls_receipt",return_value=stale_tls), \
             patch.object(bootstrap,"_inspect_completed_generation",return_value=stale_observation), \
             patch.object(bootstrap,"_read_active_generation",return_value=generation), \
             self.assertRaisesRegex(bootstrap.Blocked,"current active predecessor"):
            bootstrap._promote_completed(stale_upgrade["nonce"],stale_tls["owner_nonce"],self.root)

    def test_active_generation_malformed_pointer_cannot_be_treated_as_absent(self):
        pointer = self.root / "operator-state" / "active-generation.json"
        pointer.write_text("not canonical json")
        pointer.chmod(0o600)
        with patch.object(bootstrap, "trusted_path"):
            with self.assertRaises(bootstrap.Blocked):
                bootstrap._read_active_generation()

    def test_missing_pointer_with_durable_generation_journal_cannot_fall_back_to_v1(self):
        state = self.root / "operator-state"
        journal = state / ("generation-" + "a" * 64 + "-" + "b" * 64 + ".json")
        journal.write_text("{}")
        journal.chmod(0o600)
        with patch.object(bootstrap, "trusted_path"):
            with self.assertRaisesRegex(bootstrap.Blocked, "missing beside a durable promotion journal"):
                bootstrap._read_active_generation()
            self.assertIsNone(bootstrap._read_active_generation(allow_journaled_initial_promotion=True))

    def test_generation_journal_publication_is_atomic_no_replace_with_single_link(self):
        state = self.root / "operator-state"
        journal = state / ("generation-" + "a" * 64 + "-" + "b" * 64 + ".json")
        raw = b'{"generation":"fixture"}'
        with patch.object(bootstrap, "trusted_path"):
            bootstrap._write_immutable(journal, raw)
        self.assertEqual(journal.read_bytes(), raw)
        self.assertEqual(journal.stat().st_nlink, 1)
        self.assertEqual(list(state.glob(".generation.*.pending")), [])

    def test_active_pointer_symlink_mode_and_link_count_are_rejected(self):
        state = self.root / "operator-state"
        pointer = state / "active-generation.json"
        target = state / "target.json"
        target.write_bytes(b"{}")
        target.chmod(0o600)
        real_lstat = Path.lstat
        with patch.object(bootstrap, "trusted_path"):
            pointer.symlink_to(target)
            with self.assertRaises((OSError, bootstrap.Blocked)):
                bootstrap._read_active_generation()
            pointer.unlink()

            pointer.write_bytes(b"{}")
            pointer.chmod(0o644)
            def root_lstat(path):
                info = real_lstat(path)
                if path == pointer:
                    return SimpleNamespace(st_uid=0, st_mode=info.st_mode, st_nlink=info.st_nlink)
                return info
            with patch.object(Path, "lstat", root_lstat):
                with self.assertRaisesRegex(bootstrap.Blocked, "metadata is unsafe"):
                    bootstrap._read_active_generation()
            pointer.unlink()

            pointer.write_bytes(b"{}")
            pointer.chmod(0o600)
            hardlink = state / "active-generation-copy.json"
            os.link(pointer, hardlink)
            with self.assertRaisesRegex(bootstrap.Blocked, "regular file"):
                bootstrap._read_active_generation()

    def test_valid_external_signature_derives_only_fixed_launcher_arguments(self):
        with patch.object(bootstrap, "trusted_path"):
            grant = bootstrap.validate_grant(self.signed(), "a" * 40)
        self.assertEqual(grant, self.grant)
        self.assertEqual(bootstrap.launcher_arguments(grant), ["--source-sha", "a" * 40,
            "--evidence-sha256", "c" * 64, "--env-file", "/home/hiroyuki/.config/belib-staging/i5.env", "deploy"])

    def test_v2_upgrade_grant_binds_prior_generation_and_is_upgrade_only(self):
        grant={**self.grant,"schema":"belib-staging-bootstrap-approval/v2","operation":"upgrade-retained",
            "prior_application_sha":"d"*40,"prior_generation_sha256":"e"*64,
            "retained_state_sha256":"1"*64,"backup_sha256":"2"*64,"restore_attestation_sha256":"3"*64}
        with patch.object(bootstrap,"trusted_path"):
            validated=bootstrap.validate_grant(self.signed(grant),grant["source_sha"])
        arguments=bootstrap.launcher_arguments(validated)
        self.assertIn("--prior-generation-sha256",arguments)
        self.assertEqual(arguments[arguments.index("--prior-generation-sha256")+1],"e"*64)
        with patch.object(bootstrap,"trusted_path"), self.assertRaisesRegex(bootstrap.Blocked,"restricted"):
            bootstrap.validate_grant(self.signed({**grant,"operation":"deploy"}),grant["source_sha"])

    def test_v2_retained_config_must_match_all_active_database_and_application_pins(self):
        project=bootstrap.TARGET["project"]+"-upgrade-"+"a"*32
        services={}
        for index,name in enumerate(("db","backend","frontend","nginx"),1):
            networks={project+"_isolated":str(index)*64}
            if name=="nginx": networks[project+"_ingress"]="9"*64
            services[name]={"id":str(index+1)*64,"configuration_sha256":str(index+2)*64,
                "image_id":"sha256:"+str(index+3)*64,"network_ids":networks}
        active={"project":project,"source_sha":"b"*40,"http_port":18123,"tls_loopback_port":18443,
            "database_volume":project+"_staging-postgres-data","services":services}
        predecessor=hashlib.sha256(bootstrap.canonical(active)).hexdigest()
        old={name:{"id":services[name]["id"],"configuration_sha256":services[name]["configuration_sha256"],
                   "network_ids":services[name]["network_ids"],"image_id":services[name]["image_id"]}
             for name in ("backend","frontend","nginx")}
        config={"prior_project":project,"prior_generation_sha256":predecessor,
            "source_database":{"id":services["db"]["id"],
                "configuration_sha256":services["db"]["configuration_sha256"],
                "network_ids":services["db"]["network_ids"],"volume_name":active["database_volume"],
                "image_id":services["db"]["image_id"]},
            "cutover_plan":{"schema":"belib-isolated-copy-cutover/v2","prior_project":project,
                "prior_application_sha":active["source_sha"],"prior_generation_sha256":predecessor,
                "prior_http_port":active["http_port"],"prior_tls_loopback_port":active["tls_loopback_port"],
                "old":old}}
        bootstrap.validate_generation_predecessor(active,config)
        changed={**config,"cutover_plan":{**config["cutover_plan"],"old":{
            **old,"backend":{**old["backend"],"image_id":"sha256:"+"f"*64}}}}
        with self.assertRaisesRegex(bootstrap.Blocked,"application pins differ"):
            bootstrap.validate_generation_predecessor(active,changed)
        changed={**config,"source_database":{**config["source_database"],"volume_name":bootstrap.TARGET["project"]+"_staging-postgres-data"}}
        with self.assertRaisesRegex(bootstrap.Blocked,"exact active predecessor"):
            bootstrap.validate_generation_predecessor(active,changed)

    def test_forged_wrong_target_expired_and_unknown_operation_fail(self):
        for changes in ({"uid": 0}, {"host": "other"}, {"source_sha": "d" * 40},
                        {"operation": "arbitrary-shell"}, {"expires_at": int(time.time())}, {"uid": True}):
            with self.subTest(changes=changes), patch.object(bootstrap, "trusted_path"), self.assertRaises(bootstrap.Blocked):
                bootstrap.validate_grant(self.signed({**self.grant, **changes}), "a" * 40)
        value = json.loads(self.signed())
        value["signature"] = base64.b64encode(b"x" * 64).decode()
        with patch.object(bootstrap, "trusted_path"), self.assertRaises(bootstrap.Blocked):
            bootstrap.validate_grant(bootstrap.canonical(value), "a" * 40)

    def test_worker_source_access_probe_uses_fixed_isolated_uid_drop(self):
        with patch.object(bootstrap.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
            bootstrap.verify_staging_source_access(self.root, "scripts/staging/launch-guard.py")
        argv = run.call_args.args[0]
        kwargs = run.call_args.kwargs
        self.assertEqual(argv[:3], ["/usr/bin/python3", "-I", "-B"])
        self.assertEqual(argv[3], "-c")
        self.assertIn("os.open", argv[4])
        self.assertEqual(argv[5:], [str(self.root), "scripts/staging/launch-guard.py"])
        self.assertEqual(kwargs["user"], 1000)
        self.assertEqual(kwargs["group"], 1000)
        self.assertEqual(kwargs["extra_groups"], ())
        self.assertEqual(kwargs["env"], {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C"})
        self.assertEqual(kwargs["timeout"], 5)
        self.assertFalse(kwargs["check"])
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)

    def test_worker_source_access_probe_denial_is_generic_and_fixed(self):
        with patch.object(bootstrap.subprocess, "run", return_value=SimpleNamespace(returncode=13)) as run:
            with self.assertRaisesRegex(bootstrap.Blocked, "not readable by worker UID/GID 1000"):
                bootstrap.verify_staging_source_access(self.root, "scripts/staging/prepare-retained.py")
        self.assertEqual(run.call_args.args[0][-1], "scripts/staging/prepare-retained.py")
        with self.assertRaisesRegex(bootstrap.Blocked, "unexpected fixed entry"):
            bootstrap.verify_staging_source_access(self.root, "../../private.env")

    @unittest.skipUnless(os.geteuid() == 0, "real UID/GID drop requires root; run in disposable rootless Docker fixture")
    def test_real_worker_access_probe_rejects_private_source_parent_then_allows_traversable_parent(self):
        os.chmod(self.root, 0o755)
        source = self.root / "source"
        source.mkdir(mode=0o700)
        entry = source / "scripts/staging/launch-guard.py"
        entry.parent.mkdir(parents=True)
        entry.parent.parent.chmod(0o755)
        entry.parent.chmod(0o755)
        entry.write_text("# fixed fixture\n")
        entry.chmod(0o644)
        prepared = source / "scripts/staging/prepare-retained.py"
        prepared.write_text("# fixed fixture\n")
        prepared.chmod(0o644)
        with self.assertRaisesRegex(bootstrap.Blocked, "not readable by worker"):
            bootstrap.verify_staging_source_access(source, "scripts/staging/launch-guard.py")
        source.chmod(0o711)
        bootstrap.verify_staging_source_access(source, "scripts/staging/launch-guard.py")
        bootstrap.verify_staging_source_access(source, "scripts/staging/prepare-retained.py")

    def test_nonce_is_durable_one_use(self):
        state = self.root / "state"
        state.mkdir(mode=0o700)
        with patch.object(bootstrap, "STATE", state), patch.object(bootstrap, "trusted_path"):
            bootstrap.consume_nonce(self.grant)
            self.assertTrue((state / (self.grant["nonce"] + ".used")).is_file())
            # Same signed nonce can resume its journal only until a durable result exists.
            real_lstat=Path.lstat
            def root_lstat(path):
                info=real_lstat(path)
                try: path.relative_to(state)
                except ValueError: return info
                return SimpleNamespace(st_uid=0,st_mode=info.st_mode,st_nlink=info.st_nlink,
                    st_size=info.st_size,st_dev=info.st_dev,st_ino=info.st_ino,st_mtime_ns=info.st_mtime_ns,st_ctime_ns=info.st_ctime_ns)
            with patch.object(Path,"lstat",root_lstat), \
                    patch.object(bootstrap,"read_regular",return_value=bootstrap.canonical({
                        "grant_sha256":hashlib.sha256(bootstrap.canonical(self.grant)).hexdigest(),
                        "source_sha":self.grant["source_sha"]})):
                bootstrap.consume_nonce(self.grant)
                bootstrap.write_result(self.grant,0)
                with self.assertRaisesRegex(bootstrap.Blocked,"durable result"):
                    bootstrap.consume_nonce(self.grant)

    def test_result_is_durable_exact_grant_bound_and_immutable(self):
        with patch.object(bootstrap, "trusted_path"):
            bootstrap.consume_nonce(self.grant)
            result=bootstrap.write_result(self.grant,0)
            self.assertEqual(result,bootstrap.read_result(self.grant['nonce']))
            self.assertEqual(result['grant_sha256'],hashlib.sha256(bootstrap.canonical(self.grant)).hexdigest())
            self.assertEqual(result['exit_code'],0)
            with self.assertRaises(FileExistsError):
                bootstrap.write_result(self.grant,1)

    def test_result_records_failure_and_rejects_invented_or_tampered_identity(self):
        with patch.object(bootstrap, "trusted_path"):
            bootstrap.consume_nonce(self.grant)
            result=bootstrap.write_result(self.grant,124)
            self.assertEqual(bootstrap.read_result(self.grant['nonce'])['exit_code'],124)
            path=bootstrap._result_path(self.grant['nonce'])
            path.write_bytes(bootstrap.canonical({**result,'host':'other'}))
            with self.assertRaises(bootstrap.Blocked):
                bootstrap.read_result(self.grant['nonce'])
            with self.assertRaises(FileNotFoundError):
                bootstrap.read_result('d'*64)

    def test_tls_receipt_is_exact_fixed_root_query_and_rejects_tampering(self):
        owner_nonce = "1" * 64
        operation_id = "2" * 32
        project = bootstrap.TARGET["project"] + "-upgrade-" + operation_id
        record = {
            "schema": bootstrap.TLS_COMPLETION_SCHEMA, "owner_nonce": owner_nonce,
            "plan_sha256": "3" * 64, "prepare_receipt_sha256": "4" * 64,
            "upgrade_receipt_nonce": "5" * 64, "upgrade_receipt_sha256": "6" * 64,
            "source_sha": "7" * 40, "operation_id": operation_id, "project": project,
            "previous_nginx_id": "8" * 64, "nginx_id": "9" * 64,
            "nginx_configuration_sha256": "a" * 64,
            "nginx_image_id": "sha256:" + "9" * 64,
            "nginx_network_ids": {project + "_isolated": "b" * 64, project + "_ingress": "c" * 64},
            "tls_config_sha256": "d" * 64, "certificate_sha256": "e" * 64,
            "unchanged_runtime_sha256": "f" * 64, "approval_nonce": "0" * 64,
            "evidence_sha256": "1" * 64, "serve_config_sha256": "2" * 64,
            "serve_etag": "opaque-etag", "completed_at": 123, "status": "complete",
        }
        raw = bootstrap.canonical(record)
        fake_info = type("Info", (), {"st_uid": 0, "st_nlink": 1,
                                      "st_mode": stat.S_IFREG | 0o600})()
        directory_info = type("Info", (), {"st_uid": 0, "st_nlink": 1,
                                           "st_mode": stat.S_IFDIR | 0o700})()
        with patch.object(bootstrap, "TLS_COMPLETIONS", Path("/fixed/tls-serve")), \
                patch.object(bootstrap, "trusted_path"), \
                patch.object(bootstrap, "read_regular", return_value=raw), \
                patch.object(Path, "stat", return_value=directory_info), \
                patch.object(Path, "lstat", return_value=fake_info):
            self.assertEqual(bootstrap.read_tls_receipt(owner_nonce), record)
            with self.assertRaises(bootstrap.Blocked):
                bootstrap.read_tls_receipt("../" + owner_nonce)
        for changes in ({"owner_nonce": "2" * 64}, {"status": "pending"},
                        {"operation_id": "invalid"}, {"nginx_network_ids": {}},
                        {"completed_at": True}):
            with self.subTest(changes=changes):
                damaged = bootstrap.canonical({**record, **changes})
                with patch.object(bootstrap, "TLS_COMPLETIONS", Path("/fixed/tls-serve")), \
                        patch.object(bootstrap, "trusted_path"), \
                        patch.object(bootstrap, "read_regular", return_value=damaged), \
                        patch.object(Path, "stat", return_value=directory_info), \
                        patch.object(Path, "lstat", return_value=fake_info), \
                        self.assertRaises(bootstrap.Blocked):
                    bootstrap.read_tls_receipt(owner_nonce)

    def test_hostile_tmpdir_cannot_replace_openssl_inputs(self):
        hostile = self.root / "attacker-parent"
        hostile.mkdir()
        # This envelope is invalid for the pinned key. A parent-directory owner
        # could replace all three OpenSSL inputs if verification used TMPDIR.
        envelope = json.loads(self.signed())
        envelope["signature"] = base64.b64encode(b"x" * 64).decode()
        valid = json.loads(self.signed())
        original_run = subprocess.run
        attempted = []
        parents = []
        def replace_under_hostile_parent(argv, **kwargs):
            key = Path(argv[argv.index("-inkey") + 1])
            parents.append(key.parent.parent)
            if key.parent.parent == hostile:
                attempted.append(True)
                moved = key.parent.with_name(key.parent.name + "-renamed")
                key.parent.rename(moved)
                key.parent.mkdir()
                key.write_bytes(self.key)
                (key.parent / "payload").write_bytes(base64.b64decode(valid["payload"]))
                (key.parent / "signature").write_bytes(base64.b64decode(valid["signature"]))
            return original_run(argv, **kwargs)
        with patch.dict(os.environ, {"TMPDIR": str(hostile)}), \
                patch.object(tempfile, "tempdir", str(hostile)), \
                patch.object(bootstrap, "trusted_path"), \
                patch.object(bootstrap.subprocess, "run", side_effect=replace_under_hostile_parent), \
                self.assertRaisesRegex(bootstrap.Blocked, "signature is invalid"):
            bootstrap.validate_grant(bootstrap.canonical(envelope), "a" * 40)
        self.assertEqual(attempted, [])
        self.assertEqual(parents, [bootstrap.STATE])

    def test_source_manifest_checks_real_git_revision_and_complete_bytes(self):
        sources = self.root / "sources"
        sources.mkdir()
        checkout = sources / "initial"
        checkout.mkdir()
        (checkout / "entry.py").write_text("print('fixed-source')\n")
        environment = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        def git(*args):
            return subprocess.run(["/usr/bin/git", "-C", str(checkout), *args], env=environment,
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()
        git("init", "-q")
        git("add", "entry.py")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
        sha = git("rev-parse", "HEAD")
        target = sources / sha
        checkout.rename(target)
        manifest = {"entry.py": {"sha256": hashlib.sha256((target / "entry.py").read_bytes()).hexdigest(),
                                  "mode": stat.S_IMODE((target / "entry.py").stat().st_mode)}}
        config = {"schema": "belib-staging-operator/v1", "enabled": True, "source_sha": sha, "manifest": manifest}
        with patch.object(bootstrap, "SOURCES", sources), patch.object(bootstrap, "trusted_path"):
            self.assertEqual(bootstrap.verify_source(config), target)
            (target / "entry.py").write_text("print('changed')\n")
            with self.assertRaisesRegex(bootstrap.Blocked, "manifest"):
                bootstrap.verify_source(config)
            with self.assertRaises(bootstrap.Blocked):
                bootstrap.verify_source({**config, "enabled": False})

    def test_invalid_signed_grant_reaches_no_effect_or_process(self):
        raw = self.signed({**self.grant, "host": "production"})
        grant_path = self.root / "grant.json"
        grant_path.write_bytes(raw)
        config_path = self.root / "config.json"
        config_path.write_bytes(bootstrap.canonical({"source_sha": "a" * 40}))
        consume, spawn = Mock(), Mock()
        with patch.object(bootstrap, "CONFIG", config_path), patch.object(bootstrap, "INSTALL", Path(bootstrap.__file__).resolve()), \
                patch.object(bootstrap.os, "getuid", return_value=0), patch.object(bootstrap.os, "geteuid", return_value=0), \
                patch.object(bootstrap.socket, "gethostname", return_value="ubuntu-worker"), \
                patch.object(bootstrap.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=1000, pw_gid=1000)), \
                patch.object(bootstrap.sys, "executable", "/usr/bin/python3"), \
                patch.object(bootstrap.sys, "flags", SimpleNamespace(**{
                    **{name: getattr(sys.flags, name) for name in dir(sys.flags) if isinstance(getattr(sys.flags, name), (int, bool))},
                    "isolated": True, "dont_write_bytecode": True})), \
                patch.object(bootstrap.sys, "argv", ["operator-bootstrap.py", "--grant", str(grant_path)]), \
                patch.object(bootstrap, "trusted_path"), patch.object(bootstrap, "verify_source", return_value=self.root), \
                patch.object(bootstrap, "consume_nonce", consume), patch.object(bootstrap, "spawn_admitted", spawn), \
                redirect_stderr(io.StringIO()):
            self.assertEqual(bootstrap.main(), 1)
        consume.assert_not_called()
        spawn.assert_not_called()

    def test_denied_worker_access_preserves_grant_nonce_spawn_and_result(self):
        grant_path = self.root / "grant.json"
        grant_path.write_bytes(self.signed())
        config_path = self.root / "config.json"
        config_path.write_bytes(bootstrap.canonical({
            "schema": "belib-staging-operator/v1", "enabled": True,
            "source_sha": "a" * 40, "manifest": {"fixture.py": {"sha256": "d" * 64, "mode": 0o644}},
        }))
        consume, spawn, write = Mock(), Mock(), Mock()
        with patch.object(bootstrap, "CONFIG", config_path), \
                patch.object(bootstrap, "verify_source", return_value=self.root), \
                patch.object(bootstrap, "verify_staging_source_access",
                             side_effect=bootstrap.Blocked("Staging source is not readable by worker UID/GID 1000; verify source directory traversal and fixed entry permissions")), \
                patch.object(bootstrap, "consume_nonce", consume), \
                patch.object(bootstrap, "spawn_admitted", spawn), \
                patch.object(bootstrap, "write_result", write), \
                redirect_stderr(io.StringIO()), self.main_identity(["--grant", str(grant_path)]):
            self.assertEqual(bootstrap.main(), 1)
        consume.assert_not_called()
        spawn.assert_not_called()
        write.assert_not_called()
        self.assertFalse((bootstrap.STATE / (self.grant["nonce"] + ".used")).exists())
        self.assertFalse((bootstrap.STATE / (self.grant["nonce"] + ".result.json")).exists())

    def test_v2_prior_tls_receipt_is_authenticated_before_nonce_or_spawn(self):
        source = "a"*40
        project="belib-isolated-i5-upgrade-"+"7"*32
        active={"schema":"belib-active-generation/v1", "host":bootstrap.TARGET["host"],
            "principal":bootstrap.TARGET["principal"], "uid":bootstrap.TARGET["uid"],
            "source_sha":"6"*40, "operation_id":"7"*32, "project":project,
            "upgrade_receipt_nonce":"8"*64, "upgrade_receipt_sha256":"9"*64,
            "tls_owner_nonce":"a"*64, "tls_completion_sha256":"b"*64,
            "authority_pins_sha256":"c"*64, "database_volume":project+"_staging-postgres-data",
            "shared_books_volume":bootstrap.TARGET["project"]+"_staging-book-files",
            "http_port":18083, "tls_loopback_port":18443, "certificate_sha256":"d"*64,
            "services":{}}
        for index,service in enumerate(("db","backend","frontend","nginx"),start=1):
            network_ids={project+"_isolated":str(index+3)*64}
            if service=="nginx": network_ids[project+"_ingress"]="8"*64
            active["services"][service]={"id":str(index)*64,
                "configuration_sha256":str(index+4)*64,
                "image_id":"sha256:"+str(index+5)*64,
                "network_ids":network_ids}
        bootstrap.validate_active_generation(active)
        grant = {**bootstrap.TARGET, "schema":"belib-staging-bootstrap-approval/v2",
            "source_sha":source, "operation":"upgrade-retained", "nonce":"d"*64,
            "evidence_sha256":"e"*64, "issued_at":int(time.time()), "expires_at":int(time.time())+300,
            "prior_application_sha":"6"*40,
            "prior_generation_sha256":hashlib.sha256(bootstrap.canonical(active)).hexdigest(),
            "retained_state_sha256":"1"*64, "backup_sha256":"2"*64,
            "restore_attestation_sha256":"3"*64}
        grant_path=self.root/"generation-grant.json";grant_path.write_bytes(self.signed(grant))
        config_path=self.root/"operator-config.json"
        config_path.write_bytes(bootstrap.canonical({"schema":"belib-staging-operator/v1",
            "enabled":True,"source_sha":source,"manifest":{"fixture.py":{"sha256":"a"*64,"mode":0o644}}}))
        retained={"schema":"belib-retained-operator/v2","source_sha":source,
            "prior_project":active["project"],"prior_generation_sha256":grant["prior_generation_sha256"],
            "environment_transition":{"prior_sha256":"4"*64,"candidate_sha256":"5"*64,
                "prior_source_sha":"6"*40,"candidate_source_sha":source}}
        consume,spawn,write=Mock(),Mock(),Mock()
        with patch.object(bootstrap,"CONFIG",config_path), patch.object(bootstrap,"INSTALL",Path(bootstrap.__file__).resolve()), \
                patch.object(bootstrap,"verify_source",return_value=self.root), \
                patch.object(bootstrap,"verify_staging_source_access"), \
                patch.object(bootstrap,"load_retained_config",return_value=retained), \
                patch.object(bootstrap,"_read_active_generation",return_value=active), \
                patch.object(bootstrap,"validate_generation_predecessor"), \
                patch.object(bootstrap,"read_tls_receipt",side_effect=bootstrap.Blocked("linked TLS receipt missing")) as read_tls, \
                patch.object(bootstrap,"consume_nonce",consume), patch.object(bootstrap,"spawn_admitted",spawn), \
                patch.object(bootstrap,"write_result",write), redirect_stderr(io.StringIO()), \
                self.main_identity(["--grant",str(grant_path)]):
            self.assertEqual(bootstrap.main(),1)
        read_tls.assert_called_once_with(active["tls_owner_nonce"])
        consume.assert_not_called();spawn.assert_not_called();write.assert_not_called()

    def test_denied_worker_access_blocks_retained_prepare_before_spawn(self):
        config_path = self.root / "config.json"
        config_path.write_bytes(bootstrap.canonical({
            "schema": "belib-staging-operator/v1", "enabled": True,
            "source_sha": "a" * 40, "manifest": {"fixture.py": {"sha256": "d" * 64, "mode": 0o644}},
        }))
        spawn = Mock()
        operation_id = "e" * 32
        with patch.object(bootstrap, "CONFIG", config_path), \
                patch.object(bootstrap, "verify_source", return_value=self.root), \
                patch.object(bootstrap, "verify_staging_source_access",
                             side_effect=bootstrap.Blocked("Staging source is not readable by worker UID/GID 1000; verify source directory traversal and fixed entry permissions")) as access, \
                patch.object(bootstrap, "spawn_preparation", spawn), \
                redirect_stderr(io.StringIO()), self.main_identity(["--prepare-retained", operation_id]):
            self.assertEqual(bootstrap.main(), 1)
        access.assert_called_once_with(self.root, "scripts/staging/prepare-retained.py")
        spawn.assert_not_called()
        self.assertFalse(any(self.root.glob("*.result.json")))

    def test_receipt_routes_return_before_source_access_check(self):
        for option, reader in (("--receipt", "read_result"), ("--tls-receipt", "read_tls_receipt")):
            with self.subTest(option=option):
                value = {"receipt": "fixed"}
                nonce = "f" * 64
                with patch.object(bootstrap, reader, return_value=value), \
                        patch.object(bootstrap, "verify_staging_source_access") as access, \
                        redirect_stdout(io.StringIO()), self.main_identity([option, nonce]):
                    self.assertEqual(bootstrap.main(), 0)
                access.assert_not_called()

    def test_unsigned_caller_cannot_enter_launcher(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(launch.LaunchBlocked):
            launch._require_operator_bootstrap_admission()

    def test_real_launcher_main_reaches_deploy_from_installed_source(self):
        # Real main/source admission/Git/operation binding; only the already-tested
        # root descriptor boundary, host ownership and external effects are fakes.
        source = self.root / "installed-source"
        (source / "scripts/staging").mkdir(parents=True)
        entry = source / "scripts/staging/launch-guard.py"
        entry.write_bytes(Path(launch.__file__).read_bytes())
        for args in (("init", "-q"), ("add", "."),
                     ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")):
            subprocess.run(["/usr/bin/git", "-C", str(source), *args], check=True, capture_output=True)
        sha = subprocess.check_output(["/usr/bin/git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        grant = {**self.grant, "source_sha": sha}
        admission = {"source_root": str(source), "grant": grant}
        real_lstat = Path.lstat
        def root_owned(path):
            metadata = real_lstat(path)
            fields = list(metadata)
            fields[0] &= ~0o022
            fields[4] = 0
            return os.stat_result(fields)
        seed = Mock()
        seed.SeedWrapperError = RuntimeError
        seed.validate_staging_admission.return_value = (os.open("/dev/null", os.O_RDONLY),
            {"BELIB_STAGE_VCS_REF": sha, "BELIB_STAGE_PORT": "8081"})
        seed.create_private_docker_config.return_value = tempfile.TemporaryDirectory(dir=self.root)
        source_guard = Mock()
        source_guard.SourceTreeBlocked = ValueError
        source_guard.sha_derived_build_snapshot.return_value = nullcontext(source)
        executor = Mock(return_value=0)
        def root_admit():
            launch.ROOT_ADMISSION = admission
        with ExitStack() as stack:
            for target, name, value in (
                (launch, "__file__", str(entry)),
                (launch, "_require_operator_bootstrap_admission", root_admit),
                (launch, "verify_python_runtime", Mock()),
                (launch, "ROOT_ADMISSION", None),
                (launch, "ADMITTED_SOURCE_ROOT", None),
                (launch.os, "getuid", Mock(return_value=1000)),
                (launch.os, "geteuid", Mock(return_value=1000)),
                (launch.pwd, "getpwuid", Mock(return_value=SimpleNamespace(pw_name="hiroyuki", pw_uid=1000))),
                (launch.socket, "gethostname", Mock(return_value="ubuntu-worker")),
                (Path, "lstat", root_owned),
                (launch, "load_seed_module", Mock(return_value=seed)),
                (launch, "project_lock", Mock(return_value=nullcontext())),
                (launch, "load_source_tree_guard", Mock(return_value=source_guard)),
                (launch, "_run_preflight", Mock()),
                (launch, "_trusted_rootless_context", Mock(return_value=({}, Path("/run/user/1000/docker.sock")))),
                (launch, "_validate_compose_model", Mock()),
                (launch, "run_deploy_transaction", executor),
            ):
                stack.enter_context(patch.object(target, name, value))
            stack.enter_context(patch.dict(os.environ, {"HOME": "/home/hiroyuki", "PATH": "/usr/bin:/bin"}, clear=True))
            self.assertEqual(launch.main(bootstrap.launcher_arguments(grant)), 0)
            self.assertEqual(launch.ADMITTED_SOURCE_ROOT, source)
        executor.assert_called_once()
        self.assertEqual(executor.call_args.kwargs["source_sha"], sha)
        self.assertEqual(executor.call_args.kwargs["build_root"], source)

    def test_sealed_admission_is_process_and_argv_bound(self):
        if not hasattr(os, "memfd_create") or not hasattr(fcntl, "F_ADD_SEALS"):
            self.skipTest("Linux memfd unavailable")
        arguments = bootstrap.launcher_arguments(self.grant)
        for wrong in (False, True):
            fd = os.memfd_create("fixture", os.MFD_ALLOW_SEALING)
            admission = {"schema": "belib-staging-operator-admission/v1", "grant": self.grant,
                         "source_root": "/opt/belib-staging/sources/" + "a" * 40,
                         "pid": os.getpid() + (1 if wrong else 0),
                         "argv_sha256": hashlib.sha256(bootstrap.canonical(arguments)).hexdigest()}
            os.write(fd, bootstrap.canonical(admission))
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            info = os.fstat(fd)
            # Only root ownership is synthetic here; kernel seals/PID/argv are real.
            root_info = SimpleNamespace(st_uid=0, st_mode=info.st_mode, st_nlink=info.st_nlink, st_size=info.st_size)
            with patch.dict(os.environ, {"BELIB_BOOTSTRAP_FD": str(fd)}), \
                    patch.object(launch.os, "fstat", return_value=root_info), \
                    patch.object(launch.os, "getuid", return_value=1000), \
                    patch.object(launch.socket, "gethostname", return_value="ubuntu-worker"), \
                    patch.object(launch.sys, "argv", ["launch-guard.py", *arguments]):
                if wrong:
                    with self.assertRaises(launch.LaunchBlocked):
                        launch._require_operator_bootstrap_admission()
                else:
                    launch._require_operator_bootstrap_admission()
                    args = SimpleNamespace(source_sha="a" * 40, evidence_sha256="c" * 64, operation="deploy")
                    launch._require_operation_approval(args)
                    args.operation = "resume-ingress"
                    args.prior_source_sha = bootstrap.PRIOR_SOURCE
                    args.retained_state_sha256 = "d" * 64
                    with self.assertRaises(launch.LaunchBlocked):
                        launch._require_operation_approval(args)

    def test_handoff_executes_actual_child_with_sealed_pid_bound_descriptor(self):
        if not hasattr(os, "memfd_create") or not hasattr(fcntl, "F_ADD_SEALS"):
            self.skipTest("Linux memfd unavailable")
        source = self.root / "source"
        helper = source / "scripts/staging/launch-guard.py"
        helper.parent.mkdir(parents=True)
        marker = self.root / "child-result.json"
        helper.write_text("""import os,json,fcntl,sys,hashlib
fd=int(os.environ['BELIB_BOOTSTRAP_FD'])
value=json.loads(os.read(fd,16384))
assert value['pid']==os.getpid()
assert value['argv_sha256']==hashlib.sha256(json.dumps(sys.argv[1:],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
assert fcntl.fcntl(fd,fcntl.F_GET_SEALS)&fcntl.F_SEAL_WRITE
with open(%r,'w') as out: json.dump({'uid':os.getuid(),'operation':value['grant']['operation']},out)
""" % str(marker))
        with patch.object(bootstrap, "trusted_path"):
            grant = bootstrap.validate_grant(self.signed(), "a" * 40)
        # This test already runs as the target uid; it performs no host privilege change.
        with patch.object(bootstrap.os, "setgroups"), patch.object(bootstrap.os, "setgid"), patch.object(bootstrap.os, "setuid"):
            self.assertEqual(bootstrap.spawn_admitted(source, grant), 0)
        self.assertEqual(json.loads(marker.read_text()), {"uid": os.getuid(), "operation": "deploy"})


if __name__ == "__main__":
    unittest.main()
