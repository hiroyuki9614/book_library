import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


DEPLOY_DIR = Path(__file__).resolve().parent
INSTALLER = DEPLOY_DIR / "install_kagoya_pull_agent.sh"


class InstallerLegacyBootstrapTests(unittest.TestCase):
    def test_legacy_override_refuses_before_image_or_timer_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            home = root / "agent-home"
            bin_dir.mkdir()
            home.mkdir()
            env_file = root / "production.env"
            env_file.write_text("SECRET_VALUE=must-not-be-printed\n", encoding="utf-8")
            env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
            docker_log = root / "docker.log"
            systemctl_log = root / "systemctl.log"

            stubs = {
                "id": """#!/bin/sh
if [ "$1" = -un ]; then echo agent; else echo 1234; fi
""",
                "getent": """#!/bin/sh
printf 'agent:x:1234:1234::%s:/bin/bash\\n' "$TEST_AGENT_HOME"
""",
                "loginctl": "#!/bin/sh\necho yes\n",
                "docker": """#!/bin/sh
printf '%s\\n' "$*" >> "$TEST_DOCKER_LOG"
if [ "$1" = info ]; then echo rootless; fi
""",
                "systemctl": """#!/bin/sh
printf '%s\\n' "$*" >> "$TEST_SYSTEMCTL_LOG"
if [ "$1" = --user ] && [ "$2" = list-unit-files ]; then exit 0; fi
exit 0
""",
                "python3": f"""#!/bin/sh
if [ "$1" = - ]; then cat >/dev/null; exit 0; fi
if [ "$1" = "{DEPLOY_DIR / 'kagoya_compose.py'}" ]; then
  cat <<'JSON'
{{"project":"belib-production","image_id":"sha256:{'a' * 64}","project_dir":"/srv/belib","compose_file":"/srv/belib/compose.yaml","compose_override_file":"/srv/belib/.deployment/compose.production.yaml","expected_api_base_url":"https://api.example.test","compose_contract_sha256":"{'b' * 64}","frontend_bootstrap_mode":"legacy-override-update-required","networks":{{"default":"belib-production_default"}}}}
JSON
  exit 0
fi
exec "{os.path.realpath('/usr/bin/python3')}" "$@"
""",
            }
            for name, content in stubs.items():
                path = bin_dir / name
                path.write_text(content, encoding="utf-8")
                path.chmod(0o700)

            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{bin_dir}:{environment['PATH']}",
                    "TEST_AGENT_HOME": str(home),
                    "TEST_DOCKER_LOG": str(docker_log),
                    "TEST_SYSTEMCTL_LOG": str(systemctl_log),
                    "XDG_RUNTIME_DIR": "",
                }
            )
            result = subprocess.run(
                ["bash", str(INSTALLER), str(env_file)],
                cwd=DEPLOY_DIR.parent.parent,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("update only that override", result.stderr)
            self.assertIn("read-only KAGOYA preflight", result.stderr)
            self.assertNotIn("must-not-be-printed", result.stdout + result.stderr)
            self.assertNotIn("image tag", docker_log.read_text(encoding="utf-8"))
            self.assertNotIn("enable --now", systemctl_log.read_text(encoding="utf-8"))
            self.assertFalse((home / ".config/belib-production-cd").exists())


if __name__ == "__main__":
    unittest.main()
