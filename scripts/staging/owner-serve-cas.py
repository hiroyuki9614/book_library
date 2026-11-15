#!/usr/bin/python3
"""Standalone, owner-only Tailscale Serve CAS transaction. Never run from the staging launcher."""

from __future__ import annotations

import copy
import base64
import contextlib
import ctypes
import fcntl
import hashlib
import http.client
import json
import os
import re
import signal
import secrets
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
PLAN_SCHEMA_V2 = "belib-tls-owner-retirement-plan/v2"
PLAN_SCHEMA_V3 = "belib-tls-owner-stable-serve-plan/v3"
PLAN_DISPOSITION = "retire-old-fedora-bri-image-publication-handler-only"
PLAN_KEYS = {"schema", "disposition", "source_sha", "prior_application_sha", "retained_state_sha256", "serve_config_sha256",
             "serve_etag", "certificate_sha256", "tls_config_sha256", "nonce", "expires_at"}
PLAN_KEYS_V2 = PLAN_KEYS | {"upgrade_receipt_nonce", "upgrade_receipt_sha256", "operation_id", "project"}
PLAN_KEYS_V3 = PLAN_KEYS_V2 | {"prior_generation_sha256", "prior_tls_owner_nonce", "prior_tls_completion_sha256"}
PREPARE_SCHEMA_V2 = "belib-staging-tls-prepare-receipt/v2"
PREPARE_KEYS_V2 = {"schema", "plan_sha256", "retained_state_sha256", "nginx_id", "source_sha",
                   "tls_config_sha256", "certificate_sha256", "approval_nonce", "evidence_sha256",
                   "prepared", "owner_nonce", "upgrade_receipt_nonce", "upgrade_receipt_sha256",
                   "operation_id", "project", "previous_nginx_id", "nginx_configuration_sha256",
                   "nginx_image_id", "nginx_network_ids", "unchanged_runtime_sha256"}
COMPLETION_SCHEMA = "belib-tls-owner-completion/v1"
COMPLETION_SCHEMA_V2 = "belib-tls-owner-completion/v2"
COMPLETION_KEYS = {"schema", "owner_nonce", "plan_sha256", "prepare_receipt_sha256",
                   "upgrade_receipt_nonce", "upgrade_receipt_sha256", "source_sha", "operation_id",
                   "project", "previous_nginx_id", "nginx_id", "nginx_configuration_sha256",
                   "nginx_image_id", "nginx_network_ids", "tls_config_sha256", "certificate_sha256",
                   "unchanged_runtime_sha256", "approval_nonce", "evidence_sha256",
                   "serve_config_sha256", "serve_etag", "completed_at", "status"}
COMPLETION_KEYS_V2 = COMPLETION_KEYS | {"prior_generation_sha256", "prior_tls_owner_nonce", "prior_tls_completion_sha256"}
UPGRADE_RESULTS = Path("/var/lib/belib-staging/operator")
UPGRADE_PROJECT = "belib-isolated-i5"
UPGRADE_RETAINED_DATABASE_ID = "9ab9d141599e8b33f51a1e4e9bcd16d66e92b9d134889014d9d9bd618b42f73d"
UPGRADE_PROJECT = "belib-isolated-i5"
UPGRADE_RETAINED_DATABASE_ID = "9ab9d141599e8b33f51a1e4e9bcd16d66e92b9d134889014d9d9bd618b42f73d"
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
        return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs_no_duplicates,
                          parse_constant=lambda value: (_ for _ in ()).throw(Stop("Non-finite JSON value")))
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise Stop("Malformed bounded JSON") from exc


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()


