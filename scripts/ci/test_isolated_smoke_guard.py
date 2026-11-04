"""Guard tests for the GitHub-hosted isolated recovery rehearsal.

The fake Docker executable records only its arguments; these tests never call Docker,
systemctl, a daemon socket, or a real service.
"""

import os
import subprocess
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
case "$*" in
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

    def test_rejects_unexpected_running_container_before_compose(self):
        result = self.run_guard(FAKE_RUNNING_CONTAINERS="unexpected-container-id")
        self.assertNotEqual(result.returncode, 0)
        calls = self.logged_docker_calls()
        self.assertEqual(calls, ["ps -q --no-trunc"])
        self.assertNotIn("attacker-compose.yml", result.stdout + result.stderr)

    def test_rejects_existing_production_container_before_compose(self):
        result = self.run_guard(FAKE_PRODUCTION_CONTAINERS="old-production-container")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            self.logged_docker_calls(),
            [
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
                "ps -q --no-trunc",
                "ps -aq --filter label=com.docker.compose.project=belib-production",
                "volume ls -q --filter label=com.docker.compose.project=belib-recovery-ci-123-1",
            ],
        )


if __name__ == "__main__":
    unittest.main()
