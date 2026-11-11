#!/usr/bin/env python3
"""Read-only, allowlisted BeLib runtime watch candidate.

This script collects a small set of live status fields when explicitly run by
the approved rootless service owner. It never changes Docker or systemd state.
"""

import argparse
import json
import os
import re
import selectors
import stat
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

APPROVED_UID = 1002
PROJECT = "belib-production"
SERVICES = ("db", "backend", "frontend", "nginx")
CONTAINERS = {name: f"{PROJECT}-{name}-1" for name in SERVICES}
VOLUMES = {
    "postgres": f"{PROJECT}_postgres-data",
    "book_files": f"{PROJECT}_book-files",
}
BASE_URL = "http://127.0.0.1:8080"
DOCKER_SOCKET = f"unix:///run/user/{APPROVED_UID}/docker.sock"
USER_BUS_SOCKET = f"/run/user/{APPROVED_UID}/bus"
TIMEOUT_SECONDS = 3
JOURNAL_TERMINATE_GRACE_SECONDS = 0.2
JOURNAL_KILL_REAP_SECONDS = 0.2
# Journal collection has a three-second deadline, followed by at most these
# two bounded cleanup waits (3.4 seconds total, excluding scheduler/OS delays).
MAX_JOURNAL_LINES = 1000
MAX_JOURNAL_BYTES = 1024 * 1024
EVENT_PATTERNS = (
    "container ID already in use",
    "container with given ID already exists",
    "OCI runtime create failed",
    "failed to restore network namespace",
    "libnetwork: restoring thread network namespace failed",
    "failed to restore thread's network namespace: operation not permitted",
)


class WatchError(Exception):
    """A bounded collection operation failed or returned malformed data."""


