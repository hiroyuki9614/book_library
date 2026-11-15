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
import io
import json
import os
import shutil
import socket
import ssl
import stat
import subprocess
import tempfile
import time
import traceback
import unittest
import uuid
import fcntl
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
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
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.tls_port = listener.getsockname()[1]
        self.port_overlay_temp = tempfile.TemporaryDirectory(prefix="belib-test-port-overlay-")
        self.addCleanup(self.port_overlay_temp.cleanup)
        port_stage = Path(self.port_overlay_temp.name) / "staging"
        shutil.copytree(STAGE, port_stage)
        # The source protocol correctly pins 18443 in production. This isolated
        # Docker fixture uses a random free host port, so rewrite every runtime
        # copy consistently before the fixture Git SHA and active pointer exist.
        for path in port_stage.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".yaml", ".yml", ".conf", ".json"}:
                raw = path.read_bytes()
                if b"18443" in raw:
                    path.write_bytes(raw.replace(b"18443", str(self.tls_port).encode("ascii")))
        self.prep_test.STAGE = port_stage
        self.prep_test.PREP_SOURCE = port_stage / "prepare-retained.py"
        entry.RUNTIME = tuple(dict.fromkeys((*entry.RUNTIME, *EXTRA)))
        self.fixture = self.prep_test.PrepareFinalizeExecutorIntegration(
            "test_prepare_finalize_execute_replay_and_response_loss")
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        # The isolated fixture already runs as UID/GID 1000. The source guard
        # launches a subprocess with setuid/setgid arguments intended for the
        # privileged production parent; asking a non-root fixture runner to
        # set its own IDs raises PermissionError before the real probe starts.
        # Remove only those redundant identity arguments for this exact probe,
        # then run its unchanged path traversal/open/fstat logic as UID/GID 1000.
        self.source_access_probes = []
        self.assertEqual(os.getuid(), 1000, "the disposable worker fixture must run as its real UID 1000")
        self.assertEqual(os.getgid(), 1000, "the disposable worker fixture must run as its real GID 1000")
        real_subprocess_run = subprocess.run
        def worker_identity_probe(command, **kwargs):
            if (isinstance(command, list) and len(command) >= 7 and command[:3] ==
                    ["/usr/bin/python3", "-I", "-B"] and
                    "root, entry = sys.argv[1], sys.argv[2]" in command[4]):
                self.assertEqual(kwargs.get("user"), 1000)
                self.assertEqual(kwargs.get("group"), 1000)
                self.assertEqual(kwargs.get("extra_groups"), ())
                source_root, fixed_entry = Path(command[-2]), command[-1]
                # Prove this fixture relies only on UID/GID 1000's owner/group/
                # other permission bits, never an inherited supplementary group.
                for directory in (source_root, *source_root.parents):
                    info = directory.stat()
                    allowed = ((info.st_uid == 1000 and info.st_mode & stat.S_IXUSR)
                        or (info.st_gid == 1000 and info.st_mode & stat.S_IXGRP)
                        or info.st_mode & stat.S_IXOTH)
                    self.assertTrue(stat.S_ISDIR(info.st_mode) and allowed, str(directory))
                entry_info = (source_root / fixed_entry).stat()
                self.assertTrue(stat.S_ISREG(entry_info.st_mode))
                self.assertTrue((entry_info.st_uid == 1000 and entry_info.st_mode & stat.S_IRUSR)
                    or (entry_info.st_gid == 1000 and entry_info.st_mode & stat.S_IRGRP)
                    or entry_info.st_mode & stat.S_IROTH)
                self.source_access_probes.append((str(source_root), fixed_entry, os.getuid(), os.getgid()))
                kwargs.pop("user")
                kwargs.pop("group")
                kwargs.pop("extra_groups")
            return real_subprocess_run(command, **kwargs)
        self._worker_identity_patch = patch.object(subprocess, "run", side_effect=worker_identity_probe)
        self._worker_identity_patch.start()
        self.addCleanup(self._worker_identity_patch.stop)
        # This existing test leaves actual prepared records and the successful
        # immutable root-upgrade fixture receipt for our next operation.
        self.fixture.test_prepare_finalize_execute_replay_and_response_loss()
        self.assertGreaterEqual(len(self.source_access_probes), 2,
            "the unchanged worker source-access guard must run for prepare and launch entries")
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
        self.serve_client_type = Serve
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
            if (host, port) in {("100.109.171.70", 443), ("127.0.0.1", self.tls_port)}:
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
            if hasattr(self.b, "_check_owner_identity"):
                stack.enter_context(patch.object(self.b, "_check_owner_identity", return_value=None))
            if hasattr(self.b, "ensure_journal_dir"):
                stack.enter_context(patch.object(self.b, "ensure_journal_dir",
                    side_effect=lambda: self.b._check_dir(self.b.JOURNAL, mode=0o700)))
            yield

    @contextlib.contextmanager
    def bootstrap_receipt_identity(self):
        """Map root identity only for fixture receipt paths and the sealed inspector FD."""
        f, b = self.fixture, self.bootstrap
        roots = (f.state.absolute(), b.TLS_COMPLETIONS.absolute())
        real_lstat, real_fstat = os.lstat, os.fstat

        def under_receipt_root(path):
            candidate = Path(path).absolute()
            return any(candidate == root or root in candidate.parents for root in roots)

        def fixture_lstat(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            return RootMetadata(info) if under_receipt_root(path) else info

        def fixture_fstat(fd, *args, **kwargs):
            info = real_fstat(fd, *args, **kwargs)
            try:
                target = os.readlink("/proc/self/fd/" + str(fd))
            except OSError:
                target = ""
            if ("memfd:belib-active-generation" in target
                    or (target and not target.endswith(" (deleted)") and under_receipt_root(target))):
                return RootMetadata(info)
            return info

        with patch.object(os, "lstat", side_effect=fixture_lstat), \
             patch.object(os, "fstat", side_effect=fixture_fstat):
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

    def promote_fixture_generation(self, upgrade_nonce, completion):
        """Run the actual root pointer CAS and CD FD inspector with test-only identity plumbing."""
        f, b, cd = self.fixture, self.bootstrap, self.cd
        expected = json.loads((f.state / (upgrade_nonce + ".result.json")).read_bytes())["expected_runtime"]
        cd.PROJECT = f.project
        cd.TARGET = {**cd.TARGET, "project": f.project}
        cd.RETAINED_DATABASE_ID = expected["retained"]["db"]
        cd.OLD_APPLICATION = expected.get("prior_application_sha", cd.OLD_APPLICATION)
        cd.DATABASE_IMAGE = f.pg.IMAGE
        cd.DOCKER_SOCKET = Path(f"/run/user/{os.getuid()}/docker.sock")
        cd.DOCKER_ENDPOINT = "unix://" + str(cd.DOCKER_SOCKET)
        cd.ENV_FILE = f.env

        original_inspect_kwdefaults = cd.inspect_completed_generation.__kwdefaults__
        runtime = lambda upgrade, tls, port: cd.collect_tls_final_readback(
            upgrade, tls, port, runner=self.runner)
        cd.inspect_completed_generation.__kwdefaults__ = {
            **original_inspect_kwdefaults, "runtime_readback": runtime,
            "ingress_probe": cd.verify_tls_ingress, "health_probe": cd.loopback_health_probe,
            "env_file_path": f.env, "file_owner_uid": 1000,
            "readback_lock": lambda: contextlib.nullcontext()}
        def inspector_run(command, **kwargs):
            if not (isinstance(command, list) and "--inspect-completed-generation-fd" in command):
                return subprocess.run(command, **kwargs)
            fd = int(command[-1])
            projection = cd.inspect_completed_generation_fd(fd)
            return subprocess.CompletedProcess(command, 0, canonical(projection).decode() + "\n", "")

        subprocess_proxy = SimpleNamespace(**{
            name: value for name, value in vars(subprocess).items() if name != "run"})
        subprocess_proxy.run = inspector_run

        # The only synthetic privilege is the ownership bit on bootstrap's
        # sealed inspection memfd. CD validators, Docker readback, HTTP/TLS/SNI
        # probes, and the root pointer CAS execute their real code.
        try:
            with self.bootstrap_receipt_identity(), patch.object(b, "trusted_path", return_value=None), \
                 patch.object(b, "subprocess", subprocess_proxy):
                return b._promote_completed(upgrade_nonce, completion["owner_nonce"], f.source)
        finally:
            cd.inspect_completed_generation.__kwdefaults__ = original_inspect_kwdefaults

    def new_source_revision(self):
        """Create a second clean Git identity over the same pinned fixture bytes."""
        f = self.fixture
        work = f.sources / ("continuation-work-" + uuid.uuid4().hex)
        subprocess.run(["/usr/bin/git", "clone", "--quiet", "--no-hardlinks", str(f.source), str(work)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        subprocess.run(["/usr/bin/git", "-C", str(work), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm",
                        "disposable second generation"], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE)
        revision = subprocess.check_output(["/usr/bin/git", "-C", str(work), "rev-parse", "HEAD"], text=True).strip()
        target = f.sources / revision
        work.rename(target)
        self.assertEqual(subprocess.check_output(["/usr/bin/git", "-C", str(target), "status", "--porcelain"], text=True), "")
        return target, revision

    def write_operator_manifest(self, source, revision):
        manifest = {path.relative_to(source).as_posix():
                    {"sha256": sha(path.read_bytes()), "mode": stat.S_IMODE(path.stat().st_mode)}
                    for path in source.rglob("*") if path.is_file() and ".git" not in path.relative_to(source).parts}
        config = {"schema": "belib-staging-operator/v1", "enabled": True,
                  "source_sha": revision, "manifest": manifest}
        self.fixture.main_config.write_bytes(canonical(config))
        self.fixture.main_config.chmod(0o600)

    def invoke_generation_v2(self, active, *, predecessor_digest=None, interrupt=False):
        """Use the existing signed root->sealed launch path for a v2 grant."""
        f, b, l = self.fixture, self.bootstrap, self.fixture.launch
        revision = f.sha
        issued = int(time.time())
        grant = {**b.TARGET, "schema": "belib-staging-bootstrap-approval/v2",
            "source_sha": revision, "operation": "upgrade-retained",
            "nonce": uuid.uuid4().hex + uuid.uuid4().hex,
            "evidence_sha256": "b" * 64, "issued_at": issued, "expires_at": issued + 120,
            "prior_application_sha": active["source_sha"],
            "prior_generation_sha256": predecessor_digest or sha(canonical(active)),
            "retained_state_sha256": f.fixture.manifest_sha,
            "backup_sha256": f.fixture.backup_sha,
            "restore_attestation_sha256": f.fixture.verify()}
        raw = canonical(grant)
        payload, signature = f.root / ("v2-payload-" + grant["nonce"]), f.root / ("v2-signature-" + grant["nonce"])
        payload.write_bytes(raw)
        subprocess.run(["/usr/bin/openssl", "pkeyutl", "-sign", "-inkey", str(f.key), "-rawin",
                        "-in", str(payload), "-out", str(signature)], check=True)
        envelope = {"payload": base64.b64encode(raw).decode(),
                    "signature": base64.b64encode(signature.read_bytes()).decode()}
        grant_path = f.root / ("v2-grant-" + grant["nonce"])
        grant_path.write_bytes(canonical(envelope)); grant_path.chmod(0o600)

        real_lstat, real_fstat = Path.lstat, os.fstat
        def source_root_owned(path):
            original = real_lstat(path)
            if path == f.source or path in f.source.parents:
                return SimpleNamespace(st_mode=original.st_mode & ~0o022, st_uid=0,
                    st_nlink=original.st_nlink, st_size=original.st_size)
            return original

        def spawn(root, checked, config):
            arguments = b.launcher_arguments(checked)
            arguments[arguments.index("--env-file") + 1] = str(f.env)
            admission = {"schema": "belib-staging-operator-admission/v1", "grant": checked,
                "source_root": str(root), "pid": os.getpid(),
                "argv_sha256": sha(canonical(arguments)), "retained_config": config}
            descriptor = os.memfd_create("fixture-root-admission", os.MFD_ALLOW_SEALING)
            os.write(descriptor, canonical(admission))
            fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS,
                fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)
            def root_descriptor(number):
                info = real_fstat(number)
                if number == descriptor and info.st_nlink == 0:
                    return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_nlink=0, st_size=info.st_size)
                return info
            try:
                with patch.dict(os.environ, {"PATH": "/usr/bin:/bin", "HOME": str(f.home),
                                             "BELIB_BOOTSTRAP_FD": str(descriptor)}, clear=True), \
                     patch.object(os, "fstat", side_effect=root_descriptor), \
                     patch.object(sys, "argv", ["launch-guard.py", *arguments]):
                    l.ROOT_ADMISSION = None
                    return l.main(arguments)
            finally:
                os.close(descriptor)

        lock_path = Path("/tmp") / ("belib-c2b-v2-" + grant["nonce"] + ".lock")
        actual_lock = l.project_lock
        output, errors = io.StringIO(), io.StringIO()
        child = {"active": False}
        with contextlib.ExitStack() as stack:
            for target, name, value in (
                (b, "CONFIG", f.main_config), (b, "RETAINED_CONFIG", f.retained_config),
                (b, "STATE", f.state), (b, "SOURCES", f.sources), (b, "INSTALL", Path(b.__file__).resolve()),
                (b, "PUBLIC_KEY", f.public.read_bytes()), (b, "PUBLIC_FINGERPRINT", sha(f.public.read_bytes())),
                (b, "trusted_path", lambda *args, **kwargs: None),
                (b, "spawn_admitted", lambda *args: (child.__setitem__("active", True), spawn(*args))[1]),
                (l, "OPERATOR_SOURCES", f.sources), (l, "EXPECTED_HOME", f.home),
                (l, "project_lock", lambda: actual_lock(lock_path)),
                (l, "load_retained_executor", lambda _: f.executor),
                (Path, "lstat", source_root_owned), (sys, "executable", "/usr/bin/python3")):
                stack.enter_context(patch.object(target, name, value))
            stack.enter_context(patch.object(b.os, "getuid", side_effect=lambda: 1000 if child["active"] else 0))
            stack.enter_context(patch.object(b.os, "geteuid", side_effect=lambda: 1000 if child["active"] else 0))
            stack.enter_context(patch.object(sys, "argv", ["operator-bootstrap.py", "--grant", str(grant_path)]))
            if interrupt:
                original_migrate = f.executor.IsolatedCopyOperations.migrate
                def fail_after_candidate_migration(operations):
                    original_migrate(operations)
                    raise RuntimeError("injected post-migration generation-2 recovery")
                stack.enter_context(patch.object(f.executor.IsolatedCopyOperations, "migrate",
                                                  fail_after_candidate_migration))
            stack.enter_context(redirect_stdout(output)); stack.enter_context(redirect_stderr(errors))
            with self.bootstrap_receipt_identity():
                status = b.main()
        lock_path.unlink(missing_ok=True)
        return status, output.getvalue(), errors.getvalue(), grant

    def prepare_generation2(self, active, *, source_revision=None):
        f = self.fixture
        source, revision = source_revision or self.new_source_revision()
        f.source, f.sha = source, revision
        self.write_operator_manifest(source, revision)
        f.old_db = active["services"]["db"]["id"]
        f.source_port = f.pg.RetainedPostgres(f.old_db, f.root, active["operation_id"])
        f.prep_operation = uuid.uuid4().hex
        self._c2b_operation_ids.append(f.prep_operation)
        migration = f.root / ("c2b-migration-" + f.prep_operation)
        migration.mkdir(mode=0o700)
        (migration / "migration.sql").write_text(
            "SET ROLE belib_staging_migrate;\n"
            "ALTER TABLE reading_infos ALTER COLUMN current_position TYPE text;\n"
            "ALTER TABLE reading_infos ADD COLUMN IF NOT EXISTS progress_percentage integer;\n"
            "ALTER TABLE reading_infos ADD COLUMN IF NOT EXISTS save_sequence bigint NOT NULL DEFAULT 0;\n",
            encoding="utf-8")
        (migration / "migration.sql").chmod(0o644)
        (migration / "npx").write_text(
            "#!/bin/sh\n[ \"$*\" = \"prisma migrate deploy\" ] || exit 91\n"
            "exec psql -X -v ON_ERROR_STOP=1 \"$DATABASE_URL\" -f /migration.sql\n", encoding="utf-8")
        (migration / "npx").chmod(0o755)
        (migration / "Dockerfile").write_text(
            "FROM " + f.pg.IMAGE + "\n"
            "RUN groupadd -g 1000 node && useradd -u 1000 -g 1000 node\n"
            "COPY npx /usr/local/bin/npx\nCOPY migration.sql /migration.sql\n", encoding="utf-8")
        migration_tag = "belib-c2b-migration:" + f.prep_operation
        migrated_image = f.docker("build", "--network", "none", "--quiet", "--tag", migration_tag,
                                  str(migration)).decode().strip()
        f.images.append(migrated_image)
        self._c2b_migration_tags.append(migration_tag)
        f.migrationimage = migrated_image
        f.prepared = f.root / f.prep_operation
        f.prepared.mkdir(mode=0o700)
        f.prep_module = load(source / "scripts/staging/prepare-retained.py", "c2b_prepare_generation2")
        f.configure_preparer()
        prep = f.prep_module
        prep.OLD_DB_ID = f.old_db
        prep.OLD_APP_SHA = active["source_sha"]
        prep.BOOK_VOLUME = active["shared_books_volume"]
        prep.DB_VOLUME = active["database_volume"]
        f.prep_docker = prep.Docker()
        f.prep_original_run = f.prep_docker.run
        f.prep_calls = []
        f.prep_docker.run = f.counted_docker
        f.image_tags = {name: "belib-retained:" + f.prep_operation + "-" + name
                        for name in ("backend", "frontend", "migrate")}
        f.build_fixture_tags = {}
        f.fixture_base_tags = {}
        f.fixture_tagged_images = set()
        f.build_fixture_images()
        admission = {"source_root": str(source), "source_sha": revision,
            "operation_id": f.prep_operation, "prepared_directory": str(f.prepared),
            "env_file": str(f.env), "active_generation": active,
            "active_generation_sha256": sha(canonical(active))}
        receipt = f.prepare_call(admission)
        self.assertTrue(receipt["candidate_database_started"])
        self.assertTrue(receipt["candidate_apps_stopped"])
        self.assertFalse(receipt["original_database_mutated"])
        finalized = f.sign_prepared_evidence()
        f.executor.pg.PROJECT = f.project
        f.executor.pg.RETAINED_ID = f.old_db
        f.executor.proof.EXPECTED_PROJECT = f.project
        f.executor.proof.OLD_DB_CONTAINER_ID = f.old_db
        f.executor.proof.OLD_APPLICATION_SHA = active["source_sha"]
        f.executor.proof.OLD_DATABASE_LINEAGE = f.fixture.manifest["database_lineage"]
        f.executor.proof.RETAINED_VOLUMES = (active["shared_books_volume"], active["database_volume"])
        f.executor.proof.OLD_DB_IMAGE = prep.OLD_DB_IMAGE
        f.executor.cutover_module.PROJECT = f.project
        f.executor.STAGING_ENV_FILE = f.env
        self.assertEqual(finalized["backup_sha256"], f.fixture.backup_sha)
        return revision, receipt

    def test_two_generation_copy_tls_and_active_pointer_lineage(self):
        """Actual disposable Docker/PG prep, cutover and TLS; fake only fixed host identity."""
        # The existing generation-one chain is exercised unchanged. Its final
        # receipt pair is then promoted by the production root CAS through the
        # sealed FD inspection adapter above.
        self.test_actual_tls_owner_chain_reconciliation_and_drift()
        f, b, a, cd = self.fixture, self.bootstrap, self.a, self.cd
        self._c2b_operation_ids = [f.prep_operation]
        self._c2b_migration_tags = []
        def cleanup_c2b_projects():
            for operation in self._c2b_operation_ids:
                project = f.project + "-upgrade-" + operation
                try:
                    rows = f.docker("ps", "--all", "--no-trunc", "--filter",
                                    "label=com.docker.compose.project=" + project,
                                    "--format", "{{.ID}}").decode().splitlines()
                    for identity in rows:
                        if len(identity) == 64 and all(ch in "0123456789abcdef" for ch in identity):
                            f.docker("rm", "--force", identity)
                except Exception:
                    pass
                for name in (project + "_isolated", project + "_ingress"):
                    try: f.docker("network", "rm", name)
                    except Exception: pass
                try: f.docker("volume", "rm", project + "_staging-postgres-data")
                except Exception: pass
                for service in ("backend", "frontend", "migrate"):
                    try: f.docker("image", "rm", "belib-retained:" + operation + "-" + service)
                    except Exception: pass
            for tag in self._c2b_migration_tags:
                try: f.docker("image", "rm", tag)
                except Exception: pass
        self.addCleanup(cleanup_c2b_projects)
        genesis_upgrade = self.upgrade_nonce
        genesis_completion = self.genesis_completion
        genesis_pointer = self.promote_fixture_generation(genesis_upgrade, genesis_completion)
        with self.root_identity():
            active = b._read_active_generation()
        self.assertEqual(active, genesis_pointer)
        self.assertEqual(active["schema"], "belib-active-generation/v1")

        # Add a row after promotion so generation 2 proves it copied live data
        # from generation 1 (not the untouched genesis database).
        marker = "c2b-" + uuid.uuid4().hex
        f.docker("exec", active["services"]["db"]["id"], "psql", "-v", "ON_ERROR_STOP=1",
                 "-U", f.pg.BOOTSTRAP, "-d", f.pg.DATABASE, "-c",
                 "CREATE TABLE IF NOT EXISTS c2b_generation_marker(value text PRIMARY KEY); "
                 "INSERT INTO c2b_generation_marker VALUES ('" + marker + "')")
        genesis_database = active["services"]["db"]["id"]
        genesis_database_volume = active["database_volume"]
        genesis_database_running = json.loads(f.docker("inspect", genesis_database))[0]["State"]["Running"]
        original_database = f.old_db
        original_volume = f.project + "_staging-postgres-data"
        original_database_running = json.loads(f.docker("inspect", original_database))[0]["State"]["Running"]
        original_volume_identity = json.loads(f.docker("volume", "inspect", original_volume))[0]
        genesis_volume_identity = json.loads(f.docker("volume", "inspect", genesis_database_volume))[0]
        books_volume = active["shared_books_volume"]
        books_volume_identity = json.loads(f.docker("volume", "inspect", books_volume))[0]

        source2, source2_sha = self.new_source_revision()
        self.prepare_generation2(active, source_revision=(source2, source2_sha))
        self.assertNotEqual(source2_sha, active["source_sha"])
        operation2 = f.prep_operation
        config2 = json.loads(f.retained_config.read_bytes())
        self.assertEqual(config2["schema"], "belib-retained-operator/v2")
        self.assertEqual(config2["prior_generation_sha256"], sha(canonical(active)))
        self.assertEqual(config2["source_database"]["id"], genesis_database)
        self.assertEqual(config2["source_database"]["volume_name"], genesis_database_volume)
        self.assertEqual(config2["prior_project"], active["project"])
        self.assertNotEqual(config2["cutover_plan"]["operation_id"], active["operation_id"])

        # A signed but stale predecessor is rejected by bootstrap before its
        # nonce journal is created or any Docker command is issued.
        before_calls = len(f.prep_calls)
        stale_status, _, stale_errors, stale_grant = self.invoke_generation_v2(
            active, predecessor_digest="0" * 64)
        self.assertEqual(stale_status, 1, stale_errors)
        self.assertFalse((f.state / (stale_grant["nonce"] + ".used")).exists())
        self.assertEqual(len(f.prep_calls), before_calls)

        # Inject a process interruption after the actual candidate migration.
        # The transaction must restore the exact active generation and env;
        # then a fresh operation re-prepares against the same signed pointer.
        prior_env = f.env.read_bytes()
        prior_serve, prior_etag = self.serve_raw, self.serve_etag
        interrupted_status, _, interrupted_errors, interrupted_grant = self.invoke_generation_v2(
            active, interrupt=True)
        self.assertEqual(interrupted_status, 1, interrupted_errors)
        interrupted_result = json.loads((f.state / (interrupted_grant["nonce"] + ".result.json")).read_bytes())
        self.assertNotEqual(interrupted_result["exit_code"], 0)
        self.assertEqual(f.env.read_bytes(), prior_env)
        self.assertEqual((self.serve_raw, self.serve_etag), (prior_serve, prior_etag))
        self.cd.loopback_health_probe(active["http_port"])
        self.cd.verify_tls_ingress(genesis_completion["certificate_sha256"])
        for service in ("db", "backend", "frontend", "nginx"):
            identity = active["services"][service]["id"]
            observed = json.loads(f.docker("inspect", identity))[0]
            self.assertEqual(observed["Id"], identity)
            self.assertTrue(observed["State"]["Running"], service)
        self.prepare_generation2(active, source_revision=(source2, source2_sha))
        operation2 = f.prep_operation
        config2 = json.loads(f.retained_config.read_bytes())

        # Run the exact current predecessor grant through real bootstrap
        # validation, sealed launch admission, executor cutover and receipt.
        status, output, errors, grant2 = self.invoke_generation_v2(active)
        self.assertEqual(status, 0, errors + output)
        upgrade2 = json.loads((f.state / (grant2["nonce"] + ".result.json")).read_bytes())
        self.assertEqual(upgrade2["exit_code"], 0)
        self.assertEqual(upgrade2["expected_runtime"]["prior_generation_sha256"], sha(canonical(active)))
        self.assertNotEqual(upgrade2["expected_runtime"]["services"]["db"], genesis_database)

        # Verify the copied DB includes the generation-one-only marker using
        # PostgreSQL itself, and preserve the original/genesis/books volumes.
        db2 = upgrade2["expected_runtime"]["services"]["db"]
        count = f.docker("exec", db2, "psql", "-At", "-v", "ON_ERROR_STOP=1", "-U", f.pg.BOOTSTRAP,
                         "-d", f.pg.DATABASE, "-c",
                         "SELECT count(*) FROM c2b_generation_marker WHERE value='" + marker + "'").decode().strip()
        self.assertEqual(count, "1")
        original_db_state = json.loads(f.docker("inspect", original_database))[0]["State"]["Running"]
        self.assertIs(original_db_state, original_database_running)
        self.assertEqual(json.loads(f.docker("inspect", genesis_database))[0]["Id"], genesis_database)
        self.assertIs(json.loads(f.docker("inspect", genesis_database))[0]["State"]["Running"],
                      genesis_database_running)
        self.assertEqual(json.loads(f.docker("volume", "inspect", original_volume))[0], original_volume_identity)
        self.assertEqual(json.loads(f.docker("volume", "inspect", genesis_database_volume))[0], genesis_volume_identity)
        self.assertEqual(json.loads(f.docker("volume", "inspect", books_volume))[0], books_volume_identity)

        # Continue from the active TLS owner receipt. The v3 owner operation
        # validates the exact Serve digest/ETag in read-only mode.
        self.upgrade = upgrade2
        self.upgrade_bytes = (f.state / (grant2["nonce"] + ".result.json")).read_bytes()
        self.upgrade_nonce = grant2["nonce"]
        self.expected = upgrade2["expected_runtime"]
        self.b = load(source2 / "scripts/staging/owner-serve-cas.py", "c2b_owner_generation2")
        self.cd = load(source2 / "scripts/staging/cd-handoff-executor.py", "c2b_cd_generation2")
        self.bootstrap = b
        a.PROJECT = a.TLS.PROJECT = f.project
        a.STATE = f.root
        a.TLS_CONTEXT = f.root / "tls-work-context-generation2"
        a.PREPARED_ROOT = f.root
        a.ENV_PATH = f.env
        a.GUARD.ADMITTED_SOURCE_ROOT = source2
        self.b.JOURNAL = f.root / "tls-root-completions"
        self.b.UPGRADE_RESULTS = f.state
        self.b.UPGRADE_PROJECT = f.project
        self.b.UPGRADE_RETAINED_DATABASE_ID = genesis_database
        self.cd.PROJECT = f.project
        self.cd.TARGET = {**self.cd.TARGET, "project": f.project}
        self.cd.RETAINED_DATABASE_ID = genesis_database
        self.cd.OLD_APPLICATION = active["source_sha"]
        self.cd.DATABASE_IMAGE = f.pg.IMAGE
        self.cd.TLS_LOOPBACK_PORT = self.tls_port
        self.cd.PROJECT_LOCK_PATH = a.GUARD.PROJECT_LOCK_PATH
        self.stack.enter_context(patch.object(a, "_v2_load_executor", return_value=self.cd))
        self.stack.enter_context(patch.object(self.b, "LocalAPI", self.serve_client_type))
        self.evidence = "8" * 64
        serve_before, etag_before, posts_before = self.serve_raw, self.serve_etag, self.serve_posts
        raw2, context2 = a.v2_create_plan(source2, self.upgrade_nonce, runner=self.runner)
        plan2, plan2_sha = json.loads(raw2), sha(raw2)
        self.assertEqual(plan2["schema"], "belib-tls-owner-stable-serve-plan/v3")
        receipt2 = a.v2_prepare_live(raw2, plan2_sha, self.evidence, owner_ready=True, source=source2,
            runner=self.runner, approval_callback=self.signed_grant, tls_port=self.tls_port, tls_ca_file=self.ca)
        prepared2 = (context2 / "prepare-receipt.json").read_bytes()
        with self.root_identity():
            completion2 = self.b.run(raw2, plan2_sha, prepared2, sha(prepared2))
            self.assertEqual(self.b.run(raw2, plan2_sha, prepared2, sha(prepared2)), completion2)
        self.assertEqual(self.serve_raw, serve_before)
        self.assertEqual(self.serve_etag, etag_before)
        self.assertEqual(self.serve_posts, posts_before)
        self.assertEqual(completion2["prior_generation_sha256"], sha(canonical(active)))
        self.assertEqual(completion2["prior_tls_owner_nonce"], genesis_completion["owner_nonce"])
        self.assertEqual(receipt2["upgrade_receipt_nonce"], self.upgrade_nonce)

        handoff_helper2 = load(source2 / "scripts/staging/test-cd-handoff-executor.py",
                               "c2b_handoff_fixture_generation2")
        handoff2 = handoff_helper2.make_handoff(source2_sha)
        handoff2["target"]["compose_project"] = f.project
        handoff_path2 = f.root / "tls-handoff-generation2.json"
        handoff_path2.write_bytes(canonical(handoff2)); handoff_path2.chmod(0o600)
        readback2 = self.cd.readback_tls_handoff(handoff_path2, self.upgrade_nonce,
            completion2["owner_nonce"], runner=self.runner, env_file_path=f.env)
        self.assertEqual(readback2["status"], "LOCAL_TLS_DEPLOYMENT_CONFIRMED")

        pointer2 = self.promote_fixture_generation(self.upgrade_nonce, completion2)
        with self.root_identity():
            pointer_bytes_before_retry = (f.state / "active-generation.json").read_bytes()
        effect_count_before_retry = len(self.effects)
        self.assertEqual(self.promote_fixture_generation(self.upgrade_nonce, completion2), pointer2)
        with self.root_identity(), patch.object(b, "trusted_path", return_value=None):
            reread = b._read_active_generation()
            pointer_bytes_after_retry = (f.state / "active-generation.json").read_bytes()
        self.assertEqual(pointer_bytes_after_retry, pointer_bytes_before_retry)
        self.assertEqual(len(self.effects), effect_count_before_retry)
        self.assertEqual(reread["schema"], "belib-active-generation/v2")
        self.assertEqual(reread["previous_generation_sha256"], sha(canonical(active)))
        self.assertEqual(reread["genesis_generation_sha256"], sha(canonical(active)))
        self.assertEqual(reread["services"]["db"]["id"], db2)
        self.assertEqual(reread["shared_books_volume"], books_volume)
        self.assertEqual(reread["database_volume"], upgrade2["expected_runtime"]["plan"]["candidate_database"]["volume_name"])
        self.assertEqual(reread["serve_config_sha256"], completion2["serve_config_sha256"])
        self.assertEqual(reread["serve_etag"], completion2["serve_etag"])
        live_source, live_env_sha = self.cd._staging_source(f.env, owner_uid=1000)
        self.assertEqual(live_source, source2_sha)
        self.assertEqual(live_env_sha, reread["staging_env_sha256"])
        self.assertEqual(sha(f.env.read_bytes()), reread["staging_env_sha256"])
        print("REAL TWO-GENERATION CONTINUATION PASS: Docker/PG/Compose/nginx, retained DB marker copy, "
              "v2 grant, CD readback, v3 read-only TLS and idempotent root pointer lineage; "
              "fixture seams: random port overlay, synthetic key/UID receipt metadata, fake LocalAPI, "
              "and fixture app/migration images")

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
        # Keep the immutable first-generation pair available to the separate
        # continuation case. The test below promotes it through the real
        # bootstrap CAS and CD descriptor inspector before preparing generation 2.
        self.genesis_plan = plan
        self.genesis_plan_raw = raw
        self.genesis_plan_sha = plan_sha
        self.genesis_prepared_raw = prepared_raw
        self.genesis_completion = completion
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
