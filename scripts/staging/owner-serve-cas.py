#!/usr/bin/python3
"""Standalone, owner-only Tailscale Serve CAS transaction. Never run from the staging launcher."""

from __future__ import annotations

import copy
import base64
import contextlib
import fcntl
import hashlib
import http.client
import json
import os
import re
import signal
import socket
import ssl
import stat
import sys
import time
from pathlib import Path

SOCKET = "/var/run/tailscale/tailscaled.sock"
HOST = "local-tailscaled.sock"
CUSTOM = "stg-bl.hiroyuki9614.com"
SELF = "ubuntu-worker.tail63cd2d.ts.net"
SELF_IP = "100.109.171.70"
BRI = "/bri-referee-pilot-comp.png"
LEGACY_BRI_HOST = "fedora-worker.tail63cd2d.ts.net"
BRI_FILE = "/home/hiroyuki/tailscale-share/bri-referee-pilot-comp.png"
MAX_TLS_RESPONSE_BYTES = 4 * 1024 * 1024
JOURNAL = Path("/var/lib/belib-staging/tls-serve")
CA_FILE = "/etc/ssl/certs/ca-certificates.crt"
MAX_CONFIG = 1024 * 1024
PLAN_SCHEMA = "belib-tls-owner-retirement-plan/v1"
PLAN_DISPOSITION = "retire-old-fedora-bri-image-publication-handler-only"
PLAN_KEYS = {"schema", "disposition", "source_sha", "prior_application_sha", "retained_state_sha256", "serve_config_sha256",
             "serve_etag", "certificate_sha256", "tls_config_sha256", "nonce", "expires_at"}
TAILSCALE_BUILD = "1.104.5-t788384763-gd9218e278"
PRIOR_APP_SHA = "81256268276d7ead95029849037f69ec6beec202"


class Stop(Exception):
    pass


class HTTPFailure(Stop):
    """A definite LocalAPI HTTP response; the requested write did not happen."""

    def __init__(self, status):
        super().__init__(f"LocalAPI rejected the request with HTTP {status}")
        self.status = status


class TransportFailure(Stop):
    """An I/O outcome that can be ambiguous after POST."""


class OperationInterrupted(BaseException):
    pass


class MutationAttempt:
    """One CAS call's exact pre-send and post-acceptance state."""

    __slots__ = ("post_attempted", "post_accepted")

    def __init__(self):
        self.post_attempted = False
        self.post_accepted = False


class CasPostAmbiguous(TransportFailure):
    pass


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def pairs_no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise Stop("Duplicate plan/config field")
        out[key] = value
    return out


def strict_json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs_no_duplicates)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Stop("Malformed bounded JSON") from exc


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def validate_plan(plan, now=None):
    now = int(time.time()) if now is None else now
    if (not isinstance(plan, dict) or set(plan) != PLAN_KEYS or plan["schema"] != PLAN_SCHEMA
            or plan["disposition"] != PLAN_DISPOSITION):
        raise Stop("Plan schema is not the fixed owner contract")
    if plan["prior_application_sha"] != PRIOR_APP_SHA:
        raise Stop("Live application revision differs from the approved TLS baseline")
    for field in ("source_sha",):
        if not isinstance(plan[field], str) or not re.fullmatch(r"[0-9a-f]{40}", plan[field]):
            raise Stop("Malformed source identity")
    for field in PLAN_KEYS - {"schema", "disposition", "source_sha", "prior_application_sha", "expires_at", "nonce"}:
        if not isinstance(plan[field], str) or not re.fullmatch(r"[0-9a-f]{64}", plan[field]):
            raise Stop("Malformed plan evidence digest")
    if type(plan["expires_at"]) is not int or not now < plan["expires_at"] <= now + 1800:
        raise Stop("Owner plan is expired or outside its thirty-minute window")
    if not isinstance(plan["nonce"], str) or not re.fullmatch(r"[0-9a-f]{64}", plan["nonce"]):
        raise Stop("Malformed one-use plan nonce")
    return plan


def config(raw: bytes):
    value = strict_json(raw)
    if (not isinstance(value, dict) or set(value) != {"TCP", "Web"}
            or not isinstance(value["TCP"], dict) or not isinstance(value["Web"], dict)):
        raise Stop("Serve config must contain only the reviewed TCP and Web scopes")
    return value