def validate_plan(plan, now=None, *, allow_expired=False):
    now = int(time.time()) if now is None else now
    if not isinstance(plan, dict):
        raise Stop("Plan schema is not the fixed owner contract")
    version = plan.get("schema")
    expected_disposition = "reuse-exact-active-custom-tls-ingress" if version == PLAN_SCHEMA_V3 else PLAN_DISPOSITION
    if plan.get("disposition") != expected_disposition:
        raise Stop("Plan schema is not the fixed owner contract")
    keys = PLAN_KEYS_V3 if version == PLAN_SCHEMA_V3 else PLAN_KEYS_V2 if version == PLAN_SCHEMA_V2 else PLAN_KEYS
    if set(plan) != keys or version not in {PLAN_SCHEMA, PLAN_SCHEMA_V2, PLAN_SCHEMA_V3}:
        raise Stop("Plan fields do not match the fixed owner contract")
    if version == PLAN_SCHEMA and plan["prior_application_sha"] != PRIOR_APP_SHA:
        raise Stop("Live application revision differs from the approved TLS baseline")
    for field in ("source_sha",):
        if not isinstance(plan[field], str) or not re.fullmatch(r"[0-9a-f]{40}", plan[field]):
            raise Stop("Malformed source identity")
    for field in ("retained_state_sha256", "serve_config_sha256", "certificate_sha256", "tls_config_sha256"):
        if not isinstance(plan[field], str) or not re.fullmatch(r"[0-9a-f]{64}", plan[field]):
            raise Stop("Malformed plan evidence digest")
    if type(plan["expires_at"]) is not int or (not allow_expired and not now < plan["expires_at"] <= now + 1800):
        raise Stop("Owner plan is expired or outside its thirty-minute window")
    if allow_expired and plan["expires_at"] <= 0:
        raise Stop("Owner plan expiry is malformed")
    if not isinstance(plan["nonce"], str) or not re.fullmatch(r"[0-9a-f]{64}", plan["nonce"]):
        raise Stop("Malformed one-use plan nonce")
    if version in {PLAN_SCHEMA_V2, PLAN_SCHEMA_V3}:
        if (plan["source_sha"] != plan["prior_application_sha"]
                or not isinstance(plan["upgrade_receipt_nonce"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", plan["upgrade_receipt_nonce"])
                or not isinstance(plan["upgrade_receipt_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", plan["upgrade_receipt_sha256"])
                or not isinstance(plan["operation_id"], str)
                or not re.fullmatch(r"[0-9a-f]{32}", plan["operation_id"])
                or plan["project"] != UPGRADE_PROJECT + "-upgrade-" + plan["operation_id"]):
            raise Stop("TLS v2 project or retained-upgrade identity is invalid")
    if version == PLAN_SCHEMA_V3:
        if (not all(_sha(plan.get(name)) for name in ("prior_generation_sha256", "prior_tls_owner_nonce", "prior_tls_completion_sha256"))
                or plan.get("disposition") != "reuse-exact-active-custom-tls-ingress"):
            raise Stop("TLS v3 predecessor links or stable Serve disposition are invalid")
    return plan


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _safe_root_path(directory):
    current = Path("/")
    for part in Path(directory).parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except OSError as error:
            raise Stop("Fixed root receipt parent is unavailable") from error
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise Stop("Fixed root receipt parent is unsafe")


def _read_root_regular(path, limit):
    try:
        before = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o600 or before.st_size <= 0 or before.st_size > limit):
            raise Stop("Fixed root upgrade receipt metadata is unsafe")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            opened = os.fstat(fd)
            if opened.st_ino != before.st_ino or opened.st_dev != before.st_dev or opened.st_size != before.st_size:
                raise Stop("Fixed root upgrade receipt changed during open")
            raw = b""
            while len(raw) <= limit:
                chunk = os.read(fd, min(65536, limit + 1 - len(raw)))
                if not chunk:
                    break
                raw += chunk
            if len(raw) != opened.st_size or len(raw) > limit:
                raise Stop("Fixed root upgrade receipt changed during read")
            return raw
        finally:
            os.close(fd)
    except OSError as error:
        raise Stop("Fixed root upgrade receipt is unavailable or unsafe") from error


def _root_unchanged_digest_from_upgrade(plan):
    path = UPGRADE_RESULTS / (plan["upgrade_receipt_nonce"] + ".result.json")
    _safe_root_path(path.parent)
    result = strict_json(_read_root_regular(path, 4 * 1024 * 1024))
    if digest(canonical(result)) != plan["upgrade_receipt_sha256"]:
        raise Stop("Fixed root upgrade receipt digest differs from the owner plan")
    if (not isinstance(result, dict) or result.get("schema") != "belib-staging-operation-result/v1"
            or result.get("operation") != "upgrade-retained" or result.get("exit_code") != 0
            or result.get("nonce_consumed") is not True or result.get("nonce") != plan["upgrade_receipt_nonce"]
            or result.get("source_sha") != plan["source_sha"] or result.get("host") != "ubuntu-worker"
            or result.get("principal") != "hiroyuki" or type(result.get("uid")) is not int
            or result.get("uid") != 1000 or result.get("project") != UPGRADE_PROJECT
            or type(result.get("completed_at")) is not int or result["completed_at"] <= 0
            or not _sha(result.get("grant_sha256")) or not isinstance(result.get("expected_runtime"), dict)):
        raise Stop("Fixed root upgrade receipt does not bind the TLS owner plan")
    runtime = result["expected_runtime"]
    if runtime.get("project") != plan["project"] or runtime.get("operation_id") != plan["operation_id"]:
        raise Stop("Root upgrade receipt project or operation differs from the owner plan")
    if not _sha(runtime.get("plan_sha256")) or not isinstance(runtime.get("services"), dict):
        raise Stop("Root upgrade runtime pins are malformed")
    plan_object = runtime.get("plan")
    if not isinstance(plan_object, dict) or hashlib.sha256(canonical(plan_object)).hexdigest() != runtime["plan_sha256"]:
        raise Stop("Root upgrade runtime plan digest is invalid")
    cutover = plan_object.get("cutover_plan")
    if (not isinstance(cutover, dict) or cutover.get("source_sha") != plan["source_sha"]
            or cutover.get("operation_id") != plan["operation_id"]
            or not isinstance(cutover.get("old"), dict) or not isinstance(cutover.get("new"), dict)
            or cutover["old"].get("nginx", {}).get("id") == cutover["new"].get("nginx", {}).get("id")
            or runtime["services"].get("nginx") != cutover["new"]["nginx"].get("id")):
        raise Stop("Root upgrade nginx transition pins are malformed")
    stable = copy.deepcopy(runtime)
    del stable["services"]["nginx"]
    del stable["plan"]["cutover_plan"]["new"]["nginx"]["id"]
    del stable["plan"]["cutover_plan"]["new"]["nginx"]["configuration_sha256"]
    del stable["plan_sha256"]
    return digest(canonical(stable)), runtime


def _validate_v3_predecessor(plan, runtime):
    prior = runtime.get("prior_tls_completion")
    if (not isinstance(prior, dict)
            or set(prior) != {"owner_nonce", "completion_sha256", "serve_config_sha256", "serve_etag", "certificate_sha256"}
            or plan.get("prior_generation_sha256") != runtime.get("prior_generation_sha256")
            or plan.get("prior_tls_owner_nonce") != prior.get("owner_nonce")
            or plan.get("prior_tls_completion_sha256") != prior.get("completion_sha256")
            or plan.get("serve_config_sha256") != prior.get("serve_config_sha256")
            or plan.get("serve_etag") != prior.get("serve_etag")
            or plan.get("certificate_sha256") != prior.get("certificate_sha256")):
        raise Stop("TLS v3 plan differs from the root-pinned active TLS predecessor")
    return prior


def _verify_stable_active_serve(plan, prepared, plan_sha256):
    unchanged, runtime = _root_unchanged_digest_from_upgrade(plan)
    prior = _validate_v3_predecessor(plan, runtime)
    if (prepared["unchanged_runtime_sha256"] != unchanged
            or prepared["previous_nginx_id"] != runtime["services"]["nginx"]):
        raise Stop("Prepared candidate does not link to the exact v3 upgrade runtime")
    expected_map = {"TCP": {"443": {"TCPForward": "127.0.0.1:18443"}}, "Web": {}}
    api = LocalAPI()
    api.status()
    live, etag = api.get()
    if (canonical(config(live)) != canonical(expected_map)
            or digest(live) != prior["serve_config_sha256"] or etag != prior["serve_etag"]):
        raise Stop("Active Serve ingress differs from its predecessor completion")
    port = runtime["port"]
    _http_health(port)
    tls_request(443, CUSTOM, "/health", connect_host=SELF_IP, fingerprint=plan["certificate_sha256"])
    tls_unknown_rejected(443, connect_host=SELF_IP)
    tls_no_sni_rejected(443, connect_host=SELF_IP)
    tls_legacy_sni_rejected(443, connect_host=SELF_IP)
    tls_self_sni_rejected(443, connect_host=SELF_IP)
    tls_request(443, CUSTOM, "/health", connect_host=SELF_IP, fingerprint=plan["certificate_sha256"])
    api.status()
    after, after_etag = api.get()
    if (after != live or after_etag != etag or digest(after) != prior["serve_config_sha256"]
            or after_etag != prior["serve_etag"]):
        raise Stop("Active Serve changed during read-only v3 verification")
    return {"status": "complete", "serve_config_sha256": digest(live), "serve_etag": etag,
            "prior_generation_sha256": plan["prior_generation_sha256"],
            "prior_tls_owner_nonce": plan["prior_tls_owner_nonce"],
            "prior_tls_completion_sha256": plan["prior_tls_completion_sha256"]}


def _http_health(port):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=4)
    try:
        connection.request("GET", "/health", headers={"Host": CUSTOM})
        response = connection.getresponse()
        response.read(1024 * 1024)
        if response.status != 200:
            raise Stop("Loopback HTTP health probe failed during stable Serve verification")
    except Stop:
        raise
    except (OSError, http.client.HTTPException, TimeoutError) as error:
        raise Stop("Loopback HTTP health probe failed during stable Serve verification") from error
    finally:
        connection.close()


def _validate_prepare_v2(prepared_raw, prepared_sha256, plan, plan_sha256):
    if not isinstance(prepared_raw, bytes) or not prepared_raw or len(prepared_raw) > 16384:
        raise Stop("Prepared TLS receipt is empty or exceeds its fixed bound")
    if not _sha(prepared_sha256) or digest(prepared_raw) != prepared_sha256:
        raise Stop("Prepared TLS receipt bytes do not match the reviewed loader digest")
    prepared = strict_json(prepared_raw)
    if (not isinstance(prepared, dict) or set(prepared) != PREPARE_KEYS_V2
            or prepared.get("schema") != PREPARE_SCHEMA_V2 or canonical(prepared) != prepared_raw):
        raise Stop("Prepared TLS receipt does not match the exact v2 schema")
    links = {"plan_sha256": plan_sha256, "retained_state_sha256": plan["retained_state_sha256"],
             "source_sha": plan["source_sha"], "tls_config_sha256": plan["tls_config_sha256"],
             "certificate_sha256": plan["certificate_sha256"], "owner_nonce": plan["nonce"],
             "upgrade_receipt_nonce": plan["upgrade_receipt_nonce"],
             "upgrade_receipt_sha256": plan["upgrade_receipt_sha256"],
             "operation_id": plan["operation_id"], "project": plan["project"]}
    if any(prepared.get(key) != value for key, value in links.items()) or prepared.get("prepared") is not True:
        raise Stop("Prepared TLS receipt does not bind the adopted plan and root upgrade")
    for field in ("approval_nonce", "evidence_sha256", "unchanged_runtime_sha256", "nginx_configuration_sha256"):
        if not _sha(prepared.get(field)):
            raise Stop("Prepared TLS receipt has malformed evidence digests")
    for field in ("nginx_id", "previous_nginx_id"):
        if not _sha(prepared.get(field)):
            raise Stop("Prepared TLS receipt has malformed nginx IDs")
    if prepared["nginx_id"] == prepared["previous_nginx_id"]:
        raise Stop("Prepared TLS receipt does not describe a nginx transition")
    if not isinstance(prepared.get("nginx_image_id"), str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", prepared["nginx_image_id"]):
        raise Stop("Prepared TLS receipt has malformed nginx image identity")
    networks = prepared.get("nginx_network_ids")
    expected_names = {plan["project"] + "_isolated", plan["project"] + "_ingress"}
    if (not isinstance(networks, dict) or set(networks) != expected_names
            or any(not _sha(value) for value in networks.values())):
        raise Stop("Prepared TLS receipt nginx network pins are malformed")
    unchanged, runtime = _root_unchanged_digest_from_upgrade(plan)
    original_nginx = runtime["services"]["nginx"]
    if (prepared["unchanged_runtime_sha256"] != unchanged or prepared["previous_nginx_id"] != original_nginx):
        raise Stop("Prepared TLS receipt stable-runtime or original nginx pin differs from root upgrade pins")
    return prepared


def config(raw: bytes):
    value = strict_json(raw)
    if not isinstance(value, dict) or set(value) - {"TCP", "Web"}:
        raise Stop("Serve config must contain only the reviewed TCP and Web scopes")
    tcp, web = value.get("TCP", {}), value.get("Web", {})
    if not isinstance(tcp, dict) or not isinstance(web, dict):
        raise Stop("Serve TCP and Web scopes must be objects when present")
    return {"TCP": tcp, "Web": web}


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


def journal_start(plan, original, etag, plan_sha256, adoption=None):
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
            if adoption is not None:
                initial["adoption"] = adoption
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


def _completion_path(owner_nonce):
    if not isinstance(owner_nonce, str) or not re.fullmatch(r"[0-9a-f]{64}", owner_nonce):
        raise Stop("Malformed owner completion nonce")
    return JOURNAL / (owner_nonce + ".complete.json")


def _read_completion(owner_nonce):
    path = _completion_path(owner_nonce)
    try:
        before = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o600 or before.st_size <= 0 or before.st_size > 16384):
            raise Stop("TLS completion record metadata is unsafe")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            opened = os.fstat(fd)
            if opened.st_ino != before.st_ino or opened.st_dev != before.st_dev or opened.st_size != before.st_size:
                raise Stop("TLS completion record changed while opening")
            raw = b""
            while len(raw) <= 16384:
                chunk = os.read(fd, min(65536, 16385 - len(raw)))
                if not chunk:
                    break
                raw += chunk
            if len(raw) != opened.st_size or len(raw) > 16384:
                raise Stop("TLS completion record changed while reading")
        finally:
            os.close(fd)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise Stop("TLS completion record is unavailable or unsafe") from error
    value = strict_json(raw)
    completion_v2 = isinstance(value, dict) and value.get("schema") == COMPLETION_SCHEMA_V2
    if (not isinstance(value, dict) or set(value) != (COMPLETION_KEYS_V2 if completion_v2 else COMPLETION_KEYS)
            or value.get("schema") != (COMPLETION_SCHEMA_V2 if completion_v2 else COMPLETION_SCHEMA) or value.get("owner_nonce") != owner_nonce
            or value.get("status") != "complete" or type(value.get("completed_at")) is not int
            or value["completed_at"] <= 0
            or canonical(value) != raw):
        raise Stop("TLS completion record does not match its immutable schema")
    for field in ("plan_sha256", "prepare_receipt_sha256", "upgrade_receipt_nonce", "upgrade_receipt_sha256",
                  "nginx_configuration_sha256", "tls_config_sha256", "certificate_sha256",
                  "unchanged_runtime_sha256", "approval_nonce", "evidence_sha256", "serve_config_sha256"):
        if not _sha(value.get(field)):
            raise Stop("TLS completion has malformed digest or nonce links")
    if (not isinstance(value.get("source_sha"), str) or not re.fullmatch(r"[0-9a-f]{40}", value["source_sha"])
            or not isinstance(value.get("operation_id"), str)
            or not re.fullmatch(r"[0-9a-f]{32}", value["operation_id"])
            or value.get("project") != UPGRADE_PROJECT + "-upgrade-" + value["operation_id"]
            or any(not _sha(value.get(field)) for field in ("previous_nginx_id", "nginx_id"))
            or value["previous_nginx_id"] == value["nginx_id"]
            or not isinstance(value.get("nginx_image_id"), str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["nginx_image_id"])):
        raise Stop("TLS completion target or nginx identities are malformed")
    if completion_v2 and any(not _sha(value.get(name)) for name in (
            "prior_generation_sha256", "prior_tls_owner_nonce", "prior_tls_completion_sha256")):
        raise Stop("TLS v2 completion predecessor links are malformed")
    expected_networks = {value["project"] + "_isolated", value["project"] + "_ingress"}
    if (not isinstance(value.get("nginx_network_ids"), dict)
            or set(value["nginx_network_ids"]) != expected_networks
            or any(not _sha(network_id) for network_id in value["nginx_network_ids"].values())
            or not isinstance(value.get("serve_etag"), str) or not value["serve_etag"]
            or len(value["serve_etag"]) > 256
            or any(ord(char) < 0x20 or ord(char) == 0x7f for char in value["serve_etag"])):
        raise Stop("TLS completion network or Serve identity is malformed")
    return value


def _completion_value(plan, plan_sha256, prepare_sha256, prepared, serve_result):
    value = {
        "schema": COMPLETION_SCHEMA_V2 if plan.get("schema") == PLAN_SCHEMA_V3 else COMPLETION_SCHEMA,
        "owner_nonce": plan["nonce"], "plan_sha256": plan_sha256,
        "prepare_receipt_sha256": prepare_sha256, "upgrade_receipt_nonce": plan["upgrade_receipt_nonce"],
        "upgrade_receipt_sha256": plan["upgrade_receipt_sha256"], "source_sha": plan["source_sha"],
        "operation_id": plan["operation_id"], "project": plan["project"],
        "previous_nginx_id": prepared["previous_nginx_id"], "nginx_id": prepared["nginx_id"],
        "nginx_configuration_sha256": prepared["nginx_configuration_sha256"],
        "nginx_image_id": prepared["nginx_image_id"], "nginx_network_ids": prepared["nginx_network_ids"],
        "tls_config_sha256": plan["tls_config_sha256"], "certificate_sha256": plan["certificate_sha256"],
        "unchanged_runtime_sha256": prepared["unchanged_runtime_sha256"],
        "approval_nonce": prepared["approval_nonce"], "evidence_sha256": prepared["evidence_sha256"],
        "serve_config_sha256": serve_result["serve_config_sha256"], "serve_etag": serve_result["serve_etag"],
        "completed_at": int(time.time()), "status": "complete",
    }
    if plan.get("schema") == PLAN_SCHEMA_V3:
        value.update({name: plan[name] for name in (
            "prior_generation_sha256", "prior_tls_owner_nonce", "prior_tls_completion_sha256")})
    return value


def _publish_completion(value):
    ensure_journal_dir()
    final = _completion_path(value["owner_nonce"])
    existing = _read_completion(value["owner_nonce"])
    if existing is not None:
        if not all(existing.get(key) == expected for key, expected in value.items() if key != "completed_at"):
            raise Stop("A conflicting immutable TLS completion already exists")
        return existing
    temporary = JOURNAL / ("." + value["owner_nonce"] + "." + secrets.token_hex(16) + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(temporary, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1:
                raise Stop("Temporary TLS completion metadata is unsafe")
            _write_all(fd, canonical(value))
            os.fsync(fd)
        finally:
            os.close(fd)
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise Stop("Atomic no-replace completion publication is unavailable")
        renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameat2.restype = ctypes.c_int
        if renameat2(-100, os.fsencode(temporary), -100, os.fsencode(final), 1) != 0:
            error = ctypes.get_errno()
            if error == 17:
                current = _read_completion(value["owner_nonce"])
                if current is not None and all(current.get(key) == expected for key, expected in value.items()
                                               if key != "completed_at"):
                    return current
            raise OSError(error, os.strerror(error))
        dirfd = os.open(JOURNAL, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
        result = _read_completion(value["owner_nonce"])
        if result != value:
            raise Stop("Published TLS completion differs from its verified bytes")
        return result
    except OSError as error:
        raise Stop("Atomic immutable TLS completion could not be published") from error
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _recover_v2_completion(plan, plan_sha256, prepare_sha256, prepared):
    path = JOURNAL / (plan["nonce"] + ".jsonl")
    raw = _read_root_regular(path, 1024 * 1024)
    rows = []
    chunks = raw.split(b"\n")
    complete_lines = chunks[:-1]  # A non-newline-terminated tail is not a durable event.
    if not complete_lines:
        raise Stop("TLS journal has no complete newline-terminated records")
    for line in complete_lines:
        if not line:
            raise Stop("TLS journal contains an empty durable record")
        rows.append(strict_json(line))
    if not rows or not isinstance(rows[0], dict):
        raise Stop("TLS journal has no durable prepared intent")
    initial = rows[0]
    adoption = initial.get("adoption")
    expected_adoption = {"prepare_receipt_sha256": prepare_sha256,
                         "upgrade_receipt_nonce": plan["upgrade_receipt_nonce"],
                         "upgrade_receipt_sha256": plan["upgrade_receipt_sha256"],
                         "source_sha": plan["source_sha"], "operation_id": plan["operation_id"],
                         "project": plan["project"], "previous_nginx_id": prepared["previous_nginx_id"],
                         "nginx_id": prepared["nginx_id"],
                         "nginx_configuration_sha256": prepared["nginx_configuration_sha256"],
                         "nginx_image_id": prepared["nginx_image_id"],
                         "nginx_network_ids": prepared["nginx_network_ids"],
                         "unchanged_runtime_sha256": prepared["unchanged_runtime_sha256"]}
    if (initial.get("schema") != "belib-tls-owner-journal/v1" or initial.get("event") != "prepared"
            or initial.get("plan_sha256") != plan_sha256 or adoption != expected_adoption):
        raise Stop("TLS journal does not match the exact adopted plan and preparation receipt")
    try:
        original = base64.b64decode(initial["original_config_b64"], validate=True)
    except (KeyError, ValueError, TypeError) as error:
        raise Stop("TLS journal baseline encoding is invalid") from error
    if (digest(original) != initial.get("original_sha256")
            or digest(original) != plan["serve_config_sha256"]
            or initial.get("original_etag") != plan["serve_etag"]):
        raise Stop("TLS journal baseline differs from the owner plan")
    commits = [row for row in rows[1:] if isinstance(row, dict)
               and row.get("event") == "retirement-committed"
               and row.get("phase") == "add-custom-tls"]
    if not commits:
        raise Stop("TLS journal has no durable final Serve commit to reconcile")
    commit = commits[-1]
    final = activate_custom_tls(remove_legacy_handler(original))
    api = LocalAPI()
    api.status()
    live, etag = api.get()
    if (canonical(config(live)) != canonical(config(final))
            or digest(live) != commit.get("config_sha256") or etag != commit.get("etag")):
        raise Stop("Live Serve state does not match the exact journal-owned final state")
    http_18083()
    pin = plan["certificate_sha256"]
    tls_request(443, CUSTOM, "/health", connect_host=SELF_IP, fingerprint=pin)
    tls_unknown_rejected(443, connect_host=SELF_IP)
    tls_no_sni_rejected(443, connect_host=SELF_IP)
    tls_legacy_sni_rejected(443, connect_host=SELF_IP)
    tls_self_sni_rejected(443, connect_host=SELF_IP)
    tls_request(443, CUSTOM, "/health", connect_host=SELF_IP, fingerprint=pin)
    return {"status": "complete", "serve_config_sha256": digest(live), "serve_etag": etag}


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


def _serve_transaction(plan, plan_sha256, adoption=None):
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
    journal = (journal_start(plan, original, etag, plan_sha256) if adoption is None
               else journal_start(plan, original, etag, plan_sha256, adoption=adoption))
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


def run(plan_raw: bytes, expected_plan_sha256: str, prepared_raw=None, prepared_sha256=None):
    """Execute a digest-pinned plan; v1 retains its original two-argument API."""
    _check_owner_identity()
    if not isinstance(plan_raw, bytes) or len(plan_raw) > 16384:
        raise Stop("Plan input exceeds the fixed byte bound")
    if not isinstance(expected_plan_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_plan_sha256):
        raise Stop("Verified-loader plan digest is malformed")
    observed_sha = digest(plan_raw)
    if observed_sha != expected_plan_sha256:
        raise Stop("In-memory plan bytes do not match the reviewed loader digest")
    parsed = strict_json(plan_raw)
    is_v3 = isinstance(parsed, dict) and parsed.get("schema") == PLAN_SCHEMA_V3
    is_v2 = isinstance(parsed, dict) and parsed.get("schema") in {PLAN_SCHEMA_V2, PLAN_SCHEMA_V3}
    plan = validate_plan(parsed, allow_expired=is_v2)
    if canonical(plan) != plan_raw:
        raise Stop("Plan bytes must be canonical JSON")
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    def interrupt(signum, frame):
        raise OperationInterrupted(f"signal {signum}")
    try:
        for sig in previous:
            signal.signal(sig, interrupt)
        with operation_lock():
            if not is_v2:
                return _serve_transaction(plan, observed_sha)
            prepared = _validate_prepare_v2(prepared_raw, prepared_sha256, plan, observed_sha)
            existing = _read_completion(plan["nonce"])
            if existing is not None:
                expected_fields = {
                    "plan_sha256": observed_sha, "prepare_receipt_sha256": prepared_sha256,
                    "upgrade_receipt_nonce": plan["upgrade_receipt_nonce"],
                    "upgrade_receipt_sha256": plan["upgrade_receipt_sha256"], "source_sha": plan["source_sha"],
                    "operation_id": plan["operation_id"], "project": plan["project"],
                    "previous_nginx_id": prepared["previous_nginx_id"], "nginx_id": prepared["nginx_id"],
                    "nginx_configuration_sha256": prepared["nginx_configuration_sha256"],
                    "nginx_image_id": prepared["nginx_image_id"], "nginx_network_ids": prepared["nginx_network_ids"],
                    "tls_config_sha256": plan["tls_config_sha256"], "certificate_sha256": plan["certificate_sha256"],
                    "unchanged_runtime_sha256": prepared["unchanged_runtime_sha256"],
                    "approval_nonce": prepared["approval_nonce"], "evidence_sha256": prepared["evidence_sha256"],
                }
                if is_v3:
                    expected_fields.update({name: plan[name] for name in (
                        "prior_generation_sha256", "prior_tls_owner_nonce", "prior_tls_completion_sha256")})
                if any(existing.get(key) != value for key, value in expected_fields.items()):
                    raise Stop("Existing TLS completion belongs to conflicting immutable inputs")
                return existing
            if is_v3:
                try:
                    validate_plan(plan)
                except Stop as error:
                    raise Stop("Expired or out-of-window TLS v3 plan cannot publish its first completion") from error
            adoption = {"prepare_receipt_sha256": prepared_sha256,
                        "upgrade_receipt_nonce": plan["upgrade_receipt_nonce"],
                        "upgrade_receipt_sha256": plan["upgrade_receipt_sha256"],
                        "source_sha": plan["source_sha"], "operation_id": plan["operation_id"],
                        "project": plan["project"], "previous_nginx_id": prepared["previous_nginx_id"],
                        "nginx_id": prepared["nginx_id"],
                        "nginx_configuration_sha256": prepared["nginx_configuration_sha256"],
                        "nginx_image_id": prepared["nginx_image_id"],
                        "nginx_network_ids": prepared["nginx_network_ids"],
                        "unchanged_runtime_sha256": prepared["unchanged_runtime_sha256"]}
            journal_path = JOURNAL / (plan["nonce"] + ".jsonl")
            try:
                journal_path.lstat()
                prior = True
            except FileNotFoundError:
                prior = False
            if is_v3:
                serve_result = _verify_stable_active_serve(plan, prepared, observed_sha)
            elif prior:
                serve_result = _recover_v2_completion(plan, observed_sha, prepared_sha256, prepared)
            else:
                validate_plan(plan)
                serve_result = _serve_transaction(plan, observed_sha, adoption=adoption)
            completion = _completion_value(plan, observed_sha, prepared_sha256, prepared, serve_result)
            return _publish_completion(completion)
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
