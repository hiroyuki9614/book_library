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

    def test_valid_external_signature_derives_only_fixed_launcher_arguments(self):
        with patch.object(bootstrap, "trusted_path"):
            grant = bootstrap.validate_grant(self.signed(), "a" * 40)
        self.assertEqual(grant, self.grant)
        self.assertEqual(bootstrap.launcher_arguments(grant), ["--source-sha", "a" * 40,
            "--evidence-sha256", "c" * 64, "--env-file", "/home/hiroyuki/.config/belib-staging/i5.env", "deploy"])

    def test_forged_wrong_target_expired_and_unknown_operation_fail(self):
        for changes in ({"uid": 0}, {"host": "other"}, {"source_sha": "d" * 40},
                        {"operation": "arbitrary-shell"}, {"expires_at": int(time.time())}, {"uid": True}):
            with self.subTest(changes=changes), patch.object(bootstrap, "trusted_path"), self.assertRaises(bootstrap.Blocked):
                bootstrap.validate_grant(self.signed({**self.grant, **changes}), "a" * 40)
        value = json.loads(self.signed())
        value["signature"] = base64.b64encode(b"x" * 64).decode()
        with patch.object(bootstrap, "trusted_path"), self.assertRaises(bootstrap.Blocked):
            bootstrap.validate_grant(bootstrap.canonical(value), "a" * 40)

    def test_nonce_is_durable_one_use(self):
        state = self.root / "state"
        state.mkdir(mode=0o700)
        with patch.object(bootstrap, "STATE", state), patch.object(bootstrap, "trusted_path"):
            bootstrap.consume_nonce(self.grant)
            self.assertTrue((state / (self.grant["nonce"] + ".used")).is_file())
            with self.assertRaises(FileExistsError):
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
