"""Guard tests for the GitHub-hosted isolated recovery rehearsal.

The fake Docker executable records only its arguments; these tests never call Docker,
systemctl, a daemon socket, or a real service.
"""

import ast
import json
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

    def test_synthetic_restore_is_private_isolated_and_fail_closed(self):
        source = SCRIPT.read_text(encoding="utf-8")
        function = re.search(
            r"(?ms)^synthetic_restore_drill\(\) \{\n.*?^\}", source
        )
        self.assertIsNotNone(function)
        body = function.group(0)
        self.assertIn('[[ "$recovery_rehearsal" == "github-hosted-v1" ]] ||', body)
        self.assertIn('mktemp -d "/tmp/belib-recovery-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}.XXXXXX"', body)
        self.assertIn('chmod 700 "$restore_backup_dir"', body)
        self.assertIn('mktemp "$restore_backup_dir/postgres.XXXXXX"', body)
        self.assertIn('chmod 600 "$backup_file"', body)
        self.assertIn('pg_dump --format=custom -U "$POSTGRES_USER" -d "$POSTGRES_DB" >"$backup_file" 2>/dev/null', body)
        self.assertIn('createdb --template=template0 -U "$POSTGRES_USER" "$restore_db"', body)
        self.assertIn('pg_restore --exit-on-error --no-owner --no-privileges', body)
        self.assertIn('-d "$restore_db" - <"$backup_file" 2>&1 >/dev/null', body)
        self.assertIn('read(16384)', body)
        self.assertIn('diagnostic category: $restore_error_category', body)
        self.assertIn('chmod 600 "$restore_error_file"', body)
        self.assertNotIn('cat "$restore_error_file"', body)
        self.assertIn('SELECT EXISTS (SELECT 1 FROM ci_recovery_probe WHERE marker = \'$marker\')', body)
        self.assertIn('[[ "$restored_marker" == t ]] ||', body)
        self.assertNotIn('echo "$marker"', body)
        self.assertNotIn('echo "$restored_marker"', body)
        self.assertIn('rm -rf -- "$restore_backup_dir"', source)

        marker_insert = source.index('INSERT INTO ci_recovery_probe VALUES (\'$marker\')')
        drill_call = source.index('synthetic_restore_drill "$marker"')
        crash_call = source.index('crash_owned_service frontend')
        self.assertLess(marker_insert, drill_call)
        self.assertLess(drill_call, crash_call)

    def test_synthetic_restore_function_with_fake_docker(self):
        source = SCRIPT.read_text(encoding="utf-8")
        function = re.search(
            r"(?ms)^synthetic_restore_drill\(\) \{\n.*?^\}", source
        )
        self.assertIsNotNone(function)
        capture_bin = self.root / "capture-bin"
        capture_bin.mkdir()
        capture_python = capture_bin / "python3"
        capture_python.write_text(
            "#!/usr/bin/env bash\n"
            'if [[ "${1:-}" == "-c" && -n "${FAKE_CAPTURE_EXIT:-}" ]]; then\n'
            '  cat >/dev/null\n'
            '  exit "$FAKE_CAPTURE_EXIT"\n'
            "fi\n"
            'exec "$REAL_PYTHON" "$@"\n',
            encoding="utf-8",
        )
        capture_python.chmod(0o755)
        harness = "\n".join(
            (
                "set -euo pipefail",
                'restore_backup_dir=""',
                'project="belib-recovery-test"',
                'compose_file="compose.yaml"',
                'cleanup_fake_restore() { [[ -z "${restore_backup_dir:-}" ]] || rm -rf -- "$restore_backup_dir"; }',
                "trap cleanup_fake_restore EXIT",
                'docker() {',
                '  local stage="" arg directory backup_file',
                '  for arg in "$@"; do',
                '    case "$arg" in pg_dump|createdb|pg_restore) stage="$arg" ;; psql) stage="marker_psql" ;; esac',
                '  done',
                '  printf "%s\\n" "$stage" >> "$FAKE_CALL_LOG"',
                '  if [[ "$stage" == pg_dump ]]; then',
                '    directory=(/tmp/belib-recovery-"${GITHUB_RUN_ID}"-"${GITHUB_RUN_ATTEMPT}".*)',
                '    [[ -d "${directory[0]}" ]] || return 91',
                '    backup_file=("${directory[0]}"/postgres.*)',
                '    [[ -f "${backup_file[0]}" ]] || return 92',
                '    [[ "$(stat -c %a "${directory[0]}")" == 700 ]] || return 93',
                '    [[ "$(stat -c %a "${backup_file[0]}")" == 600 ]] || return 94',
                '  fi',
                '  if [[ "$stage" == createdb ]]; then',
                '    [[ " $* " == *" --template=template0 "* ]] || return 95',
                '  fi',
                '  if [[ "$FAKE_FAIL_STAGE" == "$stage" ]]; then',
                '    if [[ "$stage" == pg_restore ]]; then printf "%s\\n" "$FAKE_RESTORE_ERROR" >&2; else printf "%s\\n" "private marker $FAKE_MARKER SQL credential" >&2; fi',
                '    [[ "$stage" == pg_dump ]] && printf "%s\\n" "partial archive"',
                '    return 23',
                '  fi',
                '  case "$stage" in pg_dump) printf "%s\\n" "synthetic archive" ;; marker_psql) printf "%s\\n" "$FAKE_MARKER_RESULT" ;; esac',
                '}',
                function.group(0),
                'synthetic_restore_drill "$FAKE_MARKER"',
            )
        )

        def run_function(*, failure="", mode="github-hosted-v1", marker_result="t", capture_exit="", restore_error="unsupported version 1.15 in archive for private marker ci-private-marker-777 SQL credential"):
            run_id = f"7{os.getpid()}"
            call_log = self.root / "restore-calls.log"
            call_log.unlink(missing_ok=True)
            env = {
                "PATH": f"{capture_bin}:/usr/bin:/bin",
                "REAL_PYTHON": sys.executable,
                "FAKE_CAPTURE_EXIT": capture_exit,
                "FAKE_CALL_LOG": str(call_log),
                "FAKE_FAIL_STAGE": failure,
                "FAKE_MARKER": "ci-private-marker-777",
                "FAKE_MARKER_RESULT": marker_result,
                "FAKE_RESTORE_ERROR": restore_error,
                "recovery_rehearsal": mode,
                "GITHUB_RUN_ID": run_id,
                "GITHUB_RUN_ATTEMPT": "1",
                "POSTGRES_USER": "ci-private-credential",
                "POSTGRES_DB": "ci_private_db",
                "COMPOSE_PROJECT_NAME": "belib-recovery-test",
            }
            result = subprocess.run(
                ["bash", "-c", harness],
                cwd=ROOT,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            calls = call_log.read_text(encoding="utf-8").splitlines() if call_log.exists() else []
            leftovers = list(Path("/tmp").glob(f"belib-recovery-{run_id}-1.*"))
            self.assertEqual(leftovers, [], "synthetic dump files were not cleaned up")
            for private_value in (
                "ci-private-marker-777",
                "ci-private-credential",
                "SELECT EXISTS",
                "private marker",
                "SQL credential",
            ):
                self.assertNotIn(private_value, result.stdout + result.stderr)
            return result, calls

        result, calls = run_function()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("marker verified", result.stdout)
        self.assertEqual(calls, ["pg_dump", "createdb", "pg_restore", "marker_psql"])

        expected_failures = (
            ("pg_dump", ["pg_dump"], "custom-format dump failed"),
            ("createdb", ["pg_dump", "createdb"], "separate synthetic restore database"),
            ("pg_restore", ["pg_dump", "createdb", "pg_restore"], "diagnostic category: unsupported_archive_version"),
            ("marker_psql", ["pg_dump", "createdb", "pg_restore", "marker_psql"], "Could not verify marker"),
        )
        for stage, expected_calls, safe_message in expected_failures:
            with self.subTest(stage=stage):
                result, calls = run_function(failure=stage)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, expected_calls)
                self.assertIn(safe_message, result.stderr)

        result, calls = run_function(failure="pg_restore")
        self.assertNotIn("private marker", result.stderr)
        self.assertNotIn("1.15", result.stderr)
        self.assertNotIn("ci_private_db", result.stderr)

        result, calls = run_function(
            failure="pg_restore",
            restore_error="unclassified failure for ci-private-marker-777 SQL credential",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, ["pg_dump", "createdb", "pg_restore"])
        self.assertIn("diagnostic category: unknown", result.stderr)
        self.assertIn("compose pg_restore exit: 23; diagnostic capture exit: 0", result.stderr)
        for private_value in ("unclassified failure", "ci-private-marker-777", "SQL credential"):
            self.assertNotIn(private_value, result.stdout + result.stderr)

        for restore_failure, expected_statuses in (
            ("", "compose pg_restore exit: 0; diagnostic capture exit: 19"),
            ("pg_restore", "compose pg_restore exit: 23; diagnostic capture exit: 19"),
        ):
            with self.subTest(restore_failure=restore_failure, capture_exit=19):
                result, calls = run_function(failure=restore_failure, capture_exit="19")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, ["pg_dump", "createdb", "pg_restore"])
                self.assertIn("diagnostic category: unknown", result.stderr)
                self.assertIn(expected_statuses, result.stderr)
                for private_value in ("ci-private-marker-777", "ci-private-credential", "SQL credential"):
                    self.assertNotIn(private_value, result.stdout + result.stderr)

        result, calls = run_function(mode="ordinary-smoke")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside guarded GitHub-hosted rehearsal", result.stderr)
        self.assertEqual(calls, [])

        result, calls = run_function(marker_result="f")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Expected synthetic marker is missing", result.stderr)
        self.assertEqual(calls, ["pg_dump", "createdb", "pg_restore", "marker_psql"])

    def test_real_cleanup_exit_trap_removes_backup_and_scopes_compose_down(self):
        source = SCRIPT.read_text(encoding="utf-8")
        cleanup_matches = re.findall(r"(?ms)^cleanup\(\) \{\n.*?^\}", source)
        trap_matches = re.findall(r"(?m)^trap cleanup EXIT$", source)
        self.assertEqual(len(cleanup_matches), 1, "cleanup source extraction must be unambiguous")
        self.assertEqual(len(trap_matches), 1, "cleanup EXIT trap source must be unambiguous")
        cleanup_start = source.index(cleanup_matches[0])
        trap_start = source.index(trap_matches[0])
        self.assertLess(cleanup_start, trap_start, "EXIT trap must follow the extracted cleanup function")
        self.assertIn('docker compose --project-name "$project" --file "$compose_file" down --volumes --remove-orphans', cleanup_matches[0])

        cleanup_bin = self.root / "cleanup-bin"
        cleanup_bin.mkdir()
        cleanup_bin.joinpath("rm").symlink_to("/usr/bin/rm")
        harness = "\n".join(
            (
                "set -u",
                'docker() { printf "%s\\0" "$@" >> "$FAKE_DOCKER_ARGS"; }',
                cleanup_matches[0],
                trap_matches[0],
                'exit "$FAKE_EXIT_STATUS"',
            )
        )
        expected_docker_args = [
            "compose",
            "--project-name",
            "belib-cleanup-test",
            "--file",
            "compose.yaml",
            "down",
            "--volumes",
            "--remove-orphans",
        ]

        for exit_status in (0, 23):
            with self.subTest(exit_status=exit_status):
                backup_dir = self.root / f"cleanup-owned-backup-{exit_status}"
                backup_dir.mkdir()
                (backup_dir / "synthetic.dump").write_bytes(b"disposable test data")
                args_log = self.root / f"cleanup-docker-args-{exit_status}"
                args_log.unlink(missing_ok=True)
                env = {
                    "PATH": str(cleanup_bin),
                    "FAKE_DOCKER_ARGS": str(args_log),
                    "FAKE_EXIT_STATUS": str(exit_status),
                    "restore_backup_dir": str(backup_dir),
                    "project": "belib-cleanup-test",
                    "compose_file": "compose.yaml",
                }
                result = subprocess.run(
                    ["/bin/bash", "-c", harness],
                    cwd=ROOT,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, exit_status, result.stderr)
                self.assertFalse(backup_dir.exists(), "EXIT trap did not remove the owned backup directory")
                self.assertEqual(
                    args_log.read_bytes().split(b"\0")[:-1],
                    [arg.encode() for arg in expected_docker_args],
                )

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
            r"(?ms)^safe_mount_diff\(\) \{\n.*?^\}(?=\nsame_runtime_identity)", source
        )
        self.assertIsNotNone(function)
        harness = "\n".join(
            (
                "set -euo pipefail",
                function.group(0),
                "safe_mount_diff \"$1\" \"$2\"",
            )
        )
        before_mounts = (
            '[{"Type":"bind","Source":"/secret/belib/nginx.conf",'
            '"Destination":"/etc/nginx/conf.d/default.conf","Mode":"ro","RW":false},'
            '{"Type":"volume","Name":"books","Source":"/var/lib/books",'
            '"Destination":"/books","Mode":"rw","RW":true}]'
        )
        reordered_mounts = (
            '[{"Type":"volume","Name":"books","Source":"/var/lib/books",'
            '"Destination":"/books","Mode":"rw","RW":true},'
            '{"RW":false,"Mode":"ro","Destination":"/etc/nginx/conf.d/default.conf",'
            '"Source":"/secret/belib/nginx.conf","Type":"bind"}]'
        )
        before = f"a|sha256:img|project|nginx|unless-stopped|{before_mounts}"
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
        def compare_pair(before_mounts, after_mounts):
            before_record = f"a|sha256:img|project|nginx|unless-stopped|{before_mounts}"
            after_record = f"a|sha256:img|project|nginx|unless-stopped|{after_mounts}"
            return subprocess.run(
                ["bash", "-c", harness, "mount-diff", before_record, after_record],
                cwd=ROOT,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )

        def compare(after_mounts):
            return compare_pair(before_mounts, after_mounts)

        result = compare(reordered_mounts)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

        mounts = json.loads(before_mounts)
        for field, changed_value in (
            ("Source", "/secret/changed/nginx.conf"),
            ("Destination", "/etc/nginx/changed.conf"),
            ("Type", "volume"),
            ("Mode", "rw"),
            ("RW", True),
        ):
            with self.subTest(field=field):
                changed_mounts = json.loads(before_mounts)
                changed_mounts[0][field] = changed_value
                result = compare(json.dumps(changed_mounts))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"Mounts[0].{field}", result.stdout)
                self.assertNotIn("/secret", result.stdout + result.stderr)
                self.assertNotIn("nginx.conf", result.stdout + result.stderr)

        result = compare(json.dumps(mounts[:1]))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Mounts.count", result.stdout)
        result = compare(json.dumps(mounts + [mounts[0]]))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Mounts.count", result.stdout)
        for malformed_mounts in ('[{"Type":"bind"', '[{"Type":NaN}]'):
            with self.subTest(malformed_mounts=malformed_mounts):
                result = compare(malformed_mounts)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid Docker mount receipt", result.stdout)

        numeric_mounts = (
            '[{"Type":"bind","x-private-marker":1e999}]',
            '[{"Type":"bind","x-private-marker":9e999}]',
            '[{"Type":"bind","x-private-marker":3.141592653589793}]',
            '[{"Type":"bind","x-private-marker":314159265358979323846}]',
        )
        for malformed_mounts in numeric_mounts:
            with self.subTest(malformed_mounts=malformed_mounts):
                result = compare(malformed_mounts)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    result.stdout,
                    "mount field differences unavailable (invalid Docker mount receipt)\n",
                )
                self.assertEqual(result.stderr, "")
                self.assertNotIn("x-private-marker", result.stdout + result.stderr)
                for numeric_value in ("1e999", "9e999", "3.141592653589793", "314159265358979323846"):
                    self.assertNotIn(numeric_value, result.stdout + result.stderr)

        duplicate_property_cases = (
            (
                '[{"Source":"/secret/old-source"}]',
                '[{"Source":"/secret/new-source","Source":"/secret/old-source"}]',
                ("/secret/new-source", "/secret/old-source"),
            ),
            (
                '[{"Type":"bind","x-private-marker":"old-value"}]',
                '[{"Type":"bind","x-private-marker":"new-value",'
                '"x-private-marker":"old-value"}]',
                ("x-private-marker", "new-value", "old-value"),
            ),
        )
        for before_receipt, after_receipt, private_values in duplicate_property_cases:
            with self.subTest(after_receipt=after_receipt):
                result = compare_pair(before_receipt, after_receipt)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    result.stdout,
                    "mount field differences unavailable (invalid Docker mount receipt)\n",
                )
                self.assertEqual(result.stderr, "")
                for private_value in private_values:
                    self.assertNotIn(private_value, result.stdout + result.stderr)
        python_argv = python_argv_log.read_text(encoding="utf-8")
        self.assertIn("-c", python_argv)
        self.assertNotIn(before, python_argv)
        self.assertNotIn(reordered_mounts, python_argv)
        self.assertNotIn("/secret", python_argv)
        self.assertIn("runtime_identity_changed=false", source)
        self.assertIn('[[ "$runtime_identity_changed" == false \\', source)

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
