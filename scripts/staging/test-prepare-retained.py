#!/usr/bin/env python3
"""Offline contract tests for retained preparation and disabled finalization."""
import importlib.util
import json
import os
import hashlib
import fcntl
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, nullcontext, redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

MODULE = Path(__file__).with_name("prepare-retained.py")
spec = importlib.util.spec_from_file_location("prepare_retained_tests", MODULE)
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PreparationContractTests(unittest.TestCase):
    def test_build_only_docker_uses_empty_private_operation_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            prepared = Path(temporary) / ("a" * 32)
            prepared.mkdir(mode=0o700)
            spec = prepare._build_environment_spec(prepared)
            self.assertFalse(any(Path(path).exists() for path in spec.values()))
            default = prepare.Docker()
            default_env = dict(default.env)
            build = prepare._build_docker(prepared, default)
            self.assertEqual(default.env, default_env)
            self.assertEqual(build.env["HOME"], str(prepared / "docker-home"))
            self.assertEqual(build.env["DOCKER_CONFIG"], str(prepared / "docker-config"))
            self.assertEqual(build.env["BUILDX_CONFIG"], str(prepared / "buildx-state"))
            for name in ("docker-home", "docker-config", "buildx-state"):
                path = prepared / name
                self.assertEqual(path.stat().st_uid, os.getuid())
                self.assertEqual(path.stat().st_gid, os.getgid())
                self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            self.assertEqual(list((prepared / "docker-home").iterdir()), [])
            self.assertEqual(list((prepared / "docker-config").iterdir()), [])
            with patch.object(prepare.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=b"ok")) as run:
                self.assertEqual(build.run("version"), b"ok")
            self.assertEqual(run.call_args.kwargs["env"], build.env)
            self.assertEqual(run.call_args.kwargs["umask"], 0o077)
            self.assertEqual(default.env, default_env)

    def test_build_only_docker_resumes_private_buildx_state_without_importing_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            prepared = Path(temporary) / ("b" * 32)
            prepared.mkdir(mode=0o700)
            default = prepare.Docker()
            first = prepare._build_docker(prepared, default)
            state = Path(first.env["BUILDX_CONFIG"])
            (state / "builder-state.json").write_text("{}")
            (state / "builder-state.json").chmod(0o600)
            resumed = prepare._build_docker(prepared, default)
            self.assertEqual(resumed.env, first.env)
            (Path(first.env["DOCKER_CONFIG"]) / "config.json").write_text('{"auths":{}}')
            with self.assertRaisesRegex(prepare.PreparationBlocked, "must not import"):
                prepare._build_docker(prepared, default)

    def test_build_only_docker_rejects_symlinked_buildx_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            prepared = Path(temporary) / ("c" * 32)
            prepared.mkdir(mode=0o700)
            (prepared / "buildx-state").symlink_to(Path(temporary))
            with self.assertRaisesRegex(prepare.PreparationBlocked, "state path is unsafe"):
                prepare._build_docker(prepared, prepare.Docker())

    def test_admission_must_have_exact_trusted_fields(self):
        for value in (None, {}, {"source_root": "/tmp", "source_sha": "a" * 40}):
            with self.subTest(value=value), self.assertRaises(prepare.PreparationBlocked):
                prepare._prepare_admitted(value)

    def test_prepare_accepts_only_complete_active_generation_admission(self):
        source = Path(__file__).resolve().parents[2]
        active_operation = "a" * 32
        active_project = prepare.PROJECT + "-upgrade-" + active_operation
        active = {"schema":"belib-active-generation/v2", "host":"ubuntu-worker", "principal":"hiroyuki",
            "uid":1000,"source_sha":"b"*40,"operation_id":active_operation,"project":active_project,
            "upgrade_receipt_nonce":"2"*64,"upgrade_receipt_sha256":"3"*64,"tls_owner_nonce":"4"*64,
            "tls_completion_sha256":"5"*64,"authority_pins_sha256":"6"*64,
            "database_volume":active_project+"_staging-postgres-data","shared_books_volume":prepare.BOOK_VOLUME,
            "http_port":18083,"tls_loopback_port":18443,"certificate_sha256":"7"*64,
            "previous_generation_sha256":"8"*64,"genesis_generation_sha256":"9"*64,
            "staging_env_sha256":"a"*64,"services":{}}
        for service in ("db", "backend", "frontend", "nginx"):
            networks = {active_project+"_isolated":"d"*64}
            if service == "nginx":
                networks[active_project+"_ingress"] = "e"*64
            active["services"][service] = {"id":"f"*64,"configuration_sha256":"1"*64,
                "image_id":"sha256:"+"2"*64,"network_ids":networks}
        admission = {"source_root":str(source),"source_sha":"c"*40,"operation_id":"b"*32,
            "prepared_directory":"/tmp/prepared","env_file":"/tmp/i5.env","active_generation":active,
            "active_generation_sha256":hashlib.sha256(json.dumps(active,sort_keys=True,separators=(",",":"),
                ensure_ascii=False).encode()).hexdigest()}
        validated = []
        def prepare_admitted(value, *, docker=None):
            validated.append(prepare._validate_active_baseline(value))
            return {"accepted":True}
        guard = SimpleNamespace(project_lock=lambda: nullcontext())
        with patch.object(prepare, "_load", return_value=guard), \
                patch.object(prepare, "_prepare_admitted", side_effect=prepare_admitted):
            self.assertEqual(prepare.prepare(admission), {"accepted":True})
        self.assertEqual(validated, [(active, admission["active_generation_sha256"])])

        for mutate in (
            lambda value: value.pop("active_generation_sha256"),
            lambda value: value.update(untrusted_extra="ignored"),
        ):
            malformed = dict(admission)
            mutate(malformed)
            with self.subTest(fields=set(malformed)), \
                    patch.object(prepare, "_load") as load, \
                    patch.object(prepare, "_prepare_admitted") as run, \
                    self.assertRaisesRegex(prepare.PreparationBlocked, "Trusted root admission"):
                prepare.prepare(malformed)
            load.assert_not_called()
            run.assert_not_called()

    def test_retained_database_port_uses_root_generation_database_pins(self):
        database = {"id":"1"*64,"configuration_sha256":"2"*64,"image_id":"sha256:"+"3"*64,
            "network_ids":{"belib-isolated-i5-upgrade-"+"a"*32+"_isolated":"4"*64}}
        active = {"project":"belib-isolated-i5-upgrade-"+"a"*32,
            "database_volume":"belib-isolated-i5-upgrade-"+"a"*32+"_staging-postgres-data",
            "services":{"db":database}}
        calls = []
        class PortFactory:
            class RetainedPostgres:
                @staticmethod
                def for_generation(directory, operation, baseline):
                    calls.append(("generation", directory, operation, baseline))
                    return "active-port"

                @staticmethod
                def for_retained(directory, operation):
                    calls.append(("legacy", directory, operation))
                    return "legacy-port"

        prepared = Path("/private/prepared")
        self.assertEqual(prepare._retained_database_port(PortFactory, prepared, "b"*32, active), "active-port")
        self.assertEqual(calls, [("generation", prepared, "b"*32, {"id":database["id"],
            "project":active["project"],"volume_name":active["database_volume"],
            "network_ids":database["network_ids"],"image_id":database["image_id"],
            "configuration_sha256":database["configuration_sha256"]})])
        self.assertEqual(prepare._retained_database_port(PortFactory, prepared, "c"*32, None), "legacy-port")
        self.assertEqual(calls[-1], ("legacy", prepared, "c"*32))

    def test_malformed_operation_is_rejected_before_docker(self):
        with tempfile.TemporaryDirectory() as root:
            prepared = Path(root) / "prepared"
            prepared.mkdir(mode=0o700)
            admission = {"source_root": root, "source_sha": "a" * 40, "operation_id": "../bad",
                         "prepared_directory": str(prepared), "env_file": str(Path(root) / "env")}
            with self.assertRaises(prepare.PreparationBlocked):
                prepare._prepare_admitted(admission)

    def test_second_generation_preparation_targets_the_sealed_active_database(self):
        """A later prep resolves the prior generation, not the removed v1 DB."""
        active_operation = "a" * 32
        active_project = prepare.PROJECT + "-upgrade-" + active_operation
        active_db_id = "f" * 64
        network_id = "d" * 64
        config_sha = "e" * 64
        image_id = "sha256:" + "1" * 64
        live_env = b"BELIB_STAGE_VCS_REF=" + b"b" * 40 + b"\nBELIB_STAGE_PORT=18083\n"
        active = {"schema":"belib-active-generation/v2", "host":"ubuntu-worker", "principal":"hiroyuki",
            "uid":1000,"source_sha":"b"*40,"operation_id":active_operation,"project":active_project,
            "upgrade_receipt_nonce":"2"*64,"upgrade_receipt_sha256":"3"*64,"tls_owner_nonce":"4"*64,
            "tls_completion_sha256":"5"*64,"authority_pins_sha256":"6"*64,"database_volume":active_project+"_staging-postgres-data",
            "shared_books_volume":prepare.BOOK_VOLUME,"http_port":18083,"tls_loopback_port":18443,"certificate_sha256":"7"*64,
            "previous_generation_sha256":"a"*64,"genesis_generation_sha256":"a"*64,
            "staging_env_sha256":hashlib.sha256(live_env).hexdigest(),"services":{}}
        for service in ("db", "backend", "frontend", "nginx"):
            networks = {active_project+"_isolated":network_id}
            if service == "nginx": networks[active_project+"_ingress"] = "8"*64
            active["services"][service] = {"id":active_db_id if service == "db" else format(int(active_db_id,16)-1,"064x"),
                "configuration_sha256":config_sha,"image_id":image_id,"network_ids":networks}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scripts/staging").mkdir(parents=True)
            prepared = root / "prepared"
            prepared.mkdir(mode=0o700)
            env_file = root / "i5.env"
            operation = "b" * 32
            fd = os.memfd_create("preparation-env-fixture")
            values = {"BELIB_STAGE_VCS_REF": active["source_sha"], "BELIB_STAGE_PORT": "18083"}
            seed = SimpleNamespace(open_staging_env_file=lambda *_: fd,
                read_staging_env_file=lambda *_: (live_env, values),
                verify_reviewed_source=lambda *_: None,
                create_sealed_env_snapshot=lambda *_: os.memfd_create("candidate-env"))
            def load(_path, name):
                if name == "prepare_seed": return seed
                if name == "prepare_cutover": return SimpleNamespace(configuration_digest=lambda _info: config_sha)
                return SimpleNamespace()
            class ActiveDocker:
                def __init__(self): self.inspected = []; self.runs = []
                def inspect(self, identity):
                    self.inspected.append(identity)
                    if identity == active_db_id:
                        return {"Id": active_db_id, "Image": image_id, "Config": {"Image": "postgres:16", "Labels": {
                                    "com.docker.compose.project":active_project,"com.docker.compose.service":"db"}},
                                "State": {"Running": True, "Health": {"Status": "healthy"}},
                                "NetworkSettings": {"Networks": {active_project + "_isolated":
                                    {"NetworkID": network_id}}}, "Mounts": [{"Destination":"/var/lib/postgresql/data",
                                    "Type":"volume","Name":active["database_volume"]}],"HostConfig":{"PortBindings":{}}}
                    raise prepare.PreparationBlocked("Pinned active application unavailable")
                def run(self, *args, **kwargs):
                    self.runs.append(args)
                    return b""
            docker = ActiveDocker()
            admission = {"source_root": str(root), "source_sha": "c"*40,
                "operation_id": operation, "prepared_directory": str(prepared), "env_file": str(env_file),
                "active_generation":active,
                "active_generation_sha256":hashlib.sha256(json.dumps(active,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()}
            with patch.object(prepare, "_load", side_effect=load), \
                    self.assertRaisesRegex(prepare.PreparationBlocked, "Pinned active application unavailable"):
                prepare._prepare_admitted(admission, docker=docker)
            self.assertEqual(docker.inspected, [active_db_id, active["services"]["backend"]["id"]])
            self.assertEqual(docker.runs, [])

    def test_active_baseline_rejects_digest_mismatch_before_docker(self):
        active = {"schema":"belib-active-generation/v1", "host":"ubuntu-worker", "principal":"hiroyuki",
            "uid":1000,"source_sha":"b"*40,"operation_id":"a"*32,"project":prepare.PROJECT+"-upgrade-"+"a"*32,
            "upgrade_receipt_nonce":"2"*64,"upgrade_receipt_sha256":"3"*64,"tls_owner_nonce":"4"*64,
            "tls_completion_sha256":"5"*64,"authority_pins_sha256":"6"*64,"database_volume":prepare.PROJECT+"-upgrade-"+"a"*32+"_staging-postgres-data",
            "shared_books_volume":prepare.BOOK_VOLUME,"http_port":18083,"tls_loopback_port":18443,"certificate_sha256":"7"*64,
            "services":{}}
        for service in ("db", "backend", "frontend", "nginx"):
            networks = {active["project"]+"_isolated":"8"*64}
            if service == "nginx": networks[active["project"]+"_ingress"] = "9"*64
            active["services"][service] = {"id":"a"*64,"configuration_sha256":"b"*64,
                "image_id":"sha256:"+"c"*64,"network_ids":networks}
        admission = {"source_root":"/tmp/source",
            "source_sha":"c"*40,"operation_id":"d"*32,"prepared_directory":"/tmp/prepared",
            "env_file":"/tmp/env","active_generation":active,"active_generation_sha256":"0"*64}
        with self.assertRaisesRegex(prepare.PreparationBlocked, "digest differs"):
            prepare._validate_active_baseline(admission)

    def test_finalize_blocks_when_required_external_evidence_is_missing(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "prepared"
            directory.mkdir(mode=0o700)
            manifest = {"source_sha": "a" * 40}
            (directory / "manifest.json").write_text(json.dumps(manifest))
            (directory / "manifest.json").chmod(0o600)
            config = {"schema": "belib-retained-operator/v1", "enabled": False}
            (directory / "candidate-config.json").write_text(json.dumps(config))
            (directory / "candidate-config.json").chmod(0o600)
            authorities = {name: {"issuer":"test", "public_key_pem":"bad", "fingerprint":"f"*64}
                           for name in ("restore", "network", "roots")}
            with self.assertRaises(prepare.PreparationBlocked):
                prepare.finalize(directory, authorities)
            self.assertFalse(json.loads((directory / "candidate-config.json").read_text())["enabled"])

    def test_sealed_admission_rejects_missing_descriptor(self):
        old = os.environ.pop("BELIB_PREPARATION_FD", None)
        try:
            with self.assertRaises(prepare.PreparationBlocked):
                prepare._read_root_admission()
        finally:
            if old is not None:
                os.environ["BELIB_PREPARATION_FD"] = old

    def _admission_fd(self, *, pid=None, source_root=None, seal=True):
        operation = "a" * 32
        admission = {"schema":"belib-retained-preparation-admission/v1",
            "source_root":str(source_root or Path(__file__).resolve().parents[2]),
            "source_sha":"b"*40,"operation_id":operation,
            "prepared_directory":"/home/hiroyuki/.local/state/belib-staging/retained-upgrades/"+operation,
            "env_file":"/home/hiroyuki/.config/belib-staging/i5.env","pid":os.getpid() if pid is None else pid}
        fd = os.memfd_create("prepare-admission-test", os.MFD_ALLOW_SEALING)
        os.write(fd, json.dumps(admission, sort_keys=True, separators=(",", ":")).encode())
        if seal:
            fcntl.fcntl(fd, prepare.F_ADD_SEALS, prepare.REQUIRED_SEALS)
        os.lseek(fd, 0, os.SEEK_SET)
        return fd

    def _main_identity_patches(self):
        flags = SimpleNamespace(isolated=1, dont_write_bytecode=1)
        return (patch.object(prepare.os, "getuid", return_value=1000),
                patch.object(prepare.os, "geteuid", return_value=1000),
                patch.object(prepare.os, "getgid", return_value=1000),
                patch.object(prepare.sys, "executable", "/usr/bin/python3"),
                patch.object(prepare.sys, "flags", flags))

    def test_public_main_consumes_sealed_pid_bound_admission_and_calls_prepare(self):
        fd = self._admission_fd()
        real_fstat = os.fstat
        def root_owned_memfd(target):
            info = real_fstat(target)
            if target == fd:
                return SimpleNamespace(st_uid=0, st_mode=info.st_mode, st_nlink=info.st_nlink, st_size=info.st_size)
            return info
        called = []
        output = io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.object(prepare.os, "fstat", side_effect=root_owned_memfd))
            stack.enter_context(patch.object(prepare, "prepare", side_effect=lambda value: called.append(value) or {"ok":True}))
            stack.enter_context(patch.object(prepare.sys, "argv", [str(MODULE)]))
            stack.enter_context(patch.dict(os.environ, {"BELIB_PREPARATION_FD":str(fd)}, clear=False))
            stack.enter_context(redirect_stdout(output))
            for identity_patch in self._main_identity_patches():
                stack.enter_context(identity_patch)
            result = prepare.main()
        try:
            os.close(fd)
        except OSError:
            pass
        self.assertEqual(result, 0)
        self.assertEqual(len(called), 1)
        self.assertEqual(called[0]["operation_id"], "a"*32)
        self.assertEqual(json.loads(output.getvalue()), {"ok":True})

    def test_public_main_rejects_pid_seal_uid_source_and_argument_mismatches_before_prepare(self):
        real_fstat = os.fstat
        for case in ("pid", "seals", "uid", "source", "noargs"):
            with self.subTest(case=case):
                source = Path(__file__).resolve().parents[2]
                fd = None
                if case != "noargs":
                    fd = self._admission_fd(pid=os.getpid()+1 if case == "pid" else None,
                        source_root=source.parent if case == "source" else source, seal=case != "seals")
                def metadata(target):
                    info = real_fstat(target)
                    if target == fd and case != "uid":
                        return SimpleNamespace(st_uid=0, st_mode=info.st_mode, st_nlink=info.st_nlink, st_size=info.st_size)
                    return info
                called = []
                args = [str(MODULE)]
                env = {} if case == "noargs" else {"BELIB_PREPARATION_FD":str(fd)}
                output = io.StringIO()
                with ExitStack() as stack:
                    stack.enter_context(patch.object(prepare.os, "fstat", side_effect=metadata))
                    stack.enter_context(patch.object(prepare, "prepare", side_effect=lambda value: called.append(value) or {"ok":True}))
                    stack.enter_context(patch.object(prepare.sys, "argv", args))
                    stack.enter_context(patch.dict(os.environ, env, clear=False))
                    if case == "noargs":
                        os.environ.pop("BELIB_PREPARATION_FD", None)
                    stack.enter_context(redirect_stdout(output))
                    stack.enter_context(redirect_stderr(io.StringIO()))
                    for identity_patch in self._main_identity_patches():
                        stack.enter_context(identity_patch)
                    result = prepare.main()
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                self.assertEqual(result, 1)
                self.assertEqual(called, [])

    def test_signed_finalize_produces_disabled_candidate_without_installing_pins(self):
        fixture_path = Path(__file__).with_name("test-retained-upgrade.py")
        if not fixture_path.is_file():
            fixture_path = Path(__file__).resolve().parents[3] / "lane-c-execution/scripts/staging/test-retained-upgrade.py"
        fixture_spec = importlib.util.spec_from_file_location("prepare_finalize_fixture", fixture_path)
        fixture_module = importlib.util.module_from_spec(fixture_spec)
        fixture_spec.loader.exec_module(fixture_module)
        def test_key(public_key_pem, fingerprint):
            with tempfile.TemporaryDirectory() as work:
                work = Path(work)
                pub = work / "key.pem"
                der = work / "key.der"
                pub.write_bytes(public_key_pem)
                subprocess.run(["/usr/bin/openssl", "pkey", "-pubin", "-in", str(pub), "-outform", "DER", "-out", str(der)], check=True)
                encoded = der.read_bytes()
                if hashlib.sha256(encoded[12:]).hexdigest() != fingerprint:
                    raise fixture_module.upgrade.UpgradePreflightError("Test key fingerprint mismatch")
                key_pem = bytes(public_key_pem)
            class Key:
                def verify(self, signature, message):
                    with tempfile.TemporaryDirectory() as temporary:
                        work = Path(temporary)
                        pub = work / "key.pem"
                        pub.write_bytes(key_pem)
                        (work / "message").write_bytes(message)
                        (work / "signature").write_bytes(signature)
                        result = subprocess.run(["/usr/bin/openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(pub),
                            "-rawin", "-in", str(work / "message"), "-sigfile", str(work / "signature")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                        if result.returncode:
                            raise fixture_module.upgrade.UpgradePreflightError("Invalid test signature")
            return Key()
        fixture_module.upgrade._trusted_ed25519_key = test_key
        fixture = fixture_module.RetainedUpgradeTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            operation = "1" * 32
            directory = temp / operation
            directory.mkdir(mode=0o700)
            helper = temp / "retained-upgrade.py"
            shutil.copyfile(fixture_path.with_name("retained-upgrade.py"), helper)
            shutil.copyfile(fixture_path.with_name("retained-cutover.py"), temp / "retained-cutover.py")
            original_module_path = prepare.__file__
            prepare.__file__ = str(temp / "prepare-retained.py")
            self.addCleanup(setattr, prepare, "__file__", original_module_path)
            original_loader = prepare._load
            prepare._load = lambda path, name: (fixture_module.upgrade if Path(path).name == "retained-upgrade.py"
                else original_loader(path, name))
            self.addCleanup(setattr, prepare, "_load", original_loader)
            manifest = fixture.manifest
            backup = b"offline signed fixture bytes"
            (directory / "backup.dump").write_bytes(backup)
            (directory / "backup.dump").chmod(0o600)
            backup_sha = hashlib.sha256(backup).hexdigest()
            query_value = {"data": {"books": {"count": 0, "sha256": "a"*64}}, "schema_sha256": "b"*64}
            query = json.dumps(query_value, sort_keys=True, separators=(",", ":")).encode()
            manifest_sha = fixture.manifest_digest(manifest)
            payload = {"schema":"belib-retained-upgrade-restore-attestation/v1","evidence_issuer":fixture.issuer,
                "backup_sha256":backup_sha,"manifest_sha256":manifest_sha,"postgresql_major_version":16,
                "restore_target_identity":{"disposable":True,"identity":"isolated-restore-test"},
                "integrity_query_sha256":hashlib.sha256(query).hexdigest(),"restore_success":True,
                "issued_at":1000,"expires_at":1200}
            evidence = {
                "manifest.json": fixture_module.upgrade.canonical_json(manifest),
                "network-inventory.json": fixture_module.upgrade.canonical_json(fixture.network_inventory),
                "backup-roots.json": fixture_module.upgrade.canonical_json(fixture.root_contract),
                "restore-attestation.json": fixture_module.upgrade.canonical_json(fixture.sign(payload)),
                "query-report.json": fixture_module.upgrade.canonical_json(query_value),
            }
            for name, raw in evidence.items():
                path = directory / name
                path.write_bytes(raw)
                path.chmod(0o600)
            oldnet, oldingress = "2" * 64, "3" * 64
            newnet, newingress = "4" * 64, "5" * 64
            def pin(identity, networks):
                return {"id":identity*64,"configuration_sha256":("a" if identity=="a" else "b")*64,
                    "network_ids":networks}
            cutover_plan = {"schema":"belib-isolated-copy-cutover/v1","source_sha":fixture.source_sha,
                "operation_id":operation,"port":18083,
                "old":{"backend":pin("a",{"belib-isolated-i5_isolated":oldnet}),
                    "frontend":pin("b",{"belib-isolated-i5_isolated":oldnet}),
                    "nginx":pin("c",{"belib-isolated-i5_isolated":oldnet,"belib-isolated-i5_ingress":oldingress})},
                "new":{"backend":pin("d",{"belib-isolated-i5-upgrade-"+operation+"_isolated":newnet}),
                    "frontend":pin("e",{"belib-isolated-i5-upgrade-"+operation+"_isolated":newnet}),
                    "nginx":pin("f",{"belib-isolated-i5-upgrade-"+operation+"_isolated":newnet,
                        "belib-isolated-i5-upgrade-"+operation+"_ingress":newingress})}}
            config = {"schema":"belib-retained-operator/v1","enabled":False,"source_sha":fixture.source_sha,
                "topology":"isolated-copy","prepared_directory":str(directory),"authorities":{},
                "cutover_plan":cutover_plan,"candidate_database":{"id":"8"*64,"configuration_sha256":"9"*64,
                    "volume_name":"belib-isolated-i5-upgrade-"+operation+"_staging-postgres-data","network_id":newnet},
                "source_database":{"id":fixture_module.upgrade.OLD_DB_CONTAINER_ID,"configuration_sha256":"7"*64,
                    "network_ids":{"belib-isolated-i5_isolated":oldnet},"volume_name":"belib-isolated-i5_staging-postgres-data"},
                "migration_image":"sha256:"+"c"*64}
            (directory / "candidate-config.json").write_text(json.dumps(config, sort_keys=True, separators=(",", ":")))
            (directory / "candidate-config.json").chmod(0o600)
            pins = {"restore":{"issuer":fixture.issuer,"public_key_pem":fixture.public_pem.decode(),"fingerprint":fixture.fingerprint},
                "network":{"issuer":fixture.network_issuer,"public_key_pem":fixture.network_public_pem.decode(),"fingerprint":fixture.network_fingerprint},
                "roots":{"issuer":fixture.root_issuer,"public_key_pem":fixture.root_public_pem.decode(),"fingerprint":fixture.root_fingerprint}}
            result = prepare.finalize(directory, pins)
            candidate = json.loads((directory / "candidate-config.json").read_text())
            self.assertEqual(result["enabled"], False)
            self.assertEqual(candidate["enabled"], False)
            self.assertEqual(candidate["authorities"], pins)
            self.assertTrue(json.loads((directory / "finalize-candidate.json").read_text())["requires_root_adoption"])
            before = (directory / "candidate-config.json").read_bytes()
            attestation_path = directory / "restore-attestation.json"
            attestation = json.loads(attestation_path.read_text())
            attestation["signature"] = "eA=="  # malformed/unsigned external envelope
            attestation_path.write_text(json.dumps(attestation, sort_keys=True, separators=(",", ":")))
            attestation_path.chmod(0o600)
            with self.assertRaises(prepare.PreparationBlocked):
                prepare.finalize(directory, pins)
            self.assertEqual((directory / "candidate-config.json").read_bytes(), before)
            self.assertFalse(json.loads((directory / "candidate-config.json").read_text())["enabled"])


if __name__ == "__main__":
    unittest.main()
