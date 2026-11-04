import os
import re
import socket
import stat
import string
import subprocess
import tempfile
import unittest
from pathlib import Path


DEPLOY_DIR = Path(__file__).resolve().parent
INSTALLER = DEPLOY_DIR / "install_kagoya_pull_agent.sh"


def _extract_heredoc(source, target, delimiter):
    match = re.search(
        rf'^cat >"\${target}" <<(?:\'{delimiter}\'|{delimiter})\n(?P<body>.*?)\n{delimiter}\n',
        source,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"could not find {target} heredoc")
    return match.group("body")


def _render_unit_template(source, target, delimiter):
    template = string.Template(_extract_heredoc(source, target, delimiter))
    return template.substitute(
        PYTHON="/usr/bin/python3",
        LIBEXEC_DIR="/home/agent/.local/libexec/belib-production-cd",
        CONFIG_PATH="/home/agent/.config/belib-production-cd/config.json",
        STATE_DIR="/home/agent/.local/state/belib-production-cd",
        RELEASE_DIR="/home/agent/.local/share/belib-production-cd/releases",
    )


def _parse_unit(unit_text):
    sections = {}
    section = None
    for line in unit_text.splitlines():
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line
            sections[section] = {}
            continue
        if section is None or "=" not in line:
            raise AssertionError(f"invalid unit line: {line!r}")
        key, value = line.split("=", 1)
        if key in sections[section]:
            raise AssertionError(f"duplicate unit key: {key}")
        sections[section][key] = value
    return sections


class InstallerSystemdTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.installer_source = INSTALLER.read_text(encoding="utf-8")

    def test_service_template_preserves_contract_and_rootless_socket_compatibility(self):
        service = _render_unit_template(self.installer_source, "SERVICE_PATH", "EOF")
        parsed = _parse_unit(service)

        self.assertEqual(
            parsed,
            {
                "[Unit]": {
                    "Description": "BeLib production frontend release dispatcher",
                },
                "[Service]": {
                    "Type": "oneshot",
                    "UMask": "0077",
                    "NoNewPrivileges": "true",
                    "WorkingDirectory": "/home/agent/.local/share/belib-production-cd/releases",
                    "ExecStart": (
                        "/usr/bin/python3 /home/agent/.local/libexec/belib-production-cd/"
                        "kagoya_pull_agent.py --once --config "
                        "/home/agent/.config/belib-production-cd/config.json"
                    ),
                },
            },
        )
        for directive in ("PrivateTmp", "ProtectSystem", "ReadWritePaths"):
            self.assertNotIn(directive, parsed["[Service]"])

        with tempfile.TemporaryDirectory() as temporary:
            socket_path = Path(temporary) / "docker.sock"
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(server.close)
            server.bind(str(socket_path))
            server.listen(1)
            self.addCleanup(socket_path.unlink, missing_ok=True)
            self.assertTrue(stat.S_ISSOCK(os.stat(socket_path).st_mode))
            self.assertEqual(os.stat(socket_path).st_uid, os.getuid())

            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(client.close)
            client.connect(str(socket_path))

    def test_timer_template_keeps_existing_schedule_and_service_binding(self):
        timer = _render_unit_template(self.installer_source, "TIMER_PATH", "EOF")

        self.assertEqual(
            _parse_unit(timer),
            {
                "[Unit]": {
                    "Description": "Poll reviewed BeLib production frontend requests",
                },
                "[Timer]": {
                    "OnBootSec": "2min",
                    "OnUnitActiveSec": "5min",
                    "RandomizedDelaySec": "30s",
                    "Persistent": "true",
                    "Unit": "belib-production-cd.service",
                },
                "[Install]": {
                    "WantedBy": "timers.target",
                },
            },
        )

    def test_config_schema_records_only_the_absolute_sidecar_location(self):
        self.assertIn(
            'PUBLIC_DEMO_CREDENTIALS_FILE="$(realpath -m -- "$CONFIG_DIR/public-demo-credentials.json")"',
            self.installer_source,
        )
        self.assertIn(
            '"public_demo_credentials_file": public_demo_credentials_file,',
            self.installer_source,
        )


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
