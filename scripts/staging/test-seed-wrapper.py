#!/usr/bin/env python3
"""Offline checks for the staging seed host-side credential guard."""

import importlib.util
import fcntl
import os
import socket
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
TEST_DB_PASSWORD = "1" * 64
TEST_AUTH_SECRET = "2" * 64


def staging_env_contents(**overrides: str) -> bytes:
    values = {
        "BELIB_STAGE_DB_PASSWORD": TEST_DB_PASSWORD,
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
            "COMPOSE_FILE",
            "COMPOSE_PROJECT_NAME",
            "BELIB_STAGE_PORT",
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
        environment = seed_wrapper.docker_environment(Path("/home/hiroyuki"))
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
        self.assertEqual(set(environment), {"PATH", "HOME", "USER", "LOGNAME"})
        self.assertFalse(any(name.startswith(("DOCKER_", "COMPOSE_", "BELIB_STAGE_")) for name in environment))

    def test_daemon_identity_probe_fails_closed_for_rootful_mismatch_or_timeout(self) -> None:
        endpoint = Path(f"/run/user/{os.getuid()}/docker.sock")
        environment = seed_wrapper.docker_environment(Path("/home/hiroyuki"))
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

    def test_main_does_not_open_credentials_if_docker_guard_fails(self) -> None:
        account = SimpleNamespace(pw_uid=os.getuid(), pw_dir=str(self.root))
        stderr = StringIO()
        env_fd = os.open(os.devnull, os.O_RDONLY)
        with (
            patch.object(seed_wrapper.pwd, "getpwnam", return_value=account),
            patch.object(seed_wrapper.socket, "gethostname", return_value="ubuntu-worker"),
            patch.object(seed_wrapper, "validate_staging_admission", return_value=(env_fd, {})),
            patch.object(seed_wrapper, "validate_docker_runtime", side_effect=seed_wrapper.SeedWrapperError("guard")),
            patch.object(seed_wrapper, "open_private_regular_file") as open_credentials,
            patch.dict(os.environ, {}, clear=True),
            redirect_stderr(stderr),
        ):
            result = seed_wrapper.main()

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
            b"x" * (seed_wrapper.ENV_FILE_MAX_BYTES + 1),
            staging_env_contents().replace(b"BELIB_STAGE_PORT=18080\n", b""),
            staging_env_contents().replace(b"BELIB_STAGE_PORT=18080\n", b"BELIB_STAGE_PORT=18080\nBELIB_STAGE_PORT=18081\n"),
            staging_env_contents(BELIB_STAGE_DB_PASSWORD="x" * 64),
            staging_env_contents(BELIB_STAGE_VCS_REF="A" * 40),
            staging_env_contents(BELIB_STAGE_PORT="0800"),
            staging_env_contents(BELIB_STAGE_AUTH_SECRET=TEST_DB_PASSWORD),
        )
        for index, contents in enumerate(invalid_contents):
            with self.subTest(index=index), patch.object(seed_wrapper, "verify_reviewed_source") as verify_source:
                self.write_env(contents)
                with self.assertRaises(seed_wrapper.SeedWrapperError):
                    seed_wrapper.validate_staging_admission(self.root, self.env_file, os.getuid())
                verify_source.assert_not_called()

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

    def test_rejects_dirty_worktree_after_reading_only_one_status_byte(self) -> None:
        process, popen, selector_factory = self.status_probe(b"M" * 100_000)
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
        seed_wrapper.verify_reviewed_source(
            self.root,
            TEST_SOURCE_SHA,
            git_runner=self.git_runner(),
            popen=popen,
            selector_factory=selector_factory,
        )
        self.assertEqual(process.returncode, 0)
        self.assertEqual(process.reads, [1])

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
                    result = seed_wrapper.main()

                self.assertEqual(result, 1)
                open_credentials.assert_not_called()
                docker_guard.assert_not_called()
                self.assertIn("BLOCKED:", stdout.getvalue())

    def test_main_opens_credentials_only_after_valid_admission(self) -> None:
        account = SimpleNamespace(pw_uid=os.getuid(), pw_dir=str(self.root))
        endpoint = Path(f"/run/user/{os.getuid()}/docker.sock")
        environment = seed_wrapper.docker_environment(self.root)
        original_open_credentials = seed_wrapper.open_private_regular_file
        with (
            patch.object(seed_wrapper.pwd, "getpwnam", return_value=account),
            patch.object(seed_wrapper.socket, "gethostname", return_value="ubuntu-worker"),
            patch.object(seed_wrapper, "read_source_sha", return_value=TEST_SOURCE_SHA),
            patch.object(seed_wrapper, "assert_clean_source_worktree"),
            patch.object(seed_wrapper, "validate_docker_runtime", return_value=("/usr/bin/docker", endpoint, environment)),
            patch.object(seed_wrapper, "open_private_regular_file", wraps=original_open_credentials) as open_credentials,
            patch.object(seed_wrapper.os, "dup2") as duplicate_stdin,
            patch.object(seed_wrapper.os, "execve") as exec_command,
            patch.dict(os.environ, {}, clear=True),
        ):
            result = seed_wrapper.main()

        self.assertEqual(result, 1)
        open_credentials.assert_called_once_with(self.credentials, os.getuid())
        duplicate_stdin.assert_called_once()
        command = exec_command.call_args.args[1]
        env_file_argument = command[command.index("--env-file") + 1]
        self.assertRegex(env_file_argument, rf"^/proc/{os.getpid()}/fd/[0-9]+$")


if __name__ == "__main__":
    unittest.main()