def baseline(raw: bytes):
    value = config(raw)
    tcp, web = value["TCP"], value["Web"]
    old_host = f"{LEGACY_BRI_HOST}:443"
    exact_handler = {"Path": BRI_FILE}
    exact_web = {old_host: {"Handlers": {BRI: exact_handler}}}
    if tcp != {"443": {"HTTPS": True}} or web != exact_web:
        raise Stop("Serve baseline differs from exact single HTTPS/BRI configuration")
    if "8443" in tcp or f"{SELF}:8443" in web or f"{LEGACY_BRI_HOST}:8443" in web:
        raise Stop("Serve 8443 is already occupied")
    return value


def remove_legacy_handler(raw: bytes):
    baseline(raw)
    return canonical({"TCP": {}, "Web": {}})


def activate_custom_tls(raw: bytes):
    if config(raw) != {"TCP": {}, "Web": {}}:
        raise Stop("Custom TLS activation requires the exact empty Serve state")
    return canonical({"TCP": {"443": {"TCPForward": "127.0.0.1:18443"}}, "Web": {}})


def retire_legacy_handler(raw: bytes):
    return activate_custom_tls(remove_legacy_handler(raw))


class LocalAPI:
    def request(self, method, body=None, etag=None, path="/localapi/v0/serve-config", before_send=None):
        connection = http.client.HTTPConnection(HOST, timeout=4)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(4)
            connection.sock = sock
            sock.connect(SOCKET)
            headers = {"Accept": "application/json"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            if etag is not None:
                headers["If-Match"] = etag
            if before_send is not None:
                before_send()
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read(MAX_CONFIG + 1)
            if len(data) > MAX_CONFIG:
                raise Stop("LocalAPI response exceeds fixed bound")
            if response.status != 200:
                raise HTTPFailure(response.status)
            return data, response.getheader("Etag", "")
        except Stop:
            raise
        except (OSError, http.client.HTTPException, TimeoutError) as exc:
            raise TransportFailure("LocalAPI transport failed") from exc
        finally:
            connection.close()

    def get(self):
        raw, etag = self.request("GET")
        if not re.fullmatch(r"[0-9a-f]{64}", etag):
            raise Stop("LocalAPI omitted strict ETag")
        config(raw)
        return raw, etag

    def set(self, raw, etag, attempt, before_write=None):
        def mark_attempted():
            if before_write is not None:
                before_write()
            attempt.post_attempted = True
        self.request("POST", raw, etag, before_send=mark_attempted)
        attempt.post_accepted = True

    def status(self):
        raw, _ = self.request("GET", path="/localapi/v0/status?peers=false")
        value = strict_json(raw)
        self_value = value.get("Self") if isinstance(value, dict) else None
        dns_name = self_value.get("DNSName") if isinstance(self_value, dict) else None
        ips = self_value.get("TailscaleIPs") if isinstance(self_value, dict) else None
        if (not isinstance(value, dict) or value.get("Version") != TAILSCALE_BUILD
                or not isinstance(dns_name, str) or dns_name.rstrip(".") != SELF
                or not isinstance(ips, list) or SELF_IP not in ips):
            raise Stop("LocalAPI version or self identity differs from the reviewed host")


def tls_request(port, hostname, path, *, connect_host="127.0.0.1", fingerprint=None,
                expect_status=200, expected_body_sha256=None):
    raw = None
    try:
        context = ssl.create_default_context(cafile=CA_FILE)
        raw = socket.create_connection((connect_host, port), timeout=4)
        with context.wrap_socket(raw, server_hostname=hostname) as secure:
            if fingerprint and digest(secure.getpeercert(binary_form=True)) != fingerprint:
                raise Stop("TLS leaf certificate pin mismatch")
            request = f"GET {path} HTTP/1.1\r\nHost: {hostname}\r\nConnection: close\r\n\r\n".encode("ascii")
            secure.sendall(request)
            response = http.client.HTTPResponse(secure)
            response.begin()
            body = response.read(MAX_TLS_RESPONSE_BYTES + 1)
            if len(body) > MAX_TLS_RESPONSE_BYTES:
                raise Stop("Verified TLS/HTTP response exceeds its fixed bound")
            if response.status != expect_status:
                raise Stop(f"TLS HTTP probe returned {response.status}, expected {expect_status}")
            if expected_body_sha256 is not None:
                if (not isinstance(expected_body_sha256, str)
                        or not re.fullmatch(r"[0-9a-f]{64}", expected_body_sha256)
                        or digest(body) != expected_body_sha256):
                    raise Stop("TLS response bytes do not match the expected digest")
    except Stop:
        raise
    except (OSError, ssl.SSLError, http.client.HTTPException, TimeoutError) as exc:
        raise Stop("Verified TLS/HTTP readiness probe failed") from exc
    finally:
        if raw is not None:
            try:
                raw.close()
            except OSError:
                pass


def _negative_tls_rejected(port, connect_host, server_hostname):
    raw = socket.create_connection((connect_host, port), timeout=4)
    try:
        # Insecure verification is used only to classify whether any handshake completed.
        context = ssl._create_unverified_context()
        with context.wrap_socket(raw, server_hostname=server_hostname):
            raise Stop("Negative TLS probe completed a handshake")
    except Stop:
        raise
    except (OSError, ssl.SSLError, TimeoutError):
        return


def tls_unknown_rejected(port, connect_host="127.0.0.1"):
    _negative_tls_rejected(port, connect_host, "unknown.invalid")


def tls_no_sni_rejected(port, connect_host="127.0.0.1"):
    _negative_tls_rejected(port, connect_host, None)


def tls_legacy_sni_rejected(port, connect_host="127.0.0.1"):
    _negative_tls_rejected(port, connect_host, LEGACY_BRI_HOST)


def tls_self_sni_rejected(port, connect_host="127.0.0.1"):
    _negative_tls_rejected(port, connect_host, SELF)


def http_18083():
    connection = http.client.HTTPConnection("127.0.0.1", 18083, timeout=4)
    try:
        connection.request("GET", "/health", headers={"Host": CUSTOM})
        response = connection.getresponse()
        response.read(1024 * 1024)
        if response.status != 200:
            raise Stop("Existing loopback HTTP health endpoint is not 200")
    except (OSError, http.client.HTTPException, TimeoutError) as exc:
        raise Stop("Existing loopback HTTP health probe failed") from exc
    finally:
        connection.close()


def _write_all(fd, raw):
    view = memoryview(raw)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short journal write")
        view = view[written:]


def _check_dir(path: Path, *, create=False, mode=None):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if not create:
            raise Stop("Required fixed owner-state directory is missing")
        create_mode = 0o755 if mode is None else mode
        os.mkdir(path, create_mode)
        os.chmod(path, create_mode, follow_symlinks=False)
        info = os.lstat(path)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
            or mode is not None and stat.S_IMODE(info.st_mode) != mode):
        raise Stop("Owner-state path has unsafe ownership, mode, or type")
    return info


