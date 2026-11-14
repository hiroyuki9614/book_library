#!/usr/bin/env python3
"""Offline fail-closed tests for the i5 staging Compose launcher."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
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


class LaunchPrivateDockerContextTests(unittest.TestCase):
    """Exercise the *private* Docker context path without a daemon or credentials."""

    def setUp(self) -> None:
        self.uid = launch.EXPECTED_UID
        self.home = launch.EXPECTED_HOME
        self.config = Path("/tmp/belib-private-docker-config-test")
        self.endpoint = Path("/run/user/1000/docker.sock")
        self.socket_url = f"unix://{self.endpoint}"
        self.environment = launch._docker_environment(self.home, self.config)
        self.seed = SimpleNamespace(
            SeedWrapperError=ValueError,
            verify_trusted_docker_plugins=Mock(),
            expected_docker_socket=Mock(return_value=self.endpoint),
            validate_rootless_socket=Mock(),
            verify_private_docker_config=Mock(),
        )

    def responses(self) -> list[tuple[int, str]]:
        return [
            (0, "created"),
            (0, self.socket_url + "\n"),
            (0, f'{launch.EXPECTED_HOST}|["name=rootless"]\n'),
            (0, "Docker Compose version v2"),
            (0, f"Name: belib\nDriver: docker\nEndpoint: {self.socket_url}\n"),
        ]

    def run_mock_context(self, responses: list[tuple[int, str]]):
        commands = []

        def mocked_run(command, *, environment):
            self.assertEqual(environment, self.environment)
            self.assertEqual(command[0], launch.DOCKER_CLI)
            self.assertLess(len(commands), len(responses), "unexpected Docker operation")
            commands.append(command)
            exit_code, output = responses[len(commands) - 1]
            return SimpleNamespace(returncode=exit_code, stdout=output)

        with patch.object(launch, "_run", side_effect=mocked_run):
            result = launch._trusted_rootless_context(
                self.seed, self.uid, self.home, self.config
            )
        return result, commands

    def test_private_context_creation_inspection_and_rootless_buildx_identity(self) -> None:
        (environment, endpoint), commands = self.run_mock_context(self.responses())
        self.assertEqual(environment, self.environment)
        self.assertEqual(endpoint, self.endpoint)
        self.assertEqual(
            commands[0],
            [
                launch.DOCKER_CLI, "context", "create", launch.CONTEXT_NAME,
                "--docker", f"host={self.socket_url}",
            ],
        )
        self.assertEqual(commands[1][1:4], ["context", "inspect", launch.CONTEXT_NAME])
        for command in commands[2:]:
            self.assertEqual(command[1:3], ["--context", launch.CONTEXT_NAME])
        self.assertEqual(len(commands), 5)
        self.seed.verify_trusted_docker_plugins.assert_called_once_with()
        self.seed.expected_docker_socket.assert_called_once_with(self.uid)
        self.seed.validate_rootless_socket.assert_called_once_with(self.endpoint, self.uid)
        self.seed.verify_private_docker_config.assert_called_once_with(self.config, self.uid)
        self.assertEqual(environment["DOCKER_CONFIG"], str(self.config))
        self.assertNotIn("DOCKER_HOST", environment)
        self.assertNotIn("DOCKER_CONTEXT", environment)

    def test_context_creation_failure_is_fail_closed_before_inspection(self) -> None:
        with self.assertRaisesRegex(launch.LaunchBlocked, "create the private rootless"):
            self.run_mock_context([(1, "")])

    def test_context_endpoint_mismatch_is_fail_closed(self) -> None:
        responses = self.responses()
        responses[1] = (0, "tcp://builder.invalid:2375")
        with self.assertRaisesRegex(launch.LaunchBlocked, "validated rootless socket"):
            self.run_mock_context(responses[:2])

    def test_nonrootless_daemon_identity_is_rejected(self) -> None:
        responses = self.responses()
        responses[2] = (0, f'{launch.EXPECTED_HOST}|["name=seccomp"]')
        with self.assertRaisesRegex(launch.LaunchBlocked, "rootless daemon"):
            self.run_mock_context(responses[:3])

    def test_remote_buildx_endpoint_is_rejected(self) -> None:
        responses = self.responses()
        responses[4] = (0, "Name: remote\nDriver: docker\nEndpoint: tcp://builder.invalid:2375\n")
        with self.assertRaisesRegex(launch.LaunchBlocked, "not bound to this user"):
            self.run_mock_context(responses)

    def test_unsupported_buildx_driver_is_rejected(self) -> None:
        responses = self.responses()
        responses[4] = (0, "Name: remote\nDriver: remote\nEndpoint: tcp://builder.invalid:2375\n")
        with self.assertRaisesRegex(launch.LaunchBlocked, "local i5 Docker driver"):
            self.run_mock_context(responses)



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

    def test_cli_fails_closed_before_candidate_imports_for_each_operation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="belib-pregrant-import-test-") as directory:
            candidate = Path(directory)
            marker = candidate / "pregrant-import-ran"
            payload = (
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('ran', encoding='ascii')\n"
            )
            for name in ("seed.py", "approval.py", "source-tree-guard.py"):
                (candidate / name).write_text(payload, encoding="utf-8")

            common = [
                "--source-sha", SOURCE_SHA,
                "--evidence-sha256", "e" * 64,
                "--env-file", str(candidate / "outside.env"),
            ]
            operation_args = {
                "deploy": [],
                "resume-ingress": ["--prior-source-sha", launch.RETAINED_PRIOR_SHA,
                                   "--retained-state-sha256", "d" * 64],
                "manifest-ingress": ["--prior-source-sha", launch.RETAINED_PRIOR_SHA],
            }
            with patch.object(launch, "ADMITTED_SOURCE_ROOT", candidate):
                for operation, extra_args in operation_args.items():
                    with self.subTest(operation=operation):
                        stderr = io.StringIO()
                        with (
                            patch.object(launch, "verify_python_runtime") as runtime_check,
                            patch.object(launch, "reject_caller_environment") as environment_check,
                            patch.object(launch, "load_seed_module", wraps=launch.load_seed_module) as seed_load,
                            patch.object(launch, "load_approval_module", wraps=launch.load_approval_module) as approval_load,
                            patch.object(launch, "load_source_tree_guard", wraps=launch.load_source_tree_guard) as source_guard_load,
                            patch.object(launch, "_admission_source_root") as source_admission,
                            patch.object(launch, "project_lock") as project_lock,
                            patch.object(launch, "_run") as shell_or_docker_action,
                            patch.object(launch, "_run_preflight") as preflight,
                            redirect_stderr(stderr),
                        ):
                            result = launch.main([*common, *extra_args, operation])

                        self.assertEqual(result, 1)
                        self.assertIn("independent operator-installed immutable bootstrap", stderr.getvalue())
                        for untouched in (runtime_check, environment_check, seed_load, approval_load,
                                          source_guard_load, source_admission, project_lock,
                                          shell_or_docker_action, preflight):
                            untouched.assert_not_called()
                        self.assertFalse(marker.exists())

    def test_mocked_operator_seam_still_requires_matching_operation_grant(self) -> None:
        account = pwd.getpwuid(os.getuid())
        error_output = io.StringIO()
        arguments = [
            "--source-sha", SOURCE_SHA,
            "--evidence-sha256", "e" * 64,
            "--env-file", str(Path(account.pw_dir) / ".config/belib-staging/i5.env"),
            "deploy",
        ]
        operation_check = Mock(side_effect=launch.LaunchBlocked("operation grant mismatch"))
        with (
            # Mock descriptor admission only; operation binding remains required.
            patch.object(launch, "_require_operator_bootstrap_admission", return_value=None),
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
            patch.object(launch, "_require_operation_approval", operation_check),
            redirect_stderr(error_output),
        ):
            result = launch.main(arguments)

        self.assertEqual(result, 1)
        self.assertIn("operation grant mismatch", error_output.getvalue())
        operation_check.assert_called_once()


class LaunchOperationTests(unittest.TestCase):
    @staticmethod
    def retained_manifest(*, state="exited", db_id=launch.RETAINED_DB_CONTAINER_ID, oneoff="False", ingress=False,
                          current_source_sha=None, swapped_volumes=False):
        prior = launch.RETAINED_PRIOR_SHA
        snapshot = launch.SNAPSHOT_ROOT / prior
        volumes = sorted(launch.RETAINED_VOLUMES)
        if swapped_volumes:
            volumes.reverse()
        def volume_mount(name, destination):
            return {"type": "volume", "name": name, "source": f"/var/lib/docker/volumes/{name}/_data",
                    "destination": destination, "read_write": True}
        def bind_mount(path, destination):
            return {"type": "bind", "name": "", "source": str(snapshot / path),
                    "destination": destination, "read_write": False}
        def container(service, cid, mounts):
            networks = [f"{launch.COMPOSE_PROJECT}_isolated"]
            if ingress and service == "nginx":
                networks.append(f"{launch.COMPOSE_PROJECT}_ingress")
            return {"id": cid, "service": service, "project": launch.COMPOSE_PROJECT,
                    "oneoff": oneoff, "state": state, "image_id": "sha256:" + "a" * 64,
                    "image_ref": "postgres:16.14-bookworm@sha256:" + "b" * 64,
                    "mounts": sorted(mounts, key=lambda item: (item["destination"], item["source"])),
                    "networks": sorted(networks)}
        containers = [
            container("db", db_id, [
                volume_mount(volumes[1], "/var/lib/postgresql/data"),
                bind_mount("scripts/staging/10-belib-staging-roles.sh", "/docker-entrypoint-initdb.d/10-belib-staging-roles.sh"),
            ]),
            container("backend", "a" * 64, [volume_mount(volumes[0], "/home/node")]),
            container("frontend", "b" * 64, []),
            container("nginx", "c" * 64, [
                {**bind_mount("nginx/nginx.conf", "/etc/nginx/nginx.conf"),
                 "source": str(launch.SNAPSHOT_ROOT / (current_source_sha or prior) / "nginx/nginx.conf")},
                {**bind_mount("scripts/staging/nginx-default.conf", "/etc/nginx/conf.d/default.conf"),
                 "source": str(launch.SNAPSHOT_ROOT / (current_source_sha or prior) / "scripts/staging/nginx-default.conf")},
            ]),
        ]
        volumes_data = []
        for name in volumes:
            label = name.removeprefix(launch.COMPOSE_PROJECT + "_")
            volumes_data.append({
                "name": name, "driver": "local", "scope": "local", "created_at": "2026-11-09T00:00:00Z",
                "mountpoint": f"/var/lib/docker/volumes/{name}/_data",
                "labels": {"com.docker.compose.project": launch.COMPOSE_PROJECT, "com.docker.compose.volume": label},
                "options": {},
            })
        networks = [{
            "id": "d" * 64, "name": f"{launch.COMPOSE_PROJECT}_isolated", "driver": "bridge", "internal": True,
            "labels": {"com.docker.compose.project": launch.COMPOSE_PROJECT, "com.docker.compose.network": "isolated"},
        }]
        if ingress:
            networks.append({
                "id": "e" * 64, "name": f"{launch.COMPOSE_PROJECT}_ingress", "driver": "bridge", "internal": False,
                "labels": {"com.docker.compose.project": launch.COMPOSE_PROJECT, "com.docker.compose.network": "ingress"},
            })
        return {"schema": "belib-staging-retained-state/v1", "project": launch.COMPOSE_PROJECT,
                "prior_source_sha": prior, "containers": sorted(containers, key=lambda item: item["id"]),
                "volumes": volumes_data, "networks": networks}

    def test_retained_manifest_rejects_running_unexpected_wrong_dbid_and_wrong_prior_mounts(self):
        valid = self.retained_manifest()
        launch.validate_retained_manifest(valid, launch.RETAINED_PRIOR_SHA)
        mutations = []
        running = self.retained_manifest(state="running")
        mutations.append(running)
        mutations.append(self.retained_manifest(db_id="f" * 64))
        mutations.append(self.retained_manifest(oneoff="true"))
        mutations.append(self.retained_manifest(oneoff="unknown"))
        bad_mount = self.retained_manifest()
        next(row for row in bad_mount["containers"] if row["service"] == "nginx")["mounts"][0]["source"] = "/tmp/untrusted"
        mutations.append(bad_mount)
        old_nginx_snapshot = self.retained_manifest(ingress=True, current_source_sha=launch.RETAINED_PRIOR_SHA)
        next(row for row in old_nginx_snapshot["containers"] if row["service"] == "nginx")["mounts"][0]["source"] = str(
            launch.SNAPSHOT_ROOT / ("f" * 40) / "nginx/nginx.conf"
        )
        with self.assertRaises(launch.LaunchBlocked):
            launch.validate_retained_manifest(old_nginx_snapshot, launch.RETAINED_PRIOR_SHA,
                                              allow_running=True, allow_ingress=True,
                                              current_source_sha="f" * 40)
        wrong_volume = self.retained_manifest(swapped_volumes=True)
        with self.assertRaises(launch.LaunchBlocked):
            launch.validate_retained_manifest(wrong_volume, launch.RETAINED_PRIOR_SHA)
        extra = self.retained_manifest()
        extra["networks"].append({"id": "f" * 64, "name": "unexpected", "driver": "bridge", "internal": False, "labels": {}})
        mutations.append(extra)
        for manifest in mutations:
            with self.subTest(manifest=manifest), self.assertRaises(launch.LaunchBlocked):
                launch.validate_retained_manifest(manifest, launch.RETAINED_PRIOR_SHA)

    def test_manifest_collector_is_read_only_and_never_inspects_environment(self):
        expected = self.retained_manifest()
        by_id = {item["id"]: item for item in expected["containers"]}
        by_volume = {item["name"]: item for item in expected["volumes"]}
        network = expected["networks"][0]
        commands = []
        def runner(command, **kwargs):
            commands.append(command)
            if command[1:3] == ["--context", launch.CONTEXT_NAME]:
                tail = command[3:]
            else:
                tail = command[1:]
            if tail[:2] == ["ps", "--all"]:
                return SimpleNamespace(returncode=0, stdout="\n".join(by_id) + "\n")
            if tail[:2] == ["volume", "ls"]:
                return SimpleNamespace(returncode=0, stdout="\n".join(by_volume) + "\n")
            if tail[:2] == ["network", "ls"]:
                return SimpleNamespace(returncode=0, stdout=network["id"] + "\n")
            if tail[:2] == ["volume", "inspect"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps(by_volume[tail[-1]]) + "\n")
            if tail[:2] == ["network", "inspect"]:
                return SimpleNamespace(returncode=0, stdout=json.dumps(network) + "\n")
            if tail[:2] == ["inspect", "--format"]:
                source = by_id[tail[-1]]
                raw = {key: value for key, value in source.items() if key not in ("mounts", "networks")}
                raw["mounts"] = [{
                    "Type": mount["type"], "Name": mount["name"], "Source": mount["source"],
                    "Destination": mount["destination"], "RW": mount["read_write"],
                } for mount in source["mounts"]]
                raw["networks"] = {name: {} for name in source["networks"]}
                return SimpleNamespace(returncode=0, stdout=json.dumps(raw) + "\n")
            raise AssertionError(command)
        result = launch.build_retained_manifest(
            launch.RETAINED_PRIOR_SHA, {"PATH": "/usr/bin:/bin", "DOCKER_CONFIG": "/nonexistent"},
            runner=runner,
        )
        self.assertEqual(result, expected)
        self.assertTrue(all("Env" not in " ".join(command) for command in commands))
        self.assertFalse(any(any(word in command for word in ("start", "up", "run", "stop", "down", "rm")) for command in commands))

    def test_resume_digest_mismatch_is_rejected_before_any_docker_command(self):
        manifest = self.retained_manifest()
        commands = []
        def runner(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="")
        with self.assertRaisesRegex(launch.LaunchBlocked, "digest does not match"):
            launch.run_resume_ingress_transaction(
                compose=["/usr/bin/docker", "--context", launch.CONTEXT_NAME, "compose", "-p", launch.COMPOSE_PROJECT],
                build_root=Path("/tmp/snapshot"), environment={}, port="18083",
                prior_source_sha=launch.RETAINED_PRIOR_SHA, signed_digest="0" * 64,
                manifest_provider=lambda *args, **kwargs: manifest, runner=runner,
            )
        self.assertEqual(commands, [])

    def test_resume_starts_exact_db_then_only_web_services_and_checks_readback(self):
        original = self.retained_manifest()
        resumed = self.retained_manifest(state="running", ingress=True, current_source_sha=SOURCE_SHA)
        commands = []
        def provider(_prior, validate=True):
            return original if not commands else resumed
        def runner(command, **kwargs):
            commands.append(command)
            if "inspect" in command:
                return SimpleNamespace(returncode=0, stdout="healthy")
            if "port" in command:
                return SimpleNamespace(returncode=0, stdout="127.0.0.1:18083\n")
            if command[0] == "/usr/bin/curl":
                return SimpleNamespace(returncode=0, stdout="200")
            return SimpleNamespace(returncode=0, stdout="")
        digest = launch.retained_manifest_sha256(original)
        result = launch.run_resume_ingress_transaction(
            compose=["/usr/bin/docker", "--context", launch.CONTEXT_NAME, "compose", "-p", launch.COMPOSE_PROJECT],
            build_root=Path("/tmp/snapshot"), environment={}, port="18083",
            prior_source_sha=launch.RETAINED_PRIOR_SHA, signed_digest=digest,
            manifest_provider=provider, runner=runner, sleep=lambda _: None, monotonic=lambda: 0,
            current_source_sha=SOURCE_SHA,
            expected_db_image="postgres:16.14-bookworm@sha256:" + "b" * 64,
            report=lambda *args, **kwargs: None,
        )
        self.assertEqual(result, 0)
        self.assertIn(["/usr/bin/docker", "--context", launch.CONTEXT_NAME, "start", launch.RETAINED_DB_CONTAINER_ID], commands)
        compose_mutations = [command for command in commands if "compose" in command and ("up" in command or "run" in command)]
        self.assertEqual(len(compose_mutations), 1)
        self.assertEqual(compose_mutations[0][-5:], ["--no-deps", "--build", "backend", "frontend", "nginx"])
        self.assertFalse(any("migrate" in command or "seed" in command for command in commands))

    def test_resume_failure_stops_services_without_deleting_retained_volumes(self):
        original = self.retained_manifest()
        commands = []
        reports = []
        def runner(command, **kwargs):
            commands.append(command)
            if command[0] == "/usr/bin/docker" and "inspect" in command:
                return SimpleNamespace(returncode=0, stdout="healthy")
            if "compose" in command and "up" in command:
                return SimpleNamespace(returncode=1, stdout="")
            return SimpleNamespace(returncode=0, stdout="")
        digest = launch.retained_manifest_sha256(original)
        with self.assertRaisesRegex(launch.LaunchBlocked, "web services did not start"):
            launch.run_resume_ingress_transaction(
                compose=["/usr/bin/docker", "--context", launch.CONTEXT_NAME, "compose", "-p", launch.COMPOSE_PROJECT],
                build_root=Path("/tmp/snapshot"), environment={}, port="18083",
                prior_source_sha=launch.RETAINED_PRIOR_SHA, signed_digest=digest,
                manifest_provider=lambda *args, **kwargs: original, runner=runner,
                report=lambda message, **kwargs: reports.append(message),
            )
        self.assertTrue(any("stop nginx frontend backend db" in " ".join(command) for command in commands))
        self.assertFalse(any("down" in command or "volume rm" in command for command in commands))
        self.assertTrue(any(message.startswith("STAGING_ROLLBACK_PASS stopped-retained-services-volumes-preserved") for message in reports))

    def setUp(self):
        launch.ADMITTED_SOURCE_ROOT = None

    def test_detached_admission_rejects_stale_dirty_sibling_branch_and_modified_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="belib-admission-test-") as directory:
            base = Path(directory)
            source_sha = "a" * 40
            candidate = base / ("admission-" + source_sha[:7])
            (candidate / "scripts/staging").mkdir(parents=True)
            launcher_path = candidate / "scripts/staging/launch-guard.py"
            launcher_path.write_text("reviewed launcher")
            launcher_path.chmod(0o644)
            checkout = base / "claimed-checkout"
            checkout.mkdir()

            def git_results(*, head=source_sha, branch=launch.EXPECTED_BRANCH, dirty="", common="/shared/git\n", modified=False):
                def run(root, *args):
                    if args[:2] == ("branch", "--show-current"):
                        return SimpleNamespace(returncode=0, stdout=branch + "\n")
                    if args[:3] == ("rev-parse", "--verify", "HEAD"):
                        return SimpleNamespace(returncode=0, stdout=head + "\n")
                    if args[:1] == ("rev-parse",):
                        return SimpleNamespace(returncode=0, stdout=common if root == candidate else "/shared/git\n")
                    if args[:1] == ("symbolic-ref",):
                        return SimpleNamespace(returncode=1, stdout="")
                    if args[:1] == ("ls-tree",):
                        data = b"reviewed launcher"
                        oid = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                        return SimpleNamespace(returncode=0, stdout=f"100644 blob {oid}\tscripts/staging/launch-guard.py\0")
                    if args[:1] == ("status",):
                        return SimpleNamespace(returncode=0, stdout=dirty)
                    if "diff" in args:
                        return SimpleNamespace(returncode=1 if modified else 0, stdout="")
                    raise AssertionError(args)
                return run

            with (
                patch.object(launch, "ADMISSION_WORKTREE_PREFIX", base / "admission-"),
                patch.object(launch, "__file__", str(launcher_path)),
                patch.object(launch, "EXPECTED_UID", os.getuid()),
            ):
                for overrides, message in (
                    ({"head": "b" * 40}, "selected SHA"),
                    ({"dirty": "?? unsafe.py\\n"}, "must be clean"),
                    ({"common": "/other/git\\n"}, "claimed repository"),
                    ({"branch": "codex/wrong"}, "Original checkout branch"),
                    ({"modified": True}, "must be clean"),
                ):
                    with self.subTest(overrides=overrides), patch.object(launch, "_trusted_git", side_effect=git_results(**overrides)):
                        with self.assertRaisesRegex(launch.LaunchBlocked, message):
                            launch._admission_source_root(checkout, source_sha)

                with patch.object(launch, "_trusted_git", side_effect=git_results()):
                    self.assertEqual(launch._admission_source_root(checkout, source_sha), candidate)
                    launcher_path.write_text("tampered helper hidden by cached Git stat")
                    with self.assertRaisesRegex(launch.LaunchBlocked, "bytes differ"):
                        launch._admission_source_root(checkout, source_sha)

    def test_helper_imports_are_bound_to_admitted_source_root(self) -> None:
        with self.assertRaisesRegex(launch.LaunchBlocked, "has not been admitted"):
            launch.load_seed_module()
        with patch.object(launch, "ADMITTED_SOURCE_ROOT", ROOT), patch.object(launch.importlib.util, "spec_from_file_location") as loader:
            loader.return_value = None
            with self.assertRaisesRegex(launch.LaunchBlocked, "unavailable"):
                launch.load_seed_module()
            self.assertEqual(loader.call_args.args[1], ROOT / "scripts/staging/seed.py")

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
            "--evidence-sha256", "e" * 64,
            "--env-file", str(Path(account.pw_dir) / ".config/belib-staging/i5.env"),
            "deploy",
        ]
        with (
            # Explicitly mock only the unavailable external admission seam so this
            # legacy defense-in-depth failure test can reach the snapshot check.
            patch.object(launch, "_require_operator_bootstrap_admission", return_value=None),
            patch.object(launch, "verify_python_runtime"),
            patch.object(launch, "reject_caller_environment"),
            patch.object(launch, "EXPECTED_USER", account.pw_name),
            patch.object(launch, "EXPECTED_UID", account.pw_uid),
            patch.object(launch, "EXPECTED_HOME", Path(account.pw_dir)),
            patch.object(launch, "EXPECTED_HOST", "test-host"),
            patch.object(launch.socket, "gethostname", return_value="test-host"),
            patch.object(launch, "EXPECTED_REPO_ROOT", ROOT),
            patch.object(launch, "_admission_source_root", return_value=ROOT),
            patch.object(launch, "_require_operation_approval"),
            patch.object(launch, "load_seed_module", return_value=fake_seed),
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
            output = '{"Health":"healthy"}' if "ps" in command and "--all" not in command else ("200" if command[0] == "/usr/bin/curl" else "")
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
        self.assertLess(serialized.index("ps --all"), serialized.index("up -d db"))
        self.assertLess(serialized.index("up -d db"), serialized.index("--profile migration run"))
        self.assertIn("--build --rm -T migrate", serialized)
        self.assertLess(serialized.index("--profile migration run --build --rm -T migrate"), serialized.index("up -d --build backend"))
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
            output = '{"Health":"healthy"}' if "ps" in command and "--all" not in command else ""
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
        self.assertFalse(any("down" in command or "volume rm" in command or "production" in command for command in flattened))

    def test_failed_rollback_is_never_reported_as_pass(self) -> None:
        reports = []
        def runner(command, **kwargs):
            output = '{"Health":"healthy"}' if "ps" in command and "--all" not in command else ""
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
        self.assertTrue(any(r.startswith("STAGING_ROLLBACK_FAILED manual-review-required") for r in reports))
        self.assertNotIn("STAGING_ROLLBACK_PASS stopped-project-services-volumes-preserved", reports)

    def test_existing_project_resources_are_rejected_before_any_docker_mutation(self) -> None:
        commands = []
        reports = []
        def runner(command, **kwargs):
            commands.append(command)
            output = '{"Name":"belib-isolated-i5-db-1"}' if "--all" in command else ""
            return SimpleNamespace(returncode=0, stdout=output)
        with self.assertRaisesRegex(launch.LaunchBlocked, "already exist"):
            launch.run_deploy_transaction(
                compose=["/usr/bin/docker", "--context", launch.CONTEXT_NAME, "compose", "-p", launch.COMPOSE_PROJECT],
                env_file=Path("/tmp/env"), build_root=Path("/tmp/source"), environment={}, env_fd=9,
                seed=SimpleNamespace(main=Mock(return_value=0)), source_sha=SOURCE_SHA, port="18080",
                stage_values={"BELIB_STAGE_VCS_REF": SOURCE_SHA}, docker_endpoint=Path("/run/user/1000/docker.sock"),
                runner=runner, sleep=lambda _: None, monotonic=lambda: 0,
                report=lambda message, **kwargs: reports.append(message),
            )
        self.assertFalse(any(" up " in " " + " ".join(command) + " " for command in commands))
        self.assertFalse(any(" stop " in " " + " ".join(command) + " " for command in commands))
        self.assertNotIn("STAGING_ROLLBACK_PASS stopped-project-services-volumes-preserved", reports)

    def test_exact_unlabeled_fresh_project_volumes_and_networks_block_before_compose_mutation(self) -> None:
        named_resources = [
            ("volume", name) for name in launch.FRESH_PROJECT_VOLUMES
        ] + [
            ("network", name) for name in launch.FRESH_PROJECT_NETWORKS
        ]
        for kind, name in named_resources:
            commands = []

            def runner(command, **kwargs):
                commands.append(command)
                if command[3:5] == [kind, "ls"] and "--filter" not in command:
                    return SimpleNamespace(returncode=0, stdout=name + "\n")
                return SimpleNamespace(returncode=0, stdout="")

            with self.subTest(kind=kind, name=name), self.assertRaisesRegex(launch.LaunchBlocked, "already exist"):
                launch.run_deploy_transaction(
                    compose=["/usr/bin/docker", "--context", launch.CONTEXT_NAME, "compose", "-p", launch.COMPOSE_PROJECT],
                    env_file=Path("/tmp/env"), build_root=Path("/tmp/source"), environment={}, env_fd=9,
                    seed=SimpleNamespace(main=Mock(return_value=0)), source_sha=SOURCE_SHA, port="18080",
                    stage_values={}, docker_endpoint=Path("/run/user/1000/docker.sock"), runner=runner,
                    sleep=lambda _: None, monotonic=lambda: 0, report=Mock(),
                )
            self.assertFalse(any("compose" in command and any(word in command for word in ("up", "run", "stop", "down"))
                                 for command in commands))

    def test_fresh_deploy_keeps_project_label_volume_guard(self) -> None:
        commands = []
        def runner(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout="legacy-project-data\n" if "--filter" in command else "")
        with self.assertRaises(launch.LaunchBlocked):
            launch.run_deploy_transaction(
                compose=["/usr/bin/docker", "--context", launch.CONTEXT_NAME, "compose", "-p", launch.COMPOSE_PROJECT],
                env_file=Path("/tmp/env"), build_root=Path("/tmp/source"), environment={}, env_fd=9,
                seed=SimpleNamespace(main=Mock(return_value=0)), source_sha=SOURCE_SHA, port="18080",
                stage_values={}, docker_endpoint=Path("/run/user/1000/docker.sock"), runner=runner,
                sleep=lambda _: None, monotonic=lambda: 0, report=Mock(),
            )
        self.assertFalse(any("compose" in command and any(word in command for word in ("up", "run", "stop", "down"))
                             for command in commands))

    def test_fresh_project_resource_inventory_accepts_absence_and_unrelated_names(self) -> None:
        for volume_names, network_names in (("", ""), ("unrelated-data\n", "unrelated-network\n")):
            def runner(command, **kwargs):
                output = volume_names if command[3:5] == ["volume", "ls"] else network_names
                return SimpleNamespace(returncode=0, stdout=output)

            launch.reject_existing_fresh_project_resources(Path("/tmp/source"), {}, runner=runner)

    def test_fresh_project_resource_inventory_fails_closed_on_errors_and_malformed_output(self) -> None:
        cases = [
            lambda command: SimpleNamespace(returncode=1, stdout=""),
            lambda command: SimpleNamespace(returncode=0, stdout="bad resource name\n"),
        ]
        for response in cases:
            with self.subTest(response=response), self.assertRaises(launch.LaunchBlocked):
                launch.reject_existing_fresh_project_resources(
                    Path("/tmp/source"), {}, runner=lambda command, **kwargs: response(command),
                )

    def test_oneoff_remaining_or_failed_inventory_prevents_rollback_pass(self) -> None:
        for inspect_code, remaining in ((0, "oneoff-id"), (1, ""), (0, "")):
            reports = []
            def runner(command, **kwargs):
                if command[3:4] == ["ps"]:
                    return SimpleNamespace(returncode=inspect_code, stdout=remaining)
                if "migration" in command:
                    return SimpleNamespace(returncode=1, stdout="")
                output = '{"Health":"healthy"}' if "ps" in command and "--all" not in command else ""
                return SimpleNamespace(returncode=0, stdout=output)
            with self.assertRaises(launch.LaunchBlocked):
                launch.run_deploy_transaction(
                    compose=["/usr/bin/docker", "--context", launch.CONTEXT_NAME, "compose", "-p", launch.COMPOSE_PROJECT],
                    env_file=Path("/tmp/env"), build_root=Path("/tmp/source"), environment={}, env_fd=9,
                    seed=SimpleNamespace(main=Mock(return_value=0)), source_sha=SOURCE_SHA, port="18080",
                    stage_values={}, docker_endpoint=Path("/run/user/1000/docker.sock"),
                    runner=runner, sleep=lambda _: None, monotonic=lambda: 0,
                    report=lambda message, **kwargs: reports.append(message),
                )
            passed = any(message.startswith("STAGING_ROLLBACK_PASS ") for message in reports)
            self.assertEqual(passed, inspect_code == 0 and not remaining)

    def test_redirect_is_not_accepted_as_http_health(self) -> None:
        reports = []
        ticks = iter((0, 0, 0, 0, 121))
        def runner(command, **kwargs):
            if command[0] == "/usr/bin/curl":
                self.assertEqual(command[1], "-q")
                self.assertEqual(kwargs["stdout"], launch.subprocess.PIPE)
                return SimpleNamespace(returncode=0, stdout="302")
            output = '{"Health":"healthy"}' if "ps" in command and "--all" not in command and "compose" in command else ""
            return SimpleNamespace(returncode=0, stdout=output)
        with self.assertRaisesRegex(launch.LaunchBlocked, "http-health"):
            launch.run_deploy_transaction(
                compose=["/usr/bin/docker", "--context", launch.CONTEXT_NAME, "compose", "-p", launch.COMPOSE_PROJECT],
                env_file=Path("/tmp/env"), build_root=Path("/tmp/source"), environment={}, env_fd=9,
                seed=SimpleNamespace(main=Mock(return_value=0)), source_sha=SOURCE_SHA, port="18080",
                stage_values={}, docker_endpoint=Path("/run/user/1000/docker.sock"),
                runner=runner, sleep=lambda _: None, monotonic=lambda: next(ticks),
                report=lambda message, **kwargs: reports.append(message),
            )
        self.assertFalse(any(message.startswith("STAGING_TRANSACTION_PASS") for message in reports))

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
