import json
import contextlib
import io
import os
import stat
import subprocess
import sys
import unittest
from types import SimpleNamespace
from email.message import Message
from unittest.mock import patch
from urllib.request import HTTPHandler, ProxyHandler, build_opener as urllib_build_opener
from urllib.response import addinfourl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import belib_runtime_watch as watch


class RuntimeWatchTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        environment = patch.dict(os.environ, {
            "XDG_RUNTIME_DIR": "/test/runtime",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/test/bus",
        })
        environment.start()
        self.addCleanup(environment.stop)

    def command(self, args, capture_output, text, timeout, check):
        self.calls.append(args)
        if args[0] == "docker" and "inspect" in args and "volume" not in args:
            name = args[-1]
            service = name.removeprefix("belib-production-").removesuffix("-1")
            health = "not_configured" if service in {"frontend", "nginx"} else "healthy"
            mount = "postgres=true" if service == "db" else ""
            book_files = "book_files=true" if service == "backend" else ""
            storage_mode = "mode=local;" if service == "backend" else ""
            return subprocess.CompletedProcess(
                args, 0,
                f"running|{health}|unless-stopped|network=attached|{mount}|{book_files}|{storage_mode}\n", ""
            )
        if args[0] == "docker" and "volume" in args and "inspect" in args:
            return subprocess.CompletedProcess(args, 0, args[-1] + "\n", "")
        if args[:3] == ["systemctl", "--user", "is-active"]:
            return subprocess.CompletedProcess(args, 0, "active\n", "")
        if args[:3] == ["systemctl", "--user", "is-enabled"]:
            return subprocess.CompletedProcess(args, 0, "enabled\n", "")
        if args[0] == "loginctl":
            return subprocess.CompletedProcess(args, 0, "yes\n", "")
        if args[0] == "journalctl":
            return subprocess.CompletedProcess(args, 0, "", "")
        self.fail(f"unexpected command: {args!r}")

    def collect(self, *, http_get=None):
        def journal_command(args, timeout=watch.TIMEOUT_SECONDS):
            self.calls.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")

        with patch.object(watch.subprocess, "run", side_effect=self.command), \
                patch.object(watch, "_run_journal", side_effect=journal_command):
            return watch.collect(15, uid=watch.APPROVED_UID, http_get=http_get or (lambda path: {
                "/health": 200,
                "/login": 200,
                "/favicon.svg": 200,
                "/api/v1/books": 401,
            }[path]))

    def test_healthy_status_and_frontend_without_healthcheck(self):
        report = self.collect()
        self.assertEqual(report["status"], "HEALTHY")
        self.assertTrue(all(
            service["production_network_attached"]
            for service in report["services"].values()
        ))
        self.assertEqual(report["services"]["frontend"]["health"], "not_configured")
        self.assertEqual(report["services"]["nginx"]["health"], "not_configured")
        self.assertTrue(report["services"]["db"]["postgres_rw_attached"])
        self.assertTrue(report["services"]["backend"]["book_files_rw_attached"])
        self.assertEqual(report["routes"]["books_401"], True)
        self.assertEqual(report["journal_counts"], {
            "duplicate_id": 0, "runtime_create": 0, "namespace_restore": 0, "truncated": False,
        })
        self.assertTrue(all(args[0] == "docker" or args[0] in {"systemctl", "loginctl", "journalctl"} for args in self.calls))
        self.assertTrue(all(watch.DOCKER_SOCKET in args for args in self.calls if args[0] == "docker"))

    def test_missing_required_network_is_alerted_for_each_service(self):
        for service in watch.SERVICES:
            for attachment in ("zero networks", "wrong network"):
                with self.subTest(service=service, attachment=attachment):
                    def inspect_without_required_network(args, **kwargs):
                        if args[0] == "docker" and "inspect" in args and args[-1] == watch.CONTAINERS[service]:
                            health = "not_configured" if service in {"frontend", "nginx"} else "healthy"
                            mount = "postgres=true" if service == "db" else ""
                            book_files = "book_files=true" if service == "backend" else ""
                            storage_mode = "mode=local;" if service == "backend" else ""
                            # The fixed inspect template maps both an empty network map and
                            # a map containing only another network to this safe enum.
                            return subprocess.CompletedProcess(
                                args, 0,
                                f"running|{health}|unless-stopped|network=missing|{mount}|{book_files}|{storage_mode}\n",
                                "",
                            )
                        return self.command(args, **kwargs)

                    with patch.object(watch.subprocess, "run", side_effect=inspect_without_required_network), \
                            patch.object(watch, "_run_journal", return_value=subprocess.CompletedProcess(
                                ["journalctl"], 0, "", ""
                            )):
                        report = watch.collect(10, uid=watch.APPROVED_UID, http_get=lambda path: (
                            401 if path == "/api/v1/books" else 200
                        ))

                    self.assertEqual(report["status"], "ALERT")
                    self.assertFalse(report["services"][service]["production_network_attached"])
                    self.assertIn(f"service_{service}_network_not_attached", report["failures"])
                    self.assertNotIn("another-network", json.dumps(report))

    def test_malformed_network_metadata_fails_closed_without_leakage(self):
        cases = (
            "network=other-network",
            "network=SECRET=never-print-this",
            "",
        )
        for network_field in cases:
            with self.subTest(network_field=network_field):
                output = io.StringIO()
                with patch.object(watch.os, "getuid", return_value=watch.APPROVED_UID), \
                        patch.object(watch, "_run", return_value=subprocess.CompletedProcess(
                            ["docker"], 0,
                            f"running|healthy|unless-stopped|{network_field}|||mode=local;\n", ""
                        )), contextlib.redirect_stdout(output):
                    code = watch.main(["--window-minutes", "5"])

                self.assertEqual(code, 2)
                report = json.loads(output.getvalue())
                self.assertEqual(report, {"status": "ALERT", "failures": ["collection_failed"]})
                self.assertNotIn("network", output.getvalue())
                self.assertNotIn("SECRET", output.getvalue())
                self.assertNotIn("never-print-this", output.getvalue())

    def test_backend_inspect_template_checks_exact_book_files_mount(self):
        with patch.object(watch, "_run", return_value=subprocess.CompletedProcess(
            ["docker"], 0, "running|healthy|unless-stopped|network=attached||book_files=true|mode=local;\n", ""
        )) as run:
            result = watch._inspect_service("backend")

        template = run.call_args.args[0][5]
        self.assertIn('eq (index . "Name") "belib-production_postgres-data"', template)
        self.assertIn('eq (index . "Name") "belib-production_book-files"', template)
        self.assertIn('index .NetworkSettings.Networks "belib-production_default"', template)
        self.assertIn("network=attached", template)
        self.assertIn("network=missing", template)
        self.assertIn('eq (index . "Destination") "/var/lib/belib/books"', template)
        self.assertIn("book_files={{.RW}}", template)
        self.assertIn('split . "="', template)
        self.assertIn('eq (index $parts 0) "BOOK_FILE_STORAGE_DRIVER"', template)
        self.assertIn('eq (index $parts 1) "local"', template)
        self.assertIn('eq (index $parts 1) "r2"', template)
        self.assertIn("mode=invalid;", template)
        self.assertNotIn("{{.Config.Env}}", template)
        self.assertTrue(result["book_files_rw_attached"])
        self.assertTrue(result["production_network_attached"])
        self.assertEqual(result["storage_mode"], "local")

    def test_nginx_bind_mount_without_name_does_not_mark_container_missing(self):
        with patch.object(watch, "_run", return_value=subprocess.CompletedProcess(
            ["docker"], 0, "running|not_configured|unless-stopped|network=attached|||\n", ""
        )) as run:
            result = watch._inspect_service("nginx")

        template = run.call_args.args[0][5]
        self.assertIn('index . "Name"', template)
        self.assertIn('index . "Destination"', template)
        self.assertTrue(result["exists"])
        self.assertTrue(result["running"])

    def test_optional_health_lookup_handles_services_without_healthcheck(self):
        with patch.object(watch, "_run", return_value=subprocess.CompletedProcess(
            ["docker"], 0, "running|not_configured|unless-stopped|network=attached|||\n", ""
        )) as run:
            result = watch._inspect_service("frontend")

        template = run.call_args.args[0][5]
        self.assertIn('index .State "Health"', template)
        self.assertEqual(result["health"], "not_configured")

    def test_service_inspect_accepts_only_exact_not_found_for_expected_container(self):
        for message in (
            f"Error: No such object: {watch.CONTAINERS['backend']}\n",
            f"Error response from daemon: No such container: {watch.CONTAINERS['backend']}\n",
        ):
            with self.subTest(message=message):
                with patch.object(watch, "_run", return_value=subprocess.CompletedProcess(
                    ["docker"], 1, "", message
                )):
                    result = watch._inspect_service("backend")
                self.assertEqual(result, {
                    "exists": False, "running": False,
                    "health": "unknown", "restart": "unknown",
                })

    def test_service_inspect_permission_and_daemon_failures_are_unavailable(self):
        cases = (
            "permission denied: secret /private/docker.sock\n",
            "Cannot connect to the Docker daemon at /private/docker.sock\n",
            f"Error: No such object: {watch.CONTAINERS['frontend']}-unexpected\n",
            f"Error: No such object: {watch.CONTAINERS['backend']}\nextra diagnostic\n",
        )
        for stderr in cases:
            with self.subTest(stderr=stderr):
                with patch.object(watch, "_run", return_value=subprocess.CompletedProcess(
                    ["docker"], 1, "", stderr
                )):
                    with self.assertRaises(watch.WatchError) as error:
                        watch._inspect_service("backend")
                self.assertEqual(str(error.exception), "docker_inspect_failed")

    def test_service_inspect_malformed_success_and_conflicting_stderr_fail_closed(self):
        cases = (
            subprocess.CompletedProcess(["docker"], 0, "secret host path|running", ""),
            subprocess.CompletedProcess(
                ["docker"], 0,
                "running|healthy|unless-stopped|network=attached|||mode=local;\n", "private warning\n",
            ),
        )
        for result in cases:
            with self.subTest(stderr=bool(result.stderr), stdout=result.stdout):
                with patch.object(watch, "_run", return_value=result):
                    with self.assertRaises(watch.WatchError) as error:
                        watch._inspect_service("backend")
                self.assertEqual(str(error.exception), "docker_output_invalid")

    def test_runtime_warning_does_not_become_health_outage(self):
        warning_output = (
            "container ID already in use\n"
            "container with given ID already exists\n"
            "OCI runtime create failed\n"
            "failed to restore network namespace\n"
            "libnetwork: restoring thread network namespace failed\n"
            "failed to restore thread's network namespace: operation not permitted\n"
            "container with given ID already exists; OCI runtime create failed\n"
            "unrelated SECRET=never-print-this\n"
        )

        def command_with_warning(args, **kwargs):
            return self.command(args, **kwargs)

        def journal_with_warning(args, timeout=watch.TIMEOUT_SECONDS):
            self.calls.append(args)
            return subprocess.CompletedProcess(args, 0, warning_output, "")

        with patch.object(watch.subprocess, "run", side_effect=command_with_warning), \
                patch.object(watch, "_run_journal", side_effect=journal_with_warning):
            report = watch.collect(15, uid=watch.APPROVED_UID, http_get=lambda path: {
                "/health": 200, "/login": 200, "/favicon.svg": 200, "/api/v1/books": 401,
            }[path])
        self.assertEqual(report["status"], "HEALTHY_WITH_RUNTIME_WARNINGS")
        self.assertEqual(report["journal_counts"], {
            "duplicate_id": 3, "runtime_create": 2, "namespace_restore": 3, "truncated": False,
        })
        self.assertNotIn("SECRET", json.dumps(report))
        self.assertNotIn("container ID", json.dumps(report))

    def test_journalctl_empty_grep_result_is_normal_no_events(self):
        args = ["journalctl", "--user", "-u", "docker.service", "--grep=pattern"]
        with patch.object(watch, "_run_journal", return_value=subprocess.CompletedProcess(args, 1, "", "")):
            counts = watch._journal_counts(2)
        self.assertEqual(counts, {
            "duplicate_id": 0, "runtime_create": 0, "namespace_restore": 0, "truncated": False,
        })

    def test_journalctl_errors_fail_closed_without_reflecting_private_output(self):
        args = ["journalctl", "--user", "-u", "docker.service", "--grep=pattern"]
        abnormal_results = (
            subprocess.CompletedProcess(args, 1, "private log line", ""),
            subprocess.CompletedProcess(args, 1, "", "private journal error"),
            subprocess.CompletedProcess(args, 0, "", "private warning"),
            subprocess.CompletedProcess(args, 2, "", ""),
        )
        for result in abnormal_results:
            with self.subTest(returncode=result.returncode, stdout=bool(result.stdout), stderr=bool(result.stderr)):
                with patch.object(watch, "_run_journal", return_value=result):
                    with self.assertRaises(watch.WatchError) as error:
                        watch._journal_counts(2)
                self.assertEqual(str(error.exception), "journal_unavailable")

    def test_health_route_failure_is_alert_with_allowlisted_reason(self):
        report = self.collect(http_get=lambda path: 503 if path == "/health" else {
            "/login": 200, "/favicon.svg": 200, "/api/v1/books": 401,
        }[path])
        self.assertEqual(report["status"], "ALERT")
        self.assertIn("route_health_200", report["failures"])

    def test_inactive_cd_timer_is_reported_as_scoped_alert(self):
        def inactive_timer(args, **kwargs):
            if args[:3] == ["systemctl", "--user", "is-active"] and args[-1] == "belib-production-cd.timer":
                return subprocess.CompletedProcess(args, 3, "inactive\n", "")
            return self.command(args, **kwargs)

        with patch.object(watch.subprocess, "run", side_effect=inactive_timer):
            report = watch.collect(15, uid=watch.APPROVED_UID, http_get=lambda path: {
                "/health": 200, "/login": 200, "/favicon.svg": 200, "/api/v1/books": 401,
            }[path])
        self.assertEqual(report["status"], "ALERT")
        self.assertFalse(report["cd_timer"]["active"])
        self.assertIn("cd_timer_inactive", report["failures"])

    def test_disabled_user_units_are_reported_as_scoped_alerts(self):
        def disabled_units(args, **kwargs):
            if args[:3] == ["systemctl", "--user", "is-enabled"] and args[-1] in {
                "docker.service", "belib-production-cd.timer",
            }:
                return subprocess.CompletedProcess(args, 1, "disabled\n", "")
            return self.command(args, **kwargs)

        with patch.object(watch.subprocess, "run", side_effect=disabled_units):
            report = watch.collect(15, uid=watch.APPROVED_UID, http_get=lambda path: {
                "/health": 200, "/login": 200, "/favicon.svg": 200, "/api/v1/books": 401,
            }[path])
        self.assertEqual(report["status"], "ALERT")
        self.assertFalse(report["docker_service"]["enabled"])
        self.assertFalse(report["cd_timer"]["enabled"])
        self.assertIn("docker_service_disabled", report["failures"])
        self.assertIn("cd_timer_disabled", report["failures"])

    def test_systemctl_status_and_return_code_must_agree(self):
        cases = (
            (["systemctl", "--user", "is-active", "docker.service"], 3, "active\n"),
            (["systemctl", "--user", "is-enabled", "docker.service"], 1, "enabled\n"),
            (["systemctl", "--user", "is-active", "docker.service"], 4, "failed\n"),
            (["systemctl", "--user", "is-enabled", "docker.service"], 5, "unknown\n"),
        )
        for args, returncode, stdout in cases:
            with self.subTest(args=args, returncode=returncode, stdout=stdout.strip()):
                with patch.object(
                    watch, "_run",
                    return_value=subprocess.CompletedProcess(args, returncode, stdout, ""),
                ):
                    with self.assertRaises(watch.WatchError):
                        watch._command_status(args, allowed={
                            "active", "inactive", "failed", "enabled", "disabled",
                        })

    def test_systemctl_uses_fixed_environment_for_verified_approved_user_bus(self):
        bus_stat = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=watch.APPROVED_UID)
        captured = {}

        def run(args, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(args, 0, "active\n", "")

        with patch.dict(os.environ, {}, clear=True), \
                patch.object(watch.os, "getuid", return_value=watch.APPROVED_UID), \
                patch.object(watch.os, "lstat", return_value=bus_stat) as stat_call, \
                patch.object(watch.subprocess, "run", side_effect=run):
            self.assertTrue(watch._unit_active("docker.service"))

        stat_call.assert_called_once_with(watch.USER_BUS_SOCKET)
        self.assertEqual(captured["env"]["XDG_RUNTIME_DIR"], f"/run/user/{watch.APPROVED_UID}")
        self.assertEqual(
            captured["env"]["DBUS_SESSION_BUS_ADDRESS"],
            f"unix:path={watch.USER_BUS_SOCKET}",
        )

    def test_systemctl_bus_fallback_fails_closed_for_missing_or_wrong_socket(self):
        cases = (
            FileNotFoundError("private path"),
            SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=watch.APPROVED_UID - 1),
            SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=watch.APPROVED_UID),
        )
        for socket_stat in cases:
            with self.subTest(socket=type(socket_stat).__name__), \
                    patch.dict(os.environ, {}, clear=True), \
                    patch.object(watch.os, "getuid", return_value=watch.APPROVED_UID), \
                    patch.object(
                        watch.os,
                        "lstat",
                        side_effect=socket_stat if isinstance(socket_stat, Exception) else None,
                        return_value=None if isinstance(socket_stat, Exception) else socket_stat,
                    ), \
                    patch.object(watch.subprocess, "run") as run:
                with self.assertRaises(watch.WatchError) as error:
                    watch._unit_active("docker.service")
                self.assertEqual(str(error.exception), "systemctl_unavailable")
                run.assert_not_called()

    def test_systemctl_does_not_override_explicit_bus_environment(self):
        with patch.dict(os.environ, {
            "XDG_RUNTIME_DIR": "/explicit/runtime",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/explicit/bus",
        }), patch.object(watch.os, "lstat") as stat_call:
            self.assertIsNone(watch._systemctl_environment())
        stat_call.assert_not_called()

    def test_systemctl_bus_fallback_rejects_symlink_to_owned_socket(self):
        symlink_stat = SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=watch.APPROVED_UID)
        target_socket_stat = SimpleNamespace(
            st_mode=stat.S_IFSOCK | 0o600, st_uid=watch.APPROVED_UID
        )
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(watch.os, "getuid", return_value=watch.APPROVED_UID), \
                patch.object(watch.os, "lstat", return_value=symlink_stat) as lstat_call, \
                patch.object(watch.os, "stat", return_value=target_socket_stat) as stat_call, \
                patch.object(watch.subprocess, "run") as run:
            with self.assertRaises(watch.WatchError) as error:
                watch._unit_active("docker.service")

        self.assertEqual(str(error.exception), "systemctl_unavailable")
        lstat_call.assert_called_once_with(watch.USER_BUS_SOCKET)
        stat_call.assert_not_called()
        run.assert_not_called()

    def test_systemctl_bus_fallback_rejects_unapproved_process_uid(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(watch.os, "getuid", return_value=watch.APPROVED_UID - 1), \
                patch.object(watch.os, "lstat") as stat_call, \
                patch.object(watch.subprocess, "run") as run:
            with self.assertRaises(watch.WatchError) as error:
                watch._unit_active("docker.service")
        self.assertEqual(str(error.exception), "systemctl_unavailable")
        stat_call.assert_not_called()
        run.assert_not_called()

    def test_systemctl_access_denied_is_not_retried(self):
        bus_stat = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=watch.APPROVED_UID)
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(watch.os, "getuid", return_value=watch.APPROVED_UID), \
                patch.object(watch.os, "lstat", return_value=bus_stat), \
                patch.object(watch.subprocess, "run", side_effect=PermissionError("private detail")) as run:
            with self.assertRaises(watch.WatchError) as error:
                watch._unit_active("docker.service")
        self.assertEqual(str(error.exception), "command_failed")
        run.assert_called_once()

    def test_http_status_disables_environment_proxies(self):
        proxy_env = {
            "http_proxy": "http://proxy.invalid:3128",
            "HTTP_PROXY": "http://proxy.invalid:3128",
            "all_proxy": "http://proxy.invalid:3128",
            "ALL_PROXY": "http://proxy.invalid:3128",
        }
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.status = 200
        opener = unittest.mock.Mock()
        opener.open.return_value = response
        with patch.dict(os.environ, proxy_env), patch.object(
            watch, "build_opener", return_value=opener
        ) as build_opener:
            self.assertEqual(watch._http_status("/health"), 200)
        build_opener.assert_called_once()
        handlers = build_opener.call_args.args
        proxy_handlers = [handler for handler in handlers if isinstance(handler, ProxyHandler)]
        self.assertEqual(len(proxy_handlers), 1)
        self.assertEqual(proxy_handlers[0].proxies, {})
        self.assertIn(watch._NoRedirect, handlers)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, f"{watch.BASE_URL}/health")

    def test_http_status_does_not_follow_redirect_or_use_environment_proxy(self):
        proxy_env = {
            "http_proxy": "http://proxy.invalid:3128",
            "https_proxy": "http://proxy.invalid:3128",
            "all_proxy": "http://proxy.invalid:3128",
            "HTTP_PROXY": "http://proxy.invalid:3128",
            "HTTPS_PROXY": "http://proxy.invalid:3128",
            "ALL_PROXY": "http://proxy.invalid:3128",
        }
        endpoint = "http://127.0.0.1:8765/health"
        redirect_destination = "https://example.invalid/evil"
        requested_urls = []
        proxy_open_calls = []
        proxy_handlers = []

        class SyntheticRedirectHTTPHandler(HTTPHandler):
            def http_open(self, request):
                requested_urls.append(request.full_url)
                headers = Message()
                headers["Location"] = redirect_destination
                response = addinfourl(io.BytesIO(), headers, request.full_url, code=302)
                response.msg = "Found"
                return response

        class RecordingProxyHandler(ProxyHandler):
            def __init__(self, proxies=None):
                super().__init__(proxies)
                proxy_handlers.append(self)

            def proxy_open(self, request, proxy, proxy_type):
                proxy_open_calls.append((request.full_url, proxy, proxy_type))
                return super().proxy_open(request, proxy, proxy_type)

        def build_offline_opener(*handlers):
            return urllib_build_opener(*handlers, SyntheticRedirectHTTPHandler())

        with patch.dict(os.environ, proxy_env), \
                patch.object(watch, "BASE_URL", "http://127.0.0.1:8765"), \
                patch.object(watch, "ProxyHandler", RecordingProxyHandler), \
                patch.object(watch, "build_opener", side_effect=build_offline_opener):
            status = watch._http_status("/health")

        self.assertEqual(status, 302)
        self.assertEqual(requested_urls, [endpoint])
        self.assertNotIn(redirect_destination, requested_urls)
        self.assertEqual(len(proxy_handlers), 1)
        self.assertEqual(proxy_handlers[0].proxies, {})
        self.assertEqual(proxy_open_calls, [])

    def test_missing_service_and_volume_are_alerts(self):
        def failing(args, **kwargs):
            if args[0] == "docker" and "volume" not in args and args[-1] == "belib-production-backend-1":
                return subprocess.CompletedProcess(
                    args, 1, "", f"Error: No such object: {watch.CONTAINERS['backend']}\n"
                )
            if args[0] == "docker" and "volume" in args and args[-1] == watch.VOLUMES["book_files"]:
                return subprocess.CompletedProcess(
                    args, 1, "",
                    f"Error response from daemon: get {watch.VOLUMES['book_files']}: no such volume",
                )
            return self.command(args, **kwargs)

        with patch.object(watch.subprocess, "run", side_effect=failing):
            report = watch.collect(10, uid=watch.APPROVED_UID, http_get=lambda path: {
                "/health": 200, "/login": 200, "/favicon.svg": 200, "/api/v1/books": 401,
            }[path])
        self.assertEqual(report["status"], "ALERT")
        serialized = json.dumps(report)
        self.assertNotIn("private", serialized)
        self.assertIn("service_backend_missing", report["failures"])
        self.assertIn("volume_book_files_missing", report["failures"])

    def test_backend_book_files_mount_must_match_compose_name_target_and_rw(self):
        cases = (
            ("missing", ""),
            ("wrong destination", ""),
            ("read only", "book_files=false"),
        )
        for label, mount in cases:
            with self.subTest(mount=label):
                def inspect_with_mount(args, **kwargs):
                    if args[0] == "docker" and "inspect" in args and args[-1] == watch.CONTAINERS["backend"]:
                        return subprocess.CompletedProcess(
                            args, 0,
                            f"running|healthy|unless-stopped|network=attached||{mount}|mode=local;\n", ""
                        )
                    return self.command(args, **kwargs)

                with patch.object(watch.subprocess, "run", side_effect=inspect_with_mount), \
                        patch.object(watch, "_run_journal", return_value=subprocess.CompletedProcess(
                            ["journalctl"], 0, "", ""
                        )):
                    report = watch.collect(10, uid=watch.APPROVED_UID, http_get=lambda _path: 200)

                self.assertEqual(report["status"], "ALERT")
                self.assertFalse(report["services"]["backend"]["book_files_rw_attached"])
                self.assertIn("book_files_volume_not_rw_attached", report["failures"])

    def test_r2_backend_without_local_mount_is_healthy(self):
        def r2_without_mount(args, **kwargs):
            if args[0] == "docker" and "inspect" in args and args[-1] == watch.CONTAINERS["backend"]:
                return subprocess.CompletedProcess(
                    args, 0, "running|healthy|unless-stopped|network=attached|||mode=r2;\n", ""
                )
            return self.command(args, **kwargs)

        with patch.object(watch.subprocess, "run", side_effect=r2_without_mount), \
                patch.object(watch, "_run_journal", return_value=subprocess.CompletedProcess(
                    ["journalctl"], 0, "", ""
                )):
            report = watch.collect(10, uid=watch.APPROVED_UID, http_get=lambda path: (
                401 if path == "/api/v1/books" else 200
            ))

        self.assertEqual(report["status"], "HEALTHY")
        self.assertEqual(report["services"]["backend"]["storage_mode"], "r2")
        self.assertFalse(report["services"]["backend"]["book_files_rw_attached"])
        self.assertNotIn("book_files_volume_not_rw_attached", report["failures"])

    def test_r2_backend_without_unused_book_files_volume_is_healthy(self):
        def r2_without_volume(args, **kwargs):
            if args[0] == "docker" and "inspect" in args and args[-1] == watch.CONTAINERS["backend"]:
                return subprocess.CompletedProcess(
                    args, 0, "running|healthy|unless-stopped|network=attached|||mode=r2;\n", ""
                )
            if args[0] == "docker" and "volume" in args and args[-1] == watch.VOLUMES["book_files"]:
                return subprocess.CompletedProcess(
                    args, 1, "",
                    f"Error response from daemon: get {watch.VOLUMES['book_files']}: no such volume",
                )
            return self.command(args, **kwargs)

        with patch.object(watch.subprocess, "run", side_effect=r2_without_volume), \
                patch.object(watch, "_run_journal", return_value=subprocess.CompletedProcess(
                    ["journalctl"], 0, "", ""
                )):
            report = watch.collect(10, uid=watch.APPROVED_UID, http_get=lambda path: (
                401 if path == "/api/v1/books" else 200
            ))

        self.assertEqual(report["status"], "HEALTHY")
        self.assertFalse(report["volumes"]["book_files"])
        self.assertNotIn("volume_book_files_missing", report["failures"])

    def test_r2_missing_postgres_volume_is_always_alert(self):
        def r2_without_postgres_volume(args, **kwargs):
            if args[0] == "docker" and "inspect" in args and args[-1] == watch.CONTAINERS["backend"]:
                return subprocess.CompletedProcess(
                    args, 0, "running|healthy|unless-stopped|network=attached|||mode=r2;\n", ""
                )
            if args[0] == "docker" and "volume" in args and args[-1] == watch.VOLUMES["postgres"]:
                return subprocess.CompletedProcess(
                    args, 1, "",
                    f"Error response from daemon: get {watch.VOLUMES['postgres']}: no such volume",
                )
            return self.command(args, **kwargs)

        with patch.object(watch.subprocess, "run", side_effect=r2_without_postgres_volume), \
                patch.object(watch, "_run_journal", return_value=subprocess.CompletedProcess(
                    ["journalctl"], 0, "", ""
                )):
            report = watch.collect(10, uid=watch.APPROVED_UID, http_get=lambda path: (
                401 if path == "/api/v1/books" else 200
            ))

        self.assertEqual(report["status"], "ALERT")
        self.assertIn("volume_postgres_missing", report["failures"])
        self.assertNotIn("volume_book_files_missing", report["failures"])

    def test_volume_inspect_unknown_failures_fail_closed_without_output_leak(self):
        cases = (
            ("permission denied", "Error response from daemon: permission denied: secret /private/socket"),
            ("daemon unavailable", "Cannot connect to the Docker daemon at /private/docker.sock"),
            ("ambiguous empty failure", ""),
            ("wrong volume name", "Error: No such volume: another-volume"),
            (
                "wrong case volume name",
                f"Error: No such volume: {watch.VOLUMES['book_files'].upper()}",
            ),
            (
                "absence with extra output",
                f"Error response from daemon: get {watch.VOLUMES['book_files']}: no such volume\nsecret detail",
            ),
        )
        for label, stderr in cases:
            with self.subTest(failure=label):
                def failed_volume_inspect(args, **kwargs):
                    if args[0] == "docker" and "volume" in args and args[-1] == watch.VOLUMES["book_files"]:
                        return subprocess.CompletedProcess(args, 1, "", stderr)
                    return self.command(args, **kwargs)

                output = io.StringIO()
                with patch.object(watch.os, "getuid", return_value=watch.APPROVED_UID), \
                        patch.object(watch.subprocess, "run", side_effect=failed_volume_inspect), \
                        patch.object(watch, "_run_journal", return_value=subprocess.CompletedProcess(
                            ["journalctl"], 0, "", ""
                        )), \
                        patch.object(watch, "_http_status", side_effect=lambda path: (
                            401 if path == "/api/v1/books" else 200
                        )), \
                        contextlib.redirect_stdout(output):
                    code = watch.main(["--window-minutes", "10"])

                self.assertEqual(code, 2)
                report = json.loads(output.getvalue())
                self.assertEqual(report, {"status": "ALERT", "failures": ["collection_failed"]})
                self.assertNotIn("secret", output.getvalue())
                self.assertNotIn("private", output.getvalue())

    def test_backend_storage_mode_absent_invalid_or_ambiguous_fails_closed(self):
        cases = (
            "",
            "mode=invalid;",
            "mode=r2;mode=local;",
            "mode=local;mode=local;",
            "mode=local;mode=invalid;",
        )
        for storage_mode in cases:
            with self.subTest(storage_mode=storage_mode):
                def backend_mode(args, **kwargs):
                    if args[0] == "docker" and "inspect" in args and args[-1] == watch.CONTAINERS["backend"]:
                        return subprocess.CompletedProcess(
                            args, 0,
                            f"running|healthy|unless-stopped|network=attached||book_files=true|{storage_mode}\n", ""
                        )
                    return self.command(args, **kwargs)

                with patch.object(watch.subprocess, "run", side_effect=backend_mode), \
                        patch.object(watch, "_run_journal", return_value=subprocess.CompletedProcess(
                            ["journalctl"], 0, "", ""
                        )):
                    report = watch.collect(10, uid=watch.APPROVED_UID, http_get=lambda _path: 200)

                self.assertEqual(report["status"], "ALERT")
                self.assertIn("backend_storage_mode_invalid", report["failures"])

    def test_backend_inspect_failure_fails_closed_for_book_files_attachment(self):
        def backend_inspect_failure(args, **kwargs):
            if args[0] == "docker" and "inspect" in args and args[-1] == watch.CONTAINERS["backend"]:
                return subprocess.CompletedProcess(args, 1, "", "private docker error")
            return self.command(args, **kwargs)

        with patch.object(watch.subprocess, "run", side_effect=backend_inspect_failure):
            with self.assertRaises(watch.WatchError) as error:
                watch.collect(10, uid=watch.APPROVED_UID, http_get=lambda _path: 200)
        self.assertEqual(str(error.exception), "docker_inspect_failed")

    def test_service_inspect_failure_stderr_is_not_reflected_in_final_json(self):
        private = "permission denied: SECRET=/private/docker.sock"
        output = io.StringIO()
        with patch.object(watch.os, "getuid", return_value=watch.APPROVED_UID), \
                patch.object(watch, "_run", return_value=subprocess.CompletedProcess(
                    ["docker"], 1, "", private
                )), contextlib.redirect_stdout(output):
            code = watch.main(["--window-minutes", "5"])

        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue()), {
            "status": "ALERT", "failures": ["collection_failed"],
        })
        self.assertNotIn("SECRET", output.getvalue())
        self.assertNotIn("private", output.getvalue())

    def test_malformed_docker_output_fails_closed_without_reflection(self):
        def malformed(args, **kwargs):
            if args[0] == "docker" and "volume" not in args and args[-1] == "belib-production-db-1":
                return subprocess.CompletedProcess(args, 0, "secret host path|running", "")
            return self.command(args, **kwargs)

        with patch.object(watch.subprocess, "run", side_effect=malformed):
            with self.assertRaises(watch.WatchError):
                watch.collect(5, uid=watch.APPROVED_UID, http_get=lambda _path: 200)

    def test_timeout_is_bounded_and_reported_as_sanitized_failure(self):
        with patch.object(watch.subprocess, "run", side_effect=subprocess.TimeoutExpired("docker", 3)):
            with self.assertRaises(watch.WatchError) as error:
                watch._run(["docker", "inspect", "fixed-name"])
        self.assertEqual(str(error.exception), "command_failed")

    def test_cli_emits_only_generic_failure_for_command_exception(self):
        output = io.StringIO()
        with patch.object(watch.os, "getuid", return_value=watch.APPROVED_UID), \
                patch.object(watch, "_run", side_effect=watch.WatchError("secret path /private")), \
                contextlib.redirect_stdout(output):
            code = watch.main(["--window-minutes", "5"])
        self.assertEqual(code, 2)
        report = json.loads(output.getvalue())
        self.assertEqual(report, {"status": "ALERT", "failures": ["collection_failed"]})
        self.assertNotIn("private", output.getvalue())

    def test_unapproved_uid_is_rejected_before_commands(self):
        with patch.object(watch.subprocess, "run") as run:
            with self.assertRaises(watch.WatchError) as error:
                watch.collect(5, uid=1000)
        self.assertEqual(str(error.exception), "caller_not_approved")
        run.assert_not_called()

    def test_journal_window_and_unit_queries_are_fixed_and_bounded(self):
        self.collect()
        journal = next(args for args in self.calls if args[0] == "journalctl")
        self.assertEqual(journal[:4], ["journalctl", "--user", "-u", "docker.service"])
        self.assertIn("15 minutes ago", journal)
        self.assertIn("--lines=1000", journal)
        self.assertTrue(any(arg.startswith("--grep=") for arg in journal))
        self.assertNotIn("--all", journal)

    def test_oversized_journal_output_is_bounded_reaped_and_fails_closed(self):
        read_sizes = []
        real_read = os.read

        class FakeProcess:
            def __init__(self):
                stdout_read, stdout_write = os.pipe()
                stderr_read, stderr_write = os.pipe()
                os.write(stdout_write, b"x" * 2048)
                os.close(stdout_write)
                os.close(stderr_write)
                self.stdout = os.fdopen(stdout_read, "rb", buffering=0)
                self.stderr = os.fdopen(stderr_read, "rb", buffering=0)
                self.returncode = None
                self.terminated = False
                self.waited = False
                self.wait_timeouts = []
                self.killed = False

            def poll(self):
                return self.returncode

            def terminate(self):
                self.terminated = True
                self.returncode = -15

            def wait(self, timeout=None):
                self.waited = True
                self.wait_timeouts.append(timeout)
                return self.returncode

            def kill(self):
                self.killed = True
                self.returncode = -9

        fake_process = FakeProcess()

        def tracked_read(fd, size):
            read_sizes.append(size)
            return real_read(fd, size)

        with patch.object(watch, "MAX_JOURNAL_BYTES", 128), \
                patch.object(watch.subprocess, "Popen", return_value=fake_process) as popen, \
                patch.object(watch.os, "read", side_effect=tracked_read):
            with self.assertRaises(watch.WatchError) as error:
                watch._journal_counts(15)

        self.assertEqual(str(error.exception), "journal_unavailable")
        self.assertTrue(read_sizes)
        self.assertLessEqual(max(read_sizes), 129)
        self.assertTrue(fake_process.terminated)
        self.assertTrue(fake_process.waited)
        self.assertEqual(fake_process.wait_timeouts, [watch.JOURNAL_TERMINATE_GRACE_SECONDS])
        self.assertFalse(fake_process.killed)
        popen.assert_called_once()
        self.assertFalse(popen.call_args.kwargs.get("shell", False))

    def test_journal_cleanup_bounds_wait_after_kill_for_stubborn_child(self):
        stdout_read, stdout_write = os.pipe()
        stderr_read, stderr_write = os.pipe()
        os.write(stdout_write, b"x" * 2048)
        os.close(stdout_write)
        os.close(stderr_write)

        class StubbornProcess:
            def __init__(self):
                self.stdout = os.fdopen(stdout_read, "rb", buffering=0)
                self.stderr = os.fdopen(stderr_read, "rb", buffering=0)
                self.wait_timeouts = []
                self.killed = False

            def poll(self):
                return None

            def terminate(self):
                pass

            def kill(self):
                self.killed = True

            def wait(self, timeout=None):
                self.wait_timeouts.append(timeout)
                raise subprocess.TimeoutExpired("journalctl", timeout)

        stubborn = StubbornProcess()
        with patch.object(watch, "MAX_JOURNAL_BYTES", 128), \
                patch.object(watch.subprocess, "Popen", return_value=stubborn):
            with self.assertRaises(watch.WatchError) as error:
                watch._journal_counts(15)

        self.assertEqual(str(error.exception), "journal_unavailable")
        self.assertTrue(stubborn.killed)
        self.assertEqual(stubborn.wait_timeouts, [
            watch.JOURNAL_TERMINATE_GRACE_SECONDS,
            watch.JOURNAL_KILL_REAP_SECONDS,
        ])


if __name__ == "__main__":
    unittest.main()
