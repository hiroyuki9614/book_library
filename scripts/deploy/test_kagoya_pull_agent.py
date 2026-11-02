import fcntl
import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from kagoya_compose import (
    ComposeIdentityError,
    bootstrap_preflight,
    compose_contract_sha256,
    validate_candidate_protected_contract,
    validate_compose_services,
    validate_live_compose_identity,
    validate_frontend_service,
    validate_private_owner_file,
)
from kagoya_pull_agent import (
    DeployError,
    _DeferredPreSwitchGitTransport,
    _previous_state_record,
    _load_state,
    _http_get,
    _is_retryable_git_transport_failure,
    _main_sha,
    _run,
    _retained_image_ref,
    _up_frontend,
    _verify_target_image,
    deploy_frontend,
    dispatch_once,
    rollback_frontend,
)
from release_gate import GateError, TransientGitHubAPIError


SOURCE_SHA = "a" * 40
OLD_ID = "sha256:" + "b" * 64
NEW_ID = "sha256:" + "a" * 64
DEPLOYED_ID = "sha256:" + "c" * 64


class FakeDocker:
    def __init__(self, project_dir, production_override):
        self.current_id = OLD_ID
        self.current_ref = "belib-frontend:legacy"
        self.images = {self.current_ref: {"Id": OLD_ID, "Config": {"Labels": {}}}}
        self.current_project_dir = project_dir
        self.current_base_file = project_dir / "compose.yaml"
        self.production_override = production_override
        self.tags = {}
        self.commands = []
        self.up_targets = []
        self.frontend_compose_overrides = {}
        self.frontend_build_arg_overrides = {}
        self.replace_frontend_build_args = False
        self.other_service_overrides = {}
        self.migrate_profiles = ["migration"]

    def container(self):
        return [
            {
                "Name": "/belib-production-frontend-1",
                "Image": self.current_id,
                "Config": {
                    "Image": self.current_ref,
                    "Labels": {
                        "com.docker.compose.project": "belib-production",
                        "com.docker.compose.service": "frontend",
                        "com.docker.compose.project.working_dir": str(self.current_project_dir),
                        "com.docker.compose.project.config_files": (
                            f"{self.current_base_file},{self.production_override}"
                        ),
                        "org.opencontainers.image.revision": (
                            SOURCE_SHA if self.current_ref == f"belib-frontend:{SOURCE_SHA}" else ""
                        ),
                    },
                },
                "State": {"Running": True},
                "NetworkSettings": {"Networks": {"belib-production_default": {}}},
            }
        ]

    def run(self, argv, *, env=None, check=True):
        argv = list(argv)
        self.commands.append((argv, dict(env or {})))
        stdout, returncode = "", 0
        if argv[:2] == ["docker", "inspect"]:
            stdout = json.dumps(self.container())
        elif argv[:3] == ["docker", "image", "inspect"]:
            ref = argv[3]
            image = self.images.get(ref)
            if image is None:
                returncode = 1
            else:
                stdout = json.dumps([image])
        elif argv[:4] == ["docker", "image", "tag", OLD_ID] or argv[:4] == [
            "docker",
            "image",
            "tag",
            NEW_ID,
        ]:
            self.tags[argv[4]] = argv[3]
            self.images[argv[4]] = self.images.get(argv[3], {"Id": argv[3], "Config": {"Labels": {}}})
        elif argv[:3] == ["docker", "compose", "--project-name"]:
            if "config" in argv and "--format" in argv:
                checkout = Path(argv[argv.index("--project-directory") + 1])
                source_sha = (env or {})["VCS_REF"]
                frontend = {
                    "build": {
                        "args": {
                            "VCS_REF": source_sha,
                            "VITE_API_BASE_URL": (env or {}).get("VITE_API_BASE_URL"),
                            "VITE_PUBLIC_DEMO_EMAIL": "",
                            "VITE_PUBLIC_DEMO_PASSWORD": "",
                        },
                        "context": str((checkout / "frontend").resolve()),
                        "dockerfile": "Dockerfile",
                        "target": "runtime",
                    },
                    "command": None,
                    "entrypoint": None,
                    "expose": ["8080"],
                    "image": f"belib-frontend:{source_sha}",
                    "logging": {
                        "driver": "json-file",
                        "options": {"max-file": "3", "max-size": "10m"},
                    },
                    "networks": {"default": None},
                    "restart": "unless-stopped",
                }
                frontend["build"]["args"].update(self.frontend_build_arg_overrides)
                if self.replace_frontend_build_args:
                    frontend["build"]["args"] = dict(self.frontend_build_arg_overrides)
                frontend.update(self.frontend_compose_overrides)
                services = {
                    "db": {},
                    "migrate": {"profiles": self.migrate_profiles},
                    "backend": {},
                    "frontend": frontend,
                    "nginx": {},
                }
                services.update(self.other_service_overrides)
                stdout = json.dumps(
                    {
                        "name": "belib-production",
                        "services": services,
                        "networks": {"default": {"name": "belib-production_default"}},
                        "volumes": {"postgres-data": {}, "book-files": {}},
                    }
                )
            elif "build" in argv:
                self.images[f"belib-frontend:{SOURCE_SHA}"] = {
                    "Id": NEW_ID,
                    "Config": {"Labels": {"org.opencontainers.image.revision": SOURCE_SHA}},
                }
            elif "up" in argv:
                ref = (env or {}).get("BELIB_FRONTEND_IMAGE", f"belib-frontend:{SOURCE_SHA}")
                compose_files = [
                    Path(argv[index + 1])
                    for index, value in enumerate(argv[:-1])
                    if value == "--file"
                ]
                self.current_project_dir = Path(argv[argv.index("--project-directory") + 1])
                self.current_base_file = compose_files[0]
                self.production_override = compose_files[1]
                image = self.images[ref]
                self.current_ref = ref
                self.current_id = image["Id"]
                self.up_targets.append(argv[-1])
        else:
            raise AssertionError(f"Unexpected command: {argv}")
        return subprocess.CompletedProcess(argv, returncode, stdout, "")


class ProductionComposeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project_dir = Path(self.temporary.name) / "production"
        deployment_dir = self.project_dir / ".deployment"
        deployment_dir.mkdir(parents=True)
        self.base = self.project_dir / "compose.yaml"
        self.base.write_text("services: {}\n", encoding="utf-8")
        self.override = deployment_dir / "compose.production.yaml"
        self.override.write_text("services: {}\n", encoding="utf-8")
        self.override.chmod(0o600)
        self.labels = {
            "com.docker.compose.project": "belib-production",
            "com.docker.compose.project.working_dir": str(self.project_dir),
            "com.docker.compose.project.config_files": f"{self.base},{self.override}",
        }

    def test_accepts_only_the_canonical_production_base_and_private_override(self):
        result = validate_live_compose_identity(
            project=self.labels["com.docker.compose.project"],
            working_dir=self.labels["com.docker.compose.project.working_dir"],
            config_files=self.labels["com.docker.compose.project.config_files"],
            agent_uid=os.getuid(),
        )
        self.assertEqual(result["compose_file"], str(self.base.resolve()))
        self.assertEqual(result["compose_override_file"], str(self.override.resolve()))

    def test_rejects_unrelated_override_path(self):
        unrelated = self.project_dir / ".deployment" / "other.yaml"
        unrelated.write_text("services: {}\n", encoding="utf-8")
        unrelated.chmod(0o600)
        with self.assertRaisesRegex(ComposeIdentityError, "approved base and override paths"):
            validate_live_compose_identity(
                project="belib-production",
                working_dir=str(self.project_dir),
                config_files=f"{self.base},{unrelated}",
                agent_uid=os.getuid(),
            )

    def test_rejects_an_injected_third_compose_file(self):
        with self.assertRaisesRegex(ComposeIdentityError, "exactly two approved files"):
            validate_live_compose_identity(
                project="belib-production",
                working_dir=str(self.project_dir),
                config_files=f"{self.base},{self.override},{self.base}",
                agent_uid=os.getuid(),
            )

    def test_rejects_override_owned_by_another_uid(self):
        with self.assertRaisesRegex(ComposeIdentityError, "owned by the production agent"):
            validate_private_owner_file(
                self.override, os.getuid() + 1, description="production Compose override"
            )

    def test_rejects_group_writable_override_directory(self):
        (self.project_dir / ".deployment").chmod(0o775)
        with self.assertRaisesRegex(ComposeIdentityError, "agent-owned and not group/world writable"):
            validate_live_compose_identity(
                project="belib-production",
                working_dir=str(self.project_dir),
                config_files=f"{self.base},{self.override}",
                agent_uid=os.getuid(),
            )

    def test_rejects_group_or_world_accessible_override(self):
        self.override.chmod(0o644)
        with self.assertRaisesRegex(ComposeIdentityError, "owner-readable and private"):
            validate_live_compose_identity(
                project="belib-production",
                working_dir=str(self.project_dir),
                config_files=f"{self.base},{self.override}",
                agent_uid=os.getuid(),
            )

    def test_rejects_symlinked_override_escape(self):
        outside = Path(self.temporary.name) / "outside.yaml"
        outside.write_text("services: {}\n", encoding="utf-8")
        outside.chmod(0o600)
        self.override.unlink()
        self.override.symlink_to(outside)
        with self.assertRaisesRegex(ComposeIdentityError, "regular file, not a symlink"):
            validate_live_compose_identity(
                project="belib-production",
                working_dir=str(self.project_dir),
                config_files=f"{self.base},{self.override}",
                agent_uid=os.getuid(),
            )

    def test_resolved_contract_keeps_four_active_services_and_profiled_migration(self):
        resolved = self.compose_fixture()
        validate_compose_services(resolved, require_migrate=True)
        digest = compose_contract_sha256(resolved, self.project_dir)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_all_profiles_requires_migrate_but_default_config_may_omit_it(self):
        default_config = self.compose_fixture()
        del default_config["services"]["migrate"]
        validate_compose_services(default_config)
        with self.assertRaisesRegex(ComposeIdentityError, "migration service is missing"):
            validate_compose_services(default_config, require_migrate=True)

    def test_frontend_sha_image_and_build_context_are_excluded_from_protected_projection(self):
        base = self.compose_fixture()
        effective = copy.deepcopy(base)
        effective["services"]["frontend"]["image"] = "belib-frontend:reviewed-sha"
        effective["services"]["frontend"]["build"] = {
            "context": str(self.project_dir / "frontend-candidate"),
            "args": {"VCS_REF": SOURCE_SHA},
        }
        self.assertEqual(
            compose_contract_sha256(base, self.project_dir),
            compose_contract_sha256(effective, self.project_dir),
        )

    def test_candidate_must_match_frozen_effective_contract_for_all_protected_parts(self):
        approved = self.compose_fixture(self.project_dir)
        approved["services"]["backend"]["image"] = "belib-backend:approved-host-image"
        approved["services"]["backend"]["environment"] = {"R2_BUCKET_NAME": "approved-bucket"}
        approved["services"]["nginx"]["image"] = "nginx:approved-host-image"
        approved["volumes"]["book-files"] = {
            "name": "belib-production_book-files",
            "external": True,
        }
        approved_digest = compose_contract_sha256(approved, self.project_dir)
        base_digest = compose_contract_sha256(self.compose_fixture(self.project_dir), self.project_dir)
        self.assertNotEqual(base_digest, approved_digest)
        validate_candidate_protected_contract(approved, self.project_dir, approved_digest)

        mutations = (
            ("backend image", lambda candidate: candidate["services"]["backend"].update(image="belib-backend:changed")),
            ("R2 setting", lambda candidate: candidate["services"]["backend"]["environment"].update(R2_BUCKET_NAME="changed-bucket")),
            ("book-file mount", lambda candidate: candidate["services"]["backend"]["volumes"][0].update(source="other-volume")),
            ("database", lambda candidate: candidate["services"]["db"]["environment"].update(POSTGRES_PASSWORD="changed")),
            ("nginx image", lambda candidate: candidate["services"]["nginx"].update(image="nginx:changed")),
            ("nginx mount", lambda candidate: candidate["services"]["nginx"]["volumes"][0].update(source="/other/nginx.conf")),
            ("network", lambda candidate: candidate["networks"]["default"].update(name="changed_default")),
            ("book-files volume", lambda candidate: candidate["volumes"]["book-files"].update(name="changed-book-files")),
        )
        for part, mutate in mutations:
            with self.subTest(part=part):
                candidate = copy.deepcopy(approved)
                mutate(candidate)
                with self.assertRaisesRegex(ComposeIdentityError, "candidate changes protected services"):
                    validate_candidate_protected_contract(candidate, self.project_dir, approved_digest)

    def test_migrate_runtime_fields_are_not_pinned_beyond_the_inactive_profile_boundary(self):
        approved = self.compose_fixture(self.project_dir)
        approved_digest = compose_contract_sha256(approved, self.project_dir)
        candidate = copy.deepcopy(approved)
        candidate["services"]["migrate"].update(
            image="belib-migrate:candidate",
            command=["synthetic", "candidate"],
            profiles=["candidate-migration"],
        )
        validate_candidate_protected_contract(candidate, self.project_dir, approved_digest)

        candidate["services"]["migrate"]["profiles"] = []
        with self.assertRaisesRegex(ComposeIdentityError, "migration service is unexpectedly active"):
            validate_candidate_protected_contract(candidate, self.project_dir, approved_digest)

    def test_bootstrap_freezes_the_approved_effective_two_file_projection(self):
        frontend_dir = self.project_dir / "frontend"
        frontend_dir.mkdir()
        (frontend_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        env_file = Path(self.temporary.name) / "production.env"
        env_file.write_text("SYNTHETIC_ONLY=value\n", encoding="utf-8")
        env_file.chmod(0o600)

        api_base_url = "https://api.example.test"
        base = self.compose_fixture(self.project_dir)
        base["services"]["frontend"]["build"] = {"args": {"VITE_API_BASE_URL": api_base_url}}
        effective = copy.deepcopy(base)
        effective["services"]["backend"]["image"] = "belib-backend:approved-host-image"
        effective["services"]["nginx"]["image"] = "nginx:approved-host-image"
        effective["services"]["frontend"] = {
            "build": {
                # Match the sanitized live legacy projection: its protected
                # override pins the current image and only supplies the API URL.
                "args": {"VITE_API_BASE_URL": api_base_url},
                "context": str(frontend_dir),
                "dockerfile": "Dockerfile",
                "target": "runtime",
            },
            "command": None,
            "entrypoint": None,
            "expose": ["8080"],
            "image": "belib-frontend:70d1cc7128bda0d2d5a925a8ca93bf6920693d42",
            "logging": {
                "driver": "json-file",
                "options": {"max-file": "3", "max-size": "10m"},
            },
            "networks": {"default": None},
            "restart": "unless-stopped",
        }
        effective["volumes"]["book-files"] = {
            "name": "belib-production_book-files",
            "external": True,
        }
        self.base.write_text("services: {}\n", encoding="utf-8")
        labels = {
            "com.docker.compose.project": "belib-production",
            "com.docker.compose.service": "frontend",
            "com.docker.compose.project.working_dir": str(self.project_dir),
            "com.docker.compose.project.config_files": f"{self.base},{self.override}",
        }
        inspected = [{
            "Name": "/belib-production-frontend-1",
            "Config": {
                "Labels": labels,
                "Image": "belib-frontend:70d1cc7128bda0d2d5a925a8ca93bf6920693d42",
            },
            "State": {"Running": True},
            "Image": OLD_ID,
            "NetworkSettings": {"Networks": {"belib-production_default": {}}},
        }]
        compose_commands = []
        profile_options = []

        def resolved_config(argv, _env):
            if argv[:2] == ["docker", "inspect"]:
                return inspected
            files = [Path(argv[index + 1]) for index, value in enumerate(argv[:-1]) if value == "--file"]
            compose_commands.append(files)
            if "--profile" in argv:
                profile_options.append(argv[argv.index("--profile") + 1])
            return base if len(files) == 1 else effective

        with patch("kagoya_compose._run_json", side_effect=resolved_config):
            metadata = bootstrap_preflight("belib-production-frontend-1", str(env_file), agent_uid=os.getuid())

        self.assertEqual(
            compose_commands,
            [
                [self.base.resolve()],
                [self.base.resolve(), self.override.resolve()],
                [self.base.resolve(), self.override.resolve()],
            ],
        )
        self.assertEqual(profile_options, ["*"])
        self.assertNotEqual(compose_contract_sha256(base, self.project_dir), metadata["compose_contract_sha256"])
        self.assertEqual(
            metadata["compose_contract_sha256"],
            compose_contract_sha256(effective, self.project_dir),
        )
        self.assertEqual(metadata["frontend_bootstrap_mode"], "legacy-override-update-required")

        # A plausible but different tag is not accepted as the current live
        # legacy override, and the tag is never treated as source provenance.
        effective["services"]["frontend"]["image"] = "belib-frontend:tampered"
        with patch("kagoya_compose._run_json", side_effect=resolved_config):
            with self.assertRaisesRegex(ComposeIdentityError, "not tagged with the exact source SHA"):
                bootstrap_preflight("belib-production-frontend-1", str(env_file), agent_uid=os.getuid())

        effective["services"]["frontend"]["image"] = inspected[0]["Config"]["Image"]
        effective["services"]["frontend"]["build"]["args"]["UNAPPROVED_BUILD_ARG"] = "synthetic"
        with patch("kagoya_compose._run_json", side_effect=resolved_config):
            with self.assertRaisesRegex(ComposeIdentityError, "legacy frontend override build arguments"):
                bootstrap_preflight("belib-production-frontend-1", str(env_file), agent_uid=os.getuid())

    def test_rejects_added_service_and_unexpected_active_migration(self):
        resolved = self.compose_fixture()
        resolved["services"]["unexpected"] = {"privileged": True}
        with self.assertRaisesRegex(ComposeIdentityError, "service set differs"):
            validate_compose_services(resolved)
        resolved = self.compose_fixture()
        resolved["services"]["migrate"]["profiles"] = []
        with self.assertRaisesRegex(ComposeIdentityError, "migration service is unexpectedly active"):
            validate_compose_services(resolved)

    @staticmethod
    def compose_fixture(project_dir=None):
        project_dir = project_dir or Path(__file__).resolve().parents[2]
        return {
            "name": "belib-production",
            "services": {
                "db": {
                    "image": "postgres:16.14-bookworm",
                    "volumes": [
                        {"type": "volume", "source": "postgres-data", "target": "/var/lib/postgresql/data"}
                    ],
                    "environment": {"POSTGRES_PASSWORD": "synthetic"},
                },
                "migrate": {
                    "profiles": ["migration"],
                    "build": {"context": str(project_dir / "backend"), "args": {"VCS_REF": SOURCE_SHA}},
                },
                "backend": {
                    "build": {"context": str(project_dir / "backend"), "args": {"VCS_REF": SOURCE_SHA}},
                    "volumes": [
                        {"type": "volume", "source": "book-files", "target": "/var/lib/belib/books"}
                    ],
                },
                "frontend": {"image": "belib-frontend:synthetic"},
                "nginx": {
                    "volumes": [
                        {
                            "type": "bind",
                            "source": str(project_dir / "nginx" / "nginx.conf"),
                            "target": "/etc/nginx/nginx.conf",
                            "read_only": True,
                        }
                    ]
                },
            },
            "networks": {"default": {"name": "belib-production_default"}},
            "volumes": {"postgres-data": {}, "book-files": {}},
        }


class HttpSmokeTests(unittest.TestCase):
    def test_http_smoke_request_does_not_follow_redirect_to_another_listener(self):
        class CanaryHandler(BaseHTTPRequestHandler):
            requests = []

            def do_GET(self):
                type(self).requests.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, _format, *args):
                pass

        canary = HTTPServer(("127.0.0.1", 0), CanaryHandler)
        canary_thread = threading.Thread(target=canary.serve_forever, daemon=True)
        canary_thread.start()

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header(
                    "Location",
                    f"http://127.0.0.1:{canary.server_port}/internal-canary",
                )
                self.end_headers()

            def log_message(self, _format, *args):
                pass

        redirector = HTTPServer(("127.0.0.1", 0), RedirectHandler)
        redirect_thread = threading.Thread(target=redirector.serve_forever, daemon=True)
        redirect_thread.start()
        try:
            status, _, _ = _http_get(f"http://127.0.0.1:{redirector.server_port}/redirect")
        finally:
            redirector.shutdown()
            canary.shutdown()
            redirector.server_close()
            canary.server_close()
            redirect_thread.join(timeout=2)
            canary_thread.join(timeout=2)

        self.assertEqual(status, 302)
        self.assertEqual(CanaryHandler.requests, [])


