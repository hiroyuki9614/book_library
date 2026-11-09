#!/usr/bin/env python3
"""Offline fail-closed tests for the i5 staging Compose launcher."""

from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path


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
    def test_exact_supervisor_receipt_is_required(self) -> None:
        receipt = approved_receipt()
        self.assertEqual(launch.validate_handoff_receipt(receipt, SOURCE_SHA), receipt["receipt_id"])

    def test_pending_or_stale_receipt_is_rejected(self) -> None:
        receipt = approved_receipt()
        receipt["supervisor_verification"]["status"] = "pending-independent-provenance-verification"
        with self.assertRaisesRegex(launch.LaunchBlocked, "not authorized"):
            launch.validate_handoff_receipt(receipt, SOURCE_SHA)

        with self.assertRaisesRegex(launch.LaunchBlocked, "exact SHA"):
            launch.validate_handoff_receipt(approved_receipt(), "b" * 40)

    def test_wrong_project_and_receipt_lock_are_rejected(self) -> None:
        receipt = approved_receipt()
        receipt["target"]["compose_project"] = "production"
        with self.assertRaisesRegex(launch.LaunchBlocked, "does not match PR #103"):
            launch.validate_handoff_receipt(receipt, SOURCE_SHA)

        receipt = approved_receipt()
        receipt["lock"]["receipt_id"] = "belib-staging-" + "0" * 64
        with self.assertRaisesRegex(launch.LaunchBlocked, "receipt and lock"):
            launch.validate_handoff_receipt(receipt, SOURCE_SHA)

    def test_wrong_host_or_principal_is_rejected(self) -> None:
        for field, value in (("host", "production-worker"), ("principal", "root")):
            with self.subTest(field=field):
                receipt = approved_receipt()
                receipt["target"][field] = value
                with self.assertRaisesRegex(launch.LaunchBlocked, "does not match PR #103"):
                    launch.validate_handoff_receipt(receipt, SOURCE_SHA)

    def test_wrong_review_or_ci_sha_is_rejected(self) -> None:
        receipt = approved_receipt()
        receipt["evidence"]["independent_review"]["commit_sha"] = "c" * 40
        with self.assertRaisesRegex(launch.LaunchBlocked, "receipt and lock"):
            launch.validate_handoff_receipt(receipt, SOURCE_SHA)

        receipt = approved_receipt()
        receipt["evidence"]["staging_ci"]["run_head_sha"] = "c" * 40
        with self.assertRaisesRegex(launch.LaunchBlocked, "receipt and lock"):
            launch.validate_handoff_receipt(receipt, SOURCE_SHA)


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
