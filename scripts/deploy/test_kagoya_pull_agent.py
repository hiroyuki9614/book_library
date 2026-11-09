import fcntl
import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
    _PublicDemoCandidate,
    _PublicDemoSource,
    _PublicDemoIdentity,
    _PublicDemoValidationSession,
    PUBLIC_DEMO_SHELF_PATH,
    PUBLIC_DEMO_VALIDATION_ORIGIN,
    PUBLIC_DEMO_VALIDATION_TIMEOUT,
    _load_public_demo_credentials,
    _frontend_compose_env,
    _previous_state_record,
    _load_state,
    _http_get,
    _is_retryable_git_transport_failure,
    _main_sha,
    _run,
    _retained_image_ref,
    _up_frontend,
    _validate_public_demo_api_base_url,
    _validate_public_demo_candidate,
    _verify_target_image,
    PUBLIC_DEMO_SIDECAR_MAX_BYTES,
    PUBLIC_DEMO_ABSENT_SIDECAR_MARKER,
    PUBLIC_DEMO_SIDECAR_MARKER_VERSION,
    deploy_frontend,
    dispatch_once,
    rollback_frontend,
)
from release_gate import GateError, TransientGitHubAPIError
from public_demo_accounts import PublicDemoError, empty_build_args


SOURCE_SHA = "a" * 40
NEW_SOURCE_SHA = "c" * 40
OLD_ID = "sha256:" + "b" * 64
NEW_ID = "sha256:" + "a" * 64
DEPLOYED_ID = "sha256:" + "c" * 64
SYNTHETIC_DEMO_EMAIL = "synthetic.demo@example.invalid"
SYNTHETIC_DEMO_PASSWORD = "synthetic-only-public-password"
SYNTHETIC_DEMO_SHELF_IDS = (4, 5)


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
                            self.current_ref.removeprefix("belib-frontend:")
                            if len(self.current_ref.removeprefix("belib-frontend:")) == 40
                            and all(
                                character in "0123456789abcdef"
                                for character in self.current_ref.removeprefix("belib-frontend:")
                            )
                            else ""
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
                            "VITE_PUBLIC_DEMO_EMAIL": (env or {}).get("VITE_PUBLIC_DEMO_EMAIL", ""),
                            "VITE_PUBLIC_DEMO_PASSWORD": (env or {}).get("VITE_PUBLIC_DEMO_PASSWORD", ""),
                            "PUBLIC_DEMO_USER_BUILD_SHA256": (env or {}).get("PUBLIC_DEMO_USER_BUILD_SHA256", ""),
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
                source_sha = (env or {})["VCS_REF"]
                image_id = NEW_ID if source_sha == SOURCE_SHA else DEPLOYED_ID
                self.images[f"belib-frontend:{source_sha}"] = {
                    "Id": image_id,
                    "Config": {"Labels": {"org.opencontainers.image.revision": source_sha, "com.belib.public-demo-user-build-sha256": (env or {}).get("PUBLIC_DEMO_USER_BUILD_SHA256", "")}},
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

    def test_protected_projection_ignores_only_backend_vcs_ref_and_preserves_other_build_args(self):
        approved = self.compose_fixture(self.project_dir)
        approved["services"]["backend"]["build"].pop("args")
        candidate = copy.deepcopy(approved)
        candidate["services"]["backend"]["build"]["args"] = {"VCS_REF": SOURCE_SHA}

        approved_digest = compose_contract_sha256(approved, self.project_dir)
        self.assertEqual(
            approved_digest,
            compose_contract_sha256(candidate, self.project_dir),
        )
        validate_candidate_protected_contract(candidate, self.project_dir, approved_digest)

        approved_with_other_arg = copy.deepcopy(approved)
        approved_with_other_arg["services"]["backend"]["build"]["args"] = {"KEEP": "same"}
        candidate_with_other_arg = copy.deepcopy(approved_with_other_arg)
        candidate_with_other_arg["services"]["backend"]["build"]["args"] = {
            "KEEP": "same",
            "VCS_REF": SOURCE_SHA,
        }
        other_arg_digest = compose_contract_sha256(approved_with_other_arg, self.project_dir)
        validate_candidate_protected_contract(
            candidate_with_other_arg,
            self.project_dir,
            other_arg_digest,
        )

        candidate_with_other_arg["services"]["backend"]["build"]["args"]["KEEP"] = "changed"
        with self.assertRaisesRegex(ComposeIdentityError, "candidate changes protected services"):
            validate_candidate_protected_contract(
                candidate_with_other_arg,
                self.project_dir,
                other_arg_digest,
            )

    def test_protected_projection_compares_canonical_bind_identity_across_checkouts(self):
        isolated_checkout = Path(self.temporary.name) / "isolated-release"
        isolated_checkout.mkdir()
        shared_mount = self.project_dir / ".deployment" / "nginx-default.conf"
        shared_mount.parent.mkdir(exist_ok=True)
        shared_mount.write_text("synthetic nginx config\n", encoding="utf-8")

        approved = self.compose_fixture(self.project_dir)
        candidate = self.compose_fixture(isolated_checkout)
        for resolved in (approved, candidate):
            resolved["services"]["nginx"]["volumes"][0]["source"] = str(shared_mount)

        approved_digest = compose_contract_sha256(approved, self.project_dir)
        self.assertEqual(
            approved_digest,
            compose_contract_sha256(candidate, isolated_checkout),
        )
        validate_candidate_protected_contract(candidate, isolated_checkout, approved_digest)

        relocated_mount = isolated_checkout / ".deployment" / "nginx-default.conf"
        relocated_mount.parent.mkdir()
        relocated_mount.write_text("different synthetic nginx config\n", encoding="utf-8")
        candidate["services"]["nginx"]["volumes"][0]["source"] = str(relocated_mount)
        with self.assertRaisesRegex(ComposeIdentityError, "candidate changes protected services"):
            validate_candidate_protected_contract(candidate, isolated_checkout, approved_digest)

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
            ("backend build arg", lambda candidate: candidate["services"]["backend"]["build"]["args"].update(UNAPPROVED="changed")),
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


class PublicDemoValidationTests(unittest.TestCase):
    candidate = _PublicDemoCandidate(
        SYNTHETIC_DEMO_EMAIL,
        SYNTHETIC_DEMO_PASSWORD,
    )

    class FakeSession:
        def __init__(self, responses, *, cookies=True):
            self.responses = list(responses)
            self.requests = []
            self.cookies = [object()] if cookies else []

        def request(self, method, path, *, body=None, retain_statuses=None):
            self.requests.append((method, path, body))
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                if isinstance(response, TimeoutError):
                    raise DeployError("The public demo authorization check failed") from None
                raise response
            return response

    @staticmethod
    def response(status, payload):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        return status, body

    def valid_responses(self, *, role="user", email=SYNTHETIC_DEMO_EMAIL, user_id=7):
        identity = {"id": user_id, "email": email}
        if role is not None:
            identity["role"] = role
        return [
            self.response(200, {}),
            self.response(200, identity),
            self.response(403, {"message": "Administrator role required", "code": "FORBIDDEN"}),
            self.response(200, {
                "total": 2,
                "page": 1,
                "limit": 100,
                "books": [{"id": 4}, {"id": 5}],
            }),
        ]

    def test_valid_candidate_is_bound_to_me_admin_denial_and_exact_public_shelf(self):
        fake = self.FakeSession(self.valid_responses())
        with patch("kagoya_pull_agent._PublicDemoValidationSession", return_value=fake):
            identity = _validate_public_demo_candidate(self.candidate)

        self.assertEqual(
            identity,
            _PublicDemoIdentity(7, SYNTHETIC_DEMO_EMAIL, "user", SYNTHETIC_DEMO_SHELF_IDS),
        )
        self.assertEqual([request[0:2] for request in fake.requests], [
            ("POST", "/api/auth/sign-in/email"),
            ("GET", "/api/v1/me"),
            ("GET", "/api/v1/admin/books"),
            ("GET", PUBLIC_DEMO_SHELF_PATH),
        ])
        self.assertEqual(
            json.loads(fake.requests[0][2].decode("utf-8")),
            {"email": SYNTHETIC_DEMO_EMAIL, "password": SYNTHETIC_DEMO_PASSWORD},
        )

    def test_identity_and_status_or_body_mismatches_fail_closed_without_secret(self):
        cases = [
            ("admin role", self.valid_responses(role="admin")),
            ("legacy general role", self.valid_responses(role="GENERAL")),
            ("missing role", self.valid_responses(role=None)),
            ("unknown role", self.valid_responses(role="unknown")),
            ("different email", self.valid_responses(email="other@example.invalid")),
            ("nonpositive id", self.valid_responses(user_id=0)),
            ("sign-in 401", [self.response(401, {"password": SYNTHETIC_DEMO_PASSWORD})]),
            (
                "shelf additional book",
                self.valid_responses()[:3] + [self.response(200, {
                    "total": 2, "page": 1, "limit": 100,
                    "books": [{"id": 4}, {"id": 5}, {"id": 6}],
                })],
            ),
            (
                "shelf one book",
                self.valid_responses()[:3] + [self.response(200, {
                    "total": 2, "page": 1, "limit": 100, "books": [{"id": 4}],
                })],
            ),
            (
                "shelf empty",
                self.valid_responses()[:3] + [self.response(200, {
                    "total": 2, "page": 1, "limit": 100, "books": [],
                })],
            ),
            (
                "shelf total mismatch",
                self.valid_responses()[:3] + [self.response(200, {
                    "total": 3, "page": 1, "limit": 100,
                    "books": [{"id": 4}, {"id": 5}],
                })],
            ),
            (
                "shelf duplicate",
                self.valid_responses()[:3] + [self.response(200, {
                    "total": 2, "page": 1, "limit": 100,
                    "books": [{"id": 4}, {"id": 4}],
                })],
            ),
            (
                "shelf over limit",
                self.valid_responses()[:3] + [self.response(200, {
                    "total": 2, "page": 1, "limit": 101,
                    "books": [{"id": 4}, {"id": 5}],
                })],
            ),
            (
                "shelf ambiguous page",
                self.valid_responses()[:3] + [self.response(200, {
                    "total": 2, "page": 2, "limit": 100,
                    "books": [{"id": 4}, {"id": 5}],
                })],
            ),
            ("shelf 401", self.valid_responses()[:3] + [self.response(401, {})]),
            ("shelf 403", self.valid_responses()[:3] + [self.response(403, {})]),
            ("shelf 500", self.valid_responses()[:3] + [self.response(500, {})]),
            ("shelf invalid JSON", self.valid_responses()[:3] + [self.response(200, b"{")]),
            (
                "shelf oversize",
                self.valid_responses()[:3]
                + [self.response(200, b"x" * (64 * 1024 + 1))],
            ),
            (
                "fake admin 403",
                [*self.valid_responses()[:2], self.response(403, {"code": "FORBIDDEN"}), self.response(403, {})],
            ),
            (
                "admin 200",
                [*self.valid_responses()[:2], self.response(200, {}), self.response(200, {})],
            ),
            (
                "admin 401",
                [*self.valid_responses()[:2], self.response(401, {}), self.response(200, {})],
            ),
            ("redirect", [self.response(302, {"location": "http://127.0.0.1:9"})]),
            ("timeout", [TimeoutError("synthetic timeout")]),
        ]
        for label, responses in cases:
            with self.subTest(label=label):
                fake = self.FakeSession(responses)
                with patch("kagoya_pull_agent._PublicDemoValidationSession", return_value=fake):
                    with self.assertRaises(DeployError) as raised:
                        _validate_public_demo_candidate(self.candidate)
                self.assertNotIn(SYNTHETIC_DEMO_PASSWORD, str(raised.exception))

    def test_transport_is_fixed_origin_no_proxy_no_redirect_and_bounded_timeout(self):
        class Response:
            status = 302

            def __init__(self):
                self.remaining = b"redirect body"

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                body, self.remaining = self.remaining, b""
                return body

        opener = Mock()
        opener.open.return_value = Response()
        with patch("kagoya_pull_agent.build_opener", return_value=opener) as build_opener:
            with patch("kagoya_pull_agent.ssl.create_default_context"):
                session = _PublicDemoValidationSession()
                status, body = session.request("GET", "/api/v1/me")

        self.assertEqual((status, body), (302, b"redirect body"))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, f"{PUBLIC_DEMO_VALIDATION_ORIGIN}/api/v1/me")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], PUBLIC_DEMO_VALIDATION_TIMEOUT)
        handlers = build_opener.call_args.args
        self.assertEqual(next(handler for handler in handlers if handler.__class__.__name__ == "ProxyHandler").proxies, {})
        self.assertTrue(any(handler.__class__.__name__ == "_RejectRedirects" for handler in handlers))

        opener.open.side_effect = TimeoutError("synthetic timeout")
        with patch("kagoya_pull_agent.build_opener", return_value=opener):
            with patch("kagoya_pull_agent.ssl.create_default_context"):
                with self.assertRaisesRegex(DeployError, "authorization check failed"):
                    _PublicDemoValidationSession().request("GET", "/api/v1/me")

    def test_public_demo_api_base_url_accepts_only_the_fixed_https_origin_shape(self):
        valid = (
            PUBLIC_DEMO_VALIDATION_ORIGIN,
            f"{PUBLIC_DEMO_VALIDATION_ORIGIN}/",
        )
        invalid = (
            "http://belib.hiroyuki9614.com",
            f"{PUBLIC_DEMO_VALIDATION_ORIGIN}:8443",
            f"https://user@belib.hiroyuki9614.com",
            "https://evil.belib.hiroyuki9614.com",
            f"{PUBLIC_DEMO_VALIDATION_ORIGIN}?next=/api/v1",
            f"{PUBLIC_DEMO_VALIDATION_ORIGIN}#/api/v1",
            f"{PUBLIC_DEMO_VALIDATION_ORIGIN}/api",
        )
        for value in valid:
            with self.subTest(value=value):
                _validate_public_demo_api_base_url({"expected_api_base_url": value})
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaisesRegex(DeployError, "canonical protected HTTPS origin"):
                    _validate_public_demo_api_base_url({"expected_api_base_url": value})


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
            "expected_api_base_url": PUBLIC_DEMO_VALIDATION_ORIGIN,
            "base_url": "http://127.0.0.1:8080",
        }
        self.docker = FakeDocker(self.project_dir, self.production_override)

    def run_deploy(self, smoke):
        with (
            patch("kagoya_pull_agent._checkout", return_value=self.checkout),
            patch("kagoya_pull_agent._run", side_effect=self.docker.run),
            patch(
                "kagoya_pull_agent._validate_public_demo_candidate",
                return_value=_PublicDemoIdentity(
                    7, SYNTHETIC_DEMO_EMAIL, "user", SYNTHETIC_DEMO_SHELF_IDS
                ),
            ),
        ):
            return deploy_frontend(
                self.config,
                SOURCE_SHA,
                smoke=smoke,
                main_sha_reader=lambda _config: SOURCE_SHA,
            )

    def public_demo_sidecar(self, **overrides):
        payload = {
            "schema_version": 1,
            "publication_intent": "public_general_demo_credentials",
            "public_display_approved": True,
            "approval_ref": "synthetic-owner-approval",
            "email": SYNTHETIC_DEMO_EMAIL,
            "password": SYNTHETIC_DEMO_PASSWORD,
        }
        payload.update(overrides)
        return payload

    def write_public_demo_sidecar(self, payload=None, *, mode=0o600, name="public-demo-credentials.json"):
        path = Path(self.temporary.name) / name
        path.write_text(json.dumps(payload or self.public_demo_sidecar()), encoding="utf-8")
        path.chmod(mode)
        self.config["public_demo_credentials_file"] = str(path)
        return path

    def public_demo_sidecar_marker(self, path=None):
        if path is None:
            return dict(PUBLIC_DEMO_ABSENT_SIDECAR_MARKER)
        result = path.stat()
        return {
            "version": PUBLIC_DEMO_SIDECAR_MARKER_VERSION,
            "kind": "file",
            "uid": result.st_uid,
            "mode": stat.S_IMODE(result.st_mode),
            "nlink": result.st_nlink,
            "dev": result.st_dev,
            "ino": result.st_ino,
            "size": result.st_size,
            "ctime_ns": result.st_ctime_ns,
            "mtime_ns": result.st_mtime_ns,
        }

    def test_default_and_ambient_public_demo_values_remain_blank(self):
        self.environment_file.write_text(
            "VITE_PUBLIC_DEMO_EMAIL=protected-env@example.invalid\n"
            "VITE_PUBLIC_DEMO_PASSWORD=protected-env-password\n",
            encoding="utf-8",
        )
        with patch.dict(
            os.environ,
            {
                "VITE_PUBLIC_DEMO_EMAIL": "ambient@example.invalid",
                "VITE_PUBLIC_DEMO_PASSWORD": "ambient-password",
            },
        ):
            self.run_deploy(lambda _base_url: None)

        compose_commands = [argv for argv, _env in self.docker.commands if argv[:2] == ["docker", "compose"]]
        for argv, env in self.docker.commands:
            if argv[:2] == ["docker", "compose"] and any(
                operation in argv for operation in ("config", "build", "up")
            ):
                self.assertEqual(env["VITE_PUBLIC_DEMO_EMAIL"], "")
                self.assertEqual(env["VITE_PUBLIC_DEMO_PASSWORD"], "")
        self.assertTrue(compose_commands)

    def test_missing_public_demo_sidecar_is_blank_after_parent_validation(self):
        missing_path = Path(self.temporary.name) / "missing-public-demo-credentials.json"
        self.config["public_demo_credentials_file"] = str(missing_path)

        self.assertEqual(_load_public_demo_credentials(self.config), ("", ""))

    def test_public_demo_sidecar_swap_during_open_is_rejected_by_post_open_fstat(self):
        target_path = self.write_public_demo_sidecar(name="swap-target.json")
        replacement_path = Path(self.temporary.name) / "swap-replacement.json"
        replacement_path.write_text(json.dumps(self.public_demo_sidecar()), encoding="utf-8")
        replacement_path.chmod(0o640)
        real_open = os.open

        def open_replacement(path, flags, *args, **kwargs):
            self.assertEqual(Path(path), target_path)
            return real_open(replacement_path, flags, *args, **kwargs)

        with patch("kagoya_pull_agent.os.open", side_effect=open_replacement) as opened:
            with patch("kagoya_pull_agent.os.close", wraps=os.close) as closed:
                with self.assertRaises(DeployError):
                    _load_public_demo_credentials(self.config)

        flags = opened.call_args.args[1]
        self.assertTrue(flags & os.O_NOFOLLOW)
        closed.assert_called_once()

    def test_public_demo_sidecar_same_inode_content_mutation_is_rejected(self):
        path = self.write_public_demo_sidecar(name="same-inode-mutation.json")
        original_read = os.read
        mutated = False

        def mutate_after_read(fd, size):
            nonlocal mutated
            content = original_read(fd, size)
            if content and not mutated:
                mutated = True
                path.write_text(json.dumps(self.public_demo_sidecar(password="changed.synthetic")), encoding="utf-8")
                path.chmod(0o600)
            return content

        with patch("kagoya_pull_agent.os.read", side_effect=mutate_after_read):
            with self.assertRaises(DeployError):
                _load_public_demo_credentials(self.config)
        self.assertTrue(mutated)

    def test_public_demo_sidecar_atomic_replacement_is_rejected(self):
        path = self.write_public_demo_sidecar(name="atomic-sidecar.json")
        replacement = Path(self.temporary.name) / "atomic-replacement.json"
        replacement.write_text(json.dumps(self.public_demo_sidecar(password="replacement.synthetic")), encoding="utf-8")
        replacement.chmod(0o600)
        original_read = os.read
        replaced = False

        def replace_after_read(fd, size):
            nonlocal replaced
            content = original_read(fd, size)
            if content and not replaced:
                replaced = True
                os.replace(replacement, path)
            return content

        with patch("kagoya_pull_agent.os.read", side_effect=replace_after_read):
            with self.assertRaises(DeployError):
                _load_public_demo_credentials(self.config)
        self.assertTrue(replaced)

    def test_public_demo_sidecar_rejects_nonregular_oversized_uid_mode_and_nlink(self):
        nonregular_path = Path(self.temporary.name) / "nonregular"
        nonregular_path.mkdir()
        oversized_path = Path(self.temporary.name) / "oversized.json"
        oversized_path.write_bytes(b"x" * (PUBLIC_DEMO_SIDECAR_MAX_BYTES + 1))
        oversized_path.chmod(0o600)

        cases = [("nonregular", nonregular_path, None), ("oversized", oversized_path, None)]
        valid_path = self.write_public_demo_sidecar(name="mocked-stat.json")
        valid_size = valid_path.stat().st_size
        regular_mode = stat.S_IFREG | 0o600
        cases.extend(
            [
                (
                    "uid",
                    valid_path,
                    SimpleNamespace(
                        st_mode=regular_mode,
                        st_uid=os.getuid() + 1,
                        st_nlink=1,
                        st_size=valid_size,
                        st_dev=1,
                        st_ino=1,
                        st_ctime_ns=1,
                        st_mtime_ns=1,
                    ),
                ),
                (
                    "mode",
                    valid_path,
                    SimpleNamespace(
                        st_mode=stat.S_IFREG | 0o640,
                        st_uid=os.getuid(),
                        st_nlink=1,
                        st_size=valid_size,
                        st_dev=1,
                        st_ino=1,
                        st_ctime_ns=1,
                        st_mtime_ns=1,
                    ),
                ),
                (
                    "nlink",
                    valid_path,
                    SimpleNamespace(
                        st_mode=regular_mode,
                        st_uid=os.getuid(),
                        st_nlink=2,
                        st_size=valid_size,
                        st_dev=1,
                        st_ino=1,
                        st_ctime_ns=1,
                        st_mtime_ns=1,
                    ),
                ),
            ]
        )

        for label, path, mocked_stat in cases:
            with self.subTest(label=label):
                self.config["public_demo_credentials_file"] = str(path)
                stdout, stderr = io.StringIO(), io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    context = patch("kagoya_pull_agent.os.fstat", return_value=mocked_stat) if mocked_stat else patch(
                        "kagoya_pull_agent.os.fstat", wraps=os.fstat
                    )
                    with context:
                        with self.assertRaises(DeployError) as raised:
                            _load_public_demo_credentials(self.config)
                self.assertNotIn(SYNTHETIC_DEMO_EMAIL, str(raised.exception) + stdout.getvalue() + stderr.getvalue())
                self.assertNotIn(SYNTHETIC_DEMO_PASSWORD, str(raised.exception) + stdout.getvalue() + stderr.getvalue())

    def test_public_demo_sidecar_rejects_growth_after_fstat_with_bounded_read(self):
        path = self.write_public_demo_sidecar(name="grown.json")
        with patch(
            "kagoya_pull_agent.os.read",
            return_value=b"x" * (PUBLIC_DEMO_SIDECAR_MAX_BYTES + 1),
        ) as read:
            with self.assertRaises(DeployError):
                _load_public_demo_credentials(self.config)

        self.assertEqual(read.call_args.args[1], PUBLIC_DEMO_SIDECAR_MAX_BYTES + 1)

    def test_owner_approved_synthetic_pair_is_used_for_build_up_and_rollback_without_leaking(self):
        self.write_public_demo_sidecar()
        self.config.update(
            {
                "state_file": str(Path(self.temporary.name) / "dispatcher-state.json"),
                "lock_file": str(Path(self.temporary.name) / "dispatcher.lock"),
            }
        )
        with (
            patch("kagoya_pull_agent._requests", return_value=[
                ((1, 1), SOURCE_SHA, "refs/tags/belib-deploy-request-1-1")
            ]),
            patch("kagoya_pull_agent._main_sha", return_value=SOURCE_SHA),
            patch("kagoya_pull_agent._validate_network"),
            patch("kagoya_pull_agent._checkout", return_value=self.checkout),
            patch("kagoya_pull_agent._run", side_effect=self.docker.run),
            patch(
                "kagoya_pull_agent._validate_public_demo_candidate",
                return_value=_PublicDemoIdentity(
                    7, SYNTHETIC_DEMO_EMAIL, "user", SYNTHETIC_DEMO_SHELF_IDS
                ),
            ),
            patch("kagoya_pull_agent.smoke_test"),
        ):
            self.assertEqual(dispatch_once(self.config), 0)

        saved_state = Path(self.config["state_file"]).read_text(encoding="utf-8")
        self.assertNotIn(SYNTHETIC_DEMO_EMAIL, saved_state)
        self.assertNotIn(SYNTHETIC_DEMO_PASSWORD, saved_state)
        self.assertNotIn("public_demo_fingerprint", saved_state)
        self.assertIn("public_demo_sidecar_marker", saved_state)

        build_env = next(env for argv, env in self.docker.commands if "build" in argv)
        candidate_up_env = next(
            env
            for argv, env in self.docker.commands
            if "up" in argv and env.get("BELIB_FRONTEND_IMAGE") == f"belib-frontend:{SOURCE_SHA}"
        )
        expected_public_values = {
            "VITE_PUBLIC_DEMO_EMAIL": SYNTHETIC_DEMO_EMAIL,
            "VITE_PUBLIC_DEMO_PASSWORD": SYNTHETIC_DEMO_PASSWORD,
        }
        for env in (build_env, candidate_up_env):
            self.assertEqual(
                {key: env[key] for key in expected_public_values},
                expected_public_values,
            )

        previous_ref = _retained_image_ref(OLD_ID)
        Path(self.config["state_file"]).write_text(
            json.dumps(
                {
                    "last_request": [1, 1],
                    "last_result": "success",
                    "current_sha": SOURCE_SHA,
                    "current": {"image_id": NEW_ID, "image_ref": f"belib-frontend:{SOURCE_SHA}"},
                    "previous": {"image_id": OLD_ID, "image_ref": previous_ref, "source_sha": None},
                }
            ),
            encoding="utf-8",
        )
        with (
            patch("kagoya_pull_agent._checkout", return_value=self.checkout),
            patch("kagoya_pull_agent._run", side_effect=self.docker.run),
            patch("kagoya_pull_agent.smoke_test"),
        ):
            rollback_result = rollback_frontend(self.config)
        self.assertEqual(rollback_result, 0)
        rollback_env = next(
            env
            for argv, env in reversed(self.docker.commands)
            if "up" in argv and env.get("BELIB_FRONTEND_IMAGE") == previous_ref
        )
        for key in ("COMPOSE_PROFILES", "VCS_REF", "VITE_API_BASE_URL"):
            self.assertEqual(rollback_env[key], candidate_up_env[key])
        self.assertEqual(rollback_env["VITE_PUBLIC_DEMO_EMAIL"], "")
        self.assertEqual(rollback_env["VITE_PUBLIC_DEMO_PASSWORD"], "")

        self.docker.frontend_build_arg_overrides["VITE_API_BASE_URL"] = "https://wrong.example.invalid"
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(DeployError) as raised:
                self.run_deploy(lambda _base_url: None)
        self.assertNotIn(SYNTHETIC_DEMO_EMAIL, str(raised.exception) + stdout.getvalue() + stderr.getvalue())
        self.assertNotIn(SYNTHETIC_DEMO_PASSWORD, str(raised.exception) + stdout.getvalue() + stderr.getvalue())

    def test_identity_change_after_build_blocks_public_switch(self):
        self.write_public_demo_sidecar()
        identities = iter(
            [
                _PublicDemoIdentity(7, SYNTHETIC_DEMO_EMAIL, "user", SYNTHETIC_DEMO_SHELF_IDS),
                _PublicDemoIdentity(7, SYNTHETIC_DEMO_EMAIL, "admin", SYNTHETIC_DEMO_SHELF_IDS),
            ]
        )
        with (
            patch("kagoya_pull_agent._checkout", return_value=self.checkout),
            patch("kagoya_pull_agent._run", side_effect=self.docker.run),
            patch("kagoya_pull_agent._validate_public_demo_candidate", side_effect=lambda _candidate: next(identities)),
        ):
            with self.assertRaisesRegex(DeployError, "identity changed"):
                deploy_frontend(
                    self.config,
                    SOURCE_SHA,
                    smoke=lambda _base_url: None,
                    main_sha_reader=lambda _config: SOURCE_SHA,
                )

        self.assertTrue(any("build" in argv for argv, _env in self.docker.commands))
        self.assertFalse(any("up" in argv for argv, _env in self.docker.commands))

    def test_public_shelf_change_after_build_blocks_public_switch(self):
        self.write_public_demo_sidecar()
        identities = iter(
            [
                _PublicDemoIdentity(7, SYNTHETIC_DEMO_EMAIL, "user", SYNTHETIC_DEMO_SHELF_IDS),
                _PublicDemoIdentity(7, SYNTHETIC_DEMO_EMAIL, "user", (4, 6)),
            ]
        )
        with (
            patch("kagoya_pull_agent._checkout", return_value=self.checkout),
            patch("kagoya_pull_agent._run", side_effect=self.docker.run),
            patch("kagoya_pull_agent._validate_public_demo_candidate", side_effect=lambda _candidate: next(identities)),
        ):
            with self.assertRaisesRegex(DeployError, "identity changed"):
                deploy_frontend(
                    self.config,
                    SOURCE_SHA,
                    smoke=lambda _base_url: None,
                    main_sha_reader=lambda _config: SOURCE_SHA,
                )

        self.assertTrue(any("build" in argv for argv, _env in self.docker.commands))
        self.assertFalse(any("up" in argv for argv, _env in self.docker.commands))

    def test_public_demo_origin_mismatch_blocks_before_validation_or_switch(self):
        self.write_public_demo_sidecar()
        self.config["expected_api_base_url"] = "https://api.example.test"
        with patch(
            "kagoya_pull_agent._validate_public_demo_candidate",
            side_effect=AssertionError("synthetic network must not be attempted"),
        ):
            with self.assertRaisesRegex(DeployError, "canonical protected HTTPS origin"):
                deploy_frontend(
                    self.config,
                    SOURCE_SHA,
                    smoke=lambda _base_url: None,
                    main_sha_reader=lambda _config: SOURCE_SHA,
                )
        self.assertFalse(any("build" in argv or "up" in argv for argv, _env in self.docker.commands))

    def test_manual_rollback_ignores_missing_malformed_and_symlink_sidecars(self):
        state_path = Path(self.temporary.name) / "rollback-sidecar-state.json"
        self.config.update(
            {
                "state_file": str(state_path),
                "lock_file": str(Path(self.temporary.name) / "rollback-sidecar.lock"),
            }
        )
        previous_ref = _retained_image_ref(OLD_ID)
        self.docker.images[previous_ref] = {"Id": OLD_ID, "Config": {"Labels": {}}}
        malformed_path = Path(self.temporary.name) / "malformed-rollback-sidecar.json"
        malformed_path.write_text("{", encoding="utf-8")
        malformed_path.chmod(0o600)
        sidecar_target = Path(self.temporary.name) / "rollback-sidecar-target.json"
        sidecar_target.write_text(json.dumps(self.public_demo_sidecar()), encoding="utf-8")
        sidecar_target.chmod(0o600)
        symlink_path = Path(self.temporary.name) / "symlink-rollback-sidecar.json"
        symlink_path.symlink_to(sidecar_target)

        cases = (
            Path(self.temporary.name) / "missing-rollback-sidecar.json",
            malformed_path,
            symlink_path,
        )
        for sidecar_path in cases:
            with self.subTest(sidecar=sidecar_path.name):
                self.docker.current_id = NEW_ID
                self.docker.current_ref = f"belib-frontend:{SOURCE_SHA}"
                self.config["public_demo_credentials_file"] = str(sidecar_path)
                state_path.write_text(
                    json.dumps(
                        {
                            "last_request": [1, 1],
                            "last_result": "success",
                            "current_sha": SOURCE_SHA,
                            "current": {"image_id": NEW_ID, "image_ref": f"belib-frontend:{SOURCE_SHA}"},
                            "previous": {"image_id": OLD_ID, "image_ref": previous_ref, "source_sha": None},
                        }
                    ),
                    encoding="utf-8",
                )
                self.docker.commands.clear()
                self.docker.up_targets.clear()
                with (
                    patch("kagoya_pull_agent._checkout", return_value=self.checkout),
                    patch("kagoya_pull_agent._run", side_effect=self.docker.run),
                    patch("kagoya_pull_agent.smoke_test"),
                    patch(
                        "kagoya_pull_agent._load_public_demo_credentials",
                        side_effect=AssertionError("rollback must not read the sidecar"),
                    ),
                ):
                    self.assertEqual(rollback_frontend(self.config), 0)
                rollback_env = next(
                    env
                    for argv, env in self.docker.commands
                    if "up" in argv and env.get("BELIB_FRONTEND_IMAGE") == previous_ref
                )
                self.assertEqual(rollback_env["VITE_PUBLIC_DEMO_EMAIL"], "")
                self.assertEqual(rollback_env["VITE_PUBLIC_DEMO_PASSWORD"], "")
                self.assertFalse(any("build" in argv for argv, _env in self.docker.commands))
                self.assertEqual(self.docker.current_id, OLD_ID)

    def test_same_sha_unchanged_sidecar_is_noop_but_rotation_and_removal_fail_closed(self):
        sidecar_path = self.write_public_demo_sidecar()
        original_sidecar_marker = self.public_demo_sidecar_marker(sidecar_path)
        self.run_deploy(lambda _base_url: None)
        self.assertEqual(self.docker.current_id, NEW_ID)
        state_path = Path(self.temporary.name) / "same-sha-state.json"
        self.config.update(
            {
                "state_file": str(state_path),
                "lock_file": str(Path(self.temporary.name) / "same-sha.lock"),
            }
        )
        state_path.write_text(
            json.dumps(
                {
                    "last_request": [1, 1],
                    "last_result": "success",
                    "current_sha": SOURCE_SHA,
                    "current": {
                        "image_id": NEW_ID,
                        "image_ref": f"belib-frontend:{SOURCE_SHA}",
                    },
                    "previous": {
                        "image_id": OLD_ID,
                        "image_ref": _retained_image_ref(OLD_ID),
                        "source_sha": None,
                    },
                    "public_demo_sidecar_marker": original_sidecar_marker,
                    "previous_public_demo_sidecar_marker": None,
                }
            ),
            encoding="utf-8",
        )

        def dispatch(request_id):
            with (
                patch("kagoya_pull_agent._requests", return_value=[
                    (request_id, SOURCE_SHA, f"refs/tags/belib-deploy-request-{request_id[0]}-{request_id[1]}")
                ]),
                patch("kagoya_pull_agent._main_sha", return_value=SOURCE_SHA),
                patch("kagoya_pull_agent._validate_network"),
                patch("kagoya_pull_agent._checkout", return_value=self.checkout),
                patch("kagoya_pull_agent._run", side_effect=self.docker.run),
                patch(
                    "kagoya_pull_agent._validate_public_demo_candidate",
                    return_value=_PublicDemoIdentity(
                        7, SYNTHETIC_DEMO_EMAIL, "user", SYNTHETIC_DEMO_SHELF_IDS
                    ),
                ),
                patch("kagoya_pull_agent.smoke_test"),
            ):
                return dispatch_once(self.config)

        self.docker.commands.clear()
        self.docker.up_targets.clear()
        self.assertEqual(dispatch((2, 1)), 0)
        self.assertEqual(self.docker.current_id, NEW_ID)
        self.assertFalse(any("build" in argv or "up" in argv for argv, _env in self.docker.commands))

        self.write_public_demo_sidecar(
            self.public_demo_sidecar(
                email="rotated.synthetic.demo@example.invalid",
                password="rotated-synthetic-only-public-password",
            )
        )
        self.docker.commands.clear()
        self.docker.up_targets.clear()
        with self.assertRaisesRegex(DeployError, "new main source SHA"):
            dispatch((3, 1))
        self.assertEqual(self.docker.current_id, NEW_ID)
        self.assertFalse(any("build" in argv or "up" in argv for argv, _env in self.docker.commands))
        failed_state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(failed_state["public_demo_sidecar_marker"], original_sidecar_marker)
        self.assertIsNone(failed_state["previous_public_demo_sidecar_marker"])

        self.docker.commands.clear()
        self.docker.up_targets.clear()
        Path(self.config["public_demo_credentials_file"]).unlink()
        with self.assertRaisesRegex(DeployError, "new main source SHA"):
            dispatch((4, 1))
        self.assertEqual(self.docker.current_id, NEW_ID)
        self.assertFalse(any("build" in argv or "up" in argv for argv, _env in self.docker.commands))

    def test_new_source_sha_authorized_request_can_publish_rotated_sidecar(self):
        original_sidecar_path = self.write_public_demo_sidecar()
        self.run_deploy(lambda _base_url: None)
        state_path = Path(self.temporary.name) / "new-release-state.json"
        self.config.update(
            {
                "state_file": str(state_path),
                "lock_file": str(Path(self.temporary.name) / "new-release.lock"),
            }
        )
        state_path.write_text(
            json.dumps(
                {
                    "last_request": [1, 1],
                    "last_result": "success",
                    "current_sha": SOURCE_SHA,
                    "current": {
                        "image_id": NEW_ID,
                        "image_ref": f"belib-frontend:{SOURCE_SHA}",
                    },
                    "previous": {
                        "image_id": OLD_ID,
                        "image_ref": _retained_image_ref(OLD_ID),
                        "source_sha": None,
                    },
                    "public_demo_sidecar_marker": self.public_demo_sidecar_marker(original_sidecar_path),
                    "previous_public_demo_sidecar_marker": None,
                }
            ),
            encoding="utf-8",
        )
        new_checkout = self.release_root / NEW_SOURCE_SHA
        new_checkout.mkdir()
        (new_checkout / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        (new_checkout / "frontend").mkdir()
        (new_checkout / "frontend" / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        self.write_public_demo_sidecar(
            self.public_demo_sidecar(
                email="rotated.synthetic.demo@example.invalid",
                password="rotated-synthetic-only-public-password",
            )
        )
        with (
            patch("kagoya_pull_agent._requests", return_value=[
                ((5, 1), NEW_SOURCE_SHA, "refs/tags/belib-deploy-request-5-1")
            ]),
            patch("kagoya_pull_agent._main_sha", return_value=NEW_SOURCE_SHA),
            patch("kagoya_pull_agent._validate_network"),
            patch("kagoya_pull_agent._checkout", return_value=new_checkout),
            patch("kagoya_pull_agent._run", side_effect=self.docker.run),
            patch(
                "kagoya_pull_agent._validate_public_demo_candidate",
                return_value=_PublicDemoIdentity(
                    7,
                    "rotated.synthetic.demo@example.invalid",
                    "user",
                    SYNTHETIC_DEMO_SHELF_IDS,
                ),
            ),
            patch("kagoya_pull_agent.smoke_test"),
        ):
            self.assertEqual(dispatch_once(self.config), 0)

        self.assertEqual(self.docker.current_id, DEPLOYED_ID)
        self.assertIn("frontend", self.docker.up_targets)
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["current_sha"], NEW_SOURCE_SHA)
        self.assertEqual(saved["public_demo_sidecar_marker"], self.public_demo_sidecar_marker(original_sidecar_path))

    def test_same_sha_without_recorded_sidecar_marker_fails_closed(self):
        state_path = Path(self.temporary.name) / "unknown-marker-state.json"
        self.config.update(
            {
                "state_file": str(state_path),
                "lock_file": str(Path(self.temporary.name) / "unknown-marker.lock"),
            }
        )
        state_path.write_text(
            json.dumps(
                {
                    "last_request": [1, 1],
                    "last_result": "success",
                    "current_sha": SOURCE_SHA,
                    "current": {
                        "image_id": NEW_ID,
                        "image_ref": f"belib-frontend:{SOURCE_SHA}",
                    },
                    "previous": None,
                }
            ),
            encoding="utf-8",
        )
        self.write_public_demo_sidecar()
        with (
            patch("kagoya_pull_agent._requests", return_value=[
                ((2, 1), SOURCE_SHA, "refs/tags/belib-deploy-request-2-1")
            ]),
            patch("kagoya_pull_agent._main_sha", return_value=SOURCE_SHA),
        ):
            with self.assertRaisesRegex(DeployError, "cannot be proven"):
                dispatch_once(self.config)
        self.assertFalse(self.docker.up_targets)

    def test_public_demo_sidecar_rejects_malformed_unsafe_or_unproven_opt_in(self):
        cases = []
        malformed_path = Path(self.temporary.name) / "malformed.json"
        malformed_path.write_text("{", encoding="utf-8")
        malformed_path.chmod(0o600)
        cases.append(("malformed", malformed_path, os.getuid()))

        invalid_utf8_path = Path(self.temporary.name) / "invalid-utf8.json"
        invalid_utf8_path.write_bytes(b"\xff")
        invalid_utf8_path.chmod(0o600)
        cases.append(("invalid UTF-8", invalid_utf8_path, os.getuid()))

        duplicate_keys_path = Path(self.temporary.name) / "duplicate-keys.json"
        duplicate_keys_path.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")
        duplicate_keys_path.chmod(0o600)
        cases.append(("duplicate JSON keys", duplicate_keys_path, os.getuid()))

        unsafe_path = self.write_public_demo_sidecar(mode=0o640, name="unsafe.json")
        cases.append(("unsafe mode", unsafe_path, os.getuid()))

        target_path = Path(self.temporary.name) / "sidecar-target.json"
        target_path.write_text(json.dumps(self.public_demo_sidecar()), encoding="utf-8")
        target_path.chmod(0o600)
        symlink_path = Path(self.temporary.name) / "symlink-sidecar.json"
        symlink_path.symlink_to(target_path)
        cases.append(("symlink", symlink_path, os.getuid()))

        wrong_owner_path = self.write_public_demo_sidecar(name="wrong-owner.json")
        cases.append(("wrong owner", wrong_owner_path, os.getuid() + 1))

        legacy_claims_payload = self.public_demo_sidecar()
        legacy_claims_payload["evidence"] = {
            "general_role": "admin",
            "admin_api_403": True,
            "private_book_403": True,
        }
        legacy_claims_path = self.write_public_demo_sidecar(legacy_claims_payload, name="legacy-claims.json")
        cases.append(("legacy owner claims", legacy_claims_path, os.getuid()))

        legacy_private_book_payload = self.public_demo_sidecar()
        legacy_private_book_payload["private_book_id"] = 42
        legacy_private_book_path = self.write_public_demo_sidecar(
            legacy_private_book_payload,
            name="legacy-private-book-schema.json",
        )
        cases.append(("legacy private-book schema", legacy_private_book_path, os.getuid()))

        partial_payload = self.public_demo_sidecar()
        partial_payload.pop("password")
        partial_path = self.write_public_demo_sidecar(partial_payload, name="partial.json")
        cases.append(("partial", partial_path, os.getuid()))

        for label, path, owner_uid in cases:
            with self.subTest(label=label):
                self.config["public_demo_credentials_file"] = str(path)
                self.config["agent_uid"] = owner_uid
                with self.assertRaises(DeployError) as raised:
                    self.run_deploy(lambda _base_url: None)
                self.assertNotIn(SYNTHETIC_DEMO_EMAIL, str(raised.exception))
                self.assertNotIn(SYNTHETIC_DEMO_PASSWORD, str(raised.exception))
                self.assertFalse(any("build" in argv or "up" in argv for argv, _env in self.docker.commands))
                self.docker.commands.clear()
                self.docker.up_targets.clear()
        self.config["agent_uid"] = os.getuid()

    def test_explicit_public_accounts_are_verified_but_admin_credentials_do_not_reach_build(self):
        self.config['publish_demo_accounts'] = True
        args = {**empty_build_args(), 'VITE_PUBLIC_DEMO_EMAIL': 'user@example.test',
                'VITE_PUBLIC_DEMO_PASSWORD': 'synthetic-user-pass',
                'VITE_PUBLIC_DEMO_ADMIN_EMAIL': 'admin@example.test',
                'VITE_PUBLIC_DEMO_ADMIN_PASSWORD': 'synthetic-admin-pass',
                'PUBLIC_DEMO_USER_BUILD_SHA256': 'c' * 64}
        with patch('kagoya_pull_agent.load_accounts', return_value=(args, {'synthetic': True})), patch('kagoya_pull_agent.verify_accounts') as verify:
            result = self.run_deploy(lambda _: None)
        self.assertFalse(result['no_op'])
        self.assertEqual(verify.call_count, 2)
        build_env = next(env for argv, env in self.docker.commands if 'build' in argv)
        for key, value in args.items():
            if key.startswith('VITE_PUBLIC_DEMO_ADMIN'):
                self.assertNotIn(key, build_env)
            else:
                self.assertEqual(build_env[key], value)
        self.assertFalse(any(key.startswith('VITE_PUBLIC_DEMO_ADMIN') for key in build_env))
        self.assertEqual(self.docker.up_targets, ['frontend'])

    def test_rejects_admin_demo_compose_build_args_before_build_or_switch(self):
        self.docker.frontend_build_arg_overrides['VITE_PUBLIC_DEMO_ADMIN_EMAIL'] = 'build-boundary-placeholder'
        with self.assertRaises(DeployError):
            self.run_deploy(lambda _base_url: None)
        self.assertEqual(self.docker.current_id, OLD_ID)
        self.assertEqual(self.docker.up_targets, [])
        self.assertFalse(any('build' in argv for argv, _env in self.docker.commands))

    def test_frontend_compose_env_drops_only_known_legacy_admin_keys(self):
        config = {
            'expected_api_base_url': 'https://example.test',
            '_public_demo_build_args': {
                'VITE_PUBLIC_DEMO_EMAIL': 'user@example.test',
                'VITE_PUBLIC_DEMO_PASSWORD': 'synthetic-user-pass',
                'VITE_PUBLIC_DEMO_ADMIN_EMAIL': 'admin@example.test',
                'VITE_PUBLIC_DEMO_ADMIN_PASSWORD': 'synthetic-admin-pass',
                'PUBLIC_DEMO_USER_BUILD_SHA256': 'c' * 64,
            },
        }
        env = _frontend_compose_env(config, SOURCE_SHA, ('', ''), f'belib-frontend:{SOURCE_SHA}')
        self.assertNotIn('VITE_PUBLIC_DEMO_ADMIN_EMAIL', env)
        self.assertNotIn('VITE_PUBLIC_DEMO_ADMIN_PASSWORD', env)
        config['_public_demo_build_args']['VITE_PUBLIC_DEMO_ADMIN_PASSWORD_COPY'] = 'synthetic-only-marker'
        with self.assertRaisesRegex(DeployError, '^The public demo build argument names are unsupported$'):
            _frontend_compose_env(config, SOURCE_SHA, ('', ''), f'belib-frontend:{SOURCE_SHA}')

    def test_public_account_auth_failure_stops_before_build(self):
        self.config["publish_demo_accounts"] = True
        with patch('kagoya_pull_agent.load_accounts', return_value=(empty_build_args(), {'synthetic': True})), patch('kagoya_pull_agent.verify_accounts', side_effect=PublicDemoError('Rejected')):
            with self.assertRaises(DeployError): self.run_deploy(lambda _: None)
        self.assertFalse(any('build' in argv or 'up' in argv for argv, _ in self.docker.commands))

    def test_public_accounts_change_during_build_stops_before_switch(self):
        self.config["publish_demo_accounts"] = True
        args = empty_build_args()
        with patch('kagoya_pull_agent.load_accounts', side_effect=[(args, None), ({**args, 'PUBLIC_DEMO_USER_BUILD_SHA256': 'c'*64}, None)]):
            with self.assertRaisesRegex(DeployError, 'revalidation failed'): self.run_deploy(lambda _: None)
        self.assertEqual(self.docker.up_targets, [])

    def test_same_source_different_public_configuration_is_not_a_silent_noop(self):
        self.run_deploy(lambda _: None)
        self.config["publish_demo_accounts"] = True
        args = {**empty_build_args(), 'PUBLIC_DEMO_USER_BUILD_SHA256': 'c'*64}
        with patch('kagoya_pull_agent.load_accounts', return_value=(args, None)):
            with self.assertRaises(DeployError): self.run_deploy(lambda _: None)
        self.assertEqual(self.docker.up_targets, ['frontend'])

    def test_cannot_mix_legacy_and_dual_public_account_modes(self):
        self.config.update(publish_demo_accounts=True, public_demo_credentials_file='/never/read/legacy.json')
        with patch('kagoya_pull_agent._load_public_demo_source', return_value=_PublicDemoSource(None, dict(PUBLIC_DEMO_ABSENT_SIDECAR_MARKER))):
            with self.assertRaisesRegex(DeployError, 'exactly one'): self.run_deploy(lambda _: None)
        self.assertEqual(self.docker.commands, [])

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

    def test_state_save_failure_does_not_allow_unproven_same_sha_noop(self):
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

        with (
            patch("kagoya_pull_agent._requests", return_value=request),
            patch("kagoya_pull_agent._main_sha", return_value=SOURCE_SHA),
            patch("kagoya_pull_agent._validate_network"),
            patch("kagoya_pull_agent._checkout", return_value=self.checkout),
            patch("kagoya_pull_agent._run", side_effect=self.docker.run),
            patch("kagoya_pull_agent.smoke_test"),
        ):
            with self.assertRaisesRegex(DeployError, "cannot be proven"):
                dispatch_once(self.config)

        saved = json.loads(state_path.read_text(encoding="utf-8"))
        retained_old = _retained_image_ref(OLD_ID)
        self.assertEqual(saved["last_request"], [12, 1])
        self.assertEqual(saved["last_result"], "failed")
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

        def switch_then_raise(
            config,
            checkout,
            source_sha,
            *,
            image_override=None,
            public_demo_credentials=None,
        ):
            original_up_frontend(
                config,
                checkout,
                source_sha,
                image_override=image_override,
                public_demo_credentials=public_demo_credentials,
            )
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

        def switch_candidate_then_fail_rollback(
            config,
            checkout,
            source_sha,
            *,
            image_override=None,
            public_demo_credentials=None,
        ):
            nonlocal up_calls
            up_calls += 1
            if image_override is not None:
                raise DeployError("synthetic rollback switch failure")
            return _up_frontend(
                config,
                checkout,
                source_sha,
                image_override=image_override,
                public_demo_credentials=public_demo_credentials,
            )

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

        def deploy_while_paused(_config, source_sha, **_kwargs):
            self.assertEqual(source_sha, next_sha)
            dispatch_entered.set()
            if not allow_dispatch_to_finish.wait(timeout=5):
                raise AssertionError("synthetic dispatch was not released")
            return {
                "image_id": DEPLOYED_ID,
                "image_ref": next_ref,
                "previous_image_id": NEW_ID,
                "previous_image_ref": dispatch_previous_ref,
                "public_demo_sidecar_marker": dict(PUBLIC_DEMO_ABSENT_SIDECAR_MARKER),
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
        sidecar_path = self.write_public_demo_sidecar()
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
                    "public_demo_sidecar_marker": self.public_demo_sidecar_marker(sidecar_path),
                    "previous_public_demo_sidecar_marker": dict(PUBLIC_DEMO_ABSENT_SIDECAR_MARKER),
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
            self.assertEqual(first["public_demo_sidecar_marker"], dict(PUBLIC_DEMO_ABSENT_SIDECAR_MARKER))
            self.assertEqual(
                first["previous_public_demo_sidecar_marker"],
                self.public_demo_sidecar_marker(sidecar_path),
            )
            self.assertEqual(rollback_frontend(self.config), 0)

        restored = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(restored["current_sha"], SOURCE_SHA)
        self.assertEqual(restored["current"]["image_id"], NEW_ID)
        self.assertEqual(restored["previous"]["image_id"], OLD_ID)
        self.assertEqual(restored["previous"]["source_sha"], old_sha)
        self.assertEqual(
            restored["public_demo_sidecar_marker"],
            self.public_demo_sidecar_marker(sidecar_path),
        )
        self.assertEqual(restored["previous_public_demo_sidecar_marker"], dict(PUBLIC_DEMO_ABSENT_SIDECAR_MARKER))

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
