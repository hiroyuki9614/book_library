#!/usr/bin/env python3
"""Offline fail-closed tests for the i5 staging Compose launcher."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import pwd
import tempfile
import unittest
from contextlib import nullcontext, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
LAUNCH_PATH = Path(__file__).with_name("launch-guard.py")
SPEC = importlib.util.spec_from_file_location("belib_staging_launch_guard", LAUNCH_PATH)
assert SPEC is not None and SPEC.loader is not None
launch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launch)

CI_TEST_PATH = Path(__file__).with_name("test-ci-isolation-guard.py")
CI_TEST_SPEC = importlib.util.spec_from_file_location("belib_staging_ci_guard_tests", CI_TEST_PATH)
assert CI_TEST_SPEC is not None and CI_TEST_SPEC.loader is not None
ci_tests = importlib.util.module_from_spec(CI_TEST_SPEC)
CI_TEST_SPEC.loader.exec_module(ci_tests)

SOURCE_SHA = "a" * 40


def approved_receipt(source_sha: str = SOURCE_SHA) -> dict:
    run_id = 501
    run_attempt = 2
    receipt_id = "belib-staging-" + hashlib.sha256(
        f"{launch.REPOSITORY}:{launch.PULL_REQUEST}:{source_sha}:{run_id}:{run_attempt}".encode("utf-8")
    ).hexdigest()
    return {
        "schema": "belib-staging-handoff/v1",
        "receipt_id": receipt_id,
        "status": "approved-by-existing-staging-supervisor",
        "source": {
            "repository": launch.REPOSITORY,
            "pull_request": launch.PULL_REQUEST,
            "sha": source_sha,
        },
        "evidence": {
            "staging_ci": {"run_id": run_id, "run_attempt": run_attempt, "source_sha": source_sha, "run_head_sha": source_sha},
            "release_ci": {"source_sha": source_sha, "run_head_sha": source_sha},
            "independent_review": {"commit_sha": source_sha},
        },
        "lock": {"github_concurrency_group": launch.LOCK_GROUP, "receipt_id": receipt_id},
        "target": {
            "host": launch.EXPECTED_HOST,
            "principal": launch.EXPECTED_USER,
            "compose_project": launch.COMPOSE_PROJECT,
            "transport": "existing staging supervisor",
        },
        "supervisor_verification": {
            "required": True,
            "status": "approved",
            "deployment_authorized": True,
            "verified_source_sha": source_sha,
            "verified_receipt_id": receipt_id,
        },
        "deployment": {"authorized": True},
    }


class LaunchEnvironmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = {"PATH": "/usr/bin:/bin", "HOME": "/home/hiroyuki"}

    def test_arbitrary_path_is_rejected(self) -> None:
        self.environment["PATH"] = "/tmp/attacker:/usr/bin"
        with self.assertRaisesRegex(launch.LaunchBlocked, "fixed trusted"):
            launch.reject_caller_environment(self.environment)

    def test_docker_context_and_production_overrides_are_rejected(self) -> None:
        for variable in ("DOCKER_CONTEXT", "DOCKER_HOST", "COMPOSE_FILE", "PRODUCTION_DATABASE_URL"):
            with self.subTest(variable=variable), self.assertRaises(launch.LaunchBlocked):
                launch.reject_caller_environment({**self.environment, variable: "unexpected"})

    def test_pythonpath_and_shell_startup_are_rejected(self) -> None:
        for variable in ("PYTHONPATH", "BASH_ENV", "ENV"):
            with self.subTest(variable=variable), self.assertRaisesRegex(
                launch.LaunchBlocked, "startup overrides"
            ):
                launch.reject_caller_environment({**self.environment, variable: "/tmp/attacker"})

    def test_context_name_and_endpoint_are_pinned(self) -> None:
        endpoint = "unix:///run/user/1000/docker.sock"
        launch.validate_context_endpoint(launch.CONTEXT_NAME, endpoint, endpoint)
        with self.assertRaisesRegex(launch.LaunchBlocked, "validated rootless socket"):
            launch.validate_context_endpoint("default", endpoint, endpoint)
        with self.assertRaisesRegex(launch.LaunchBlocked, "validated rootless socket"):
            launch.validate_context_endpoint(launch.CONTEXT_NAME, "tcp://builder.invalid:2375", endpoint)

    def test_remote_or_ambiguous_buildx_builder_is_rejected(self) -> None:
        good = "Name: belib\nDriver: docker\nEndpoint: unix:///run/user/1000/docker.sock\n"
        self.assertEqual(launch.parse_buildx_inspect(good), "unix:///run/user/1000/docker.sock")
        with self.assertRaisesRegex(launch.LaunchBlocked, "local i5 Docker driver"):
            launch.parse_buildx_inspect("Driver: remote\nEndpoint: tcp://builder.invalid:2375\n")

    def test_private_docker_environment_drops_caller_values(self) -> None:
        actual = launch._docker_environment(Path("/home/hiroyuki"), Path("/tmp/docker-config"))
        self.assertEqual(
            actual,
            {
                "PATH": "/usr/bin:/bin",
                "HOME": "/home/hiroyuki",
                "USER": "hiroyuki",
                "LOGNAME": "hiroyuki",
                "DOCKER_CONFIG": "/tmp/docker-config",
            },
        )


class LaunchReceiptTests(unittest.TestCase):
    def test_fully_forged_local_receipt_cannot_authorize_staging(self) -> None:
        with self.assertRaisesRegex(launch.LaunchBlocked, "No trusted independent staging supervisor verifier"):
            launch.validate_handoff_receipt(approved_receipt(), SOURCE_SHA)

    def test_missing_receipt_and_claimed_approval_both_fail_closed(self) -> None:
        for receipt in (None, {"status": "approved", "supervisor": True}):
            with self.subTest(receipt=receipt), self.assertRaisesRegex(
                launch.LaunchBlocked, "local receipt claims cannot authorize staging"
            ):
                launch.validate_handoff_receipt(receipt, SOURCE_SHA)

    def test_malformed_source_sha_is_rejected_before_authority_check(self) -> None:
        with self.assertRaisesRegex(launch.LaunchBlocked, "full lowercase Git SHA"):
            launch.validate_handoff_receipt(approved_receipt(), "B" * 40)

    def test_launcher_stops_before_lock_when_independent_authority_is_unavailable(self) -> None:
        account = pwd.getpwuid(os.getuid())
        error_output = io.StringIO()
        arguments = [
            "--source-sha", SOURCE_SHA,
            "--env-file", str(Path(account.pw_dir) / ".config/belib-staging/i5.env"),
            "start-app",
        ]
        with (
            patch.object(launch, "verify_python_runtime"),
            patch.object(launch, "reject_caller_environment"),
            patch.object(launch, "EXPECTED_USER", account.pw_name),
            patch.object(launch, "EXPECTED_UID", account.pw_uid),
            patch.object(launch, "EXPECTED_HOME", Path(account.pw_dir)),
            patch.object(launch, "EXPECTED_HOST", "test-host"),
            patch.object(launch.socket, "gethostname", return_value="test-host"),
            patch.object(launch, "EXPECTED_REPO_ROOT", ROOT),
            patch.object(launch, "project_lock", side_effect=AssertionError("lock reached")),
            redirect_stderr(error_output),
        ):
            result = launch.main(arguments)

        self.assertEqual(result, 1)
        self.assertIn("No trusted independent staging supervisor verifier", error_output.getvalue())


class LaunchOperationTests(unittest.TestCase):
    def test_commands_are_fixed_to_staging_project_context_and_compose_file(self) -> None:
        root = Path("/home/hiroyuki/workspaces/belib-i5-isolated-staging-20261107")
        env_file = Path("/proc/123/fd/7")
        command = launch.build_compose_command(
            "start-app", repo_root=root, env_file=env_file
        )
        self.assertEqual(command[0:5], ["/usr/bin/docker", "--context", launch.CONTEXT_NAME, "compose", "-p"])
        self.assertIn(launch.COMPOSE_PROJECT, command)
        self.assertEqual(command[command.index("-f") + 1], str(root / "compose.staging.yaml"))
        self.assertEqual(command[-4:], ["--build", "backend", "frontend", "nginx"])
        self.assertNotIn("compose.yaml", command)

    def test_unknown_or_arbitrary_compose_operations_are_rejected(self) -> None:
        with self.assertRaisesRegex(launch.LaunchBlocked, "Unsupported"):
            launch.build_compose_command(
                "down", repo_root=Path("/tmp"), env_file=Path("/tmp/env")
            )
        with self.assertRaisesRegex(launch.LaunchBlocked, "private i5 rootless"):
            launch.build_compose_command(
                "migrate", repo_root=Path("/tmp"), env_file=Path("/tmp/env"), docker_context="default"
            )

    def test_compose_build_project_directory_can_be_pinned_to_snapshot(self) -> None:
        snapshot = Path("/tmp/belib-staging-source-example")
        command = launch.build_compose_command(
            "start-app", repo_root=snapshot, env_file=Path("/proc/123/fd/7")
        )
        self.assertEqual(command[command.index("--project-directory") + 1], str(snapshot))
        self.assertEqual(command[command.index("-f") + 1], str(snapshot / "compose.staging.yaml"))

    def test_snapshot_failure_stops_before_preflight_docker_or_staging_env(self) -> None:
        account = pwd.getpwuid(os.getuid())
        source_sha = "a" * 40

        class SnapshotFailure(Exception):
            pass

        class FailingSnapshotContext:
            def __enter__(self):
                raise SnapshotFailure("missing or mismatched fixed-SHA snapshot")

            def __exit__(self, exc_type, exc, traceback):
                return False

        fake_source_guard = type(
            "FakeSourceGuard",
            (),
            {
                "SourceTreeBlocked": SnapshotFailure,
                "sha_derived_build_snapshot": lambda self, root, revision: FailingSnapshotContext(),
            },
        )()
        fake_seed = type("FakeSeed", (), {"SeedWrapperError": RuntimeError})()
        stderr = io.StringIO()
        arguments = [
            "--source-sha", source_sha,
            "--env-file", str(Path(account.pw_dir) / ".config/belib-staging/i5.env"),
            "seed",
        ]
        with (
            patch.object(launch, "verify_python_runtime"),
            patch.object(launch, "reject_caller_environment"),
            patch.object(launch, "EXPECTED_USER", account.pw_name),
            patch.object(launch, "EXPECTED_UID", account.pw_uid),
            patch.object(launch, "EXPECTED_HOME", Path(account.pw_dir)),
            patch.object(launch, "EXPECTED_HOST", "test-host"),
            patch.object(launch.socket, "gethostname", return_value="test-host"),
            patch.object(launch, "EXPECTED_REPO_ROOT", ROOT),
            patch.object(launch, "validate_handoff_receipt"),
            patch.object(launch, "load_seed_module", return_value=fake_seed),
            patch.object(launch, "_checked_repo_branch"),
            patch.object(launch, "project_lock", return_value=nullcontext()),
            patch.object(launch, "load_source_tree_guard", return_value=fake_source_guard),
            patch.object(launch, "_run_preflight") as preflight,
            patch.object(launch, "_trusted_rootless_context") as docker_context,
            patch.object(launch, "_validate_compose_model") as compose_validation,
            patch.dict(os.environ, {}, clear=True),
            redirect_stderr(stderr),
        ):
            result = launch.main(arguments)

        self.assertEqual(result, 1)
        self.assertIn("fixed-SHA staging build snapshot could not be verified", stderr.getvalue())
        preflight.assert_not_called()
        docker_context.assert_not_called()
        compose_validation.assert_not_called()

    def test_seed_handoff_passes_the_verified_snapshot_and_sha(self) -> None:
        account = pwd.getpwuid(os.getuid())
        source_sha = "a" * 40

        class SnapshotFailure(Exception):
            pass

        class YieldSnapshotContext:
            def __init__(self, path: Path) -> None:
                self.path = path

            def __enter__(self):
                return self.path

            def __exit__(self, exc_type, exc, traceback):
                return False

        with tempfile.TemporaryDirectory(prefix="belib-staging-source-launch-test-", dir="/tmp") as directory:
            snapshot = Path(directory)
            fake_source_guard = type(
                "FakeSourceGuard",
                (),
                {
                    "SourceTreeBlocked": SnapshotFailure,
                    "sha_derived_build_snapshot": lambda self, root, revision: YieldSnapshotContext(snapshot),
                },
            )()
            seed_main = Mock(return_value=0)
            seed = SimpleNamespace(
                SeedWrapperError=RuntimeError,
                validate_build_snapshot=Mock(),
                validate_staging_admission=Mock(
                    return_value=(os.open(os.devnull, os.O_RDONLY), {"BELIB_STAGE_VCS_REF": source_sha})
                ),
                create_private_docker_config=Mock(
                    return_value=SimpleNamespace(name="/tmp/test-docker-config", cleanup=Mock())
                ),
                verify_exact_source_context=Mock(),
                assert_clean_source_worktree=Mock(),
                main=seed_main,
            )
            env_file = Path(account.pw_dir) / ".config/belib-staging/i5.env"
            arguments = ["--source-sha", source_sha, "--env-file", str(env_file), "seed"]
            with (
                patch.object(launch, "verify_python_runtime"),
                patch.object(launch, "reject_caller_environment"),
                patch.object(launch, "EXPECTED_USER", account.pw_name),
                patch.object(launch, "EXPECTED_UID", account.pw_uid),
                patch.object(launch, "EXPECTED_HOME", Path(account.pw_dir)),
                patch.object(launch, "EXPECTED_HOST", "test-host"),
                patch.object(launch.socket, "gethostname", return_value="test-host"),
                patch.object(launch, "EXPECTED_REPO_ROOT", ROOT),
                patch.object(launch, "validate_handoff_receipt"),
                patch.object(launch, "load_seed_module", return_value=seed),
                patch.object(launch, "_checked_repo_branch"),
                patch.object(launch, "project_lock", return_value=nullcontext()),
                patch.object(launch, "load_source_tree_guard", return_value=fake_source_guard),
                patch.object(launch, "_run_preflight"),
                patch.object(
                    launch,
                    "_trusted_rootless_context",
                    return_value=({"PATH": "/usr/bin:/bin"}, Path("/run/user/1000/docker.sock")),
                ),
                patch.object(launch, "_validate_compose_model"),
                patch.dict(os.environ, {}, clear=True),
            ):
                result = launch.main(arguments)

            self.assertEqual(result, 0)
            seed.validate_build_snapshot.assert_called_once_with(snapshot, ROOT, source_sha)
            seed_main.assert_called_once_with(
                invoked_by_launcher=True,
                build_root=snapshot,
                source_sha=source_sha,
            )

    def test_changed_build_context_and_image_digest_are_rejected(self) -> None:
        model = ci_tests.compose_model(ROOT)
        launch.validate_compose_contract(ROOT, source_sha=ci_tests.SOURCE_SHA, port=ci_tests.PORT, model=model)

        changed_context = ci_tests.compose_model(ROOT)
        changed_context["services"]["backend"]["build"]["context"] = "../production"
        with self.assertRaisesRegex(launch.LaunchBlocked, "build context"):
            launch.validate_compose_contract(
                ROOT, source_sha=ci_tests.SOURCE_SHA, port=ci_tests.PORT, model=changed_context
            )

        changed_digest = ci_tests.compose_model(ROOT)
        changed_digest["services"]["db"]["image"] = "postgres:16.14-bookworm"
        with self.assertRaisesRegex(launch.LaunchBlocked, "image digest"):
            launch.validate_compose_contract(
                ROOT, source_sha=ci_tests.SOURCE_SHA, port=ci_tests.PORT, model=changed_digest
            )

    def test_project_lock_blocks_a_second_writer(self) -> None:
        with tempfile.TemporaryDirectory(prefix="belib-launch-lock-test-") as directory:
            lock_path = Path(directory) / "project.lock"
            with launch.project_lock(lock_path, expected_uid=__import__("os").getuid()):
                with self.assertRaisesRegex(launch.LaunchBlocked, "Another staging operation"):
                    with launch.project_lock(lock_path, expected_uid=__import__("os").getuid()):
                        self.fail("second project lock unexpectedly succeeded")


if __name__ == "__main__":
    unittest.main()
