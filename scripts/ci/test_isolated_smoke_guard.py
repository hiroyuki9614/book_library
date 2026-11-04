"""Guard tests for the GitHub-hosted isolated recovery rehearsal.

The fake Docker executable records only its arguments; these tests never call Docker,
systemctl, a daemon socket, or a real service.
"""

import ast
import os
import re
import select
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "isolated-smoke.sh"


class IsolatedSmokeGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.log = self.root / "docker.log"
        docker = self.bin_dir / "docker"
        docker.write_text(
            """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
args="$*"
args="${args#--host unix:///var/run/docker.sock }"
if [[ "${FAKE_FAIL_DOCKER_QUERY:-}" == "$args" ]]; then exit 42; fi
if [[ "${FAKE_FAIL_NETWORK_INSPECT:-}" == true && "$args" == "network inspect "* ]]; then exit 43; fi
case "$args" in
  "context inspect --format {{.Endpoints.docker.Host}}") printf '%s\\n' "$FAKE_DOCKER_ENDPOINT" ;;
  "ps -q --no-trunc") printf '%s\\n' "$FAKE_RUNNING_CONTAINERS" ;;
  "ps -aq --filter label=com.docker.compose.project=belib-production") printf '%s\\n' "$FAKE_PRODUCTION_CONTAINERS" ;;
  "volume ls -q --filter label=com.docker.compose.project=belib-recovery-ci-123-1") printf '%s\\n' "$FAKE_PROJECT_VOLUMES" ;;
  "network ls -q --filter name=^belib-recovery-ci-123-1_default$") printf '%s\\n' "$FAKE_PROJECT_NETWORKS" ;;
esac
""",
            encoding="utf-8",
        )
        docker.chmod(0o755)

    def run_guard(self, **updates):
        env = {
            **os.environ,
            "PATH": f"{self.bin_dir}:/usr/bin:/bin",
            "FAKE_DOCKER_LOG": str(self.log),
            "FAKE_RUNNING_CONTAINERS": "",
            "FAKE_PRODUCTION_CONTAINERS": "",
            "FAKE_PROJECT_VOLUMES": "",
            "FAKE_PROJECT_NETWORKS": "",
            "FAKE_FAIL_DOCKER_QUERY": "",
            "FAKE_DOCKER_ENDPOINT": "unix:///var/run/docker.sock",
            "BELIB_CI_RECOVERY_REHEARSAL": "github-hosted-v1",
            "RUNNER_ENVIRONMENT": "github-hosted",
            "GITHUB_ACTIONS": "true",
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1",
            **updates,
        }
        return subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def logged_docker_calls(self):
        calls = self.raw_logged_docker_calls()
        return [call.removeprefix("--host unix:///var/run/docker.sock ") for call in calls]

    def raw_logged_docker_calls(self):
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    def test_rejects_non_github_host_without_docker_call(self):
        result = self.run_guard(RUNNER_ENVIRONMENT="self-hosted")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("GitHub-hosted", result.stderr)
        self.assertEqual(self.logged_docker_calls(), [])

    def test_rehearsal_rejects_caller_selected_path_project_or_url(self):
        for field, value in (
            ("COMPOSE_FILE", "/tmp/attacker-compose.yml"),
            ("COMPOSE_PROJECT_NAME", "belib-production"),
            ("API_URL", "https://example.invalid"),
            ("UI_URL", "https://example.invalid"),
        ):
            result = self.run_guard(**{field: value})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not accept caller-selected", result.stderr)
            self.assertEqual(self.logged_docker_calls(), [])
            self.log.unlink(missing_ok=True)

    def test_rejects_docker_endpoint_override_environment_before_docker(self):
        for field, value in (
            ("DOCKER_HOST", "tcp://remote.example:2376"),
            ("DOCKER_CONTEXT", "remote"),
            ("DOCKER_TLS", "1"),
            ("DOCKER_TLS_VERIFY", "1"),
            ("DOCKER_CERT_PATH", "/tmp/docker-certs"),
            ("DOCKER_CONFIG", "/tmp/docker-config"),
        ):
            result = self.run_guard(**{field: value})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Docker endpoint override", result.stderr)
            self.assertEqual(self.logged_docker_calls(), [])
            self.log.unlink(missing_ok=True)

    def test_rejects_nonlocal_or_unavailable_docker_context_before_inventory(self):
        for updates in (
            {"FAKE_DOCKER_ENDPOINT": "tcp://remote.example:2376"},
            {"FAKE_FAIL_DOCKER_QUERY": "context inspect --format {{.Endpoints.docker.Host}}"},
        ):
            result = self.run_guard(**updates)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("runner-local socket", result.stderr)
            self.assertEqual(
                self.logged_docker_calls(),
                ["context inspect --format {{.Endpoints.docker.Host}}"],
            )
            self.assertNotIn("remote.example", result.stdout + result.stderr)
            self.log.unlink(missing_ok=True)

    def test_rehearsal_pins_all_post_preflight_docker_calls_to_local_socket(self):
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)  # Fake network inspection has no identity receipt.
        calls = self.raw_logged_docker_calls()
        self.assertGreater(len(calls), 1)
        self.assertTrue(all(call.startswith("--host unix:///var/run/docker.sock ") for call in calls))
        self.assertIn(
            "--host unix:///var/run/docker.sock context inspect --format {{.Endpoints.docker.Host}}",
            calls,
        )

    def test_failed_post_restart_network_inspect_reports_safe_receipt(self):
        source = SCRIPT.read_text(encoding="utf-8")
        function = re.search(
            r"(?ms)^verify_post_restart_network_identity\(\) \{\n.*?^\}",
            source,
        )
        self.assertIsNotNone(function)
        harness = "\n".join(
            (
                "set -euo pipefail",
                'docker() { command docker --host unix:///var/run/docker.sock "$@"; }',
                "network=belib-recovery-ci-123-1_default",
                function.group(0),
                "verify_post_restart_network_identity 'belib-recovery-ci-123-1_default|false|belib-recovery-ci-123-1|default'",
            )
        )
        result = subprocess.run(
            ["bash", "-c", harness],
            cwd=ROOT,
            env={
                **os.environ,
                "PATH": f"{self.bin_dir}:/usr/bin:/bin",
                "FAKE_DOCKER_LOG": str(self.log),
                "FAKE_FAIL_NETWORK_INSPECT": "true",
            },
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "Expected network receipt: belib-recovery-ci-123-1_default|false|belib-recovery-ci-123-1|default",
            result.stderr,
        )
        self.assertIn(
            "Observed network receipt: unavailable (Docker network inspect failed)",
            result.stderr,
        )
        self.assertEqual(
            self.raw_logged_docker_calls(),
            ['--host unix:///var/run/docker.sock network inspect --format {{.Name}}|{{.Internal}}|{{index .Labels "com.docker.compose.project"}}|{{index .Labels "com.docker.compose.network"}} belib-recovery-ci-123-1_default'],
        )

    def test_crash_receipt_rejects_conflicting_or_malformed_cgroups(self):
        source = SCRIPT.read_text(encoding="utf-8")
        crash_python = re.search(r"<<'PYCRASH'\n(.*?)\nPYCRASH", source, re.S).group(1)
        module = ast.parse(crash_python)
        namespace = {}
        functions = {
            node.name: node for node in module.body
            if isinstance(node, ast.FunctionDef)
            and node.name in {"docker_cgroup_matches", "intended_pidfd_exit_observed"}
        }
        self.assertEqual(set(functions), {"docker_cgroup_matches", "intended_pidfd_exit_observed"})
        exec(compile(ast.Module(body=list(functions.values()), type_ignores=[]), "crash-receipt", "exec"), namespace)
        matches = namespace["docker_cgroup_matches"]
        container_id = "a" * 64
        other_id = "b" * 64
        self.assertTrue(matches(f"0::/system.slice/docker-{container_id}.scope", container_id))
        self.assertTrue(matches(f"1:name=systemd:/docker/{container_id}", container_id))
        self.assertTrue(
            matches(
                f"1:cpu:/docker/{container_id}\n2:memory:/docker/{container_id}",
                container_id,
            )
        )
        self.assertFalse(matches(f"0::/system.slice/prefix-{container_id}-suffix", container_id))
        self.assertFalse(matches(f"0::/docker/{container_id}\n1:name=systemd:/docker/{container_id}", container_id))
        self.assertFalse(
            matches(
                f"1:cpu:/docker/{container_id}\n2:memory:/docker/{other_id}",
                container_id,
            )
        )
        self.assertFalse(matches(f"1:cpu:/docker/{container_id}\n1:memory:/docker/{container_id}", container_id))
        self.assertFalse(matches(f"1:cpu,cpu:/docker/{container_id}", container_id))
        self.assertFalse(matches(f"malformed/{container_id}", container_id))

    def test_pidfd_poll_receipt_requires_intended_fd_and_pollin(self):
        source = SCRIPT.read_text(encoding="utf-8")
        crash_python = re.search(r"<<'PYCRASH'\n(.*?)\nPYCRASH", source, re.S).group(1)
        module = ast.parse(crash_python)
        function = next(
            node for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "intended_pidfd_exit_observed"
        )
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "pidfd-poll", "exec"), namespace)
        observed = namespace["intended_pidfd_exit_observed"]
        self.assertTrue(observed([(17, select.POLLIN)], 17, select.POLLIN))
        self.assertFalse(observed([(18, select.POLLIN)], 17, select.POLLIN))
        self.assertFalse(observed([(17, select.POLLHUP)], 17, select.POLLIN))

    def test_failure_diagnostics_never_print_mount_records(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("mounts_sha256=%s", source)
        self.assertIn('echo "Expected network receipt: $expected"', source)
        self.assertIn('echo "Observed network receipt: $observed"', source)
        self.assertNotIn('echo "Before: $before_record"', source)
        self.assertNotIn('echo "After:  ${runtime_records[$service]}"', source)

    def test_mount_difference_diagnostic_names_fields_without_values(self):
        source = SCRIPT.read_text(encoding="utf-8")
        function = re.search(
            r"(?ms)^safe_mount_diff\(\) \{\n.*?^\}(?=\nsafe_volume_receipt)", source
        )
        self.assertIsNotNone(function)
        harness = "\n".join(
            (
                "set -euo pipefail",
                function.group(0),
                "safe_mount_diff \"$1\" \"$2\"",
            )
        )
        before = (
            'a|sha256:img|project|nginx|unless-stopped|'
            '[{"Type":"bind","Source":"/secret/belib/nginx.conf",'
            '"Destination":"/etc/nginx/conf.d/default.conf","Mode":"ro","RW":false}]'
        )
        after = before.replace("/secret/belib/nginx.conf", "/secret/changed/nginx.conf")
        python_argv_log = self.root / "python-argv.log"
        python_wrapper = self.bin_dir / "python3"
        python_wrapper.write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "printf '%s\\n' \"$@\" > \"$FAKE_PYTHON_ARGV_LOG\"\n"
            "exec \"$REAL_PYTHON\" \"$@\"\n",
            encoding="utf-8",
        )
        python_wrapper.chmod(0o755)
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin_dir}:{env['PATH']}",
                "FAKE_PYTHON_ARGV_LOG": str(python_argv_log),
                "REAL_PYTHON": sys.executable,
            }
        )
        result = subprocess.run(
            ["bash", "-c", harness, "mount-diff", before, after],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Mounts[0].Source", result.stdout)
        self.assertNotIn("/secret", result.stdout + result.stderr)
        self.assertNotIn("nginx.conf", result.stdout + result.stderr)
        python_argv = python_argv_log.read_text(encoding="utf-8")
        self.assertIn("-c", python_argv)
        self.assertNotIn(before, python_argv)
        self.assertNotIn(after, python_argv)
        self.assertNotIn("/secret", python_argv)
        self.assertIn('runtime_records[nginx]}" == "$baseline_nginx"', source)

    def test_docker_inventory_query_errors_fail_closed(self):
        queries = (
            "ps -q --no-trunc",
            "ps -aq --filter label=com.docker.compose.project=belib-production",
            "volume ls -q --filter label=com.docker.compose.project=belib-recovery-ci-123-1",
            "network ls -q --filter name=^belib-recovery-ci-123-1_default$",
        )
        for query in queries:
            result = self.run_guard(FAKE_FAIL_DOCKER_QUERY=query)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(
                self.logged_docker_calls(),
                ["context inspect --format {{.Endpoints.docker.Host}}"]
                + list(queries[: queries.index(query) + 1]),
            )
            self.log.unlink(missing_ok=True)

    def test_rejects_unexpected_running_container_before_compose(self):
        result = self.run_guard(FAKE_RUNNING_CONTAINERS="unexpected-container-id")
        self.assertNotEqual(result.returncode, 0)
        calls = self.logged_docker_calls()
        self.assertEqual(
            calls,
            ["context inspect --format {{.Endpoints.docker.Host}}", "ps -q --no-trunc"],
        )
        self.assertNotIn("attacker-compose.yml", result.stdout + result.stderr)

    def test_rejects_existing_production_container_before_compose(self):
        result = self.run_guard(FAKE_PRODUCTION_CONTAINERS="old-production-container")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            self.logged_docker_calls(),
            [
                "context inspect --format {{.Endpoints.docker.Host}}",
                "ps -q --no-trunc",
                "ps -aq --filter label=com.docker.compose.project=belib-production",
            ],
        )

    def test_rejects_reused_synthetic_project_network_before_compose(self):
        result = self.run_guard(FAKE_PROJECT_NETWORKS="stale-network")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            self.logged_docker_calls(),
            [
                "context inspect --format {{.Endpoints.docker.Host}}",
                "ps -q --no-trunc",
                "ps -aq --filter label=com.docker.compose.project=belib-production",
                "volume ls -q --filter label=com.docker.compose.project=belib-recovery-ci-123-1",
                "network ls -q --filter name=^belib-recovery-ci-123-1_default$",
            ],
        )

    def test_rejects_reused_synthetic_project_volumes_before_compose(self):
        result = self.run_guard(FAKE_PROJECT_VOLUMES="stale-volume")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            self.logged_docker_calls(),
            [
                "context inspect --format {{.Endpoints.docker.Host}}",
                "ps -q --no-trunc",
                "ps -aq --filter label=com.docker.compose.project=belib-production",
                "volume ls -q --filter label=com.docker.compose.project=belib-recovery-ci-123-1",
            ],
        )


if __name__ == "__main__":
    unittest.main()
