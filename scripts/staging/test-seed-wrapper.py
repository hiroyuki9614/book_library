#!/usr/bin/env python3
"""Offline checks for the staging seed host-side credential guard."""

import importlib.util
import fcntl
import json
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import unittest
from contextlib import nullcontext, redirect_stderr
from io import StringIO
from pathlib import Path
from selectors import EVENT_READ
from types import SimpleNamespace
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("seed.py")
SPEC = importlib.util.spec_from_file_location("belib_staging_seed_wrapper", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
seed_wrapper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seed_wrapper)


TEST_SOURCE_SHA = "a" * 40
TEST_BOOTSTRAP_PASSWORD = "1" * 64
TEST_DB_PASSWORD = "2" * 64
TEST_MIGRATE_PASSWORD = "3" * 64
TEST_AUTH_SECRET = "4" * 64
ROLE_INIT_SCRIPT = Path(__file__).with_name("10-belib-staging-roles.sh")
REPO_ROOT = Path(__file__).resolve().parents[2]


def make_verified_snapshot(test_case: unittest.TestCase, source_sha: str = TEST_SOURCE_SHA) -> Path:
    seed_wrapper.SNAPSHOT_ROOT = Path("/tmp/belib-seed-wrapper-test-snapshots")
    seed_wrapper.SNAPSHOT_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    snapshot = seed_wrapper.SNAPSHOT_ROOT / source_sha
    shutil.rmtree(snapshot, ignore_errors=True)
    snapshot.mkdir(mode=0o700)
    for relative in seed_wrapper.SNAPSHOT_REQUIRED_DIRECTORIES:
        (snapshot / relative).mkdir(parents=True, exist_ok=True)
    for relative in seed_wrapper.SNAPSHOT_REQUIRED_FILES:
        destination = snapshot / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = REPO_ROOT / relative
        destination.write_bytes(source.read_bytes())
        destination.chmod(source.stat().st_mode & 0o777)
    revision_marker = snapshot / seed_wrapper.SNAPSHOT_REVISION_FILE
    revision_marker.write_text(f"{source_sha}\n", encoding="ascii")
    revision_marker.chmod(0o444)
    manifest = snapshot / seed_wrapper.SNAPSHOT_MANIFEST_FILE
    manifest.write_text(json.dumps({"schema": "belib-staging-snapshot/v1", "source_sha": source_sha, "files": []}), encoding="utf-8")
    manifest.chmod(0o444)
    test_case.addCleanup(shutil.rmtree, snapshot, ignore_errors=True)
    return snapshot


def staging_env_contents(**overrides: str) -> bytes:
    values = {
        "BELIB_STAGE_DB_BOOTSTRAP_PASSWORD": TEST_BOOTSTRAP_PASSWORD,
        "BELIB_STAGE_DB_APP_PASSWORD": TEST_DB_PASSWORD,
        "BELIB_STAGE_DB_MIGRATE_PASSWORD": TEST_MIGRATE_PASSWORD,
        "BELIB_STAGE_AUTH_SECRET": TEST_AUTH_SECRET,
        "BELIB_STAGE_VCS_REF": TEST_SOURCE_SHA,
        "BELIB_STAGE_PORT": "18080",
    }
    values.update(overrides)
    return "".join(f"{name}={value}\n" for name, value in values.items()).encode("ascii")


class FakeStatusSelector:
    def __init__(self, ready: bool = True) -> None:
        self.ready = ready

    def register(self, file_object, events) -> None:
        self.file_object = file_object
        self.events = events

    def select(self, timeout: float):
        self.timeout = timeout
        return [(self.file_object, EVENT_READ)] if self.ready else []

    def close(self) -> None:
        pass


class FakeStatusProcess:
    def __init__(self, output: bytes = b"", returncode: int = 0) -> None:
        self.output = output
        self.final_returncode = returncode
        self.returncode = None
        self.reads = []
        self.killed = False
        self.stdout = self

    def read(self, size: int) -> bytes:
        self.reads.append(size)
        result, self.output = self.output[:size], self.output[size:]
        return result

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = self.final_returncode
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def close(self) -> None:
        pass


class SeedWrapperCredentialGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="belib-seed-wrapper-test-")
        self.root = Path(self.temporary_directory.name)
        self.credentials = self.root / "credentials"
        self.credentials.touch(mode=0o600)
        self.credentials.chmod(0o600)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_opens_only_expected_owner_mode_0600_regular_file(self) -> None:
        fd = seed_wrapper.open_private_regular_file(self.credentials, os.getuid())
        try:
            self.assertTrue(os.path.isfile(self.credentials))
        finally:
            os.close(fd)

    def test_rejects_wrong_owner_and_loose_mode(self) -> None:
        with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "owned by hiroyuki"):
            seed_wrapper.open_private_regular_file(self.credentials, os.getuid() + 1)

        self.credentials.chmod(0o644)
        with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "mode 0600"):
            seed_wrapper.open_private_regular_file(self.credentials, os.getuid())

    def test_rejects_final_and_parent_symlinks(self) -> None:
        linked_file = self.root / "credential-link"
        linked_file.symlink_to(self.credentials)
        with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "no symlinks"):
            seed_wrapper.open_private_regular_file(linked_file, os.getuid())

        real_directory = self.root / "real-directory"
        real_directory.mkdir()
        parent_link = self.root / "directory-link"
        parent_link.symlink_to(real_directory, target_is_directory=True)
        linked_path = parent_link / "credentials"
        linked_target = real_directory / "credentials"
        linked_target.touch(mode=0o600)
        linked_target.chmod(0o600)
        with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "no symlinks"):
            seed_wrapper.open_private_regular_file(linked_path, os.getuid())

    def test_compose_command_uses_only_the_named_project_and_nonroot_stdin(self) -> None:
        endpoint = Path(f"/run/user/{os.getuid()}/docker.sock")
        command = seed_wrapper.build_compose_command(self.root, self.root / "i5.env", endpoint)
        self.assertEqual(
            command[0:6],
            ["docker", "--host", f"unix://{endpoint}", "compose", "-p", "belib-isolated-i5"],
        )
        self.assertEqual(command[6:8], ["--project-directory", str(self.root)])
        self.assertEqual(command[command.index("--env-file") + 1], str(self.root / "i5.env"))
        self.assertTrue(command[command.index("-f") + 1].endswith("compose.staging.yaml"))
        self.assertEqual(command[command.index("--user") + 1], "node")
        self.assertEqual(command[-3:], ["backend", "node", "dist/scripts/seed-staging.js"])
        self.assertIn("-i", command)
        self.assertIn("-T", command)
        self.assertNotIn("--volume", command)
        self.assertNotIn("-v", command)

    def test_direct_seed_invocation_is_rejected(self) -> None:
        stderr = StringIO()
        with (
            patch.dict(os.environ, {"PATH": "/usr/bin:/bin", "HOME": "/home/hiroyuki"}, clear=True),
            redirect_stderr(stderr),
        ):
            result = seed_wrapper.main()

        self.assertEqual(result, 1)
        self.assertIn("only through scripts/staging/launch-guard.py", stderr.getvalue())

    def test_pythonpath_startup_injection_is_rejected(self) -> None:
        stderr = StringIO()
        with (
            patch.dict(
                os.environ,
                {"PATH": "/usr/bin:/bin", "HOME": "/home/hiroyuki", "PYTHONPATH": "/tmp/attacker"},
                clear=True,
            ),
            redirect_stderr(stderr),
        ):
            result = seed_wrapper.main()

        self.assertEqual(result, 1)
        self.assertIn("Python startup", stderr.getvalue())

    def test_direct_executable_ignores_sitecustomize_then_rejects_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory(prefix="belib-seed-sitecustomize-test-") as directory:
            root = Path(directory)
            marker = root / "sitecustomize-ran"
            (root / "sitecustomize.py").write_text(
                "import os\nfrom pathlib import Path\nPath(os.environ['SEED_STARTUP_MARKER']).write_text('ran')\n",
                encoding="ascii",
            )
            environment = {
                "PATH": "/usr/bin:/bin",
                "HOME": "/home/hiroyuki",
                "PYTHONPATH": str(root),
                "SEED_STARTUP_MARKER": str(marker),
            }
            result = subprocess.run(
                [str(MODULE_PATH)],
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            marker_was_created = marker.exists()

        self.assertEqual(result.returncode, 1)
        self.assertIn("Python startup", result.stderr)
        self.assertFalse(marker_was_created, "PYTHONPATH sitecustomize ran before the seed guard")


class SeedWrapperDockerGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="belib-seed-docker-test-")
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_rejects_ambient_docker_compose_and_stage_overrides(self) -> None:
        for variable in (
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_CONFIG",
            "BUILDX_BUILDER",
            "BUILDX_CONFIG",
            "BUILDKIT_HOST",
            "COMPOSE_FILE",
            "COMPOSE_PROJECT_NAME",
            "BELIB_STAGE_PORT",
            "BELIB_STAGE_DB_BOOTSTRAP_PASSWORD",
            "BELIB_STAGE_DB_APP_PASSWORD",
            "BELIB_STAGE_DB_MIGRATE_PASSWORD",
            "POSTGRES_PASSWORD",
            "POSTGRES_USER",
            "DATABASE_URL",
            "DIRECT_URL",
            "BETTER_AUTH_SECRET",
            "R2_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "S3_ENDPOINT",
            "PRODUCTION_DATABASE_URL",
            "PROD_API_URL",
            "VITE_PUBLIC_DEMO_API_URL",
            "SSH_AUTH_SOCK",
            "SSH_AGENT_PID",
            "GIT_SSH_COMMAND",
        ):
            with self.subTest(variable=variable):
                with self.assertRaises(seed_wrapper.SeedWrapperError):
                    seed_wrapper.reject_ambient_overrides({variable: ""})

    def test_accepts_only_a_private_users_owned_socket_without_symlinks(self) -> None:
        socket_path = self.root / "docker.sock"
        docker_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            docker_socket.bind(str(socket_path))
            seed_wrapper.validate_rootless_socket(socket_path, os.getuid())
        finally:
            docker_socket.close()

        socket_path.unlink()
        socket_path.touch(mode=0o600)
        with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "not this user's socket"):
            seed_wrapper.validate_rootless_socket(socket_path, os.getuid())

        socket_path.unlink()
        target = self.root / "target.sock"
        target_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            target_socket.bind(str(target))
            socket_path.symlink_to(target)
            with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "not this user's socket"):
                seed_wrapper.validate_rootless_socket(socket_path, os.getuid())
        finally:
            target_socket.close()

        socket_path.unlink()
        real_directory = self.root / "real-runtime"
        real_directory.mkdir(mode=0o700)
        parent_socket = real_directory / "docker.sock"
        parent_docker_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            parent_docker_socket.bind(str(parent_socket))
            linked_directory = self.root / "linked-runtime"
            linked_directory.symlink_to(real_directory, target_is_directory=True)
            with self.assertRaises(seed_wrapper.SeedWrapperError):
                seed_wrapper.validate_rootless_socket(linked_directory / "docker.sock", os.getuid())
        finally:
            parent_docker_socket.close()

    def test_daemon_identity_probe_is_explicit_timed_and_nonsecret(self) -> None:
        endpoint = Path(f"/run/user/{os.getuid()}/docker.sock")
        config = self.root / "docker-config"
        config.mkdir(mode=0o700)
        environment = seed_wrapper.docker_environment(Path("/home/hiroyuki"), config)
        observed = {}

        def runner(command, **kwargs):
            observed["command"] = command
            observed["kwargs"] = kwargs
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='ubuntu-worker|["name=seccomp,profile=builtin","name=rootless"]\n',
                stderr=None,
            )

        seed_wrapper.verify_rootless_docker("/usr/bin/docker", endpoint, environment, runner=runner)
        self.assertEqual(
            observed["command"],
            [
                "/usr/bin/docker",
                "--host",
                f"unix://{endpoint}",
                "info",
                "--format",
                "{{.Name}}|{{json .SecurityOptions}}",
            ],
        )
        self.assertEqual(observed["kwargs"]["timeout"], seed_wrapper.DOCKER_INFO_TIMEOUT_SECONDS)
        self.assertEqual(observed["kwargs"]["stdin"], subprocess.DEVNULL)
        self.assertEqual(observed["kwargs"]["stderr"], subprocess.DEVNULL)
        self.assertEqual(set(environment), {"PATH", "HOME", "USER", "LOGNAME", "DOCKER_CONFIG"})
        self.assertEqual(environment["DOCKER_CONFIG"], str(config))
        self.assertFalse(any(name.startswith(("COMPOSE_", "BELIB_STAGE_")) for name in environment))
        self.assertNotIn("DOCKER_HOST", environment)
        self.assertNotIn("DOCKER_CONTEXT", environment)

    def test_daemon_identity_probe_fails_closed_for_rootful_mismatch_or_timeout(self) -> None:
        endpoint = Path(f"/run/user/{os.getuid()}/docker.sock")
        config = self.root / "docker-config"
        config.mkdir(mode=0o700)
        environment = seed_wrapper.docker_environment(Path("/home/hiroyuki"), config)
        for output in (
            'ubuntu-worker|["name=seccomp,profile=builtin"]\n',
            'remote-host|["name=rootless"]\n',
        ):
            with self.subTest(output=output):
                runner = lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout=output)
                with self.assertRaises(seed_wrapper.SeedWrapperError):
                    seed_wrapper.verify_rootless_docker("/usr/bin/docker", endpoint, environment, runner=runner)

        def timeout(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "bounded identity check"):
            seed_wrapper.verify_rootless_docker("/usr/bin/docker", endpoint, environment, runner=timeout)

        failed_command = lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="")
        with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "bounded identity check"):
            seed_wrapper.verify_rootless_docker("/usr/bin/docker", endpoint, environment, runner=failed_command)

        def spawn_error(command, **kwargs):
            raise OSError("mocked spawn failure")

        with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "bounded identity check"):
            seed_wrapper.verify_rootless_docker("/usr/bin/docker", endpoint, environment, runner=spawn_error)

    def test_user_home_plugin_and_config_plugin_takeovers_are_not_selected(self) -> None:
        home_plugin_dir = self.root / ".docker" / "cli-plugins"
        home_plugin_dir.mkdir(parents=True)
        (home_plugin_dir / "docker-compose").write_text("fake user plugin", encoding="ascii")
        config = self.root / "isolated-docker-config"
        config.mkdir(mode=0o700)

        environment = seed_wrapper.docker_environment(self.root, config)
        self.assertEqual(environment["DOCKER_CONFIG"], str(config))
        self.assertNotEqual(Path(environment["DOCKER_CONFIG"]), self.root / ".docker")
        seed_wrapper.verify_private_docker_config(config, os.getuid())

        config_plugins = config / "cli-plugins"
        config_plugins.mkdir()
        (config_plugins / "docker-compose").write_text("fake config plugin", encoding="ascii")
        with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "must be empty"):
            seed_wrapper.verify_private_docker_config(config, os.getuid())

    def test_trusted_plugins_are_root_owned_and_duplicate_paths_are_rejected(self) -> None:
        system = self.root / "system"
        usr = system / "usr"
        bin_dir = usr / "bin"
        exec_dir = usr / "libexec" / "docker"
        plugin_dir = exec_dir / "cli-plugins"
        alternate_dir = usr / "lib" / "docker" / "cli-plugins"
        plugin_dir.mkdir(parents=True)
        alternate_dir.mkdir(parents=True)
        docker_cli = bin_dir / "docker"
        docker_cli.parent.mkdir(parents=True)
        docker_cli.write_text("system docker", encoding="ascii")
        for plugin_name in ("docker-compose", "docker-buildx"):
            plugin = plugin_dir / plugin_name
            plugin.write_text("system plugin", encoding="ascii")
            plugin.chmod(0o755)

        real_lstat = os.lstat

        def root_owned_lstat(path):
            metadata = real_lstat(path)
            mode = metadata.st_mode
            if stat.S_ISDIR(mode):
                mode = stat.S_IFDIR | 0o755
            elif stat.S_ISREG(mode):
                mode = stat.S_IFREG | 0o755
            return SimpleNamespace(st_mode=mode, st_uid=0)

        parents = (
            usr,
            bin_dir,
            usr / "local",
            usr / "local" / "lib",
            usr / "local" / "lib" / "docker",
            usr / "local" / "lib" / "docker" / "cli-plugins",
            usr / "local" / "libexec",
            usr / "local" / "libexec" / "docker",
            usr / "local" / "libexec" / "docker" / "cli-plugins",
            usr / "lib",
            usr / "lib" / "docker",
            alternate_dir,
            usr / "libexec",
            exec_dir,
            plugin_dir,
        )
        search_dirs = (usr / "local" / "lib" / "docker" / "cli-plugins",
                       usr / "local" / "libexec" / "docker" / "cli-plugins",
                       alternate_dir,
                       plugin_dir)
        with (
            patch.object(seed_wrapper, "DOCKER_CLI", str(docker_cli)),
            patch.object(seed_wrapper, "DOCKER_PLUGIN_DIR", plugin_dir),
            patch.object(seed_wrapper, "DOCKER_PLUGIN_PARENTS", parents),
            patch.object(seed_wrapper, "DOCKER_PLUGIN_SEARCH_DIRS", search_dirs),
        ):
            seed_wrapper.verify_trusted_docker_plugins(lstat=root_owned_lstat)

            duplicate = alternate_dir / "docker-compose"
            duplicate.write_text("shadow plugin", encoding="ascii")
            with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "alternate Docker CLI plugin"):
                seed_wrapper.verify_trusted_docker_plugins(lstat=root_owned_lstat)

            duplicate.unlink()
            fake_lstat = lambda path: SimpleNamespace(st_mode=stat.S_IFREG | 0o777, st_uid=0) if path == plugin_dir / "docker-buildx" else root_owned_lstat(path)
            with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "0755"):
                seed_wrapper.verify_trusted_docker_plugins(lstat=fake_lstat)

    def test_main_does_not_open_credentials_if_docker_guard_fails(self) -> None:
        account = SimpleNamespace(pw_uid=os.getuid(), pw_dir=str(self.root))
        stderr = StringIO()
        env_fd = seed_wrapper.create_sealed_env_snapshot(staging_env_contents())
        with (
            patch.object(seed_wrapper.pwd, "getpwnam", return_value=account),
            patch.object(seed_wrapper.socket, "gethostname", return_value="ubuntu-worker"),
            patch.object(
                seed_wrapper,
                "validate_staging_admission",
                return_value=(env_fd, {"BELIB_STAGE_VCS_REF": TEST_SOURCE_SHA}),
            ),
            patch.object(seed_wrapper, "validate_docker_runtime", side_effect=seed_wrapper.SeedWrapperError("guard")),
            patch.object(seed_wrapper, "open_private_regular_file") as open_credentials,
            patch.dict(os.environ, {}, clear=True),
            redirect_stderr(stderr),
        ):
            result = seed_wrapper.main(
                invoked_by_launcher=True,
                build_root=make_verified_snapshot(self),
                source_sha=TEST_SOURCE_SHA,
            )

        self.assertEqual(result, 1)
        open_credentials.assert_not_called()
        self.assertIn("BLOCKED:", stderr.getvalue())


class SeedWrapperAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="belib-seed-admission-test-")
        self.root = Path(self.temporary_directory.name)
        self.config_directory = self.root / ".config" / "belib-staging"
        self.config_directory.mkdir(parents=True)
        self.env_file = self.config_directory / "i5.env"
        self.credentials = self.config_directory / "seed-credentials"
        self.write_env()
        self.credentials.write_text("synthetic only\n", encoding="ascii")
        self.credentials.chmod(0o600)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_env(self, contents: bytes | None = None, mode: int = 0o600) -> None:
        self.env_file.write_bytes(staging_env_contents() if contents is None else contents)
        self.env_file.chmod(mode)

    @staticmethod
    def git_runner(source_sha: str = TEST_SOURCE_SHA):
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout=f"{source_sha}\n", stderr=None)

        return runner

    @staticmethod
    def status_probe(output: bytes = b"", *, ready: bool = True, returncode: int = 0):
        process = FakeStatusProcess(output=output, returncode=returncode)
        selector = FakeStatusSelector(ready=ready)

        def popen(command, **kwargs):
            return process

        return process, popen, lambda: selector

    def test_accepts_exact_env_and_returns_sealed_snapshot(self) -> None:
        with patch.object(seed_wrapper, "verify_reviewed_source") as verify_source:
            env_fd, values = seed_wrapper.validate_staging_admission(
                self.root,
                self.env_file,
                os.getuid(),
            )
        try:
            self.assertEqual(values["BELIB_STAGE_VCS_REF"], TEST_SOURCE_SHA)
            self.assertEqual(os.read(env_fd, seed_wrapper.ENV_FILE_MAX_BYTES), staging_env_contents())
            seals = fcntl.fcntl(env_fd, fcntl.F_GET_SEALS)
            required_seals = fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE
            self.assertEqual(seals & required_seals, required_seals)
            verify_source.assert_called_once_with(self.root, TEST_SOURCE_SHA)
        finally:
            os.close(env_fd)

    def test_rejects_env_file_symlink_wrong_owner_and_loose_mode(self) -> None:
        target = self.root / "env-target"
        target.write_bytes(staging_env_contents())
        target.chmod(0o600)
        self.env_file.unlink()
        self.env_file.symlink_to(target)
        with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "no symlinks"):
            seed_wrapper.open_staging_env_file(self.env_file, os.getuid())

        self.env_file.unlink()
        self.write_env(mode=0o644)
        with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "0600 or 0400"):
            seed_wrapper.open_staging_env_file(self.env_file, os.getuid())
        with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "owned by hiroyuki"):
            seed_wrapper.open_staging_env_file(self.env_file, os.getuid() + 1)

        self.write_env(mode=0o400)
        fd = seed_wrapper.open_staging_env_file(self.env_file, os.getuid())
        os.close(fd)

        real_directory = self.config_directory / "real"
        real_directory.mkdir()
        real_env = real_directory / "i5.env"
        real_env.write_bytes(staging_env_contents())
        real_env.chmod(0o600)
        linked_directory = self.config_directory / "linked"
        linked_directory.symlink_to(real_directory, target_is_directory=True)
        with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "no symlinks"):
            seed_wrapper.open_staging_env_file(linked_directory / "i5.env", os.getuid())

    def test_rejects_malformed_or_unsafe_env_values_before_source_check(self) -> None:
        invalid_contents = (
            staging_env_contents() + b"DATABASE_URL=synthetic\n",
            staging_env_contents() + b"POSTGRES_PASSWORD=synthetic\n",
            staging_env_contents() + b"R2_ACCESS_KEY_ID=synthetic\n",
            staging_env_contents() + b"BELIB_STAGE_DB_ROOT_PASSWORD=synthetic\n",
            b"x" * (seed_wrapper.ENV_FILE_MAX_BYTES + 1),
            staging_env_contents().replace(b"BELIB_STAGE_PORT=18080\n", b""),
            staging_env_contents().replace(b"BELIB_STAGE_PORT=18080\n", b"BELIB_STAGE_PORT=18080\nBELIB_STAGE_PORT=18081\n"),
            staging_env_contents(BELIB_STAGE_DB_APP_PASSWORD="x" * 64),
            staging_env_contents(BELIB_STAGE_VCS_REF="A" * 40),
            staging_env_contents(BELIB_STAGE_PORT="0800"),
            staging_env_contents(BELIB_STAGE_AUTH_SECRET=TEST_DB_PASSWORD),
            staging_env_contents(BELIB_STAGE_DB_MIGRATE_PASSWORD=TEST_DB_PASSWORD),
            staging_env_contents(BELIB_STAGE_DB_BOOTSTRAP_PASSWORD=TEST_AUTH_SECRET),
        )
        for index, contents in enumerate(invalid_contents):
            with self.subTest(index=index), patch.object(seed_wrapper, "verify_reviewed_source") as verify_source:
                self.write_env(contents)
                with self.assertRaises(seed_wrapper.SeedWrapperError):
                    seed_wrapper.validate_staging_admission(self.root, self.env_file, os.getuid())
                verify_source.assert_not_called()

    def run_postgres_role_init(self, **overrides: str) -> tuple[subprocess.CompletedProcess[str], Path]:
        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir(exist_ok=True)
        capture_path = self.root / "psql-capture"
        psql_stub = fake_bin / "psql"
        psql_stub.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "capture = open(os.environ['TEST_PSQL_CAPTURE'], 'wb')\n"
            "capture.write(('\\0'.join(sys.argv[1:])).encode('utf-8'))\n"
            "capture.write(b'\\n--STDIN--\\n')\n"
            "capture.write(sys.stdin.buffer.read())\n"
            "capture.write(b'\\n--ROLE-PASSWORD-ENV--\\n')\n"
            "for name in ('BELIB_STAGE_DB_APP_PASSWORD', 'BELIB_STAGE_DB_MIGRATE_PASSWORD'):\n"
            "    capture.write(f'{name}={int(name in os.environ)}\\n'.encode('ascii'))\n"
            "capture.close()\n",
            encoding="ascii",
        )
        psql_stub.chmod(0o700)

        environment = {
            "PATH": f"{fake_bin}:{os.defpath}",
            "HOME": str(self.root),
            "DATABASE_ALREADY_EXISTS": "",
            "POSTGRES_USER": "belib_staging_bootstrap",
            "POSTGRES_DB": "belib_staging",
            "POSTGRES_PASSWORD": TEST_BOOTSTRAP_PASSWORD,
            "BELIB_STAGE_DB_APP_PASSWORD": TEST_DB_PASSWORD,
            "BELIB_STAGE_DB_MIGRATE_PASSWORD": TEST_MIGRATE_PASSWORD,
            "ROLE_INIT_SCRIPT": str(ROLE_INIT_SCRIPT),
            "TEST_PSQL_CAPTURE": str(capture_path),
        }
        environment.update(overrides)
        result = subprocess.run(
            [
                "/bin/bash",
                "-Ee",
                "-o",
                "pipefail",
                "-c",
                'source "$ROLE_INIT_SCRIPT"; if [[ ! -v BELIB_STAGE_DB_APP_PASSWORD && ! -v BELIB_STAGE_DB_MIGRATE_PASSWORD && ! -v value && "$-" != *u* ]]; then printf "ROLE_CREDENTIALS_CLEARED\\n"; else exit 23; fi',
            ],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        return result, capture_path

    def test_postgres_role_init_uses_fresh_volume_guard_and_secret_safe_psql_prompts(self) -> None:
        self.assertFalse(ROLE_INIT_SCRIPT.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
        result, capture_path = self.run_postgres_role_init()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ROLE_CREDENTIALS_CLEARED\n")

        psql_input = capture_path.read_bytes()
        arguments, psql_input = psql_input.split(b"\n--STDIN--\n", 1)
        sql_and_prompts, role_password_environment = psql_input.split(b"\n--ROLE-PASSWORD-ENV--\n", 1)
        self.assertIn(b"--no-psqlrc", arguments)
        self.assertIn(b"--no-password", arguments)
        self.assertIn(b"ON_ERROR_STOP=1", arguments)
        self.assertIn(b"belib_staging_bootstrap", arguments)
        self.assertIn(b"belib_staging", arguments)
        for password in (TEST_DB_PASSWORD.encode(), TEST_MIGRATE_PASSWORD.encode()):
            self.assertNotIn(password, arguments)
            self.assertEqual(sql_and_prompts.count(password), 2)
        self.assertNotIn(TEST_BOOTSTRAP_PASSWORD.encode(), sql_and_prompts)
        self.assertEqual(
            role_password_environment,
            b"BELIB_STAGE_DB_APP_PASSWORD=0\nBELIB_STAGE_DB_MIGRATE_PASSWORD=0\n",
        )
        self.assertNotIn(TEST_DB_PASSWORD, result.stdout)
        self.assertNotIn(TEST_MIGRATE_PASSWORD, result.stderr)

        init_lines = sql_and_prompts.splitlines()
        app_password_line = init_lines.index(b"\\password belib_staging_app")
        migrate_password_line = init_lines.index(b"\\password belib_staging_migrate")
        self.assertEqual(init_lines[app_password_line + 1 : app_password_line + 3], [TEST_DB_PASSWORD.encode()] * 2)
        self.assertEqual(init_lines[migrate_password_line + 1 : migrate_password_line + 3], [TEST_MIGRATE_PASSWORD.encode()] * 2)
        self.assertIn(b"LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS", sql_and_prompts)
        self.assertIn(b"ALTER SCHEMA public OWNER TO belib_staging_migrate", sql_and_prompts)
        self.assertIn(b"REVOKE CREATE ON DATABASE belib_staging FROM belib_staging_migrate", sql_and_prompts)
        self.assertIn(b"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO belib_staging_app", sql_and_prompts)
        self.assertIn(b"GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO belib_staging_app", sql_and_prompts)
        self.assertIn(b"belib-staging-role-bootstrap-v1", sql_and_prompts)
        self.assertIn(b"role_assertions", sql_and_prompts)

    def test_postgres_role_init_refuses_existing_volume_and_sql_unsafe_credentials(self) -> None:
        existing_volume, existing_capture = self.run_postgres_role_init(DATABASE_ALREADY_EXISTS="true")
        self.assertNotEqual(existing_volume.returncode, 0)
        self.assertRegex(existing_volume.stderr, "fresh data volume")
        self.assertFalse(existing_capture.exists())

        unsafe_password = "a" * 63 + "'"
        unsafe_value, unsafe_capture = self.run_postgres_role_init(BELIB_STAGE_DB_APP_PASSWORD=unsafe_password)
        self.assertNotEqual(unsafe_value.returncode, 0)
        self.assertIn("BELIB_STAGE_DB_APP_PASSWORD", unsafe_value.stderr)
        self.assertNotIn(unsafe_password, unsafe_value.stderr)
        self.assertFalse(unsafe_capture.exists())

    def test_rejects_revision_mismatch_before_checking_worktree(self) -> None:
        process, popen, selector_factory = self.status_probe()
        with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "HEAD differs"):
            seed_wrapper.verify_reviewed_source(
                self.root,
                TEST_SOURCE_SHA,
                git_runner=self.git_runner("b" * 40),
                popen=popen,
                selector_factory=selector_factory,
            )
        self.assertIsNone(process.returncode)

    def test_exact_source_context_uses_isolated_python_and_fails_closed(self) -> None:
        observed = {}

        def runner(command, **kwargs):
            observed["command"] = command
            observed["kwargs"] = kwargs
            return subprocess.CompletedProcess(command, 0)

        seed_wrapper.verify_exact_source_context(self.root, TEST_SOURCE_SHA, runner=runner)
        self.assertEqual(observed["command"][1:3], ["-I", "-B"])
        self.assertEqual(observed["command"][-2:], ["--source-sha", TEST_SOURCE_SHA])
        self.assertEqual(observed["kwargs"]["cwd"], self.root)
        self.assertEqual(observed["kwargs"]["stdin"], subprocess.DEVNULL)
        self.assertEqual(observed["kwargs"]["stdout"], subprocess.DEVNULL)
        self.assertEqual(observed["kwargs"]["stderr"], subprocess.DEVNULL)
        self.assertEqual(observed["kwargs"]["timeout"], seed_wrapper.SOURCE_CHECK_TIMEOUT_SECONDS)
        self.assertNotIn("PYTHONPATH", observed["kwargs"]["env"])
        self.assertNotIn("GIT_DIR", observed["kwargs"]["env"])

        with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "build inputs"):
            seed_wrapper.verify_exact_source_context(
                self.root,
                TEST_SOURCE_SHA,
                runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 1),
            )

    def test_git_admission_uses_only_root_owned_system_git(self) -> None:
        git_path = self.root / "git"
        git_path.write_text("trusted system Git fixture", encoding="ascii")
        git_path.chmod(0o755)
        self.assertEqual(seed_wrapper.git_command(self.root, "status")[0], "/usr/bin/git")

        def root_owned_lstat(path):
            if str(path) == str(git_path):
                return SimpleNamespace(st_mode=stat.S_IFREG | 0o755, st_uid=0)
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)

        with patch.object(seed_wrapper, "GIT_CLI", str(git_path)):
            seed_wrapper.verify_trusted_git_cli(lstat=root_owned_lstat)

            def writable_lstat(path):
                if str(path) == str(git_path):
                    return SimpleNamespace(st_mode=stat.S_IFREG | 0o775, st_uid=0)
                return root_owned_lstat(path)

            with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "non-writable executable"):
                seed_wrapper.verify_trusted_git_cli(lstat=writable_lstat)

    def test_rejects_dirty_worktree_after_reading_only_one_status_byte(self) -> None:
        process, popen, selector_factory = self.status_probe(b"M" * 100_000)
        with patch.object(seed_wrapper, "verify_exact_source_context"):
            with self.assertRaisesRegex(seed_wrapper.SeedWrapperError, "worktree must be clean"):
                seed_wrapper.verify_reviewed_source(
                    self.root,
                    TEST_SOURCE_SHA,
                    git_runner=self.git_runner(),
                    popen=popen,
                    selector_factory=selector_factory,
                )
        self.assertEqual(process.reads, [1])
        self.assertTrue(process.killed)

    def test_accepts_matching_sha_and_clean_worktree(self) -> None:
        process, popen, selector_factory = self.status_probe()
        with patch.object(seed_wrapper, "verify_exact_source_context"):
            seed_wrapper.verify_reviewed_source(
                self.root,
                TEST_SOURCE_SHA,
                git_runner=self.git_runner(),
                popen=popen,
                selector_factory=selector_factory,
            )
        self.assertEqual(process.returncode, 0)
        self.assertEqual(process.reads, [1])

    def test_missing_or_mismatched_snapshot_fails_before_env_docker_or_credentials(self) -> None:
        valid_snapshot = make_verified_snapshot(self)
        cases = (
            ("missing snapshot", None, TEST_SOURCE_SHA),
            ("wrong snapshot SHA", valid_snapshot, "b" * 40),
            ("checkout instead of snapshot", REPO_ROOT, TEST_SOURCE_SHA),
        )
        for case, build_root, source_sha in cases:
            with self.subTest(case=case):
                stderr = StringIO()
                with (
                    patch.object(seed_wrapper, "validate_staging_admission") as env_admission,
                    patch.object(seed_wrapper, "create_private_docker_config") as docker_config,
                    patch.object(seed_wrapper, "validate_docker_runtime") as docker_runtime,
                    patch.object(seed_wrapper, "open_private_regular_file") as open_credentials,
                    patch.object(seed_wrapper.subprocess, "run") as compose_run,
                    patch.dict(os.environ, {}, clear=True),
                    redirect_stderr(stderr),
                ):
                    result = seed_wrapper.main(
                        invoked_by_launcher=True,
                        build_root=build_root,
                        source_sha=source_sha,
                    )

                self.assertEqual(result, 1)
                self.assertIn("BLOCKED:", stderr.getvalue())
                env_admission.assert_not_called()
                docker_config.assert_not_called()
                docker_runtime.assert_not_called()
                open_credentials.assert_not_called()
                compose_run.assert_not_called()

    def test_seed_build_uses_snapshot_after_checkout_compose_is_mutated(self) -> None:
        checkout = self.root / "mutable-checkout"
        fake_seed_file = checkout / "scripts/staging/seed.py"
        fake_seed_file.parent.mkdir(parents=True)
        fake_seed_file.write_text("# seed module location fixture\n", encoding="ascii")
        mutable_compose = checkout / "compose.staging.yaml"
        mutable_compose.write_text(
            "name: attacker-controlled\nservices:\n  backend:\n    build: {context: /production}\n"
            "    env_file: /production/.env\n",
            encoding="ascii",
        )
        build_root = make_verified_snapshot(self)
        account = SimpleNamespace(pw_uid=os.getuid(), pw_dir=str(self.root))
        endpoint = Path(f"/run/user/{os.getuid()}/docker.sock")
        env_fd = seed_wrapper.create_sealed_env_snapshot(staging_env_contents())
        credential_fd = os.open(self.credentials, os.O_RDONLY)
        docker_config_temporary = tempfile.TemporaryDirectory(prefix="belib-seed-test-docker-")
        captured: dict[str, object] = {}

        def docker_runtime(uid, home, environment, docker_config):
            return (
                "/usr/bin/docker",
                endpoint,
                seed_wrapper.docker_environment(home, docker_config),
            )

        def compose_runner(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            env_path = Path(command[command.index("--env-file") + 1])
            env_descriptor = os.open(env_path, os.O_RDONLY)
            try:
                captured["staging_env"] = os.pread(
                    env_descriptor,
                    seed_wrapper.ENV_FILE_MAX_BYTES,
                    0,
                )
            finally:
                os.close(env_descriptor)
            return subprocess.CompletedProcess(command, 0)

        try:
            with (
                patch.object(seed_wrapper, "__file__", str(fake_seed_file)),
                patch.object(seed_wrapper.pwd, "getpwnam", return_value=account),
                patch.object(seed_wrapper.socket, "gethostname", return_value="ubuntu-worker"),
                patch.object(
                    seed_wrapper,
                    "validate_staging_admission",
                    return_value=(env_fd, {"BELIB_STAGE_VCS_REF": TEST_SOURCE_SHA}),
                ) as env_admission,
                patch.object(seed_wrapper, "create_private_docker_config", return_value=docker_config_temporary),
                patch.object(seed_wrapper, "validate_docker_runtime", side_effect=docker_runtime),
                patch.object(seed_wrapper, "verify_private_docker_config"),
                patch.object(seed_wrapper, "verify_trusted_docker_plugins"),
                patch.object(seed_wrapper, "open_private_regular_file", return_value=credential_fd) as open_credentials,
                patch.object(seed_wrapper.os, "dup2"),
                patch.object(seed_wrapper.subprocess, "run", side_effect=compose_runner),
                patch.dict(os.environ, {}, clear=True),
            ):
                result = seed_wrapper.main(
                    invoked_by_launcher=True,
                    build_root=build_root,
                    source_sha=TEST_SOURCE_SHA,
                )
        finally:
            docker_config_temporary.cleanup()

        self.assertEqual(result, 0)
        env_admission.assert_called_once_with(checkout, Path(self.root / ".config/belib-staging/i5.env"), os.getuid())
        open_credentials.assert_called_once_with(
            self.root / ".config/belib-staging/seed-credentials", os.getuid()
        )
        command = captured["command"]
        kwargs = captured["kwargs"]
        self.assertEqual(kwargs["cwd"], build_root)
        self.assertEqual(command[command.index("--project-directory") + 1], str(build_root))
        self.assertEqual(command[command.index("-f") + 1], str(build_root / "compose.staging.yaml"))
        self.assertIn("/production", mutable_compose.read_text(encoding="ascii"))

        snapshot_compose = (build_root / "compose.staging.yaml").read_text(encoding="utf-8")
        self.assertNotIn("/production", snapshot_compose)
        self.assertNotIn("PRODUCTION_DATABASE_URL", snapshot_compose)
        self.assertNotIn("R2_ACCESS_KEY_ID", snapshot_compose)
        self.assertIn("context: ./backend", snapshot_compose)
        self.assertIn("context: ./frontend", snapshot_compose)
        for context, dockerfile in (
            ("backend", "../scripts/staging/Dockerfile.backend.staging"),
            ("frontend", "../scripts/staging/Dockerfile.frontend.staging"),
        ):
            context_path = (build_root / context).resolve()
            dockerfile_path = (context_path / dockerfile).resolve()
            self.assertTrue(context_path.is_relative_to(build_root))
            self.assertTrue(dockerfile_path.is_relative_to(build_root))
            self.assertTrue(dockerfile_path.is_file())

        environment = kwargs["env"]
        self.assertEqual(set(environment), {"PATH", "HOME", "USER", "LOGNAME", "DOCKER_CONFIG"})
        staging_keys = {
            line.split(b"=", 1)[0].decode("ascii")
            for line in captured["staging_env"].splitlines()
        }
        self.assertEqual(staging_keys, seed_wrapper.STAGING_ENV_KEYS)
        self.assertFalse(any(key.startswith(("R2_", "AWS_", "PRODUCTION_", "PROD_")) for key in environment))

    def test_main_never_opens_credentials_for_admission_failures(self) -> None:
        account = SimpleNamespace(pw_uid=os.getuid(), pw_dir=str(self.root))
        failure_cases = (
            ("revision mismatch", None, TEST_SOURCE_SHA, None),
            ("dirty source", None, TEST_SOURCE_SHA, seed_wrapper.SeedWrapperError("dirty")),
            ("extra env key", staging_env_contents() + b"DATABASE_URL=synthetic\n", TEST_SOURCE_SHA, None),
            ("DB and auth secret reuse", staging_env_contents(BELIB_STAGE_AUTH_SECRET=TEST_DB_PASSWORD), TEST_SOURCE_SHA, None),
            ("loose env mode", None, TEST_SOURCE_SHA, None),
            ("env symlink", None, TEST_SOURCE_SHA, None),
            ("wrong env owner", None, TEST_SOURCE_SHA, None),
        )
        for case, contents, source_sha, dirty_error in failure_cases:
            with self.subTest(case=case):
                if self.env_file.is_symlink():
                    self.env_file.unlink()
                self.write_env(staging_env_contents() if contents is None else contents)
                self.env_file.chmod(0o600)
                if case == "env symlink":
                    self.env_file.unlink()
                    target = self.root / "env-target"
                    target.write_bytes(staging_env_contents())
                    target.chmod(0o600)
                    self.env_file.symlink_to(target)
                if case == "loose env mode":
                    self.env_file.chmod(0o644)

                real_fstat = os.fstat

                def wrong_owner(file_descriptor):
                    metadata = real_fstat(file_descriptor)
                    descriptor_path = Path(os.readlink(f"/proc/self/fd/{file_descriptor}"))
                    if descriptor_path.name == seed_wrapper.SNAPSHOT_REVISION_FILE:
                        return metadata
                    return SimpleNamespace(
                        st_dev=metadata.st_dev,
                        st_ino=metadata.st_ino,
                        st_uid=os.getuid() + 1,
                        st_mode=metadata.st_mode,
                        st_size=metadata.st_size,
                        st_mtime_ns=metadata.st_mtime_ns,
                        st_ctime_ns=metadata.st_ctime_ns,
                    )

                stdout = StringIO()
                fstat_patch = patch.object(seed_wrapper.os, "fstat", side_effect=wrong_owner) if case == "wrong env owner" else nullcontext()
                source_sha_patch = patch.object(
                    seed_wrapper,
                    "read_source_sha",
                    return_value=("b" * 40 if case == "revision mismatch" else source_sha),
                )
                clean_patch = patch.object(
                    seed_wrapper,
                    "assert_clean_source_worktree",
                    side_effect=dirty_error,
                ) if dirty_error is not None else patch.object(seed_wrapper, "assert_clean_source_worktree")
                with (
                    patch.object(seed_wrapper.pwd, "getpwnam", return_value=account),
                    patch.object(seed_wrapper.socket, "gethostname", return_value="ubuntu-worker"),
                    source_sha_patch,
                    clean_patch,
                    patch.object(seed_wrapper, "validate_docker_runtime") as docker_guard,
                    patch.object(seed_wrapper, "open_private_regular_file") as open_credentials,
                    patch.dict(os.environ, {}, clear=True),
                    fstat_patch,
                    redirect_stderr(stdout),
                ):
                    result = seed_wrapper.main(
                        invoked_by_launcher=True,
                        build_root=make_verified_snapshot(self),
                        source_sha=TEST_SOURCE_SHA,
                    )

                self.assertEqual(result, 1)
                open_credentials.assert_not_called()
                docker_guard.assert_not_called()
                self.assertIn("BLOCKED:", stdout.getvalue())

    def test_main_opens_credentials_only_after_valid_admission(self) -> None:
        account = SimpleNamespace(pw_uid=os.getuid(), pw_dir=str(self.root))
        endpoint = Path(f"/run/user/{os.getuid()}/docker.sock")
        original_open_credentials = seed_wrapper.open_private_regular_file

        def docker_runtime(uid, home, environment, docker_config):
            return "/usr/bin/docker", endpoint, seed_wrapper.docker_environment(home, docker_config)

        with (
            patch.object(seed_wrapper.pwd, "getpwnam", return_value=account),
            patch.object(seed_wrapper.socket, "gethostname", return_value="ubuntu-worker"),
            patch.object(seed_wrapper, "read_source_sha", return_value=TEST_SOURCE_SHA),
            patch.object(seed_wrapper, "verify_exact_source_context"),
            patch.object(seed_wrapper, "assert_clean_source_worktree"),
            patch.object(seed_wrapper, "validate_docker_runtime", side_effect=docker_runtime) as docker_guard,
            patch.object(seed_wrapper, "verify_trusted_docker_plugins"),
            patch.object(seed_wrapper, "open_private_regular_file", wraps=original_open_credentials) as open_credentials,
            patch.object(seed_wrapper.os, "dup2") as duplicate_stdin,
            patch.object(
                seed_wrapper.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0),
            ) as run_command,
            patch.dict(os.environ, {}, clear=True),
        ):
            result = seed_wrapper.main(
                invoked_by_launcher=True,
                build_root=make_verified_snapshot(self),
                source_sha=TEST_SOURCE_SHA,
            )

        self.assertEqual(result, 0)
        open_credentials.assert_called_once_with(self.credentials, os.getuid())
        self.assertIs(type(run_command.call_args.kwargs["stdin"]), int)
        with self.assertRaises(OSError):
            os.fstat(run_command.call_args.kwargs["stdin"])
        self.assertEqual(run_command.call_args.kwargs["timeout"], 900)
        duplicate_stdin.assert_not_called()
        command = run_command.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/docker")
        self.assertEqual(
            run_command.call_args.kwargs["env"]["DOCKER_CONFIG"],
            str(docker_guard.call_args.args[3]),
        )
        env_file_argument = command[command.index("--env-file") + 1]
        self.assertRegex(env_file_argument, rf"^/proc/{os.getpid()}/fd/[0-9]+$")

    def test_borrowed_launcher_env_descriptor_survives_seed_success_and_timeout(self) -> None:
        endpoint = Path(f"/run/user/{os.getuid()}/docker.sock")
        snapshot = make_verified_snapshot(self)
        docker_env = {"PATH": "/usr/bin:/bin", "HOME": str(self.root), "DOCKER_CONFIG": "/tmp/private"}

        for timeout_case in (False, True):
            env_fd = seed_wrapper.create_sealed_env_snapshot(staging_env_contents())
            captured = {}
            credential_fds = []
            duplicate_env_fds = []

            def compose_runner(command, **kwargs):
                captured.update(kwargs)
                duplicate_env_fds.extend(kwargs["pass_fds"])
                if timeout_case:
                    raise subprocess.TimeoutExpired(command, kwargs["timeout"])
                return subprocess.CompletedProcess(command, 0)

            original_open = seed_wrapper.open_private_regular_file
            def open_credential(path, uid):
                fd = original_open(path, uid)
                credential_fds.append(fd)
                return fd

            try:
                with (
                    patch.object(seed_wrapper.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=os.getuid(), pw_dir=str(self.root))),
                    patch.object(seed_wrapper.socket, "gethostname", return_value="ubuntu-worker"),
                    patch.object(seed_wrapper, "open_private_regular_file", side_effect=open_credential),
                    patch.object(seed_wrapper.subprocess, "run", side_effect=compose_runner),
                    patch.dict(os.environ, {}, clear=True),
                    redirect_stderr(StringIO()),
                ):
                    result = seed_wrapper.main(
                        invoked_by_launcher=True, build_root=snapshot, source_sha=TEST_SOURCE_SHA,
                        admitted_env_fd=env_fd,
                        stage_values={"BELIB_STAGE_VCS_REF": TEST_SOURCE_SHA},
                        docker_cli="/usr/bin/docker", docker_endpoint=endpoint,
                        docker_environment=docker_env,
                    )
                self.assertEqual(result, 1 if timeout_case else 0)
                os.fstat(env_fd)  # The wrapper borrowed this descriptor and must leave it open.
                self.assertEqual(captured["stdin"], credential_fds[0])
                self.assertEqual(captured["timeout"], 900)
                self.assertIn("pass_fds", captured)
                self.assertNotIn(captured["stdin"], captured["pass_fds"])
                with self.assertRaises(OSError):
                    os.fstat(credential_fds[0])
                with self.assertRaises(OSError):
                    os.fstat(duplicate_env_fds[0])
            finally:
                os.close(env_fd)


if __name__ == "__main__":
    unittest.main()