class PullAgentDeploymentTests(unittest.TestCase):

    def test_git_fetch_classifier_rejects_ambiguous_eof_and_integrity_failures(self):
        self.assertFalse(_is_retryable_git_transport_failure("fatal: early EOF"))
        self.assertFalse(
            _is_retryable_git_transport_failure(
                "fatal: early EOF\nfatal: fetch-pack: invalid index-pack output"
            )
        )
        self.assertFalse(
            _is_retryable_git_transport_failure(
                "fatal: unexpected disconnect while reading sideband packet\n"
                "fatal: fetch-pack: invalid index-pack output"
            )
        )

    def test_git_fetch_classifier_accepts_explicit_sideband_disconnect(self):
        self.assertTrue(
            _is_retryable_git_transport_failure(
                "fatal: fetch-pack: unexpected disconnect while reading sideband packet"
            )
        )

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.release_root = Path(self.temporary.name) / "releases"
        self.checkout = self.release_root / SOURCE_SHA
        self.checkout.mkdir(parents=True)
        (self.checkout / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        frontend = self.checkout / "frontend"
        frontend.mkdir()
        (frontend / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        self.environment_file = Path(self.temporary.name) / "production.env"
        self.environment_file.write_text("synthetic=only\n", encoding="utf-8")
        self.environment_file.chmod(0o600)
        self.project_dir = Path(self.temporary.name) / "production"
        (self.project_dir / ".deployment").mkdir(parents=True)
        (self.project_dir / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        self.production_override = self.project_dir / ".deployment" / "compose.production.yaml"
        self.production_override.write_text("services: {}\n", encoding="utf-8")
        self.production_override.chmod(0o600)
        (self.project_dir / "frontend").mkdir()
        (self.project_dir / "frontend" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        active_config = {
            "name": "belib-production",
            "services": {
                "db": {},
                "migrate": {"profiles": ["migration"]},
                "backend": {},
                "frontend": {},
                "nginx": {},
            },
            "networks": {"default": {"name": "belib-production_default"}},
            "volumes": {"postgres-data": {}, "book-files": {}},
        }
        self.config = {
            "repository": "hiroyuki9614/book_library",
            "repo_url": "https://github.com/hiroyuki9614/book_library.git",
            "expected_project": "belib-production",
            "production_container": "belib-production-frontend-1",
            "expected_networks": ["belib-production_default"],
            "production_env_file": str(self.environment_file),
            "production_compose_override": str(self.production_override),
            "expected_compose_contract_sha256": compose_contract_sha256(active_config, self.checkout),
            "frontend_bootstrap_mode": "exact-sha",
            "original_project_dir": str(self.project_dir),
            "original_compose_file": str(self.project_dir / "compose.yaml"),
            "release_root": str(self.release_root),
            "agent_uid": os.getuid(),
            "expected_api_base_url": "https://api.example.test",
            "base_url": "http://127.0.0.1:8080",
        }
        self.docker = FakeDocker(self.project_dir, self.production_override)

    def run_deploy(self, smoke):
        with patch("kagoya_pull_agent._checkout", return_value=self.checkout), patch(
            "kagoya_pull_agent._run", side_effect=self.docker.run
        ):
            return deploy_frontend(
                self.config,
                SOURCE_SHA,
                smoke=smoke,
                main_sha_reader=lambda _config: SOURCE_SHA,
            )

    def test_legacy_bootstrap_blocks_release_before_build_or_switch(self):
        self.config["frontend_bootstrap_mode"] = "legacy-override-update-required"
        self.docker.frontend_compose_overrides["image"] = self.docker.current_ref
        self.docker.frontend_build_arg_overrides = {
            "VITE_API_BASE_URL": self.config["expected_api_base_url"]
        }
        self.docker.replace_frontend_build_args = True

        with self.assertRaisesRegex(DeployError, "bootstrap-only.*exact-SHA frontend contract"):
            self.run_deploy(lambda _base: None)

        self.assertFalse(any("build" in command for command, _env in self.docker.commands))
        self.assertEqual(self.docker.up_targets, [])

    def test_replayed_success_preserves_pre_switch_rollback_after_state_save_failure(self):
        state_path = Path(self.temporary.name) / "dispatcher-state.json"
        self.config.update(
            {
                "state_file": str(state_path),
                "lock_file": str(Path(self.temporary.name) / "dispatcher.lock"),
            }
        )
        old_sha = "d" * 40
        initial_state = {
            "last_request": [0, 0],
            "last_result": "success",
            "current_sha": old_sha,
            "current": {"image_id": OLD_ID, "image_ref": f"belib-frontend:{old_sha}"},
            "previous": None,
        }
        state_path.write_text(json.dumps(initial_state), encoding="utf-8")
        request = [((12, 1), SOURCE_SHA, "refs/tags/belib-deploy-request-12-1")]

        with (
            patch("kagoya_pull_agent._requests", return_value=request),
            patch("kagoya_pull_agent._main_sha", return_value=SOURCE_SHA),
            patch("kagoya_pull_agent._validate_network"),
            patch("kagoya_pull_agent._checkout", return_value=self.checkout),
            patch("kagoya_pull_agent._run", side_effect=self.docker.run),
            # Keep this state-persistence regression independent from a real
            # localhost listener on CI or on the production host.
            patch("kagoya_pull_agent.smoke_test"),
            patch("kagoya_pull_agent._save_state", side_effect=OSError("synthetic disk failure")),
        ):
            with self.assertRaisesRegex(OSError, "synthetic disk failure"):
                dispatch_once(self.config)

        self.assertEqual(self.docker.current_id, NEW_ID)
        self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), initial_state)

        def fail_if_replay_enters_error_recovery(*_args, **_kwargs):
            # Replaying an already-switched frontend must not take the failure
            # synchronization path; retain the real cause in test-only output.
            self.fail(f"unexpected replay error: {sys.exc_info()[1]!r}")

        with (
            patch("kagoya_pull_agent._requests", return_value=request),
            patch("kagoya_pull_agent._main_sha", return_value=SOURCE_SHA),
            patch("kagoya_pull_agent._validate_network"),
            patch("kagoya_pull_agent._checkout", return_value=self.checkout),
            patch("kagoya_pull_agent._run", side_effect=self.docker.run),
            patch("kagoya_pull_agent.smoke_test"),
            patch("kagoya_pull_agent._synchronize_state_after_dispatch_failure", side_effect=fail_if_replay_enters_error_recovery),
        ):
            self.assertEqual(dispatch_once(self.config), 0)

        saved = json.loads(state_path.read_text(encoding="utf-8"))
        retained_old = _retained_image_ref(OLD_ID)
        self.assertEqual(saved["last_request"], [12, 1])
        self.assertEqual(saved["current"]["image_id"], NEW_ID)
        self.assertEqual(
            saved["previous"],
            {"image_id": OLD_ID, "image_ref": retained_old, "source_sha": old_sha},
        )
        self.assertEqual(self.docker.images[retained_old]["Id"], OLD_ID)

    def test_transient_network_failure_preserves_request_for_next_dispatch(self):
        state_path = Path(self.temporary.name) / "dispatcher-state.json"
        self.config.update(
            {
                "state_file": str(state_path),
                "lock_file": str(Path(self.temporary.name) / "dispatcher.lock"),
            }
        )
        initial_state = {
            "last_request": [0, 0],
            "last_result": "success",
            "current_sha": "d" * 40,
            "current": {"image_id": OLD_ID, "image_ref": f"belib-frontend:{'d' * 40}"},
            "previous": None,
        }
        state_path.write_text(json.dumps(initial_state), encoding="utf-8")
        request = [((12, 1), SOURCE_SHA, "refs/tags/belib-deploy-request-12-1")]

        with (
            patch("kagoya_pull_agent._requests", return_value=request),
            patch("kagoya_pull_agent._main_sha", return_value=SOURCE_SHA),
            patch(
                "kagoya_pull_agent.validate_release",
                side_effect=[TransientGitHubAPIError("synthetic transient failure"), None],
            ),
            patch("kagoya_pull_agent._checkout", return_value=self.checkout),
            patch("kagoya_pull_agent._run", side_effect=self.docker.run),
            patch("kagoya_pull_agent.smoke_test"),
        ):
            with self.assertRaisesRegex(DeployError, "Transient GitHub release check failure"):
                dispatch_once(self.config)
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), initial_state)
            self.assertEqual(self.docker.up_targets, [])

            self.assertEqual(dispatch_once(self.config), 0)

        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["last_request"], [12, 1])
        self.assertEqual(saved["last_result"], "success")
        self.assertEqual(self.docker.up_targets, ["frontend"])
        self.assertEqual(self.docker.up_targets.count("frontend"), 1)

    def test_main_ref_transport_failure_classifies_transient_vs_permanent(self):
        command = ["git", "ls-remote", "--refs", self.config["repo_url"], "refs/heads/main"]
        with patch(
            "kagoya_pull_agent._run",
            return_value=subprocess.CompletedProcess(
                command, 128, "",
                "fatal: unable to access remote: Could not resolve host: github.com\\n",
            ),
        ) as git:
            with self.assertRaisesRegex(
                _DeferredPreSwitchGitTransport, "Transient Git main ref transport failure"
            ):
                _main_sha(self.config)
            git.assert_called_once_with(command, check=False)

        with patch(
            "kagoya_pull_agent._run",
            return_value=subprocess.CompletedProcess(command, 128, "", "fatal: Authentication failed\\n"),
        ):
            with self.assertRaisesRegex(DeployError, "Command failed: git"):
                _main_sha(self.config)

        with patch(
            "kagoya_pull_agent._run",
            return_value=subprocess.CompletedProcess(command, 0, f"{SOURCE_SHA}\trefs/heads/main\n", ""),
        ):
            self.assertEqual(_main_sha(self.config), SOURCE_SHA)

    def test_main_ref_recheck_transient_failure_retains_request_until_recovery(self):
        state_path = Path(self.temporary.name) / "main-ref-recheck-state.json"
        self.config.update({
            "state_file": str(state_path),
            "lock_file": str(Path(self.temporary.name) / "main-ref-recheck.lock"),
        })
        initial_state = {
            "last_request": [0, 0],
            "last_result": "success",
            "current_sha": "d" * 40,
            "current": {"image_id": OLD_ID, "image_ref": f"belib-frontend:{'d' * 40}"},
            "previous": None,
        }
        state_path.write_text(json.dumps(initial_state), encoding="utf-8")
        request = [((12, 1), SOURCE_SHA, "refs/tags/belib-deploy-request-12-1")]
        ref_reads = [
            SOURCE_SHA,
            _DeferredPreSwitchGitTransport("Transient Git main ref transport failure"),
            SOURCE_SHA, SOURCE_SHA, SOURCE_SHA, SOURCE_SHA,
        ]
        with (
            patch("kagoya_pull_agent._requests", return_value=request),
            patch("kagoya_pull_agent._main_sha", side_effect=ref_reads),
            patch("kagoya_pull_agent._validate_network"),
            patch("kagoya_pull_agent._checkout", return_value=self.checkout),
            patch("kagoya_pull_agent._run", side_effect=self.docker.run),
            patch("kagoya_pull_agent.smoke_test"),
        ):
            with self.assertRaisesRegex(DeployError, "Transient Git main ref transport failure"):
                dispatch_once(self.config)
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), initial_state)
            self.assertEqual(self.docker.up_targets, [])
            self.assertEqual(dispatch_once(self.config), 0)

        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["last_request"], [12, 1])
        self.assertEqual(saved["last_result"], "success")
        self.assertEqual(self.docker.up_targets, ["frontend"])

    def test_main_ref_transport_failure_after_switch_consumes_request(self):
        state_path = Path(self.temporary.name) / "main-ref-post-switch-state.json"
        self.config.update({
            "state_file": str(state_path),
            "lock_file": str(Path(self.temporary.name) / "main-ref-post-switch.lock"),
        })
        request = [((12, 1), SOURCE_SHA, "refs/tags/belib-deploy-request-12-1")]
        with (
            patch("kagoya_pull_agent._requests", return_value=request),
            patch(
                "kagoya_pull_agent._main_sha",
                side_effect=[
                    SOURCE_SHA, SOURCE_SHA, SOURCE_SHA,
                    _DeferredPreSwitchGitTransport("synthetic transport outage after switch"),
                ],
            ),
            patch("kagoya_pull_agent._validate_network"),
            patch("kagoya_pull_agent._checkout", return_value=self.checkout),
            patch("kagoya_pull_agent._run", side_effect=self.docker.run),
            patch("kagoya_pull_agent.smoke_test"),
        ):
            with self.assertRaisesRegex(DeployError, "after the frontend switch"):
                dispatch_once(self.config)
            self.assertEqual(dispatch_once(self.config), 0)

        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["last_request"], [12, 1])
        self.assertEqual(saved["last_result"], "failed")
        self.assertEqual(self.docker.up_targets, ["frontend", "frontend"])

    def test_transient_git_fetch_failure_preserves_request_until_fetch_recovers(self):
        state_path = Path(self.temporary.name) / "git-fetch-state.json"
        self.config.update(
            {
                "state_file": str(state_path),
                "lock_file": str(Path(self.temporary.name) / "git-fetch.lock"),
            }
        )
        initial_state = {
            "last_request": [0, 0],
            "last_result": "success",
            "current_sha": "d" * 40,
            "current": {"image_id": OLD_ID, "image_ref": f"belib-frontend:{'d' * 40}"},
            "previous": None,
        }
        state_path.write_text(json.dumps(initial_state), encoding="utf-8")
        request = [((12, 1), SOURCE_SHA, "refs/tags/belib-deploy-request-12-1")]
        (self.checkout / ".git").mkdir()
        fetch_attempts = 0

        def run_git_then_docker(argv, *, env=None, check=True):
            nonlocal fetch_attempts
            if argv and argv[0] == "git":
                operation = argv[3]
                if operation == "status":
                    return subprocess.CompletedProcess(argv, 0, "", "")
                if operation == "fetch":
                    fetch_attempts += 1
                    if fetch_attempts == 1:
                        return subprocess.CompletedProcess(
                            argv,
                            128,
                            "",
                            "fatal: unable to access 'https://github.com/hiroyuki9614/book_library.git/': Could not resolve host: github.com\n",
                        )
                    return subprocess.CompletedProcess(argv, 0, "", "")
                if operation == "checkout":
                    return subprocess.CompletedProcess(argv, 0, "", "")
                if operation == "rev-parse":
                    return subprocess.CompletedProcess(argv, 0, f"{SOURCE_SHA}\n", "")
                raise AssertionError(f"Unexpected Git command: {argv}")
            return self.docker.run(argv, env=env, check=check)

        with (
            patch("kagoya_pull_agent._requests", return_value=request),
            patch("kagoya_pull_agent._main_sha", return_value=SOURCE_SHA),
            patch("kagoya_pull_agent._validate_network"),
            patch("kagoya_pull_agent._run", side_effect=run_git_then_docker),
            patch("kagoya_pull_agent.smoke_test"),
        ):
            with self.assertRaisesRegex(DeployError, "Transient Git fetch transport failure"):
                dispatch_once(self.config)
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), initial_state)
            self.assertEqual(self.docker.up_targets, [])

            self.assertEqual(dispatch_once(self.config), 0)

        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(fetch_attempts, 2)
        self.assertEqual(saved["last_request"], [12, 1])
        self.assertEqual(saved["last_result"], "success")
        self.assertEqual(self.docker.up_targets, ["frontend"])

    def test_permanent_git_fetch_failure_consumes_request(self):
        state_path = Path(self.temporary.name) / "git-fetch-denied-state.json"
        self.config.update(
            {
                "state_file": str(state_path),
                "lock_file": str(Path(self.temporary.name) / "git-fetch-denied.lock"),
            }
        )
        initial_state = {
            "last_request": [0, 0],
            "last_result": "success",
            "current_sha": "d" * 40,
            "current": {"image_id": OLD_ID, "image_ref": f"belib-frontend:{'d' * 40}"},
            "previous": None,
        }
        state_path.write_text(json.dumps(initial_state), encoding="utf-8")
        request = [((12, 1), SOURCE_SHA, "refs/tags/belib-deploy-request-12-1")]
        (self.checkout / ".git").mkdir()

        def run_permanent_fetch_then_docker(argv, *, env=None, check=True):
            if argv and argv[0] == "git":
                operation = argv[3]
                if operation == "status":
                    return subprocess.CompletedProcess(argv, 0, "", "")
                if operation == "fetch":
                    return subprocess.CompletedProcess(argv, 128, "", "fatal: bad object\n")
                raise AssertionError(f"Unexpected Git command: {argv}")
            return self.docker.run(argv, env=env, check=check)

        with (
            patch("kagoya_pull_agent._requests", return_value=request),
            patch("kagoya_pull_agent._main_sha", return_value=SOURCE_SHA),
            patch("kagoya_pull_agent._validate_network"),
            patch("kagoya_pull_agent._run", side_effect=run_permanent_fetch_then_docker),
        ):
            with self.assertRaisesRegex(DeployError, "Command failed: git"):
                dispatch_once(self.config)
            self.assertEqual(dispatch_once(self.config), 0)

        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["last_request"], [12, 1])
        self.assertEqual(saved["last_result"], "failed")
        self.assertEqual(self.docker.up_targets, [])

    def test_permanent_gate_denial_consumes_request_and_never_switches(self):
        state_path = Path(self.temporary.name) / "denied-state.json"
        self.config.update({
            "state_file": str(state_path),
            "lock_file": str(Path(self.temporary.name) / "denied.lock"),
        })
        request = [((12, 1), SOURCE_SHA, "refs/tags/belib-deploy-request-12-1")]
        with (
            patch("kagoya_pull_agent._requests", return_value=request),
            patch("kagoya_pull_agent._main_sha", return_value=SOURCE_SHA),
            patch("kagoya_pull_agent.validate_release", side_effect=GateError("Approval missing")),
            patch("kagoya_pull_agent._run", side_effect=self.docker.run),
        ):
            with self.assertRaisesRegex(DeployError, "Approval missing"):
                dispatch_once(self.config)
            self.assertEqual(dispatch_once(self.config), 0)
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["last_request"], [12, 1])
        self.assertEqual(saved["last_result"], "failed")
        self.assertEqual(self.docker.up_targets, [])

    def test_transient_error_after_gate_is_not_eligible_for_retry(self):
        state_path = Path(self.temporary.name) / "post-gate-state.json"
        self.config.update({
            "state_file": str(state_path),
            "lock_file": str(Path(self.temporary.name) / "post-gate.lock"),
        })
        request = [((12, 1), SOURCE_SHA, "refs/tags/belib-deploy-request-12-1")]
        with (
            patch("kagoya_pull_agent._requests", return_value=request),
            patch("kagoya_pull_agent._main_sha", return_value=SOURCE_SHA),
            patch("kagoya_pull_agent._validate_network"),
            patch("kagoya_pull_agent.deploy_frontend", side_effect=TransientGitHubAPIError("synthetic post-gate failure")),
            patch("kagoya_pull_agent._run", side_effect=self.docker.run),
        ):
            with self.assertRaisesRegex(DeployError, "The deployment request failed closed"):
                dispatch_once(self.config)
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["last_request"], [12, 1])
        self.assertEqual(saved["last_result"], "failed")
        self.assertEqual(self.docker.up_targets, [])

    def test_transport_exception_after_frontend_switch_is_consumed_and_not_retried(self):
        state_path = Path(self.temporary.name) / "late-transport-state.json"
        self.config.update(
            {
                "state_file": str(state_path),
                "lock_file": str(Path(self.temporary.name) / "late-transport.lock"),
            }
        )
        initial_state = {
            "last_request": [0, 0],
            "last_result": "success",
            "current_sha": "d" * 40,
            "current": {"image_id": OLD_ID, "image_ref": f"belib-frontend:{'d' * 40}"},
            "previous": None,
        }
        state_path.write_text(json.dumps(initial_state), encoding="utf-8")
        request = [((12, 1), SOURCE_SHA, "refs/tags/belib-deploy-request-12-1")]
        original_up_frontend = _up_frontend

        def switch_then_raise(config, checkout, source_sha, *, image_override=None):
            original_up_frontend(config, checkout, source_sha, image_override=image_override)
            if image_override is None:
                raise _DeferredPreSwitchGitTransport("synthetic late transport failure")

        with (
            patch("kagoya_pull_agent._requests", return_value=request),
            patch("kagoya_pull_agent._main_sha", return_value=SOURCE_SHA),
            patch("kagoya_pull_agent._validate_network"),
            patch("kagoya_pull_agent._checkout", return_value=self.checkout),
            patch("kagoya_pull_agent._run", side_effect=self.docker.run),
            patch("kagoya_pull_agent._up_frontend", side_effect=switch_then_raise),
            patch("kagoya_pull_agent.smoke_test"),
        ):
            with self.assertRaisesRegex(DeployError, "after the frontend switch"):
                dispatch_once(self.config)
            self.assertEqual(dispatch_once(self.config), 0)

        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["last_request"], [12, 1])
        self.assertEqual(saved["last_result"], "failed")
        self.assertEqual(self.docker.up_targets, ["frontend", "frontend"])

    def test_failed_automatic_rollback_synchronizes_actual_frontend_state(self):
        state_path = Path(self.temporary.name) / "dispatcher-state.json"
        self.config.update(
            {
                "state_file": str(state_path),
                "lock_file": str(Path(self.temporary.name) / "dispatcher.lock"),
            }
        )
        old_sha = "d" * 40
        initial_state = {
            "last_request": [0, 0],
            "last_result": "success",
            "current_sha": old_sha,
            "current": {"image_id": OLD_ID, "image_ref": f"belib-frontend:{old_sha}"},
            "previous": None,
        }
        state_path.write_text(json.dumps(initial_state), encoding="utf-8")
        request = [((12, 1), SOURCE_SHA, "refs/tags/belib-deploy-request-12-1")]
        up_calls = 0

        def switch_candidate_then_fail_rollback(config, checkout, source_sha, *, image_override=None):
            nonlocal up_calls
            up_calls += 1
            if image_override is not None:
                raise DeployError("synthetic rollback switch failure")
            return _up_frontend(config, checkout, source_sha, image_override=image_override)

        with (
            patch("kagoya_pull_agent._requests", return_value=request),
            patch("kagoya_pull_agent._main_sha", return_value=SOURCE_SHA),
            patch("kagoya_pull_agent._validate_network"),
            patch("kagoya_pull_agent._checkout", return_value=self.checkout),
            patch("kagoya_pull_agent._run", side_effect=self.docker.run),
            patch("kagoya_pull_agent._up_frontend", side_effect=switch_candidate_then_fail_rollback),
            patch("kagoya_pull_agent.smoke_test", side_effect=DeployError("synthetic candidate smoke failure")),
        ):
            with self.assertRaisesRegex(DeployError, "saved frontend rollback also failed"):
                dispatch_once(self.config)

        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(up_calls, 2)
        self.assertEqual(self.docker.current_id, NEW_ID)
        self.assertEqual(saved["last_request"], [12, 1])
        self.assertEqual(saved["last_result"], "failed")
        self.assertEqual(saved["current"], {"image_id": NEW_ID, "image_ref": f"belib-frontend:{SOURCE_SHA}"})
        self.assertEqual(saved["current_sha"], SOURCE_SHA)
        self.assertEqual(saved["previous"]["image_id"], OLD_ID)
        self.assertEqual(saved["previous"]["image_ref"], _retained_image_ref(OLD_ID))

    def test_first_deployment_replay_uses_bootstrap_rollback_image(self):
        retained_bootstrap = _retained_image_ref(OLD_ID)
        self.docker.images[retained_bootstrap] = {
            "Id": OLD_ID,
            "Config": {"Labels": {}},
        }
        self.config.update(
            {
                "bootstrap_image_id": OLD_ID,
                "bootstrap_rollback_ref": retained_bootstrap,
            }
        )
        state = {
            "last_request": [0, 0],
            "current_sha": None,
            "current": None,
            "previous": None,
        }
        result = {
            "no_op": True,
            "image_id": NEW_ID,
            "previous_image_id": NEW_ID,
            "previous_image_ref": _retained_image_ref(NEW_ID),
        }
        with patch("kagoya_pull_agent._run", side_effect=self.docker.run):
            record = _previous_state_record(self.config, state, result)
        self.assertEqual(
            record,
            {"image_id": OLD_ID, "image_ref": retained_bootstrap, "source_sha": None},
        )

    def test_noop_replay_preserves_existing_previous_rollback_pointer(self):
        retained_previous = _retained_image_ref(OLD_ID)
        self.docker.images[retained_previous] = {
            "Id": OLD_ID,
            "Config": {"Labels": {}},
        }
        state = {
            "last_request": [12, 1],
            "current_sha": SOURCE_SHA,
            "current": {"image_id": NEW_ID, "image_ref": f"belib-frontend:{SOURCE_SHA}"},
            "previous": {
                "image_id": OLD_ID,
                "image_ref": retained_previous,
                "source_sha": "d" * 40,
            },
        }
        result = {
            "no_op": True,
            "image_id": NEW_ID,
            "previous_image_id": NEW_ID,
            "previous_image_ref": _retained_image_ref(NEW_ID),
        }
        with patch("kagoya_pull_agent._run", side_effect=self.docker.run):
            record = _previous_state_record(self.config, state, result)

        self.assertEqual(
            record,
            {
                "image_id": OLD_ID,
                "image_ref": retained_previous,
                "source_sha": "d" * 40,
            },
        )

    def test_noop_replay_without_saved_rollback_does_not_record_current_as_previous(self):
        state = {
            "last_request": [12, 1],
            "current_sha": SOURCE_SHA,
            "current": {"image_id": NEW_ID, "image_ref": f"belib-frontend:{SOURCE_SHA}"},
            "previous": None,
        }
        result = {
            "no_op": True,
            "image_id": NEW_ID,
            "previous_image_id": NEW_ID,
            "previous_image_ref": _retained_image_ref(NEW_ID),
        }

        record = _previous_state_record(self.config, state, result)

        self.assertIsNone(record)

    def test_deploy_targets_only_frontend_and_reads_back_exact_source_image(self):
        result = self.run_deploy(lambda _base_url: None)
        self.assertFalse(result["no_op"])
        self.assertEqual(result["source_sha"], SOURCE_SHA)
        self.assertEqual(result["image_id"], NEW_ID)
        self.assertEqual(self.docker.current_id, NEW_ID)
        compose_commands = [argv for argv, _ in self.docker.commands if argv[:2] == ["docker", "compose"]]
        self.assertTrue(
            any(
                "--profile" in argv and argv[argv.index("--profile") + 1] == "*"
                for argv in compose_commands
            )
        )
        self.assertTrue(any("build" in argv and argv[-1] == "frontend" for argv in compose_commands))
        self.assertTrue(any("up" in argv and argv[-1] == "frontend" for argv in compose_commands))
        for argv in compose_commands:
            files = [Path(argv[index + 1]) for index, value in enumerate(argv[:-1]) if value == "--file"]
            self.assertEqual(files, [self.checkout / "compose.yaml", self.production_override])
            self.assertEqual(Path(argv[argv.index("--project-directory") + 1]), self.checkout)
        build_env = next(env for argv, env in self.docker.commands if "build" in argv)
        config_env = next(env for argv, env in self.docker.commands if "config" in argv)
        up_env = next(env for argv, env in self.docker.commands if "up" in argv)
        for compose_env in (config_env, build_env, up_env):
            self.assertEqual(compose_env["VITE_API_BASE_URL"], self.config["expected_api_base_url"])
        self.assertEqual(build_env["VITE_PUBLIC_DEMO_EMAIL"], "")
        self.assertEqual(build_env["VITE_PUBLIC_DEMO_PASSWORD"], "")
        self.assertTrue(all(env["COMPOSE_PROFILES"] == "" for _argv, env in self.docker.commands if env))
        self.assertFalse(any("down" in argv or "prune" in argv for argv in self.docker.commands))
        self.assertTrue(all(target == "frontend" for target in self.docker.up_targets))

    def test_verifies_the_exact_source_tag_and_immutable_revision_label(self):
        expected_ref = f"belib-frontend:{SOURCE_SHA}"
        self.docker.images[expected_ref] = {
            "Id": NEW_ID,
            "Config": {"Labels": {"org.opencontainers.image.revision": SOURCE_SHA}},
        }
        with patch("kagoya_pull_agent._run", side_effect=self.docker.run):
            image = _verify_target_image(self.config, SOURCE_SHA)
        self.assertEqual(image["Id"], NEW_ID)
        self.assertIn(["docker", "image", "inspect", expected_ref], [argv for argv, _env in self.docker.commands])

        self.docker.images[expected_ref]["Config"]["Labels"]["org.opencontainers.image.revision"] = "b" * 40
        with patch("kagoya_pull_agent._run", side_effect=self.docker.run):
            with self.assertRaisesRegex(DeployError, "revision label does not match"):
                _verify_target_image(self.config, SOURCE_SHA)

    def test_rejects_altered_candidate_api_url_before_build_or_switch(self):
        self.docker.frontend_build_arg_overrides["VITE_API_BASE_URL"] = "https://altered.example.test"
        with self.assertRaisesRegex(DeployError, "frontend build arguments differ from the operator-approved values"):
            self.run_deploy(lambda _base_url: None)
        self.assertEqual(self.docker.current_id, OLD_ID)
        compose_commands = [argv for argv, _ in self.docker.commands if argv[:2] == ["docker", "compose"]]
        self.assertFalse(any("build" in argv or "up" in argv for argv in compose_commands))

    def test_pinned_api_url_overrides_ambient_host_environment(self):
        expected = self.config["expected_api_base_url"]
        with patch.dict(os.environ, {"VITE_API_BASE_URL": "https://ambient.example.test"}):
            with patch(
                "kagoya_pull_agent.subprocess.run",
                return_value=subprocess.CompletedProcess(["docker"], 0, "", ""),
            ) as run:
                _run(["docker", "compose", "config"], env={"VITE_API_BASE_URL": expected})
        self.assertEqual(run.call_args.kwargs["env"]["VITE_API_BASE_URL"], expected)

    def test_failed_smoke_restores_the_exact_prior_frontend_image(self):
        smoke_calls = 0

        def fail_candidate_then_pass_rollback(_base_url):
            nonlocal smoke_calls
            smoke_calls += 1
            if smoke_calls == 1:
                raise DeployError("synthetic isolated smoke failure")

        with self.assertRaisesRegex(DeployError, "synthetic isolated smoke failure"):
            self.run_deploy(fail_candidate_then_pass_rollback)
        self.assertEqual(smoke_calls, 2)
        self.assertEqual(self.docker.current_id, OLD_ID)
        self.assertTrue(self.docker.current_ref.startswith("belib-frontend:rollback-"))
        self.assertEqual(self.docker.up_targets, ["frontend", "frontend"])
        self.assertFalse(any("up" in argv and argv[-1] in {"db", "backend", "nginx"} for argv, _ in self.docker.commands))

    def test_rejects_host_mounts_in_candidate_frontend_compose_before_build_or_switch(self):
        self.docker.frontend_compose_overrides["volumes"] = [
            {"type": "bind", "source": "/run/user/1000/docker.sock", "target": "/run/docker.sock"}
        ]
        with self.assertRaisesRegex(DeployError, "unsupported runtime capabilities"):
            self.run_deploy(lambda _base_url: None)
        self.assertEqual(self.docker.current_id, OLD_ID)
        compose_commands = [argv for argv, _ in self.docker.commands if argv[:2] == ["docker", "compose"]]
        self.assertFalse(any("build" in argv or "up" in argv for argv in compose_commands))

    def test_rejects_frontend_privilege_escalation(self):
        self.docker.frontend_compose_overrides["privileged"] = True
        with self.assertRaisesRegex(DeployError, "unsupported runtime capabilities"):
            self.run_deploy(lambda _base_url: None)
        self.assertEqual(self.docker.current_id, OLD_ID)
        self.assertFalse(any("build" in argv or "up" in argv for argv, _env in self.docker.commands))

    def test_rejects_unapproved_service_and_database_contract_changes(self):
        self.docker.other_service_overrides["helper"] = {"privileged": True}
        with self.assertRaisesRegex(DeployError, "service set differs"):
            self.run_deploy(lambda _base_url: None)
        self.assertEqual(self.docker.current_id, OLD_ID)
        self.assertFalse(any("build" in argv or "up" in argv for argv, _env in self.docker.commands))

        self.docker = FakeDocker(self.project_dir, self.production_override)
        self.docker.other_service_overrides["db"] = {"privileged": True}
        with self.assertRaisesRegex(DeployError, "candidate changes protected services"):
            self.run_deploy(lambda _base_url: None)
        self.assertEqual(self.docker.current_id, OLD_ID)
        self.assertFalse(any("build" in argv or "up" in argv for argv, _env in self.docker.commands))

    def test_rejects_unexpected_active_migration_service(self):
        self.docker.migrate_profiles = []
        with self.assertRaisesRegex(DeployError, "migration service is unexpectedly active"):
            self.run_deploy(lambda _base_url: None)
        self.assertEqual(self.docker.current_id, OLD_ID)
        self.assertFalse(any("build" in argv or "up" in argv for argv, _env in self.docker.commands))

    def test_rejects_candidate_frontend_symlink_to_external_context_before_build_or_switch(self):
        frontend = self.checkout / "frontend"
        (frontend / "Dockerfile").unlink()
        frontend.rmdir()
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (outside / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        frontend.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(DeployError, "real directory inside the isolated checkout"):
            self.run_deploy(lambda _base_url: None)
        self.assertEqual(self.docker.current_id, OLD_ID)
        compose_commands = [argv for argv, _ in self.docker.commands if argv[:2] == ["docker", "compose"]]
        self.assertFalse(any("build" in argv or "up" in argv for argv in compose_commands))

    def test_rejects_candidate_dockerfile_symlink_to_external_file_before_build_or_switch(self):
        dockerfile = self.checkout / "frontend" / "Dockerfile"
        dockerfile.unlink()
        outside = Path(self.temporary.name) / "outside.Dockerfile"
        outside.write_text("FROM scratch\n", encoding="utf-8")
        dockerfile.symlink_to(outside)
        with self.assertRaisesRegex(DeployError, "Dockerfile must be a regular file"):
            self.run_deploy(lambda _base_url: None)
        self.assertEqual(self.docker.current_id, OLD_ID)
        compose_commands = [argv for argv, _ in self.docker.commands if argv[:2] == ["docker", "compose"]]
        self.assertFalse(any("build" in argv or "up" in argv for argv in compose_commands))

    def test_stale_sha_after_build_is_rejected_before_switch(self):
        with patch("kagoya_pull_agent._checkout", return_value=self.checkout), patch(
            "kagoya_pull_agent._run", side_effect=self.docker.run
        ):
            with self.assertRaisesRegex(DeployError, "Main advanced before the frontend switch"):
                deploy_frontend(
                    self.config,
                    SOURCE_SHA,
                    smoke=lambda _base_url: None,
                    main_sha_reader=lambda _config: "c" * 40,
                )
        self.assertEqual(self.docker.current_id, OLD_ID)
        self.assertFalse(self.docker.up_targets)

    def test_main_advance_during_switch_triggers_frontend_rollback(self):
        observed = iter([SOURCE_SHA, "c" * 40])
        with patch("kagoya_pull_agent._checkout", return_value=self.checkout), patch(
            "kagoya_pull_agent._run", side_effect=self.docker.run
        ):
            with self.assertRaisesRegex(DeployError, "Main advanced during deployment"):
                deploy_frontend(
                    self.config,
                    SOURCE_SHA,
                    smoke=lambda _base_url: None,
                    main_sha_reader=lambda _config: next(observed),
                )
        self.assertEqual(self.docker.current_id, OLD_ID)
        self.assertEqual(self.docker.up_targets, ["frontend", "frontend"])

    def test_manual_rollback_waits_for_active_dispatch_transaction(self):
        state_path = Path(self.temporary.name) / "dispatcher-state.json"
        lock_path = Path(self.temporary.name) / "dispatcher.lock"
        initial_state = {
            "last_request": [0, 0],
            "last_result": "success",
            "current_sha": SOURCE_SHA,
            "current": {"image_id": NEW_ID, "image_ref": f"belib-frontend:{SOURCE_SHA}"},
            "previous": {
                "image_id": OLD_ID,
                "image_ref": "belib-frontend:rollback-previous",
                "source_sha": "b" * 40,
            },
            "preserved_metadata": "synthetic",
        }
        state_path.write_text(json.dumps(initial_state), encoding="utf-8")
        self.config.update(
            {
                "agent_uid": os.getuid(),
                "state_file": str(state_path),
                "lock_file": str(lock_path),
            }
        )

        next_sha = "f" * 40
        next_ref = f"belib-frontend:{next_sha}"
        dispatch_previous_ref = "belib-frontend:rollback-current"
        dispatch_entered = threading.Event()
        allow_dispatch_to_finish = threading.Event()
        rollback_lock_attempted = threading.Event()
        rollback_lock_acquired = threading.Event()
        rollback_state_read = threading.Event()
        rollback_up_called = threading.Event()
        rollback_finished = threading.Event()
        dispatch_lock_identity = []
        rollback_lock_identity = []
        errors = []
        original_flock = fcntl.flock
        original_load_state = _load_state
        rollback_up_calls = []

        def tracked_flock(lock_stream, operation):
            thread_name = threading.current_thread().name
            identity = os.fstat(lock_stream.fileno())
            if thread_name == "automatic-dispatch":
                result = original_flock(lock_stream, operation)
                dispatch_lock_identity.append((identity.st_dev, identity.st_ino))
                return result
            if thread_name == "manual-rollback":
                rollback_lock_identity.append((identity.st_dev, identity.st_ino))
                rollback_lock_attempted.set()
                result = original_flock(lock_stream, operation)
                rollback_lock_acquired.set()
                return result
            return original_flock(lock_stream, operation)

        def tracked_load_state(config):
            if threading.current_thread().name == "manual-rollback":
                rollback_state_read.set()
            return original_load_state(config)

        def deploy_while_paused(_config, source_sha):
            self.assertEqual(source_sha, next_sha)
            dispatch_entered.set()
            if not allow_dispatch_to_finish.wait(timeout=5):
                raise AssertionError("synthetic dispatch was not released")
            return {
                "image_id": DEPLOYED_ID,
                "image_ref": next_ref,
                "previous_image_id": NEW_ID,
                "previous_image_ref": dispatch_previous_ref,
                "no_op": False,
            }

        def record_rollback_up(*args, **kwargs):
            rollback_up_calls.append((args, kwargs))
            rollback_up_called.set()

        def run_dispatch():
            try:
                dispatch_once(self.config)
            except BaseException as error:
                errors.append(error)

        def run_rollback():
            try:
                rollback_frontend(self.config)
            except BaseException as error:
                errors.append(error)
            finally:
                rollback_finished.set()

        dispatch_thread = threading.Thread(target=run_dispatch, name="automatic-dispatch")
        rollback_thread = threading.Thread(target=run_rollback, name="manual-rollback")
        with (
            patch("kagoya_pull_agent.fcntl.flock", side_effect=tracked_flock),
            patch(
                "kagoya_pull_agent._load_state", side_effect=tracked_load_state
            ),
            patch("kagoya_pull_agent._requests", return_value=[((1, 2), next_sha, "synthetic-ref")]),
            patch("kagoya_pull_agent._main_sha", return_value=next_sha),
            patch("kagoya_pull_agent._validate_network"),
            patch("kagoya_pull_agent.deploy_frontend", side_effect=deploy_while_paused),
            patch("kagoya_pull_agent._checkout", return_value=self.checkout),
            patch("kagoya_pull_agent._container", return_value={"Image": DEPLOYED_ID}),
            patch("kagoya_pull_agent._up_frontend", side_effect=record_rollback_up),
            patch("kagoya_pull_agent._verify_running_image"),
            patch("kagoya_pull_agent.smoke_test"),
        ):
            try:
                dispatch_thread.start()
                self.assertTrue(dispatch_entered.wait(timeout=3))
                self.assertEqual(len(dispatch_lock_identity), 1)

                rollback_thread.start()
                self.assertTrue(rollback_lock_attempted.wait(timeout=3))
                self.assertEqual(rollback_lock_identity, dispatch_lock_identity)
                self.assertFalse(rollback_lock_acquired.is_set())
                self.assertFalse(rollback_state_read.is_set())
                self.assertFalse(rollback_up_called.is_set())
                self.assertFalse(rollback_finished.is_set())
                self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), initial_state)
            finally:
                allow_dispatch_to_finish.set()
                if dispatch_thread.ident is not None:
                    dispatch_thread.join(timeout=5)
                if rollback_thread.ident is not None:
                    rollback_thread.join(timeout=5)

        self.assertFalse(dispatch_thread.is_alive())
        self.assertFalse(rollback_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(rollback_lock_acquired.is_set())
        self.assertTrue(rollback_state_read.is_set())
        self.assertTrue(rollback_up_called.is_set())
        self.assertTrue(rollback_finished.is_set())
        self.assertEqual(len(rollback_up_calls), 1)
        self.assertEqual(rollback_up_calls[0][1]["image_override"], dispatch_previous_ref)

        rolled_back_state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(rolled_back_state["last_request"], [1, 2])
        self.assertEqual(rolled_back_state["last_result"], "manual frontend rollback")
        self.assertEqual(rolled_back_state["current_sha"], SOURCE_SHA)
        self.assertEqual(
            rolled_back_state["current"],
            {"image_id": NEW_ID, "image_ref": dispatch_previous_ref, "source_sha": SOURCE_SHA},
        )
        self.assertEqual(
            rolled_back_state["previous"],
            {"image_id": DEPLOYED_ID, "image_ref": next_ref, "source_sha": next_sha},
        )
        self.assertEqual(rolled_back_state["preserved_metadata"], "synthetic")

    def test_manual_rollback_switches_only_frontend_and_preserves_state(self):
        state_path = Path(self.temporary.name) / "dispatcher-state.json"
        lock_path = Path(self.temporary.name) / "dispatcher.lock"
        current = {"image_id": NEW_ID, "image_ref": f"belib-frontend:{SOURCE_SHA}"}
        previous = {
            "image_id": OLD_ID,
            "image_ref": "belib-frontend:rollback-previous",
            "source_sha": "b" * 40,
        }
        initial_state = {
            "last_request": [8, 13],
            "last_result": "success",
            "current_sha": SOURCE_SHA,
            "current": current,
            "previous": previous,
            "preserved_metadata": "synthetic",
        }
        state_path.write_text(json.dumps(initial_state), encoding="utf-8")
        self.config.update(
            {
                "agent_uid": os.getuid(),
                "state_file": str(state_path),
                "lock_file": str(lock_path),
            }
        )

        commands = []
        def fake_run(argv, *, env=None, check=True):
            commands.append((list(argv), dict(env or {}), check))
            return subprocess.CompletedProcess(argv, 0, "", "")

        with patch("kagoya_pull_agent._checkout", return_value=self.checkout), patch(
            "kagoya_pull_agent._container",
            side_effect=[{"Image": NEW_ID}, {"Image": OLD_ID}],
        ), patch("kagoya_pull_agent._run", side_effect=fake_run), patch(
            "kagoya_pull_agent.smoke_test"
        ):
            self.assertEqual(rollback_frontend(self.config), 0)

        up_commands = [argv for argv, _env, _check in commands if "up" in argv]
        self.assertEqual(len(up_commands), 1)
        self.assertEqual(up_commands[0][-1], "frontend")
        self.assertIn("--no-deps", up_commands[0])
        self.assertIn("--no-build", up_commands[0])
        up_argv, up_env, _ = next(command for command in commands if "up" in command[0])
        files = [Path(up_argv[index + 1]) for index, value in enumerate(up_argv[:-1]) if value == "--file"]
        self.assertEqual(files, [self.checkout / "compose.yaml", self.production_override])
        self.assertEqual(up_env["BELIB_FRONTEND_IMAGE"], previous["image_ref"])

        rolled_back_state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(rolled_back_state["last_request"], initial_state["last_request"])
        self.assertEqual(rolled_back_state["last_result"], "manual frontend rollback")
        self.assertEqual(rolled_back_state["current_sha"], previous["source_sha"])
        self.assertEqual(rolled_back_state["current"], previous)
        self.assertEqual(rolled_back_state["previous"], {**current, "source_sha": SOURCE_SHA})
        self.assertEqual(rolled_back_state["preserved_metadata"], "synthetic")

    def test_manual_rollback_round_trip_preserves_each_image_source_sha(self):
        state_path = Path(self.temporary.name) / "dispatcher-state.json"
        old_sha = "b" * 40
        current_ref = f"belib-frontend:{SOURCE_SHA}"
        previous_ref = _retained_image_ref(OLD_ID)
        current = {"image_id": NEW_ID, "image_ref": current_ref}
        previous = {"image_id": OLD_ID, "image_ref": previous_ref, "source_sha": old_sha}
        state_path.write_text(
            json.dumps(
                {
                    "last_request": [8, 13],
                    "last_result": "success",
                    "current_sha": SOURCE_SHA,
                    "current": current,
                    "previous": previous,
                }
            ),
            encoding="utf-8",
        )
        self.config.update(
            {
                "agent_uid": os.getuid(),
                "state_file": str(state_path),
                "lock_file": str(Path(self.temporary.name) / "dispatcher.lock"),
            }
        )
        self.docker.current_id = NEW_ID
        self.docker.current_ref = current_ref
        self.docker.images[current_ref] = {
            "Id": NEW_ID,
            "Config": {"Labels": {"org.opencontainers.image.revision": SOURCE_SHA}},
        }
        self.docker.images[previous_ref] = {"Id": OLD_ID, "Config": {"Labels": {}}}

        with (
            patch("kagoya_pull_agent._checkout", return_value=self.checkout),
            patch("kagoya_pull_agent._run", side_effect=self.docker.run),
            patch("kagoya_pull_agent.smoke_test"),
        ):
            self.assertEqual(rollback_frontend(self.config), 0)
            first = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(first["current_sha"], old_sha)
            self.assertEqual(first["previous"]["source_sha"], SOURCE_SHA)
            self.assertEqual(rollback_frontend(self.config), 0)

        restored = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(restored["current_sha"], SOURCE_SHA)
        self.assertEqual(restored["current"]["image_id"], NEW_ID)
        self.assertEqual(restored["previous"]["image_id"], OLD_ID)
        self.assertEqual(restored["previous"]["source_sha"], old_sha)

    def test_manual_rollback_verification_failure_records_observed_runtime(self):
        state_path = Path(self.temporary.name) / "dispatcher-state.json"
        self.config.update(
            {
                "agent_uid": os.getuid(),
                "state_file": str(state_path),
                "lock_file": str(Path(self.temporary.name) / "dispatcher.lock"),
            }
        )
        current = {"image_id": NEW_ID, "image_ref": f"belib-frontend:{SOURCE_SHA}"}
        previous = {
            "image_id": OLD_ID,
            "image_ref": "belib-frontend:rollback-previous",
            "source_sha": "b" * 40,
        }
        initial_state = {
            "last_request": [8, 13],
            "last_result": "success",
            "current_sha": SOURCE_SHA,
            "current": current,
            "previous": previous,
        }
        state_path.write_text(json.dumps(initial_state), encoding="utf-8")
        with (
            patch("kagoya_pull_agent._checkout", return_value=self.checkout),
            patch("kagoya_pull_agent._container", side_effect=[{"Image": NEW_ID}, {"Image": OLD_ID}]),
            patch("kagoya_pull_agent._up_frontend"),
            patch("kagoya_pull_agent._verify_running_image", side_effect=DeployError("synthetic read-back mismatch")),
        ):
            with self.assertRaisesRegex(DeployError, "synthetic read-back mismatch"):
                rollback_frontend(self.config)

        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["current"], previous)
        self.assertEqual(saved["previous"], {**current, "source_sha": SOURCE_SHA})
        self.assertEqual(saved["current_sha"], previous["source_sha"])
        self.assertEqual(saved["last_result"], "manual frontend rollback verification failed")

    def test_manual_rollback_marks_runtime_unknown_when_readback_fails(self):
        state_path = Path(self.temporary.name) / "dispatcher-state.json"
        self.config.update(
            {
                "agent_uid": os.getuid(),
                "state_file": str(state_path),
                "lock_file": str(Path(self.temporary.name) / "dispatcher.lock"),
            }
        )
        initial_state = {
            "last_request": [8, 13],
            "last_result": "success",
            "current_sha": SOURCE_SHA,
            "current": {"image_id": NEW_ID, "image_ref": f"belib-frontend:{SOURCE_SHA}"},
            "previous": {
                "image_id": OLD_ID,
                "image_ref": "belib-frontend:rollback-previous",
                "source_sha": "b" * 40,
            },
        }
        state_path.write_text(json.dumps(initial_state), encoding="utf-8")
        with (
            patch("kagoya_pull_agent._checkout", return_value=self.checkout),
            patch(
                "kagoya_pull_agent._container",
                side_effect=[{"Image": NEW_ID}, DeployError("synthetic read-back unavailable")],
            ),
            patch("kagoya_pull_agent._up_frontend"),
            patch("kagoya_pull_agent._verify_running_image", side_effect=DeployError("synthetic verification failure")),
        ):
            with self.assertRaisesRegex(DeployError, "state was marked unknown"):
                rollback_frontend(self.config)

        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIsNone(saved["current"])
        self.assertIsNone(saved["current_sha"])
        self.assertEqual(saved["last_result"], "manual frontend rollback failed; live image is unknown")

    def test_dispatch_stops_when_manual_rollback_runtime_is_unknown(self):
        state_path = Path(self.temporary.name) / "dispatcher-state.json"
        self.config.update(
            {
                "agent_uid": os.getuid(),
                "state_file": str(state_path),
                "lock_file": str(Path(self.temporary.name) / "dispatcher.lock"),
            }
        )
        state_path.write_text(
            json.dumps(
                {
                    "last_request": [8, 13],
                    "last_result": "manual frontend rollback failed; live image is unknown",
                    "current_sha": None,
                    "current": None,
                    "previous": {"image_id": OLD_ID, "image_ref": "belib-frontend:rollback-previous"},
                }
            ),
            encoding="utf-8",
        )
        with patch("kagoya_pull_agent._requests") as requests:
            with self.assertRaisesRegex(DeployError, "live frontend state is unknown"):
                dispatch_once(self.config)
        requests.assert_not_called()

    def test_dispatch_marks_unknown_and_stops_when_failed_switch_cannot_be_read_back(self):
        state_path = Path(self.temporary.name) / "dispatcher-state.json"
        self.config.update(
            {
                "agent_uid": os.getuid(),
                "state_file": str(state_path),
                "lock_file": str(Path(self.temporary.name) / "dispatcher.lock"),
            }
        )
        state_path.write_text(
            json.dumps(
                {
                    "last_request": [0, 0],
                    "last_result": "success",
                    "current_sha": "d" * 40,
                    "current": {"image_id": OLD_ID, "image_ref": "belib-frontend:old"},
                    "previous": None,
                }
            ),
            encoding="utf-8",
        )
        request = [((12, 1), SOURCE_SHA, "refs/tags/belib-deploy-request-12-1")]
        with (
            patch("kagoya_pull_agent._requests", return_value=request),
            patch("kagoya_pull_agent._main_sha", return_value=SOURCE_SHA),
            patch("kagoya_pull_agent._validate_network"),
            patch("kagoya_pull_agent.deploy_frontend", side_effect=DeployError("synthetic switch failure")),
            patch("kagoya_pull_agent._container", side_effect=DeployError("synthetic read-back failure")),
        ):
            with self.assertRaisesRegex(DeployError, "synthetic switch failure"):
                dispatch_once(self.config)

        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIsNone(saved["current"])
        self.assertIsNone(saved["current_sha"])
        self.assertEqual(saved["last_result"], "deployment failed; live image is unknown")
        with patch("kagoya_pull_agent._requests") as requests:
            with self.assertRaisesRegex(DeployError, "live frontend state is unknown"):
                dispatch_once(self.config)
        requests.assert_not_called()


if __name__ == "__main__":
    unittest.main()