def ensure_journal_dir():
    _check_dir(Path("/var"))
    _check_dir(Path("/var/lib"))
    _check_dir(Path("/var/lib/belib-staging"), create=True)
    _check_dir(JOURNAL, create=True, mode=0o700)


@contextlib.contextmanager
def operation_lock():
    ensure_journal_dir()
    path = JOURNAL / "operation.lock"
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise Stop("Cannot safely open the fixed Serve operation lock") from exc
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise Stop("Serve operation lock has unsafe metadata")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Stop("Another owner Serve operation holds the fixed lock") from exc
        yield
    finally:
        os.close(fd)


def journal_start(plan, original, etag, plan_sha256):
    ensure_journal_dir()
    nonce_path = JOURNAL / (plan["nonce"] + ".used")
    journal_path = JOURNAL / (plan["nonce"] + ".jsonl")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        nonce_fd = os.open(nonce_path, flags, 0o600)
        try:
            nonce_info = os.fstat(nonce_fd)
            if (not stat.S_ISREG(nonce_info.st_mode) or nonce_info.st_uid != 0 or nonce_info.st_nlink != 1
                    or stat.S_IMODE(nonce_info.st_mode) != 0o600):
                raise Stop("One-use nonce record metadata is unsafe")
            _write_all(nonce_fd, b"consumed\n")
            os.fsync(nonce_fd)
        finally:
            os.close(nonce_fd)
        dirfd = os.open(JOURNAL, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
        fd = os.open(journal_path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1:
                raise Stop("Journal file metadata is unsafe")
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise Stop("Journal file mode is unsafe")
            initial = {"schema": "belib-tls-owner-journal/v1", "plan_sha256": plan_sha256,
                       "original_config_b64": base64.b64encode(original).decode("ascii"),
                       "original_sha256": digest(original), "original_etag": etag, "event": "prepared"}
            _write_all(fd, canonical(initial) + b"\n")
            os.fsync(fd)
            dirfd = os.open(JOURNAL, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
            return fd, journal_path
        except BaseException:
            os.close(fd)
            raise
    except OSError as exc:
        raise Stop("One-use nonce or durable journal could not be created") from exc


def journal_event(journal, event, **fields):
    fd, path = journal
    _write_all(fd, canonical({"event": event, **fields}) + b"\n")
    os.fsync(fd)


@contextlib.contextmanager
def bounded_rollback_signals():
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def cas(api, expected, etag, desired, attempt=None, before_write=None):
    attempt = MutationAttempt() if attempt is None else attempt
    current, current_etag = api.get()
    if current_etag != etag or canonical(config(current)) != canonical(config(expected)):
        raise Stop("Serve config changed outside this plan")
    try:
        api.set(desired, current_etag, attempt, before_write=before_write)
    except HTTPFailure:
        # Every HTTP error is a typed denial. Only I/O loss leaves a POST outcome
        # ambiguous enough to reconcile against the two operation-owned states.
        raise
    except TransportFailure as error:
        if not attempt.post_attempted:
            raise
        try:
            after, after_etag = api.get()
        except (Stop, OSError) as read_error:
            raise TransportFailure("POST outcome unknown; no compensating write attempted") from read_error
        if canonical(config(after)) == canonical(config(desired)) and after_etag != current_etag:
            return after, after_etag
        if canonical(config(after)) == canonical(config(expected)) and after_etag == current_etag:
            raise Stop("Compare-and-swap was not applied") from error
        raise Stop("POST conflicts with both owned states; stopped without overwrite") from error
    try:
        after, after_etag = api.get()
    except BaseException as read_error:
        # POST returned HTTP 200, so a failed readback must reconcile the attempted write.
        raise CasPostAmbiguous("POST was accepted but its readback failed") from read_error
    if canonical(config(after)) != canonical(config(desired)) or after_etag == current_etag:
        raise Stop("CAS readback mismatch; state requires owner reconciliation")
    return after, after_etag


def _check_owner_identity():
    if os.geteuid() != 0 or os.getuid() != 0 or socket.gethostname() != "ubuntu-worker":
        raise Stop("Run only from the explicit owner invocation on ubuntu-worker as root")


def _reconcile_inflight(api, inflight):
    expected_raw, expected_etag, desired_raw, attempt = inflight
    if not attempt.post_attempted:
        return None
    observed_raw, observed_etag = api.get()
    observed = canonical(config(observed_raw))
    if observed == canonical(config(desired_raw)) and observed_etag != expected_etag:
        return observed_raw, observed_etag
    if observed == canonical(config(expected_raw)) and observed_etag == expected_etag:
        return None
    raise Stop("Ambiguous Serve write conflicts with both owned states; manual recovery required")


def _rollback(api, original, owned, journal):
    if not owned:
        return
    current_raw, current_etag = owned[-1]
    live_raw, live_etag = api.get()
    if live_etag != current_etag or canonical(config(live_raw)) != canonical(config(current_raw)):
        raise Stop("Current Serve config is foreign/unknown; manual recovery required")
    if config(live_raw)["TCP"].get("443") == {"TCPForward": "127.0.0.1:18443"}:
        phase_b_raw = copy.deepcopy(config(live_raw))
        del phase_b_raw["TCP"]["443"]
        phase_b_raw = canonical(phase_b_raw)
        journal_event(journal, "rollback-intent", desired_sha256=digest(phase_b_raw), etag=live_etag)
        live_raw, live_etag = cas(api, live_raw, live_etag, phase_b_raw)
        journal_event(journal, "rollback-forward-removed", config_sha256=digest(live_raw), etag=live_etag)
    journal_event(journal, "restore-intent", desired_sha256=digest(original), etag=live_etag)
    restored, restored_etag = cas(api, live_raw, live_etag, original)
    journal_event(journal, "restored", config_sha256=digest(restored), etag=restored_etag)


def _serve_transaction(plan, plan_sha256):
    api = LocalAPI()
    api.status()
    original, etag = api.get()
    baseline(original)
    if digest(original) != plan["serve_config_sha256"] or etag != plan["serve_etag"]:
        raise Stop("Live Serve baseline digest or ETag differs from pinned plan")

    http_18083()
    pin = plan["certificate_sha256"]
    # Prove the custom SNI route and fail-closed default before consuming the plan.
    tls_request(18443, CUSTOM, "/health", fingerprint=pin)
    tls_unknown_rejected(18443)
    tls_no_sni_rejected(18443)
    tls_legacy_sni_rejected(18443)
    tls_self_sni_rejected(18443)
    tls_request(18443, CUSTOM, "/health", fingerprint=pin)

    validate_plan(plan)
    journal = journal_start(plan, original, etag, plan_sha256)
    current, current_etag = original, etag
    owned = []
    inflight = None
    try:
        empty = remove_legacy_handler(original)
        final = activate_custom_tls(empty)
        for phase, desired in (("remove-old-https", empty), ("add-custom-tls", final)):
            journal_event(journal, "retirement-intent", phase=phase, disposition=PLAN_DISPOSITION,
                          desired_sha256=digest(desired), etag=current_etag)
            validate_plan(plan)
            attempt = MutationAttempt()
            inflight = (current, current_etag, desired, attempt)
            current, current_etag = cas(api, current, current_etag, desired, attempt,
                                        before_write=lambda: validate_plan(plan))
            owned.append((current, current_etag))
            inflight = None
            journal_event(journal, "retirement-committed", phase=phase,
                          config_sha256=digest(current), etag=current_etag)
            validate_plan(plan)

        # The final Serve state exposes only this machine's custom TLS ingress.
        tls_request(443, CUSTOM, "/health", connect_host=SELF_IP, fingerprint=pin)
        tls_unknown_rejected(443, connect_host=SELF_IP)
        tls_no_sni_rejected(443, connect_host=SELF_IP)
        tls_legacy_sni_rejected(443, connect_host=SELF_IP)
        tls_self_sni_rejected(443, connect_host=SELF_IP)
        tls_request(443, CUSTOM, "/health", connect_host=SELF_IP, fingerprint=pin)
        journal_event(journal, "complete", disposition=PLAN_DISPOSITION,
                      config_sha256=digest(current), etag=current_etag)
        return {"status": "complete", "serve_config_sha256": digest(current), "serve_etag": current_etag}
    except BaseException as failure:
        # Definitive HTTP denials are never reconciled as operation-owned writes.
        if (inflight is not None and inflight[3].post_attempted
                and isinstance(failure, (TransportFailure, OperationInterrupted, KeyboardInterrupt))):
            try:
                reconciled = _reconcile_inflight(api, inflight)
            except BaseException as reconcile_error:
                raise Stop("Serve outcome unknown; journal retained for manual recovery") from reconcile_error
            if reconciled is not None:
                owned.append(reconciled)
        if owned:
            try:
                with bounded_rollback_signals():
                    _rollback(api, original, owned, journal)
            except BaseException as rollback_error:
                raise Stop(f"Serve rollback uncertain; manual recovery required: {rollback_error}") from failure
        if isinstance(failure, (OperationInterrupted, KeyboardInterrupt)):
            raise Stop("Owner operation interrupted; owned Serve state was rolled back when safe") from failure
        raise
    finally:
        os.close(journal[0])


def run(plan_raw: bytes, expected_plan_sha256: str):
    """Execute only the exact in-memory plan whose digest the reviewed loader pinned."""
    _check_owner_identity()
    if not isinstance(plan_raw, bytes) or len(plan_raw) > 16384:
        raise Stop("Plan input exceeds the fixed byte bound")
    if not isinstance(expected_plan_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_plan_sha256):
        raise Stop("Verified-loader plan digest is malformed")
    observed_sha = digest(plan_raw)
    if observed_sha != expected_plan_sha256:
        raise Stop("In-memory plan bytes do not match the reviewed loader digest")
    plan = validate_plan(strict_json(plan_raw))
    if canonical(plan) != plan_raw:
        raise Stop("Plan bytes must be canonical JSON")
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    def interrupt(signum, frame):
        raise OperationInterrupted(f"signal {signum}")
    try:
        for sig in previous:
            signal.signal(sig, interrupt)
        with operation_lock():
            return _serve_transaction(plan, observed_sha)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main():
    raise Stop("Direct CLI is disabled; use the digest-pinned reviewed owner loader")


if __name__ == "__main__":
    try:
        main()
    except Stop as error:
        print(f"owner-serve-cas: {error}", file=sys.stderr)
        raise SystemExit(2)
