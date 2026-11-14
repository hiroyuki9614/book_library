#!/usr/bin/env python3
"""Offline regression tests for the staging CI Compose guard."""

from __future__ import annotations

import importlib.util
import re
import shutil
import tempfile
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


def compose_model(root: Path = ROOT) -> dict:
    return {
        "name": guard.COMPOSE_PROJECT,
        "services": {
            "db": {
                "image": guard.POSTGRES_IMAGE,
                "platform": guard.EXPECTED_PLATFORM,
                "command": None,
                "entrypoint": None,
                "environment": {
                    "POSTGRES_USER": "belib_staging_bootstrap",
                    "POSTGRES_DB": "belib_staging",
                    "POSTGRES_PASSWORD": BOOTSTRAP_PASSWORD,
                    "BELIB_STAGE_DB_APP_PASSWORD": APP_PASSWORD,
                    "BELIB_STAGE_DB_MIGRATE_PASSWORD": MIGRATE_PASSWORD,
                },
                "healthcheck": {
                    "test": ["CMD-SHELL", guard.EXPECTED_DB_HEALTHCHECK_COMMAND],
                    "interval": "5s",
                    "timeout": "5s",
                    "retries": 12,
                },
                "networks": {"isolated": None},
                "restart": "unless-stopped",
                "volumes": [
                    {
                        "type": "volume",
                        "source": "staging-postgres-data",
                        "target": "/var/lib/postgresql/data",
                        "volume": {},
                    },
                    {
                        "type": "bind",
                        "source": str(root / "scripts/staging/10-belib-staging-roles.sh"),
                        "target": "/docker-entrypoint-initdb.d/10-belib-staging-roles.sh",
                        "read_only": True,
                        "bind": {},
                    },
                ],
            },
            "migrate": {
                "build": {
                    "context": "./backend",
                    "dockerfile": guard.EXPECTED_DOCKERFILE_REFERENCES["migrate"],
                    "target": "migration",
                },
                "command": ["npx", "prisma", "migrate", "deploy"],
                "depends_on": {"db": {"condition": "service_healthy", "required": True}},
                "entrypoint": None,
                "environment": {"DATABASE_URL": MIGRATE_DATABASE_URL},
                "user": "node",
                "platform": guard.EXPECTED_PLATFORM,
                "networks": {"isolated": None},
                "profiles": ["migration"],
                "restart": "no",
            },
            "backend": {
                "build": {
                    "context": "./backend",
                    "dockerfile": guard.EXPECTED_DOCKERFILE_REFERENCES["backend"],
                    "target": "runtime",
                    "args": {"VCS_REF": SOURCE_SHA},
                },
                "command": None,
                "platform": guard.EXPECTED_PLATFORM,
                "depends_on": {"db": {"condition": "service_healthy", "required": True}},
                "entrypoint": None,
                "user": "node",
                "environment": {
                    "DATABASE_URL": APP_DATABASE_URL,
                    "APP_PORT": "3000",
                    "ENVIRONMENT": "test",
                    "FRONTEND_URL": "https://stg-bl.hiroyuki9614.com",
                    "BETTER_AUTH_URL": "https://stg-bl.hiroyuki9614.com",
                    "BETTER_AUTH_SECRET": AUTH_SECRET,
                    "BOOK_FILE_STORAGE_DRIVER": "local",
                    "BOOK_FILE_STORAGE_ROOT": "/home/node/books",
                    "NPM_CONFIG_CACHE": "/tmp/npm-cache",
                },
                "networks": {"isolated": None},
                "expose": ["3000"],
                "restart": "unless-stopped",
                "volumes": [
                    {
                        "type": "volume",
                        "source": "staging-book-files",
                        "target": "/home/node",
                        "volume": {},
                    }
                ],
            },
            "frontend": {
                "build": {
                    "context": "./frontend",
                    "dockerfile": guard.EXPECTED_DOCKERFILE_REFERENCES["frontend"],
                    "target": "runtime",
                    "args": {
                        "VCS_REF": SOURCE_SHA,
                        "VITE_API_BASE_URL": "https://stg-bl.hiroyuki9614.com",
                        "VITE_PUBLIC_DEMO_EMAIL": "",
                        "VITE_PUBLIC_DEMO_PASSWORD": "",
                        "VITE_PUBLIC_DEMO_ADMIN_EMAIL": "",
                        "VITE_PUBLIC_DEMO_ADMIN_PASSWORD": "",
                    },
                },
                "command": None,
                "platform": guard.EXPECTED_PLATFORM,
                "entrypoint": None,
                "networks": {"isolated": None},
                "expose": ["8080"],
                "restart": "unless-stopped",
            },
            "nginx": {
                "image": guard.NGINX_IMAGE,
                "platform": guard.EXPECTED_PLATFORM,
                "command": None,
                "depends_on": {
                    "backend": {"condition": "service_started", "required": True},
                    "frontend": {"condition": "service_started", "required": True},
                },
                "entrypoint": None,
                "ports": [
                    {
                        "mode": "ingress",
                        "host_ip": "127.0.0.1",
                        "published": str(PORT),
                        "target": 8080,
                        "protocol": "tcp",
                    }
                ],
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(root / "nginx/nginx.conf"),
                        "target": "/etc/nginx/nginx.conf",
                        "read_only": True,
                        "bind": {},
                    },
                    {
                        "type": "bind",
                        "source": str(root / "scripts/staging/nginx-default.conf"),
                        "target": "/etc/nginx/conf.d/default.conf",
                        "read_only": True,
                        "bind": {},
                    },
                ],
                "networks": {"isolated": None, "ingress": None},
                "restart": "unless-stopped",
            },
        },
        "networks": {
            "isolated": {
                "internal": True,
                "ipam": {},
                "name": "belib-isolated-i5_isolated",
            },
            "ingress": {
                "driver": "bridge",
                "ipam": {},
                "name": "belib-isolated-i5_ingress",
            },
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

    def source_with_build_entry(self, service: str, entry: str) -> str:
        marker = f"  {service}:\n"
        start = self.compose_source.index(marker)
        next_service = re.search(r"(?m)^  [A-Za-z0-9][A-Za-z0-9_-]*:\s*$", self.compose_source[start + len(marker):])
        end = start + len(marker) + next_service.start() if next_service else len(self.compose_source)
        service_block = self.compose_source[start:end]
        target_line = next(
            line for line in service_block.splitlines(keepends=True)
            if re.fullmatch(r"      target: [^\n]+\n?", line)
        )
        service_block = service_block.replace(target_line, target_line + f"      {entry}\n", 1)
        return self.compose_source[:start] + service_block + self.compose_source[end:]

    def source_with_service_entry(self, service: str, entry: str) -> str:
        marker = f"  {service}:\n"
        return self.compose_source.replace(marker, marker + f"    {entry}\n", 1)

    def populate_compose_root(self, root: Path) -> None:
        for directory in (
            "backend", "frontend", "nginx", "scripts/staging", ".github/workflows"
        ):
            (root / directory).mkdir(parents=True, exist_ok=True)
        for relative in (
            "nginx/nginx.conf",
            "scripts/staging/10-belib-staging-roles.sh",
            "scripts/staging/nginx-default.conf",
        ):
            (root / relative).write_text("fixture\n", encoding="utf-8")
        (root / "scripts/staging/10-belib-staging-roles.sh").chmod(0o644)
        for relative in guard.EXPECTED_DOCKERFILE_FROM_LINES:
            (root / relative).write_text(
                (ROOT / relative).read_text(encoding="utf-8"), encoding="utf-8"
            )
        (root / ".github/workflows/staging-ci.yml").write_text(
            f"image: {guard.CI_POSTGRES_IMAGE}\n", encoding="utf-8"
        )

    def test_accepts_the_isolated_compose_model_for_exact_pr_head(self):
        guard.validate(
            source_sha=SOURCE_SHA,
            checkout_sha=SOURCE_SHA,
            compose_source=self.compose_source,
            compose_model=compose_model(),
        )

    def test_accepts_github_compose_normalized_mount_defaults(self):
        model = compose_model()
        for service in model["services"].values():
            for mount in service.get("volumes", []):
                if mount["type"] == "bind":
                    mount["bind"] = {"create_host_path": False}
                else:
                    mount["volume"] = {"nocopy": False, "subpath": ""}
                    mount["read_only"] = False

        guard.validate(
            source_sha=SOURCE_SHA,
            checkout_sha=SOURCE_SHA,
            compose_source=self.compose_source,
            compose_model=model,
        )

    def test_source_comments_preserve_hashes_in_plain_and_quoted_scalars(self):
        self.assertEqual(
            guard._strip_yaml_comments(
                'source: ./nginx/nginx.conf#alt # trailing comment\n'
                'target: "/etc/nginx/nginx.conf#alt" # trailing comment\n'
                '  - "value # inside quotes" # trailing comment\n'
            ),
            'source: ./nginx/nginx.conf#alt \n'
            'target: "/etc/nginx/nginx.conf#alt" \n'
            '  - "value # inside quotes" ',
        )
        source = self.compose_source.replace(
            "          create_host_path: false\n",
            "          create_host_path: false # explicit safe value\n",
            1,
        )
        guard._source_contract(source, SOURCE_SHA)

    def test_yaml_comments_use_only_ascii_space_and_tab_as_separators(self):
        source = (
            "plain: ./nginx/nginx.conf#alt # trailing comment\n"
            'quoted: "/etc/nginx/nginx.conf#alt" # trailing comment\n'
            "tabbed: value\t# trailing comment\n"
            "nbsp: value\u00a0#alt\n"
            "narrow_nbsp: value\u202f#alt\n"
        )
        self.assertEqual(
            guard._strip_yaml_comments(source),
            "plain: ./nginx/nginx.conf#alt \n"
            'quoted: "/etc/nginx/nginx.conf#alt" \n'
            "tabbed: value\t\n"
            "nbsp: value\u00a0#alt\n"
            "narrow_nbsp: value\u202f#alt",
        )

    def test_rejects_non_ascii_compose_source_before_yaml_parsing(self):
        separators = (
            "\u00a0",  # no-break space
            "\u202f",  # narrow no-break space
            "\u2000",  # en quad
            "\u2002",  # en space
            "\u2028",  # line separator
            "\u3000",  # ideographic space
        )
        original = "        target: /etc/nginx/nginx.conf\n"
        for separator in separators:
            with self.subTest(codepoint=f"U+{ord(separator):04X}"):
                source = self.compose_source.replace(
                    original,
                    f"        target: /etc/nginx/nginx.conf{separator}#alt\n",
                    1,
                )
                self.assertNotEqual(source, self.compose_source)
                with self.assertRaisesRegex(guard.GuardError, "ASCII"):
                    guard._source_contract(source, SOURCE_SHA)
                self.assert_rejected(source=source, model=compose_model())

    def test_accepts_yaml_tab_comment_separator_and_rejects_tab_indentation(self):
        source = self.compose_source.replace(
            "        target: /etc/nginx/nginx.conf\n",
            "        target: /etc/nginx/nginx.conf\t# explicit safe value\n",
            1,
        )
        guard._source_contract(source, SOURCE_SHA)

        indented_tab = self.compose_source.replace(
            "        source: ./nginx/nginx.conf\n",
            "\t        source: ./nginx/nginx.conf\n",
            1,
        )
        with self.assertRaisesRegex(guard.GuardError, "Tabs are forbidden"):
            guard._source_contract(indented_tab, SOURCE_SHA)

    def test_rejects_ascii_controls_that_are_not_yaml_whitespace(self):
        for control in ("\v", "\f", "\x7f"):
            with self.subTest(codepoint=f"U+{ord(control):04X}"):
                with self.assertRaisesRegex(guard.GuardError, "control characters"):
                    guard._source_contract(self.compose_source + control, SOURCE_SHA)

    def test_rejects_hash_suffixes_in_bind_sources_and_targets(self):
        for old, new in (
            (
                "        source: ./nginx/nginx.conf\n",
                "        source: ./nginx/nginx.conf#/suffix\n",
            ),
            (
                "        target: /etc/nginx/nginx.conf\n",
                "        target: /etc/nginx/nginx.conf#alt\n",
            ),
        ):
            with self.subTest(replacement=new.strip()):
                source = self.compose_source.replace(old, new, 1)
                self.assertNotEqual(source, self.compose_source)
                self.assert_rejected(source=source, model=compose_model())

    def test_rejects_hash_suffixes_inside_quoted_bind_scalars(self):
        for old, new in (
            (
                "        source: ./nginx/nginx.conf\n",
                '        source: "./nginx/nginx.conf#alt"\n',
            ),
            (
                "        target: /etc/nginx/nginx.conf\n",
                '        target: "/etc/nginx/nginx.conf#alt"\n',
            ),
        ):
            with self.subTest(replacement=new.strip()):
                source = self.compose_source.replace(old, new, 1)
                self.assertNotEqual(source, self.compose_source)
                self.assert_rejected(source=source, model=compose_model())

    def test_rejects_mutable_or_substituted_compose_images(self):
        for service, expected in guard.EXPECTED_SOURCE_IMAGES.items():
            tag_only = expected.split("@", 1)[0]
            substitutions = (
                tag_only,
                f"{tag_only}@sha256:{'0' * 64}",
                f"attacker.invalid/replacement:latest@sha256:{'0' * 64}",
            )
            for replacement in substitutions:
                with self.subTest(service=service, replacement=replacement):
                    source = self.compose_source.replace(
                        f"image: {expected}", f"image: {replacement}", 1
                    )
                    self.assert_rejected(source=source)
                    model = compose_model()
                    model["services"][service]["image"] = replacement
                    self.assert_rejected(model=model)

    def test_rejects_mutable_or_substituted_staging_base_images(self):
        dockerfile_paths = tuple(guard.EXPECTED_DOCKERFILE_FROM_LINES)
        for relative in dockerfile_paths:
            for line in guard.EXPECTED_DOCKERFILE_FROM_LINES[relative]:
                parts = line.split()
                if len(parts) < 3 or parts[1] == "build":
                    continue
                pinned = parts[1]
                tag_only = pinned.split("@", 1)[0]
                for replacement in (
                    tag_only,
                    f"{tag_only}@sha256:{'0' * 64}",
                    f"attacker.invalid/replacement:latest@sha256:{'0' * 64}",
                ):
                    with self.subTest(path=relative, replacement=replacement), tempfile.TemporaryDirectory() as temp:
                        repo = Path(temp) / "checkout"
                        repo.mkdir()
                        self.populate_compose_root(repo)
                        dockerfile = repo / relative
                        source = dockerfile.read_text(encoding="utf-8")
                        dockerfile.write_text(
                            source.replace(line, line.replace(pinned, replacement), 1),
                            encoding="utf-8",
                        )
                        with self.assertRaises(guard.GuardError):
                            guard.validate(
                                source_sha=SOURCE_SHA,
                                checkout_sha=SOURCE_SHA,
                                compose_source=self.compose_source,
                                compose_model=compose_model(repo),
                                compose_root=repo,
                            )

    def test_staging_dockerfiles_preserve_shared_multistage_recipes(self):
        mirrors = {
            "backend/Dockerfile": (
                "scripts/staging/Dockerfile.backend.staging",
                (
                    ("FROM node:22-bookworm AS build", f"FROM {guard.NODE_BUILD_IMAGE} AS build"),
                    (
                        "FROM node:22-bookworm-slim AS runtime",
                        f"FROM {guard.NODE_RUNTIME_IMAGE} AS runtime",
                    ),
                ),
            ),
            "frontend/Dockerfile": (
                "scripts/staging/Dockerfile.frontend.staging",
                (
                    ("FROM node:22-bookworm AS build", f"FROM {guard.NODE_BUILD_IMAGE} AS build"),
                    (
                        "FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime",
                        f"FROM {guard.NGINX_UNPRIVILEGED_IMAGE} AS runtime",
                    ),
                ),
            ),
        }
        for shared_path, (staging_path, substitutions) in mirrors.items():
            with self.subTest(shared_path=shared_path):
                expected = (ROOT / shared_path).read_text(encoding="utf-8")
                for source_line, pinned_line in substitutions:
                    self.assertEqual(expected.count(source_line), 1)
                    expected = expected.replace(source_line, pinned_line, 1)
                actual = (ROOT / staging_path).read_text(encoding="utf-8")
                self.assertEqual(actual, expected)

    def test_rejects_shared_or_suspicious_dockerfile_paths(self):
        for replacement in ("Dockerfile", "../../outside/Dockerfile"):
            with self.subTest(replacement=replacement):
                model = compose_model()
                model["services"]["backend"]["build"]["dockerfile"] = replacement
                self.assert_rejected(model=model)

                source = self.compose_source.replace(
                    guard.EXPECTED_DOCKERFILE_REFERENCES["backend"], replacement, 1
                )
                self.assert_rejected(source=source)

    def test_rejects_mutable_staging_ci_postgres_image(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "checkout"
            repo.mkdir()
            self.populate_compose_root(repo)
            workflow = repo / ".github/workflows/staging-ci.yml"
            workflow.write_text("image: postgres:16\n", encoding="utf-8")
            with self.assertRaises(guard.GuardError):
                guard.validate(
                    source_sha=SOURCE_SHA,
                    checkout_sha=SOURCE_SHA,
                    compose_source=self.compose_source,
                    compose_model=compose_model(repo),
                    compose_root=repo,
                )

    def test_database_comment_marker_uses_shared_object_lookup(self):
        role_script = (ROOT / "scripts/staging/10-belib-staging-roles.sh").read_text(encoding="utf-8")
        for source in (self.compose_source, role_script):
            self.assertIn("shobj_description(", source)
            self.assertNotRegex(source, r"(?<!sh)obj_description\s*\(")

    def test_rejects_changed_database_healthcheck_structure_and_model(self):
        mutations = (
            ("test", ["CMD-SHELL", "test \"$(true)\" = \"1\""]),
            ("interval", "30s"),
            ("timeout", "30s"),
            ("retries", 1),
            ("start_period", "1s"),
        )
        for key, value in mutations:
            with self.subTest(key=key):
                model = compose_model()
                model["services"]["db"]["healthcheck"][key] = value
                self.assert_rejected(model=model)

        source = self.compose_source.replace(
            "      retries: 12\n", "      retries: 13\n", 1
        )
        self.assert_rejected(source=source, model=compose_model())

    def test_rejects_unexpected_raw_environment_values_even_when_model_matches(self):
        for service, values in guard.EXPECTED_SOURCE_ENV_VALUES.items():
            for key, expected in values.items():
                with self.subTest(service=service, key=key):
                    old_line = f"      {key}: {expected}\n"
                    new_line = f"      {key}: ${{UNRELATED_SECRET}}\n"
                    source = self.compose_source.replace(old_line, new_line, 1)
                    self.assertNotEqual(source, self.compose_source)
                    self.assert_rejected(source=source, model=compose_model())

        source = self.compose_source.replace(
            "BETTER_AUTH_SECRET: ${BELIB_STAGE_AUTH_SECRET:?Set staging-only Better Auth secret}",
            "BETTER_AUTH_SECRET: ${UNRELATED_SECRET:-"
            + "f" * 64
            + "}",
            1,
        )
        self.assert_rejected(source=source, model=compose_model())

    def test_rejects_symlinked_checkout_build_and_bind_paths(self):
        for component in ("backend", "frontend", "nginx", "scripts"):
            with self.subTest(component=component), tempfile.TemporaryDirectory() as temp:
                temporary_root = Path(temp)
                repo = temporary_root / "checkout"
                repo.mkdir()
                self.populate_compose_root(repo)
                outside = temporary_root / "outside"
                outside.mkdir()
                external_scripts = outside / "scripts"
                (external_scripts / "staging").mkdir(parents=True)
                for relative in (
                    "10-belib-staging-roles.sh",
                    "nginx-default.conf",
                ):
                    (external_scripts / "staging" / relative).write_text(
                        "fixture\n", encoding="utf-8"
                    )

                link = repo / component
                if component == "scripts":
                    shutil.rmtree(link)
                    link.symlink_to(external_scripts, target_is_directory=True)
                else:
                    shutil.rmtree(link)
                    if component == "nginx":
                        (outside / "nginx.conf").write_text("fixture\n", encoding="utf-8")
                    link.symlink_to(outside, target_is_directory=True)

                with self.assertRaises(guard.GuardError):
                    guard.validate(
                        source_sha=SOURCE_SHA,
                        checkout_sha=SOURCE_SHA,
                        compose_source=self.compose_source,
                        compose_model=compose_model(repo),
                        compose_root=repo,
                    )

    def test_rejects_out_of_repo_traversal_in_resolved_build_and_bind_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            temporary_root = Path(temp)
            repo = temporary_root / "checkout"
            repo.mkdir()
            self.populate_compose_root(repo)

            model = compose_model(repo)
            model["services"]["backend"]["build"]["context"] = "../checkout/backend"
            with self.assertRaises(guard.GuardError):
                guard.validate(
                    source_sha=SOURCE_SHA,
                    checkout_sha=SOURCE_SHA,
                    compose_source=self.compose_source,
                    compose_model=model,
                    compose_root=repo,
                )

            model = compose_model(repo)
            traversing_path = repo / "nginx" / ".." / "nginx" / "nginx.conf"
            model["services"]["nginx"]["volumes"][0]["source"] = str(traversing_path)
            with self.assertRaises(guard.GuardError):
                guard.validate(
                    source_sha=SOURCE_SHA,
                    checkout_sha=SOURCE_SHA,
                    compose_source=self.compose_source,
                    compose_model=model,
                    compose_root=repo,
                )

    def test_rejects_short_or_unmatched_source_revision(self):
        self.assert_rejected(source_sha=SOURCE_SHA[:12])
        self.assert_rejected(checkout_sha="b" * 40)

    def test_rejects_source_revision_that_is_not_pinned_in_both_images(self):
        model = compose_model()
        model["services"]["backend"]["build"]["args"]["VCS_REF"] = "main"
        self.assert_rejected(model=model)

    def test_rejects_extra_backend_or_migration_build_arguments(self):
        model = compose_model()
        model["services"]["backend"]["build"]["args"]["API_TOKEN"] = "injected"
        self.assert_rejected(model=model)

        model = compose_model()
        model["services"]["migrate"]["build"]["args"] = {"DATABASE_PASSWORD": "injected"}
        self.assert_rejected(model=model)

        model = compose_model()
        model["services"]["migrate"]["build"]["args"] = {"VCS_REF": SOURCE_SHA}
        self.assert_rejected(model=model)
        model = compose_model()
        model["services"]["frontend"]["build"]["args"]["VCS_REF"] = "latest"
        self.assert_rejected(model=model)

    def test_rejects_unsafe_buildkit_controls_in_every_staging_build(self):
        unsafe_options = {
            "ssh": ("default", ["default"], {"default": "/run/ssh-agent.sock"}),
            "network": ("host",),
            "entitlements": (["security.insecure"], ["network.host"]),
            "secrets": (["id=token,src=/run/secrets/token"],),
            "cache_from": (["example.invalid/cache:latest"],),
            "cache_to": (["type=registry,ref=example.invalid/cache:latest"],),
            "extra_contexts": ({"other": "../other"},),
            "additional_contexts": ({"other": "../other"},),
            "dockerfile_inline": ("FROM scratch",),
            "extra_hosts": (["host.docker.internal:host-gateway"],),
            "privileged": (True,),
            "ulimits": ({"nofile": {"soft": 1024, "hard": 2048}},),
        }
        for service in ("migrate", "backend", "frontend"):
            for key, values in unsafe_options.items():
                for value in values:
                    with self.subTest(service=service, key=key, value=value):
                        model = compose_model()
                        model["services"][service]["build"][key] = value
                        self.assert_rejected(model=model)

    def test_rejects_unsafe_source_build_entries_even_if_normalization_drops_them(self):
        source_entries = (
            "ssh: default",
            "ssh: [default]",
            "network: host",
            "entitlements: [security.insecure]",
            "secrets: [id=token,src=/run/secrets/token]",
            "cache_from: [example.invalid/cache:latest]",
            "cache_to: [type=registry,ref=example.invalid/cache:latest]",
            "extra_contexts: {other: ../other}",
            "additional_contexts: {other: ../other}",
            "dockerfile_inline: 'FROM scratch'",
        )
        for service in ("migrate", "backend", "frontend"):
            for entry in source_entries:
                with self.subTest(service=service, entry=entry):
                    # The model stays valid to represent Compose normalization that
                    # discards or fails to expose the unsafe source-only property.
                    self.assert_rejected(
                        source=self.source_with_build_entry(service, entry),
                        model=compose_model(),
                    )

    def test_rejects_unexpected_build_services_and_service_builds(self):
        for service in ("db", "nginx"):
            model = compose_model()
            model["services"][service]["build"] = {
                "context": "./backend",
                "target": "runtime",
                "ssh": "default",
            }
            self.assert_rejected(model=model)

        model = compose_model()
        model["services"]["worker"] = {
            "build": {"context": "./backend", "target": "runtime", "ssh": "default"}
        }
        self.assert_rejected(model=model)

        source = self.compose_source.replace(
            f"    image: {guard.POSTGRES_IMAGE}\n",
            f"    image: {guard.POSTGRES_IMAGE}\n"
            "    build:\n"
            "      context: ./backend\n"
            "      target: runtime\n",
            1,
        )
        self.assert_rejected(source=source, model=compose_model())

        source = self.compose_source.replace(
            "\nnetworks:\n",
            "\n  worker:\n"
            "    build:\n"
            "      context: ./backend\n"
            "      target: runtime\n"
            "\nnetworks:\n",
            1,
        )
        self.assert_rejected(source=source, model=compose_model())

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

    def test_network_mapping_tags_and_inline_values_are_rejected(self):
        for value in ("!override", "{}"):
            self.assert_rejected(source=self.compose_source.replace(
                "  ingress:\n", f"  ingress: {value}\n", 1))

    def test_only_nginx_may_attach_to_the_bridge_ingress_network(self):
        for service in ("db", "migrate", "backend", "frontend"):
            model = compose_model()
            model["services"][service]["networks"]["ingress"] = None
            with self.subTest(service=service):
                self.assert_rejected(model=model)

        source = self.compose_source.replace(
            "    networks: [isolated]\n", "    networks: [isolated, ingress]\n", 1
        )
        self.assert_rejected(source=source)

    def test_ingress_network_is_exact_private_bridge_without_external_or_options(self):
        model = compose_model()
        model["networks"]["unexpected"] = {"driver": "bridge", "name": "belib-isolated-i5_unexpected"}
        self.assert_rejected(model=model)

        for key, value in (
            ("driver", "host"),
            ("external", True),
            ("ipam", {"config": [{"subnet": "172.30.0.0/16"}]}),
            ("driver_opts", {"com.docker.network.bridge.name": "docker0"}),
        ):
            model = compose_model()
            model["networks"]["ingress"][key] = value
            with self.subTest(key=key):
                self.assert_rejected(model=model)

        for suffix in (
            "    external: true\n",
            "    driver_opts: {}\n",
        ):
            self.assert_rejected(source=self.compose_source.replace(
                "  ingress:\n    driver: bridge\n", "  ingress:\n    driver: bridge\n" + suffix, 1
            ))

    def test_rejects_hostile_named_volume_driver_options_in_raw_and_resolved_compose(self):
        for key, value in (
            ("driver", "local"),
            ("driver_opts", {"type": "none", "o": "bind", "device": "/etc"}),
        ):
            with self.subTest(key=key):
                model = compose_model()
                model["volumes"]["staging-book-files"][key] = value
                self.assert_rejected(model=model)

        source = self.compose_source.replace(
            "  staging-book-files: {}\n",
            "  staging-book-files:\n"
            "    driver_opts:\n"
            "      type: none\n"
            "      o: bind\n"
            "      device: /etc\n",
            1,
        )
        self.assert_rejected(source=source)

    def test_rejects_extra_service_environment_and_network_settings(self):
        model = compose_model()
        model["services"]["migrate"]["init"] = True
        self.assert_rejected(model=model)

        model = compose_model()
        model["services"]["backend"]["environment"]["INJECTED_FLAG"] = "true"
        self.assert_rejected(model=model)

        model = compose_model()
        model["networks"]["isolated"]["driver_opts"] = {"com.docker.network.bridge.name": "docker0"}
        self.assert_rejected(model=model)

        self.assert_rejected(
            source=self.source_with_service_entry("migrate", "init: true")
        )
        source = self.compose_source.replace(
            '      APP_PORT: "3000"\n',
            '      INJECTED_FLAG: "true"\n      APP_PORT: "3000"\n',
            1,
        )
        self.assert_rejected(source=source)
        source = self.compose_source.replace(
            "  isolated:\n    internal: true\n",
            "  isolated:\n    internal: true\n    driver_opts: {}\n",
            1,
        )
        self.assert_rejected(source=source)

    def test_rejects_migration_command_profile_and_backend_entrypoint_changes(self):
        model = compose_model()
        model["services"]["migrate"]["command"] = ["/bin/sh", "-c", "id"]
        self.assert_rejected(model=model)

        model = compose_model()
        model["services"]["migrate"]["profiles"] = ["migration", "default"]
        self.assert_rejected(model=model)

        model = compose_model()
        model["services"]["backend"]["entrypoint"] = ["/bin/sh"]
        self.assert_rejected(model=model)

        source = self.compose_source.replace(
            '    command: ["npx", "prisma", "migrate", "deploy"]\n',
            '    command: ["/bin/sh", "-c", "id"]\n',
            1,
        )
        self.assert_rejected(source=source)
        source = self.compose_source.replace(
            '    profiles: ["migration"]\n',
            '    profiles: ["migration", "default"]\n',
            1,
        )
        self.assert_rejected(source=source)
        self.assert_rejected(
            source=self.source_with_service_entry("backend", 'entrypoint: ["/bin/sh"]')
        )

    def test_rejects_bind_source_and_nested_mount_options(self):
        model = compose_model()
        model["services"]["nginx"]["volumes"][0]["source"] = "/etc/passwd"
        self.assert_rejected(model=model)

        model = compose_model()
        model["services"]["nginx"]["volumes"][0]["bind"] = {
            "create_host_path": True
        }
        self.assert_rejected(model=model)

        source = self.compose_source.replace(
            "        source: ./nginx/nginx.conf\n",
            "        source: /etc/passwd\n",
            1,
        )
        self.assert_rejected(source=source)

        model = compose_model()
        model["services"]["nginx"]["volumes"][0]["bind"] = {
            "create_host_path": False,
            "propagation": "rprivate",
        }
        self.assert_rejected(model=model)

    def test_rejects_shadowed_bind_mapping_even_when_normalized_options_are_empty(self):
        source = self.compose_source.replace(
            "          create_host_path: false\n",
            "          create_host_path: false\n        bind: {}\n",
            1,
        )
        self.assertNotEqual(source, self.compose_source)
        # Compose can normalize the duplicate mapping to the allowlisted empty
        # option object, so the raw source contract must reject the shadowing.
        self.assert_rejected(source=source, model=compose_model())

    def test_rejects_unsafe_named_volume_mount_options(self):
        model = compose_model()
        model["services"]["db"]["volumes"][0]["volume"] = {"nocopy": True}
        self.assert_rejected(model=model)

        model = compose_model()
        model["services"]["backend"]["volumes"][0]["volume"] = {
            "nocopy": False,
            "subpath": "outside",
        }
        self.assert_rejected(model=model)

        model = compose_model()
        model["services"]["backend"]["volumes"][0]["read_only"] = True
        self.assert_rejected(model=model)

    def test_mount_option_diagnostics_exclude_values_and_paths(self):
        model = compose_model()
        model["services"]["nginx"]["volumes"][0]["source"] = "/private/source-path"
        model["services"]["nginx"]["volumes"][0]["bind"] = {
            "create_host_path": True,
            "review_secret_fixture": "do-not-print-this",
        }
        with self.assertRaises(guard.GuardError) as raised:
            guard.validate(
                source_sha=SOURCE_SHA,
                checkout_sha=SOURCE_SHA,
                compose_source=self.compose_source,
                compose_model=model,
            )

        message = str(raised.exception)
        self.assertIn("mount keys=", message)
        self.assertIn("create_host_path", message)
        self.assertIn("True", message)
        self.assertNotIn("/private/source-path", message)
        self.assertNotIn("do-not-print-this", message)

    def test_pr_staging_ci_is_read_only_and_checks_out_exact_head(self):
        workflow = (ROOT / ".github/workflows/staging-ci.yml").read_text(encoding="utf-8")
        self.assertRegex(workflow, r"(?m)^\s*pull_request:\s*$")
        self.assertNotIn("pull_request_target", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("scripts/staging/source-tree-guard.py", workflow)
        self.assertIn('--source-sha "$STAGING_SOURCE_SHA"', workflow)
        self.assertEqual(
            re.findall(r"(?m)^\s*image:\s*(\S+)\s*$", workflow),
            [guard.CI_POSTGRES_IMAGE],
        )
        checkouts = re.findall(
            r"(?m)^\s*ref:\s*\$\{\{\s*github\.event\.pull_request\.head\.sha\s*\}\}\s*$",
            workflow,
        )
        self.assertEqual(len(checkouts), 4)
        self.assertIn("config --format json", workflow)
        self.assertNotRegex(workflow, r"(?m)^\s*docker compose .*?\bup\b")
        self.assertNotIn("BELIB_STAGE_DB_PASSWORD=", workflow)
        checkout_ref = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
        setup_node_ref = "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020"
        action_refs = re.findall(r"(?m)^\s*uses:\s*([^\s#]+)", workflow)
        self.assertEqual(action_refs.count(checkout_ref), 4)
        self.assertEqual(action_refs.count(setup_node_ref), 3)
        self.assertTrue(all(re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", ref) for ref in action_refs))
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
