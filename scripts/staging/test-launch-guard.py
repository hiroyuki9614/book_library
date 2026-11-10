#!/usr/bin/env python3
"""Offline fail-closed tests for the i5 staging Compose launcher."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import pwd
import stat
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

    def test_launcher_waits_for_external_signed_approval_under_project_lock(self) -> None:
        account = pwd.getpwuid(os.getuid())
        error_output = io.StringIO()
        arguments = [
            "--source-sha", SOURCE_SHA,
            "--env-file", str(Path(account.pw_dir) / ".config/belib-staging/i5.env"),
            "deploy",
        ]
        fake_approval = SimpleNamespace(ApprovalError=RuntimeError, request_and_wait=Mock(side_effect=RuntimeError("no verifier configured")))
        with (
            patch.object(launch, "verify_python_runtime"),
            patch.object(launch, "reject_caller_environment"),
            patch.object(launch, "EXPECTED_USER", account.pw_name),
            patch.object(launch, "EXPECTED_UID", account.pw_uid),
            patch.object(launch, "EXPECTED_HOME", Path(account.pw_dir)),
            patch.object(launch, "EXPECTED_HOST", "test-host"),
            patch.object(launch.socket, "gethostname", return_value="test-host"),
            patch.object(launch, "EXPECTED_REPO_ROOT", ROOT),
            patch.object(launch, "_admission_source_root", return_value=ROOT),
            patch.object(launch, "project_lock", return_value=nullcontext()),
            patch.object(launch, "load_approval_module", return_value=fake_approval),
            redirect_stderr(error_output),
        ):
            result = launch.main(arguments)

        self.assertEqual(result, 1)
        self.assertIn("no verifier configured", error_output.getvalue())
        fake_approval.request_and_wait.assert_called_once()


class LaunchOperationTests(unittest.TestCase):
    def test_detached_admission_requires_exact_sha_and_shared_git_store(self) -> None:
        with tempfile.TemporaryDirectory(prefix="belib-admission-test-") as directory:
            base = Path(directory)
            source_sha = "a" * 40
            candidate = base / ("admission-" + source_sha[:7])
            candidate.mkdir()
            seed = SimpleNamespace(
                SeedWrapperError=RuntimeError,
                read_source_sha=Mock(return_value=source_sha),
                git_command=lambda root, *args: [str(root), *args],
                git_environment=lambda: {},
            )
            # symbolic-ref is queried as three trailing arguments; detached means it exits nonzero.
            def run(command, **kwargs):
                return SimpleNamespace(returncode=1 if command[-3:] == ["symbolic-ref", "-q", "HEAD"] else 0, stdout="/shared/git\n")
            with patch.object(launch, "ADMISSION_WORKTREE_PREFIX", base / "admission-"), patch.object(launch, "_run", side_effect=run):
                self.assertEqual(launch._admission_source_root(seed, base / "checkout", source_sha), candidate)

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
            "deploy",
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
            patch.object(launch, "_admission_source_root", return_value=ROOT),
            patch.object(launch, "load_approval_module", return_value=SimpleNamespace(ApprovalError=RuntimeError, request_and_wait=Mock())),
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

    def test_deploy_transaction_orders_phases_and_checks_db_and_http_health(self) -> None:
        commands = []
        def runner(command, **kwargs):
            commands.append(command)
            output = '{"Health":"healthy"}' if "ps" in command else ""
            return SimpleNamespace(returncode=0, stdout=output)
        seed = SimpleNamespace(main=Mock(return_value=0))
        launch.run_deploy_transaction(
            compose=["/usr/bin/docker", "--context", launch.CONTEXT_NAME, "compose", "-p", launch.COMPOSE_PROJECT],
            env_file=Path("/tmp/env"), build_root=Path("/tmp/source"), environment={}, env_fd=9,
            seed=seed, source_sha=SOURCE_SHA, port="18080", runner=runner,
            stage_values={"BELIB_STAGE_VCS_REF": SOURCE_SHA}, docker_endpoint=Path("/run/user/1000/docker.sock"),
            sleep=lambda _: None, monotonic=lambda: 0, report=lambda *a, **k: None,
        )
        serialized = " ".join(" ".join(command) for command in commands)
        self.assertLess(serialized.index("up -d db"), serialized.index("--profile migration run"))
        self.assertLess(serialized.index("--profile migration run"), serialized.index("up -d --build backend"))
        self.assertIn("127.0.0.1:18080/health", serialized)
        self.assertEqual(seed.main.call_count, 1)
        seed.main.assert_called_once_with(
            invoked_by_launcher=True, build_root=Path("/tmp/source"), source_sha=SOURCE_SHA,
            admitted_env_fd=9, stage_values={"BELIB_STAGE_VCS_REF": SOURCE_SHA},
            docker_cli=launch.DOCKER_CLI, docker_endpoint=Path("/run/user/1000/docker.sock"),
            docker_environment={},
        )

    def test_deploy_failure_stops_later_phases_and_preserves_volumes(self) -> None:
        commands = []
        def runner(command, **kwargs):
            commands.append(command)
            output = '{"Health":"healthy"}' if "ps" in command else ""
            return SimpleNamespace(returncode=1 if "migration" in command else 0, stdout=output)
        seed = SimpleNamespace(main=Mock(return_value=0))
        with self.assertRaisesRegex(launch.LaunchBlocked, "phase failed: migration"):
            launch.run_deploy_transaction(
                compose=["/usr/bin/docker", "--context", launch.CONTEXT_NAME, "compose", "-p", launch.COMPOSE_PROJECT],
                env_file=Path("/tmp/env"), build_root=Path("/tmp/source"), environment={}, env_fd=9,
                seed=seed, source_sha=SOURCE_SHA, port="18080", runner=runner,
                stage_values={"BELIB_STAGE_VCS_REF": SOURCE_SHA}, docker_endpoint=Path("/run/user/1000/docker.sock"),
                sleep=lambda _: None, monotonic=lambda: 0, report=lambda *a, **k: None,
            )
        self.assertEqual(seed.main.call_count, 0)
        flattened = [" ".join(command) for command in commands]
        self.assertTrue(any("stop nginx frontend backend db" in command for command in flattened))
        self.assertFalse(any("down" in command or "volume" in command or "production" in command for command in flattened))

    def test_failed_rollback_is_never_reported_as_pass(self) -> None:
        reports = []
        def runner(command, **kwargs):
            output = '{"Health":"healthy"}' if "ps" in command else ""
            if "migration" in command:
                return SimpleNamespace(returncode=1, stdout=output)
            if "stop" in command:
                return SimpleNamespace(returncode=2, stdout=output)
            return SimpleNamespace(returncode=0, stdout=output)
        with self.assertRaises(launch.LaunchBlocked):
            launch.run_deploy_transaction(
                compose=["/usr/bin/docker", "--context", launch.CONTEXT_NAME, "compose", "-p", launch.COMPOSE_PROJECT],
                env_file=Path("/tmp/env"), build_root=Path("/tmp/source"), environment={}, env_fd=9,
                seed=SimpleNamespace(main=Mock(return_value=0)), source_sha=SOURCE_SHA, port="18080",
                stage_values={"BELIB_STAGE_VCS_REF": SOURCE_SHA}, docker_endpoint=Path("/run/user/1000/docker.sock"),
                runner=runner, sleep=lambda _: None, monotonic=lambda: 0,
                report=lambda message, **kwargs: reports.append(message),
            )
        self.assertIn("STAGING_ROLLBACK_FAILED manual-review-required", reports)
        self.assertNotIn("STAGING_ROLLBACK_PASS stopped-project-services-volumes-preserved", reports)

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
            real_lstat = os.lstat
            def lstat(path):
                if path == "/tmp":
                    return SimpleNamespace(st_mode=stat.S_IFDIR | 0o1777, st_uid=0)
                return real_lstat(path)
            with patch.object(launch.os, "lstat", side_effect=lstat):
                with launch.project_lock(lock_path, expected_uid=os.getuid()):
                    with self.assertRaisesRegex(launch.LaunchBlocked, "Another staging operation"):
                        with launch.project_lock(lock_path, expected_uid=os.getuid()):
                            self.fail("second project lock unexpectedly succeeded")


if __name__ == "__main__":
    unittest.main()