def _stop_and_reap(process):
    """Stop a lingering child, and fail closed if bounded reaping fails."""
    try:
        returncode = process.poll()
    except OSError as exc:
        raise WatchError("journal_unavailable") from exc
    if returncode is not None:
        return
    try:
        process.terminate()
    except OSError:
        pass
    try:
        process.wait(timeout=JOURNAL_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=JOURNAL_KILL_REAP_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise WatchError("journal_unavailable") from exc
        except OSError as exc:
            raise WatchError("journal_unavailable") from exc
    except OSError as exc:
        raise WatchError("journal_unavailable") from exc
    try:
        returncode = process.poll()
    except OSError as exc:
        raise WatchError("journal_unavailable") from exc
    if returncode is None:
        raise WatchError("journal_unavailable")


def _run(args, timeout=TIMEOUT_SECONDS, *, env=None):
    """Run one fixed argument-array command and discard stderr content."""
    try:
        options = {"capture_output": True, "text": True, "timeout": timeout, "check": False}
        if env is not None:
            options["env"] = env
        return subprocess.run(args, **options)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WatchError("command_failed") from exc


def _run_journal(args, timeout=TIMEOUT_SECONDS):
    """Capture journal output under a strict combined byte and time limit."""
    process = None
    selector = selectors.DefaultSelector()
    streams = {}
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            streams[name] = stream
            selector.register(stream, selectors.EVENT_READ, name)

        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WatchError("journal_unavailable")
            events = selector.select(remaining)
            if not events:
                raise WatchError("journal_unavailable")
            for key, _ in events:
                name = key.data
                total = len(captured["stdout"]) + len(captured["stderr"])
                room = MAX_JOURNAL_BYTES - total
                # Read at most one byte beyond the cap to detect overflow.
                chunk = os.read(key.fileobj.fileno(), min(65536, room + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                if len(chunk) > room:
                    raise WatchError("journal_unavailable")
                captured[name].extend(chunk)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WatchError("journal_unavailable")
        returncode = process.wait(timeout=remaining)
        stdout = captured["stdout"].decode("utf-8")
        stderr = captured["stderr"].decode("utf-8")
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)
    except WatchError:
        raise
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError) as exc:
        raise WatchError("journal_unavailable") from exc
    finally:
        selector.close()
        for stream in streams.values():
            if not stream.closed:
                stream.close()
        if process is not None:
            _stop_and_reap(process)


def _command_text(args, *, allowed):
    result = _run(args)
    if result.returncode != 0:
        raise WatchError("command_failed")
    value = result.stdout.strip()
    if value not in allowed:
        raise WatchError("command_output_invalid")
    return value


def _command_status(args, *, allowed, use_systemctl_env=False):
    result = _run_systemctl(args) if use_systemctl_env else _run(args)
    value = result.stdout.strip()
    if value not in allowed:
        raise WatchError("command_output_invalid" if result.returncode == 0 else "command_failed")
    if args[:3] == ["systemctl", "--user", "is-active"]:
        expected_codes = {"active": {0}, "inactive": {3}}
    elif args[:3] == ["systemctl", "--user", "is-enabled"]:
        expected_codes = {"enabled": {0}, "disabled": {1}}
    else:
        raise WatchError("command_status_invalid")
    if result.returncode not in expected_codes.get(value, set()):
        raise WatchError("command_status_invalid")
    return value


def _systemctl_environment():
    """Supply the approved user's bus only when the invocation has no bus env."""
    if "XDG_RUNTIME_DIR" in os.environ or "DBUS_SESSION_BUS_ADDRESS" in os.environ:
        return None
    if os.getuid() != APPROVED_UID:
        raise WatchError("systemctl_unavailable")
    try:
        bus_stat = os.lstat(USER_BUS_SOCKET)
    except OSError as exc:
        raise WatchError("systemctl_unavailable") from exc
    if not stat.S_ISSOCK(bus_stat.st_mode) or bus_stat.st_uid != APPROVED_UID:
        raise WatchError("systemctl_unavailable")
    env = os.environ.copy()
    env["XDG_RUNTIME_DIR"] = f"/run/user/{APPROVED_UID}"
    env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={USER_BUS_SOCKET}"
    return env


def _run_systemctl(args):
    env = _systemctl_environment()
    if env is None:
        return _run(args)
    return _run(args, env=env)


def _inspect_service(service):
    container = CONTAINERS[service]
    # The template emits only fixed state enums, restart policy, approved
    # volume bits, and allowlisted storage-mode labels. It never emits env values.
    template = (
        "{{.State.Status}}|"
        '{{if index .State "Health"}}{{index (index .State "Health") "Status"}}'
        "{{else}}not_configured{{end}}|"
        "{{.HostConfig.RestartPolicy.Name}}|"
        '{{range .Mounts}}{{if eq (index . "Name") "belib-production_postgres-data"}}'
        "postgres={{.RW}}{{end}}{{end}}|"
        '{{range .Mounts}}{{if and (eq (index . "Name") "belib-production_book-files") '
        '(eq (index . "Destination") "/var/lib/belib/books")}}'
        "book_files={{.RW}}{{end}}{{end}}|"
        '{{range .Config.Env}}{{$parts := split . "="}}'
        '{{if eq (index $parts 0) "BOOK_FILE_STORAGE_DRIVER"}}'
        '{{if eq (len $parts) 2}}'
        '{{if eq (index $parts 1) "local"}}mode=local;'
        '{{else if eq (index $parts 1) "r2"}}mode=r2;'
        '{{else}}mode=invalid;{{end}}'
        '{{else}}mode=invalid;{{end}}{{end}}{{end}}'
    )
    result = _run(["docker", "--host", DOCKER_SOCKET, "inspect", "--format", template, container])
    if result.returncode != 0:
        # A failed inspect proves absence only for Docker's exact, allowlisted
        # not-found messages naming this expected container. All other
        # failures (including mixed output) are unavailable observations.
        escaped_container = re.escape(container)
        missing_container = re.compile(
            rf"(?:Error: No such (?:object|container): {escaped_container}|"
            rf"Error response from daemon: No such (?:object|container): {escaped_container})"
        )
        diagnostic = result.stderr.removesuffix("\n")
        if not result.stdout and missing_container.fullmatch(diagnostic):
            return {"exists": False, "running": False, "health": "unknown", "restart": "unknown"}
        raise WatchError("docker_inspect_failed")
    if result.stderr:
        raise WatchError("docker_output_invalid")
    fields = result.stdout.strip().split("|")
    if len(fields) != 6:
        raise WatchError("docker_output_invalid")
    state, health, restart, postgres_mount, book_files_mount, storage_mode = fields
    if state not in {"created", "running", "paused", "restarting", "removing", "exited", "dead"}:
        raise WatchError("docker_output_invalid")
    if health not in {"healthy", "unhealthy", "starting", "not_configured"}:
        raise WatchError("docker_output_invalid")
    if restart not in {"no", "always", "on-failure", "unless-stopped"}:
        raise WatchError("docker_output_invalid")
    if postgres_mount not in {"", "postgres=true", "postgres=false"}:
        raise WatchError("docker_output_invalid")
    if book_files_mount not in {"", "book_files=true", "book_files=false"}:
        raise WatchError("docker_output_invalid")
    if storage_mode not in {"", "mode=local;", "mode=r2;"}:
        storage_mode = "unknown"
    return {
        "exists": True,
        "running": state == "running",
        "health": health,
        "restart": restart,
        "postgres_rw_attached": postgres_mount == "postgres=true",
        "book_files_rw_attached": book_files_mount == "book_files=true",
        "storage_mode": storage_mode.removeprefix("mode=").removesuffix(";") or None,
    }


def _volume_exists(name):
    template = "{{.Name}}"
    result = _run(["docker", "--host", DOCKER_SOCKET, "volume", "inspect", "--format", template, name])
    if result.returncode != 0:
        # Only Docker's exact not-found response proves absence. Permission,
        # daemon, and otherwise ambiguous failures must fail closed without
        # reflecting command output that may contain sensitive details.
        escaped_name = re.escape(name)
        missing_volume = re.compile(
            rf"(?:(?i:Error response from daemon: get ){escaped_name}(?i:: no such volume)|"
            rf"(?i:Error: No such volume: ){escaped_name})",
        )
        if not result.stdout.strip() and missing_volume.fullmatch(result.stderr.strip()):
            return False
        raise WatchError("docker_volume_inspect_failed")
    if result.stdout.strip() != name:
        raise WatchError("docker_output_invalid")
    return True


def _unit_active(unit):
    return _command_status(
        ["systemctl", "--user", "is-active", unit],
        allowed={"active", "inactive", "failed", "activating", "deactivating", "reloading"},
        use_systemctl_env=True,
    ) == "active"


def _unit_enabled(unit):
    return _command_status(
        ["systemctl", "--user", "is-enabled", unit],
        allowed={"enabled", "disabled", "static", "indirect", "masked", "generated", "linked", "alias"},
        use_systemctl_env=True,
    ) == "enabled"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_status(path):
    request = Request(f"{BASE_URL}{path}", method="GET")
    opener = build_opener(_NoRedirect, ProxyHandler({}))
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status
    except HTTPError as exc:
        return exc.code
    except (OSError, URLError, TimeoutError):
        return 0


def _journal_counts(minutes):
    regex = "|".join(re.escape(pattern) for pattern in EVENT_PATTERNS)
    result = _run_journal([
        "journalctl", "--user", "-u", "docker.service",
        "--since", f"{minutes} minutes ago", "--no-pager", "--output=cat",
        f"--grep={regex}", f"--lines={MAX_JOURNAL_LINES}",
    ])
    # journalctl uses rc=1 with no output for an empty --grep result. Accept
    # only that precise no-match shape; stderr or any other nonzero result is
    # an unavailable/abnormal journal observation.
    if result.returncode == 1 and not result.stdout and not result.stderr:
        output = ""
    elif result.returncode == 0 and not result.stderr:
        output = result.stdout
    else:
        raise WatchError("journal_unavailable")
    counts = {"duplicate_id": 0, "runtime_create": 0, "namespace_restore": 0}
    for line in output.splitlines()[:MAX_JOURNAL_LINES]:
        if any(pattern in line for pattern in EVENT_PATTERNS[:2]):
            counts["duplicate_id"] += 1
        if EVENT_PATTERNS[2] in line:
            counts["runtime_create"] += 1
        if any(pattern in line for pattern in EVENT_PATTERNS[3:]):
            counts["namespace_restore"] += 1
    counts["truncated"] = len(output.splitlines()) >= MAX_JOURNAL_LINES
    return counts


def collect(minutes, *, uid=None, http_get=None):
    """Collect and return only fixed, sanitized status fields."""
    uid = os.getuid() if uid is None else uid
    if uid != APPROVED_UID:
        raise WatchError("caller_not_approved")
    get_status = http_get or _http_status
    services = {name: _inspect_service(name) for name in SERVICES}
    docker_service = {
        "enabled": _unit_enabled("docker.service"),
        "active": _unit_active("docker.service"),
    }
    cd_timer = {
        "enabled": _unit_enabled("belib-production-cd.timer"),
        "active": _unit_active("belib-production-cd.timer"),
    }
    linger = _command_text(
        ["loginctl", "show-user", str(APPROVED_UID), "-p", "Linger", "--value"],
        allowed={"yes", "no"},
    ) == "yes"
    volumes = {key: _volume_exists(name) for key, name in VOLUMES.items()}
    routes = {
        "health_200": get_status("/health") == 200,
        "login_200": get_status("/login") == 200,
        "favicon_200": get_status("/favicon.svg") == 200,
        "books_401": get_status("/api/v1/books") == 401,
    }
    journal = _journal_counts(minutes)

    failures = []
    for name, status in services.items():
        if not status["exists"]:
            failures.append(f"service_{name}_missing")
        if not status["running"]:
            failures.append(f"service_{name}_not_running")
        if status["restart"] != "unless-stopped":
            failures.append(f"service_{name}_restart_policy")
    for name in ("db", "backend"):
        if services[name]["health"] != "healthy":
            failures.append(f"service_{name}_unhealthy")
    for name in ("frontend", "nginx"):
        health = services[name]["health"]
        if health not in {"healthy", "not_configured"}:
            failures.append(f"service_{name}_unhealthy")
    if not services["db"].get("postgres_rw_attached", False):
        failures.append("postgres_volume_not_rw_attached")
    backend_storage_mode = services["backend"].get("storage_mode")
    if backend_storage_mode not in {"local", "r2"}:
        failures.append("backend_storage_mode_invalid")
    elif backend_storage_mode == "local" and not services["backend"].get("book_files_rw_attached", False):
        failures.append("book_files_volume_not_rw_attached")
    for name, exists in volumes.items():
        if not exists and not (name == "book_files" and backend_storage_mode == "r2"):
            failures.append(f"volume_{name}_missing")
    if not docker_service["active"]:
        failures.append("docker_service_inactive")
    if not docker_service["enabled"]:
        failures.append("docker_service_disabled")
    if not cd_timer["enabled"]:
        failures.append("cd_timer_disabled")
    if not cd_timer["active"]:
        failures.append("cd_timer_inactive")
    if not linger:
        failures.append("linger_disabled")
    for name, passed in routes.items():
        if not passed:
            failures.append(f"route_{name}")

    return {
        "status": "ALERT" if failures else (
            "HEALTHY_WITH_RUNTIME_WARNINGS"
            if any(value for value in journal.values()) else "HEALTHY"
        ),
        "failures": failures,
        "services": services,
        "docker_service": docker_service,
        "cd_timer": cd_timer,
        "linger": linger,
        "volumes": volumes,
        "routes": routes,
        "journal_counts": journal,
        "journal_window_minutes": minutes,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-minutes", type=int, default=15)
    args = parser.parse_args(argv)
    if not 1 <= args.window_minutes <= 120:
        print(json.dumps({"status": "ALERT", "failures": ["window_invalid"]}, sort_keys=True))
        return 2
    try:
        report = collect(args.window_minutes)
    except WatchError as exc:
        code = str(exc) if str(exc) in {"caller_not_approved"} else "collection_failed"
        report = {"status": "ALERT", "failures": [code]}
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 2 if report["status"] == "ALERT" else 0


if __name__ == "__main__":
    sys.exit(main())
