#!/usr/bin/env python3
"""Offline regression tests for the staging CI Compose guard."""

from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GUARD_PATH = ROOT / "scripts/staging/ci-isolation-guard.py"
SPEC = importlib.util.spec_from_file_location("ci_isolation_guard", GUARD_PATH)
assert SPEC is not None and SPEC.loader is not None
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

SOURCE_SHA = "a" * 40
BOOTSTRAP_PASSWORD = "1" * 64
AUTH_SECRET = "2" * 64
APP_PASSWORD = "3" * 64
MIGRATE_PASSWORD = "4" * 64
PORT = 49199
APP_DATABASE_URL = f"postgresql://belib_staging_app:{APP_PASSWORD}@db:5432/belib_staging"
MIGRATE_DATABASE_URL = f"postgresql://belib_staging_migrate:{MIGRATE_PASSWORD}@db:5432/belib_staging"


def compose_model() -> dict:
    return {
        "name": guard.COMPOSE_PROJECT,
        "services": {
            "db": {
                "image": "postgres:16.14-bookworm",
                "command": None,
                "entrypoint": None,
                "environment": {
                    "POSTGRES_USER": "belib_staging_bootstrap",
                    "POSTGRES_DB": "belib_staging",
                    "POSTGRES_PASSWORD": BOOTSTRAP_PASSWORD,
                    "BELIB_STAGE_DB_APP_PASSWORD": APP_PASSWORD,
                    "BELIB_STAGE_DB_MIGRATE_PASSWORD": MIGRATE_PASSWORD,
                },
                "networks": {"isolated": None},
                "volumes": [
                    {
                        "type": "volume",
                        "source": "staging-postgres-data",
                        "target": "/var/lib/postgresql/data",
                    },
                    {
                        "type": "bind",
                        "source": str(ROOT / "scripts/staging/10-belib-staging-roles.sh"),
                        "target": "/docker-entrypoint-initdb.d/10-belib-staging-roles.sh",
                        "read_only": True,
                    },
                ],
            },
            "migrate": {
                "build": {"context": "./backend", "target": "migration"},
                "environment": {"DATABASE_URL": MIGRATE_DATABASE_URL},
                "user": "node",
                "networks": {"isolated": None},
            },
            "backend": {
                "build": {
                    "context": "./backend",
                    "target": "runtime",
                    "args": {"VCS_REF": SOURCE_SHA},
                },
                "user": "node",
                "environment": {
                    "DATABASE_URL": APP_DATABASE_URL,
                    "APP_PORT": "3000",
                    "ENVIRONMENT": "test",
                    "FRONTEND_URL": f"http://127.0.0.1:{PORT}",
                    "BETTER_AUTH_URL": f"http://127.0.0.1:{PORT}",
                    "BETTER_AUTH_SECRET": AUTH_SECRET,
                    "BOOK_FILE_STORAGE_DRIVER": "local",
                    "BOOK_FILE_STORAGE_ROOT": "/home/node/books",
                    "NPM_CONFIG_CACHE": "/tmp/npm-cache",
                },
                "networks": {"isolated": None},
                "expose": ["3000"],
                "volumes": [
                    {
                        "type": "volume",
                        "source": "staging-book-files",
                        "target": "/home/node",
                    }
                ],
            },
            "frontend": {
                "build": {
                    "context": "./frontend",
                    "target": "runtime",
                    "args": {
                        "VCS_REF": SOURCE_SHA,
                        "VITE_API_BASE_URL": f"http://127.0.0.1:{PORT}",
                        "VITE_PUBLIC_DEMO_EMAIL": "",
                        "VITE_PUBLIC_DEMO_PASSWORD": "",
                        "VITE_PUBLIC_DEMO_ADMIN_EMAIL": "",
                        "VITE_PUBLIC_DEMO_ADMIN_PASSWORD": "",
                    },
                },
                "networks": {"isolated": None},
                "expose": ["8080"],
            },
            "nginx": {
                "image": "nginx:1.27-alpine",
                "ports": [
                    {
                        "host_ip": "127.0.0.1",
                        "published": str(PORT),
                        "target": 8080,
                        "protocol": "tcp",
                    }
                ],
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(ROOT / "nginx/nginx.conf"),
                        "target": "/etc/nginx/nginx.conf",
                        "read_only": True,
                    },
                    {
                        "type": "bind",
                        "source": str(ROOT / "nginx/conf.d/default.conf"),
                        "target": "/etc/nginx/conf.d/default.conf",
                        "read_only": True,
                    },
                ],
                "networks": {"isolated": None},
            },
        },
        "networks": {
            "isolated": {
                "internal": True,
                "name": "belib-isolated-i5_isolated",
            }
        },
        "volumes": {
            "staging-postgres-data": {"name": "belib-isolated-i5_staging-postgres-data"},
            "staging-book-files": {"name": "belib-isolated-i5_staging-book-files"},
        },
    }


class CIIsolationGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose_source = (ROOT / "compose.staging.yaml").read_text(encoding="utf-8")

    def assert_rejected(
        self,
        *,
        source=None,
        model=None,
        source_sha=SOURCE_SHA,
        checkout_sha=SOURCE_SHA,
    ):
        with self.assertRaises(guard.GuardError):
            guard.validate(
                source_sha=source_sha,
                checkout_sha=checkout_sha,
                compose_source=self.compose_source if source is None else source,
                compose_model=compose_model() if model is None else model,
            )

    def test_accepts_the_isolated_compose_model_for_exact_pr_head(self):
        guard.validate(
            source_sha=SOURCE_SHA,
            checkout_sha=SOURCE_SHA,
            compose_source=self.compose_source,
            compose_model=compose_model(),
        )

    def test_rejects_short_or_unmatched_source_revision(self):
        self.assert_rejected(source_sha=SOURCE_SHA[:12])
        self.assert_rejected(checkout_sha="b" * 40)

    def test_rejects_source_revision_that_is_not_pinned_in_both_images(self):
        model = compose_model()
        model["services"]["backend"]["build"]["args"]["VCS_REF"] = "main"
        self.assert_rejected(model=model)
        model = compose_model()
        model["services"]["frontend"]["build"]["args"]["VCS_REF"] = "latest"
        self.assert_rejected(model=model)

    def test_rejects_unpinned_compose_source_revision(self):
        source = self.compose_source.replace(
            "${BELIB_STAGE_VCS_REF:?Set full reviewed staging SHA}",
            "${BELIB_STAGE_VCS_REF:-main}",
            1,
        )
        self.assert_rejected(source=source)

    def test_rejects_wrong_compose_name(self):
        source = self.compose_source.replace(
            "name: belib-isolated-i5", "name: belib-production", 1
        )
        self.assert_rejected(source=source)
        model = compose_model()
        model["name"] = "belib-production"
        self.assert_rejected(model=model)

    def test_rejects_wrong_port_or_non_loopback_bind(self):
        model = compose_model()
        model["services"]["nginx"]["ports"][0]["published"] = "8080"
        self.assert_rejected(model=model)
        model = compose_model()
        model["services"]["nginx"]["ports"][0]["host_ip"] = "0.0.0.0"
        self.assert_rejected(model=model)
        source = self.compose_source.replace(
            '      - "127.0.0.1:', '      - "0.0.0.0:', 1
        )
        self.assert_rejected(source=source)

    def test_rejects_production_environment_file_and_compose_overlay(self):
        for addition in (
            "    env_file: .env.production\n",
            "    extends:\n      file: compose.yaml\n      service: backend\n",
            "    include: compose.yaml\n",
        ):
            self.assert_rejected(source=self.compose_source + "\n" + addition)

    def test_rejects_external_or_production_database(self):
        model = compose_model()
        production_url = "postgresql://prod:secret@db.example.invalid/production"
        model["services"]["backend"]["environment"]["DATABASE_URL"] = production_url
        self.assert_rejected(model=model)
        model = compose_model()
        model["services"]["migrate"]["environment"]["DATABASE_URL"] = (
            "postgresql://belib_staging:secret@production-db:5432/belib_staging"
        )
        self.assert_rejected(model=model)

    def test_rejects_mixed_app_and_migration_role_identities(self):
        model = compose_model()
        model["services"]["backend"]["environment"]["DATABASE_URL"] = MIGRATE_DATABASE_URL
        self.assert_rejected(model=model)

        model = compose_model()
        model["services"]["migrate"]["environment"]["DATABASE_URL"] = APP_DATABASE_URL
        self.assert_rejected(model=model)

        model = compose_model()
        model["services"]["backend"]["environment"]["DATABASE_URL"] = (
            f"postgresql://belib_staging_bootstrap:{BOOTSTRAP_PASSWORD}@db:5432/belib_staging"
        )
        self.assert_rejected(model=model)

    def test_rejects_bad_bootstrap_identity_and_init_entrypoint_override(self):
        model = compose_model()
        model["services"]["db"]["environment"]["POSTGRES_USER"] = "belib_staging_app"
        self.assert_rejected(model=model)

        model = compose_model()
        model["services"]["db"]["environment"]["POSTGRES_DB"] = "production"
        self.assert_rejected(model=model)

        for key in ("command", "entrypoint"):
            model = compose_model()
            model["services"]["db"][key] = ["/bin/true"]
            self.assert_rejected(model=model)

    def test_rejects_shared_bootstrap_runtime_and_auth_secrets(self):
        for runtime_key in ("BELIB_STAGE_DB_APP_PASSWORD", "BELIB_STAGE_DB_MIGRATE_PASSWORD"):
            model = compose_model()
            model["services"]["db"]["environment"][runtime_key] = BOOTSTRAP_PASSWORD
            self.assert_rejected(model=model)

        model = compose_model()
        model["services"]["db"]["environment"]["BELIB_STAGE_DB_APP_PASSWORD"] = MIGRATE_PASSWORD
        self.assert_rejected(model=model)

        for shared_password in (BOOTSTRAP_PASSWORD, APP_PASSWORD, MIGRATE_PASSWORD):
            model = compose_model()
            model["services"]["backend"]["environment"]["BETTER_AUTH_SECRET"] = shared_password
            self.assert_rejected(model=model)

    def test_requires_role_initializer_readonly_on_db_and_each_data_volume_role(self):
        model = compose_model()
        model["services"]["db"]["volumes"].pop()
        self.assert_rejected(model=model)

        model = compose_model()
        model["services"]["db"]["volumes"][1]["read_only"] = False
        self.assert_rejected(model=model)

        model = compose_model()
        model["services"]["db"]["volumes"][0]["source"] = "staging-book-files"
        self.assert_rejected(model=model)

        model = compose_model()
        model["services"]["backend"]["volumes"][0]["source"] = "staging-postgres-data"
        self.assert_rejected(model=model)

    def test_rejects_r2_settings_and_public_demo_credentials(self):
        model = compose_model()
        model["services"]["backend"]["environment"]["R2_ACCESS_KEY_ID"] = "fixture"
        self.assert_rejected(model=model)
        model = compose_model()
        model["services"]["frontend"]["build"]["args"]["VITE_PUBLIC_DEMO_PASSWORD"] = "fixture"
        self.assert_rejected(model=model)
        source = self.compose_source + "\n      R2_SECRET_ACCESS_KEY: ${R2_SECRET_ACCESS_KEY}\n"
        self.assert_rejected(source=source)

    def test_requires_internal_network_and_owned_volumes(self):
        model = compose_model()
        model["networks"]["isolated"]["internal"] = False
        self.assert_rejected(model=model)
        model = compose_model()
        model["volumes"]["staging-postgres-data"]["external"] = True
        self.assert_rejected(model=model)
        model = compose_model()
        model["volumes"]["staging-book-files"]["name"] = "production-books"
        self.assert_rejected(model=model)
        model = compose_model()
        model["services"]["nginx"]["volumes"][0]["source"] = "/production/nginx.conf"
        self.assert_rejected(model=model)

    def test_pr_staging_ci_is_read_only_and_checks_out_exact_head(self):
        workflow = (ROOT / ".github/workflows/staging-ci.yml").read_text(encoding="utf-8")
        self.assertRegex(workflow, r"(?m)^\s*pull_request:\s*$")
        self.assertNotIn("pull_request_target", workflow)
        self.assertIn("contents: read", workflow)
        checkouts = re.findall(
            r"(?m)^\s*ref:\s*\$\{\{\s*github\.event\.pull_request\.head\.sha\s*\}\}\s*$",
            workflow,
        )
        self.assertEqual(len(checkouts), 4)
        self.assertIn("config --format json", workflow)
        self.assertNotRegex(workflow, r"(?m)^\s*docker compose .*?\bup\b")
        self.assertNotIn("BELIB_STAGE_DB_PASSWORD=", workflow)
        expected_fake_names = {
            "BELIB_STAGE_DB_BOOTSTRAP_PASSWORD",
            "BELIB_STAGE_DB_APP_PASSWORD",
            "BELIB_STAGE_DB_MIGRATE_PASSWORD",
            "BELIB_STAGE_AUTH_SECRET",
        }
        fake_values = []
        for line in workflow.splitlines():
            assignment = line.strip().removesuffix("\\").strip()
            name, separator, value = assignment.partition("=")
            if name in expected_fake_names and separator:
                self.assertRegex(value, r"\A[a-f0-9]{64}\Z")
                fake_values.append((name, value))
        self.assertEqual({name for name, _ in fake_values}, expected_fake_names)
        self.assertEqual(len(fake_values), 4)
        self.assertEqual(len({value for _, value in fake_values}), 4)

    def test_pr_ci_runs_real_build_lint_and_playwright_reader_commands(self):
        workflow = (ROOT / ".github/workflows/staging-ci.yml").read_text(encoding="utf-8")
        for command in (
            "npm test",
            "npm run build",
            "npm run lint",
            "npm run test:node",
            "npm run test:readers",
            "npm run test:e2e",
        ):
            self.assertIn(command, workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
