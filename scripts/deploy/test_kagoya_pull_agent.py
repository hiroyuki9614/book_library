import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kagoya_pull_agent import DeployError, deploy_frontend


SOURCE_SHA = "a" * 40
OLD_ID = "sha256:" + "b" * 64
NEW_ID = "sha256:" + "a" * 64


class FakeDocker:
    def __init__(self):
        self.current_id = OLD_ID
        self.current_ref = "belib-frontend:legacy"
        self.images = {self.current_ref: {"Id": OLD_ID, "Config": {"Labels": {}}}}
        self.tags = {}
        self.commands = []
        self.up_targets = []
        self.frontend_compose_overrides = {}

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
                            "VITE_API_BASE_URL": "https://api.example.test",
                            "VITE_PUBLIC_DEMO_EMAIL": "",
                            "VITE_PUBLIC_DEMO_PASSWORD": "",
                        },
                        "context": str(checkout / "frontend"),
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
                frontend.update(self.frontend_compose_overrides)
                stdout = json.dumps(
                    {
                        "services": {
                            "db": {},
                            "migrate": {},
                            "backend": {},
                            "frontend": frontend,
                            "nginx": {},
                        },
                        "networks": {"default": {"name": "belib-production_default"}},
                    }
                )
            elif "build" in argv:
                self.images[f"belib-frontend:{SOURCE_SHA}"] = {
                    "Id": NEW_ID,
                    "Config": {"Labels": {"org.opencontainers.image.revision": SOURCE_SHA}},
                }
            elif "up" in argv:
                override_paths = [
                    Path(argv[index + 1])
                    for index, value in enumerate(argv[:-1])
                    if value == "--file"
                ]
                override_paths = [path for path in override_paths if "override" in path.name]
                if override_paths:
                    text = override_paths[-1].read_text(encoding="utf-8")
                    ref = text.split("image:", 1)[1].strip().strip('"')
                else:
                    ref = f"belib-frontend:{SOURCE_SHA}"
                image = self.images[ref]
                self.current_ref = ref
                self.current_id = image["Id"]
                self.up_targets.append(argv[-1])
        else:
            raise AssertionError(f"Unexpected command: {argv}")
        return subprocess.CompletedProcess(argv, returncode, stdout, "")


class PullAgentDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.checkout = Path(self.temporary.name) / SOURCE_SHA
        self.checkout.mkdir()
        (self.checkout / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        self.environment_file = Path(self.temporary.name) / "production.env"
        self.environment_file.write_text("synthetic=only\n", encoding="utf-8")
        self.config = {
            "repository": "hiroyuki9614/book_library",
            "repo_url": "https://github.com/hiroyuki9614/book_library.git",
            "expected_project": "belib-production",
            "production_container": "belib-production-frontend-1",
            "expected_networks": ["belib-production_default"],
            "production_env_file": str(self.environment_file),
            "base_url": "http://127.0.0.1:8080",
        }
        self.docker = FakeDocker()

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

    def test_deploy_targets_only_frontend_and_reads_back_exact_source_image(self):
        result = self.run_deploy(lambda _base_url: None)
        self.assertFalse(result["no_op"])
        self.assertEqual(result["source_sha"], SOURCE_SHA)
        self.assertEqual(result["image_id"], NEW_ID)
        self.assertEqual(self.docker.current_id, NEW_ID)
        compose_commands = [argv for argv, _ in self.docker.commands if argv[:2] == ["docker", "compose"]]
        self.assertTrue(any("build" in argv and argv[-1] == "frontend" for argv in compose_commands))
        self.assertTrue(any("up" in argv and argv[-1] == "frontend" for argv in compose_commands))
        build_env = next(env for argv, env in self.docker.commands if "build" in argv)
        self.assertEqual(build_env["VITE_PUBLIC_DEMO_EMAIL"], "")
        self.assertEqual(build_env["VITE_PUBLIC_DEMO_PASSWORD"], "")
        self.assertFalse(any("down" in argv or "prune" in argv for argv in self.docker.commands))
        self.assertTrue(all(target == "frontend" for target in self.docker.up_targets))

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


if __name__ == "__main__":
    unittest.main()
