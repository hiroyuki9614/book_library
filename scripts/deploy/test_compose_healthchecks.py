"""Read-only Compose checks for the frontend and nginx loopback healthchecks."""

import json
import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_HEALTHCHECK = {
    "test": ["CMD", "busybox", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/"],
    "interval": "30s",
    "timeout": "3s",
    "start_period": "10s",
    "retries": 3,
}


class ComposeHealthcheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        synthetic_env = {
            "PATH": os.environ["PATH"],
            "POSTGRES_USER": "ci",
            "POSTGRES_PASSWORD": "ci-only-placeholder",
            "POSTGRES_DB": "ci_healthcheck",
            "VCS_REF": "1" * 40,
            "VITE_API_BASE_URL": "http://127.0.0.1:8080",
            "ENVIRONMENT": "test",
            "FRONTEND_URL": "http://127.0.0.1:8080",
            "BETTER_AUTH_URL": "http://127.0.0.1:8080",
            "BETTER_AUTH_SECRET": "ci-only-placeholder-secret",
            "BOOK_FILE_STORAGE_DRIVER": "local",
        }
        result = subprocess.run(
            [
                "docker", "compose", "--env-file", "/dev/null", "--file", "compose.yaml",
                "config", "--format", "json",
            ],
            cwd=ROOT,
            env=synthetic_env,
            check=True,
            capture_output=True,
            text=True,
        )
        cls.config = json.loads(result.stdout)

    def test_frontend_and_nginx_have_bounded_loopback_http_checks(self):
        services = self.config["services"]
        for name in ("frontend", "nginx"):
            with self.subTest(service=name):
                self.assertEqual(services[name]["healthcheck"], EXPECTED_HEALTHCHECK)
                self.assertEqual(services[name]["restart"], "unless-stopped")

    def test_alpine_runtime_bases_support_busybox_wget(self):
        frontend_dockerfile = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime",
            frontend_dockerfile,
        )
        self.assertEqual(self.config["services"]["nginx"]["image"], "nginx:1.27-alpine")

    def test_healthchecks_do_not_change_nginx_dependency_conditions(self):
        depends_on = self.config["services"]["nginx"]["depends_on"]
        self.assertEqual(depends_on["backend"]["condition"], "service_started")
        self.assertEqual(depends_on["frontend"]["condition"], "service_started")


if __name__ == "__main__":
    unittest.main()
