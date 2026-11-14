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
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

MODULE = Path(__file__).with_name("prepare-retained.py")
spec = importlib.util.spec_from_file_location("prepare_retained_tests", MODULE)
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PreparationContractTests(unittest.TestCase):
    def test_admission_must_have_exact_trusted_fields(self):
        for value in (None, {}, {"source_root": "/tmp", "source_sha": "a" * 40}):
            with self.subTest(value=value), self.assertRaises(prepare.PreparationBlocked):
                prepare._prepare_admitted(value)

    def test_malformed_operation_is_rejected_before_docker(self):
        with tempfile.TemporaryDirectory() as root:
            prepared = Path(root) / "prepared"
            prepared.mkdir(mode=0o700)
            admission = {"source_root": root, "source_sha": "a" * 40, "operation_id": "../bad",
                         "prepared_directory": str(prepared), "env_file": str(Path(root) / "env")}
            with self.assertRaises(prepare.PreparationBlocked):
                prepare._prepare_admitted(admission)

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
