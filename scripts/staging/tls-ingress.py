#!/usr/bin/env python3
"""Read-only validation and signed-state preparation for bounded TLS ingress."""

from __future__ import annotations

import copy
import hashlib
import http.client
import json
import os
import re
import socket
import stat
import subprocess
from pathlib import Path
from typing import Any


HOST = "ubuntu-worker"
PRINCIPAL = "hiroyuki"
UID = 1000
PROJECT = "belib-isolated-i5"
CUSTOM_HOST = "stg-bl.hiroyuki9614.com"
TAILNET_HOST = "ubuntu-worker.tail63cd2d.ts.net"
PRIOR_APPLICATION_SHA = "81256268276d7ead95029849037f69ec6beec202"
EXPECTED_CERT_SHA256 = "6db154fe51f97628b597340f86806f472f6283dc7fd1a90e90d5a3bb26fba46b"
CERT_PATH = Path("/home/hiroyuki/.config/belib-staging/letsencrypt/config/live/stg-bl.hiroyuki9614.com/fullchain.pem")
KEY_PATH = Path("/home/hiroyuki/.config/belib-staging/letsencrypt/config/live/stg-bl.hiroyuki9614.com/privkey.pem")
TLS_CONFIG_PATHS = (
    Path("scripts/staging/compose-tls.yaml"),
    Path("scripts/staging/nginx-tls.conf"),
    Path("scripts/staging/nginx-tls-default.conf"),
    Path("scripts/staging/nginx-tls-locations.conf"),
)
TAILSCALE_VERSION = "1.104.5"
TAILSCALE_BUILD = "1.104.5-t788384763-gd9218e278"
TLS_STATE_FIELDS = {
    "schema", "source_sha", "prior_application_sha", "host", "principal", "uid", "project",
    "retained_state_sha256", "serve_config_sha256", "serve_etag", "certificate_sha256", "tls_config_sha256",
    "funnel_enabled", "unknown_sni", "owner_plan_sha256",
}
BRI_PATH = "/bri-referee-pilot-comp.png"
EXPECTED_SERVE_ETAG = "031c8cd8bbc73f27c4b6a503142d1dd0a0bc0e0f1536a497041a4faa0cd1de5e"
BRI_FILE = "/home/hiroyuki/tailscale-share/bri-referee-pilot-comp.png"


class TlsIngressError(Exception):
    pass


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _run_openssl(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["/usr/bin/openssl", *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
            timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise TlsIngressError("OpenSSL certificate check failed") from error


def inspect_certificate(certificate: Path, private_key: Path, *, expected_fingerprint: str = EXPECTED_CERT_SHA256) -> str:
    """Validate certificate identity and local key match without emitting key data."""
    try:
        resolved_key = private_key.resolve(strict=True)
        key_stat = os.stat(resolved_key)
        if not stat.S_ISREG(key_stat.st_mode) or stat.S_IMODE(key_stat.st_mode) != 0o600:
            raise TlsIngressError("TLS private-key target must be a regular mode-0600 file")
    except OSError as error:
        raise TlsIngressError("TLS private-key target is unavailable") from error

    result = _run_openssl(["x509", "-in", str(certificate), "-noout", "-fingerprint", "-sha256", "-ext", "subjectAltName", "-enddate", "-checkend", "86400"])
    if result.returncode:
        raise TlsIngressError("TLS certificate is invalid or expires within 24 hours")
    output = result.stdout.decode("ascii", "replace")
    fingerprint_match = re.search(r"sha256 Fingerprint=([0-9A-Fa-f:]+)", output, re.IGNORECASE)
    normalized = fingerprint_match.group(1).replace(":", "").lower() if fingerprint_match else ""
    if normalized != expected_fingerprint:
        raise TlsIngressError("TLS certificate fingerprint does not match the approved leaf")
    sans = re.search(r"X509v3 Subject Alternative Name:\s*\n\s*([^\n]+)", output)
    if not sans or f"DNS:{CUSTOM_HOST}" not in sans.group(1).split(", "):
        raise TlsIngressError("TLS certificate SAN does not include the exact custom hostname")

    cert_pub = _run_openssl(["x509", "-in", str(certificate), "-pubkey", "-noout"])
    key_pub = _run_openssl(["pkey", "-in", str(resolved_key), "-pubout"])
    if cert_pub.returncode or key_pub.returncode or cert_pub.stdout != key_pub.stdout:
        raise TlsIngressError("TLS certificate and private key do not match")
    return normalized


def validate_serve_config(raw: bytes) -> dict[str, Any]:
    """Validate the node-level JSON shape and reject Funnel in every transition phase."""
    try:
        config = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise TlsIngressError("Original Tailscale Serve config is not valid JSON") from error
    if not isinstance(config, dict):
        raise TlsIngressError("Original Tailscale Serve config has an invalid shape")
    tcp = config.get("TCP")
    web = config.get("Web")
    funnel = config.get("AllowFunnel", {})
    if not isinstance(tcp, dict) or not isinstance(web, dict) or not isinstance(funnel, dict):
        raise TlsIngressError("Serve snapshot is missing its legacy node-level configuration")
    if any(value is True for value in funnel.values()):
        raise TlsIngressError("Tailscale Funnel must remain disabled")
    return config


def validate_original_serve_config(raw: bytes) -> dict[str, Any]:
    """Validate the exact legacy HTTPS 443 + BRI baseline before phase A."""
    config = validate_serve_config(raw)
    tcp, web = config["TCP"], config["Web"]
    if set(config) != {"TCP", "Web"}:
        raise TlsIngressError("Serve baseline contains unknown node-level scopes")
    if set(tcp) != {"443"} or len(web) != 1:
        raise TlsIngressError("Serve baseline must contain only TCP 443 and one legacy Web host")
    if web != {"fedora-worker.tail63cd2d.ts.net:443":
               {"Handlers": {BRI_PATH: {"Path": BRI_FILE}}}}:
        raise TlsIngressError("Serve baseline must retain the exact reviewed BRI file handler")
    bri_handlers = []
    for hostname_port, server in web.items():
        if not isinstance(hostname_port, str) or not hostname_port.endswith(":443") or not isinstance(server, dict):
            continue
        handlers = server.get("Handlers", {})
        if isinstance(handlers, dict) and BRI_PATH in handlers:
            bri_handlers.append((hostname_port, handlers[BRI_PATH]))
    if len(bri_handlers) != 1 or len(web) != 1 or not isinstance(bri_handlers[0][1], dict):
        raise TlsIngressError("Exactly one existing BRI Serve file handler must be retained")
    server = next(iter(web.values()))
    handlers = server.get("Handlers") if isinstance(server, dict) else None
    if (not isinstance(server, dict) or set(server) != {"Handlers"} or not isinstance(handlers, dict)
            or set(handlers) != {BRI_PATH} or set(bri_handlers[0][1]) != {"Path"}
            or not isinstance(bri_handlers[0][1].get("Path"), str)):
        raise TlsIngressError("Existing BRI Serve handler path is malformed")
    if not isinstance(tcp.get("443"), dict) or set(tcp["443"]) != {"HTTPS"} or tcp["443"].get("HTTPS") is not True:
        raise TlsIngressError("Original Tailscale HTTPS listener on 443 is not present")
    return config


def build_tls_binding(
    *, source_sha: str, prior_application_sha: str, retained_manifest: dict[str, Any],
    serve_config: bytes, serve_etag: str, certificate_sha256: str, tls_config: bytes,
    owner_plan_sha256: str, tls_config_sha256: str | None = None,
) -> dict[str, str]:
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise TlsIngressError("Current source SHA must be a full lowercase Git SHA")
    if prior_application_sha != PRIOR_APPLICATION_SHA:
        raise TlsIngressError("Prior live application source must remain the reviewed 812 snapshot")
    if (not isinstance(retained_manifest, dict)
            or retained_manifest.get("schema") != "belib-staging-retained-state/v1"
            or retained_manifest.get("project") != PROJECT
            or retained_manifest.get("prior_source_sha") != "6963103fd6bec332b4057457d07b890fe5c11e73"):
        raise TlsIngressError("Retained container, volume, and network manifest is not the reviewed staging state")
    _validate_retained_identities(retained_manifest)
    validate_original_serve_config(serve_config)
    certificate_sha256 = certificate_sha256.lower()
    if certificate_sha256 != EXPECTED_CERT_SHA256:
        raise TlsIngressError("Certificate fingerprint does not match the owner-issued leaf")
    if not re.fullmatch(r"[0-9a-f]{64}", serve_etag):
        raise TlsIngressError("Tailscale Serve ETag is malformed")
    if serve_etag != EXPECTED_SERVE_ETAG:
        raise TlsIngressError("Tailscale Serve ETag differs from the owner-reviewed baseline")
    if not re.fullmatch(r"[0-9a-f]{64}", owner_plan_sha256):
        raise TlsIngressError("Owner plan digest is malformed")
    if tls_config_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", tls_config_sha256):
        raise TlsIngressError("TLS configuration bundle digest is malformed")
    return {
        "prior_application_sha": prior_application_sha,
        "retained_state_sha256": sha256(canonical_json(retained_manifest)),
        "serve_config_sha256": sha256(serve_config),
        "serve_etag": serve_etag,
        "certificate_sha256": certificate_sha256,
        "tls_config_sha256": tls_config_sha256 or sha256(tls_config),
        "owner_plan_sha256": owner_plan_sha256,
    }


def _validate_retained_identities(manifest: dict[str, Any]) -> None:
    containers = manifest.get("containers")
    volumes = manifest.get("volumes")
    if not isinstance(containers, list) or not isinstance(volumes, list):
        raise TlsIngressError("Retained container and volume identities are required")
    db = [item for item in containers if isinstance(item, dict) and item.get("service") == "db"]
    if len(containers) != 4 or len(db) != 1 or db[0].get("id") != "9ab9d141599e8b33f51a1e4e9bcd16d66e92b9d134889014d9d9bd618b42f73d":
        raise TlsIngressError("Retained service container identities differ from the reviewed rollback state")
    expected_volumes = {"belib-isolated-i5_staging-postgres-data", "belib-isolated-i5_staging-book-files"}
    if len(volumes) != 2 or {item.get("name") for item in volumes if isinstance(item, dict)} != expected_volumes:
        raise TlsIngressError("Both original staging volumes must be retained exactly")


def make_tls_state(binding: dict[str, str], source_sha: str) -> dict[str, Any]:
    return {
        "schema": "belib-staging-tls-state/v1",
        "source_sha": source_sha,
        **binding,
        "host": HOST,
        "principal": PRINCIPAL,
        "uid": UID,
        "project": PROJECT,
        "funnel_enabled": False,
        "unknown_sni": "reject",
    }


def tls_config_digest(source_root: Path) -> str:
    files = [source_root / "compose.staging.yaml", source_root / "scripts/staging/nginx-default.conf",
             *(source_root / item for item in TLS_CONFIG_PATHS)]
    inventory = []
    for path in files:
        try:
            relative = path.relative_to(source_root).as_posix()
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode):
                raise TlsIngressError("TLS configuration input must be a regular tracked file")
            content = path.read_bytes()
        except OSError as error:
            raise TlsIngressError("A required staging TLS configuration input is missing") from error
        inventory.append({"path": relative, "sha256": sha256(content)})
    return sha256(canonical_json(inventory))


def validate_tls_compose_overlay(base: dict[str, Any], combined: dict[str, Any], source_root: Path,
                                 loopback_port: int) -> None:
    """Allow the overlay to change nginx only, on exact loopback binds and readonly cert mounts."""
    if not isinstance(base, dict) or not isinstance(combined, dict):
        raise TlsIngressError("Compose model must be an object")
    if set(base.get("services", {})) != set(combined.get("services", {})):
        raise TlsIngressError("TLS overlay must preserve the existing staging service set")
    if base.get("networks") != combined.get("networks") or base.get("volumes") != combined.get("volumes"):
        raise TlsIngressError("TLS overlay must preserve all staging networks and named volumes")
    for service in set(base["services"]) - {"nginx"}:
        if base["services"][service] != combined["services"][service]:
            raise TlsIngressError("TLS overlay may change only the existing nginx service")
    original, actual = base["services"].get("nginx"), combined["services"].get("nginx")
    if not isinstance(original, dict) or not isinstance(actual, dict):
        raise TlsIngressError("TLS overlay is missing the existing nginx service")
    allowed_keys = set(original) | {"extra_hosts"}
    if set(actual) - allowed_keys or any(actual.get(key) != value for key, value in original.items()
                                         if key not in {"ports", "volumes"}):
        raise TlsIngressError("TLS overlay changed nginx behavior outside its ports and readonly config mounts")
    expected_ports = {
        ("127.0.0.1", str(loopback_port), 8080, "tcp", "ingress"),
        ("127.0.0.1", "18443", 8443, "tcp", "ingress"),
    }
    ports = actual.get("ports")
    if not isinstance(ports, list) or len(ports) != 2:
        raise TlsIngressError("TLS overlay port map is malformed")
    observed_ports = []
    for port in ports:
        if not isinstance(port, dict):
            raise TlsIngressError("TLS overlay port map is malformed")
        observed_ports.append((port.get("host_ip"), str(port.get("published")), port.get("target"),
                               port.get("protocol"), port.get("mode")))
    if len(set(observed_ports)) != 2 or set(observed_ports) != expected_ports:
        raise TlsIngressError("TLS ingress must publish exactly HTTP and raw TLS on loopback")
    expected_sources = {
        "/etc/nginx/nginx.conf": source_root / "scripts/staging/nginx-tls.conf",
        "/etc/nginx/conf.d/default.conf": source_root / "scripts/staging/nginx-tls-default.conf",
        "/etc/nginx/conf.d/belib-proxy-locations.conf": source_root / "scripts/staging/nginx-tls-locations.conf",
        "/run/belib-tls/fullchain.pem": CERT_PATH,
        "/run/belib-tls/privkey.pem": KEY_PATH,
    }
    mounts = actual.get("volumes")
    if not isinstance(mounts, list) or len(mounts) != len(expected_sources):
        raise TlsIngressError("TLS overlay must mount exactly its three configs and two certificate files")
    by_target = {mount.get("target"): mount for mount in mounts if isinstance(mount, dict)}
    if set(by_target) != set(expected_sources):
        raise TlsIngressError("TLS overlay bind targets differ from the reviewed ingress set")
    for target, wanted in expected_sources.items():
        mount = by_target[target]
        try:
            observed = Path(mount.get("source", "")).resolve(strict=True)
            expected = wanted.resolve(strict=True)
        except OSError as error:
            raise TlsIngressError("A TLS bind source does not exist") from error
        bind = mount.get("bind")
        if (mount.get("type") != "bind" or observed != expected or mount.get("read_only") is not True
                or not isinstance(bind, dict) or bind.get("create_host_path") is not False):
            raise TlsIngressError("TLS configuration and certificate binds must be exact and readonly")
    extra_hosts = actual.get("extra_hosts")
    if extra_hosts not in (
        {TAILNET_HOST: "100.109.171.70"},
        [f"{TAILNET_HOST}:100.109.171.70"],
        [f"{TAILNET_HOST}=100.109.171.70"],
    ):
        raise TlsIngressError("TLS stream may route only the verified current Tailnet hostname")


def tls_state_sha256(state: dict[str, Any]) -> str:
    if set(state) != TLS_STATE_FIELDS or state.get("schema") != "belib-staging-tls-state/v1":
        raise TlsIngressError("TLS state fields do not match the fixed schema")
    if (state.get("host"), state.get("principal"), state.get("uid"), state.get("project")) != (HOST, PRINCIPAL, UID, PROJECT):
        raise TlsIngressError("TLS state target identity is not authorized")
    if state.get("funnel_enabled") is not False or state.get("unknown_sni") != "reject":
        raise TlsIngressError("TLS state must remain Tailnet-only and reject unknown SNI")
    if (not re.fullmatch(r"[0-9a-f]{40}", state.get("source_sha", ""))
            or state.get("prior_application_sha") != PRIOR_APPLICATION_SHA
            or any(not re.fullmatch(r"[0-9a-f]{64}", state.get(name, "")) for name in (
                "retained_state_sha256", "serve_config_sha256", "serve_etag", "certificate_sha256", "tls_config_sha256", "owner_plan_sha256"
            ))
            or state.get("certificate_sha256") != EXPECTED_CERT_SHA256):
        raise TlsIngressError("TLS state hashes do not match the fixed ingress contract")
    return sha256(canonical_json(state))


def signed_tls_binding(binding: dict[str, str], source_sha: str) -> dict[str, str]:
    state = make_tls_state(binding, source_sha)
    return {**binding, "tls_state_sha256": tls_state_sha256(state)}


class LocalApiError(TlsIngressError):
    def __init__(self, status: int):
        super().__init__(f"Tailscale LocalAPI denied or rejected the Serve update (HTTP {status})")
        self.status = status


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: Path, timeout: float):
        super().__init__("local-tailscaled.sock", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(str(self.socket_path))
        except OSError:
            sock.close()
            raise
        self.sock = sock


class TailscaleServeClient:
    """Minimal 1.104.5 LocalAPI surface: GET/POST with mandatory ETag CAS."""

    def __init__(self, socket_path: Path = Path("/var/run/tailscale/tailscaled.sock"), timeout: float = 5):
        self.socket_path = socket_path
        self.timeout = timeout

    def request(self, method: str, body: bytes | None = None, etag: str | None = None) -> tuple[int, bytes, str]:
        if method not in ("GET", "POST") or (method == "POST" and not etag):
            raise TlsIngressError("Only ETag-guarded Serve config GET/POST are supported")
        connection = _UnixHTTPConnection(self.socket_path, self.timeout)
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if etag is not None:
            headers["If-Match"] = etag
        try:
            connection.request(method, "/localapi/v0/serve-config", body=body, headers=headers)
            response = connection.getresponse()
            data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise TlsIngressError("Tailscale Serve config response exceeds its fixed bound")
            observed_etag = response.getheader("Etag", "")
            if response.status != 200:
                raise LocalApiError(response.status)
            if method == "GET" and not re.fullmatch(r"[0-9a-f]{64}", observed_etag):
                raise TlsIngressError("Tailscale Serve config response omitted its ETag")
            return response.status, data, observed_etag
        except LocalApiError:
            raise
        except (OSError, http.client.HTTPException, TimeoutError) as error:
            raise TlsIngressError("Tailscale LocalAPI transport failed") from error
        finally:
            connection.close()

    def get(self) -> tuple[bytes, str]:
        _, raw, etag = self.request("GET")
        _config_object(raw)
        return raw, etag

    def set(self, body: bytes, etag: str) -> None:
        self.request("POST", body, etag)


def _config_object(raw: bytes) -> dict[str, Any]:
    try:
        config = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise TlsIngressError("Tailscale Serve config is malformed") from error
    if not isinstance(config, dict) or not isinstance(config.get("TCP"), dict) or not isinstance(config.get("Web"), dict):
        raise TlsIngressError("Tailscale Serve config has an unsupported shape")
    def has_funnel_enabled(value: Any) -> bool:
        if isinstance(value, dict):
            return any((key == "AllowFunnel" and isinstance(child, dict) and any(flag is True for flag in child.values()))
                       or has_funnel_enabled(child) for key, child in value.items())
        if isinstance(value, list):
            return any(has_funnel_enabled(child) for child in value)
        return False
    if has_funnel_enabled(config):
        raise TlsIngressError("Tailscale Funnel must remain disabled")
    return config


def _canonical_config(config: dict[str, Any]) -> bytes:
    return canonical_json(config)


def plan_phase_a(original_raw: bytes, current_dns: str = TAILNET_HOST) -> bytes:
    if current_dns != TAILNET_HOST:
        raise TlsIngressError("Current Tailscale DNS name does not match the reviewed i5 host")
    original = validate_original_serve_config(original_raw)
    tcp, web = original["TCP"], original["Web"]
    if ("8443" in tcp or f"{current_dns}:8443" in web
            or "fedora-worker.tail63cd2d.ts.net:8443" in web):
        raise TlsIngressError("Tailscale port 8443 is already occupied")
    old_bri = [(name, value) for name, value in web.items() if name.endswith(":443")
               and isinstance(value, dict) and BRI_PATH in value.get("Handlers", {})]
    other_443 = [name for name in web if name.endswith(":443") and all(name != old_name for old_name, _ in old_bri)]
    if len(old_bri) != 1 or other_443:
        raise TlsIngressError("Original Serve 443 handlers are not the reviewed single BRI handler")
    updated = copy.deepcopy(original)
    updated["TCP"]["8443"] = {"HTTPS": True}
    updated["Web"]["fedora-worker.tail63cd2d.ts.net:8443"] = copy.deepcopy(old_bri[0][1])
    updated["Web"][f"{current_dns}:8443"] = copy.deepcopy(old_bri[0][1])
    return _canonical_config(updated)


def plan_phase_b(phase_a_raw: bytes) -> bytes:
    updated = copy.deepcopy(_config_object(phase_a_raw))
    old_bri = [(name, value) for name, value in updated["Web"].items()
               if name.endswith(":443") and isinstance(value, dict) and BRI_PATH in value.get("Handlers", {})]
    other_443 = [name for name in updated["Web"] if name.endswith(":443") and all(name != old_name for old_name, _ in old_bri)]
    if (len(old_bri) != 1 or other_443 or updated["TCP"].get("443", {}).get("HTTPS") is not True
            or updated["TCP"].get("8443", {}).get("HTTPS") is not True
            or updated["Web"].get("fedora-worker.tail63cd2d.ts.net:8443", {}).get("Handlers")
            != old_bri[0][1].get("Handlers")
            or updated["Web"].get(f"{TAILNET_HOST}:8443", {}).get("Handlers") != old_bri[0][1].get("Handlers")):
        raise TlsIngressError("Phase B may remove only the reviewed original HTTPS 443 mapping")
    del updated["TCP"]["443"]
    del updated["Web"][old_bri[0][0]]
    return _canonical_config(updated)


def plan_phase_c(phase_b_raw: bytes) -> bytes:
    updated = copy.deepcopy(_config_object(phase_b_raw))
    if "443" in updated["TCP"] or any(name.endswith(":443") for name in updated["Web"]):
        raise TlsIngressError("Phase C requires the original 443 HTTPS mapping to be absent")
    updated["TCP"]["443"] = {"TCPForward": "127.0.0.1:18443"}
    return _canonical_config(updated)


def _same_config(raw: bytes, expected: bytes) -> bool:
    return canonical_json(json.loads(raw)) == canonical_json(json.loads(expected))


def _set_cas(client: TailscaleServeClient, expected_raw: bytes, expected_etag: str, desired_raw: bytes) -> tuple[bytes, str]:
    current_raw, current_etag = client.get()
    if current_etag != expected_etag or not _same_config(current_raw, expected_raw):
        raise TlsIngressError("Tailscale Serve config changed outside this operation; refusing overwrite")
    try:
        client.set(desired_raw, current_etag)
    except Exception as error:
        # A timeout or transport failure does not prove POST was unapplied. Reconcile
        # against both operation-owned endpoints before allowing compensation.
        try:
            after_raw, after_etag = client.get()
        except Exception as read_error:
            raise TlsIngressError("Serve POST outcome is unknown; rollback stopped without overwriting config") from read_error
        if _same_config(after_raw, desired_raw) and after_etag != current_etag:
            return after_raw, after_etag
        if _same_config(after_raw, expected_raw) and after_etag == current_etag:
            raise TlsIngressError("Serve compare-and-swap was not applied") from error
        raise TlsIngressError("Serve POST outcome conflicts with both owned states; rollback stopped") from error
    try:
        readback_raw, readback_etag = client.get()
    except Exception:
        # Retry only a read for reconciliation, never the POST.
        try:
            readback_raw, readback_etag = client.get()
        except Exception as read_error:
            raise TlsIngressError("Serve POST succeeded but its state cannot be reconciled; rollback stopped") from read_error
    if not _same_config(readback_raw, desired_raw) or readback_etag == current_etag:
        raise TlsIngressError("Tailscale Serve compare-and-swap readback did not match")
    return readback_raw, readback_etag


def restore_original(client: TailscaleServeClient, original_raw: bytes, original_etag: str,
                     owned_raw: bytes, owned_etag: str, *, remove_tcp443_first: bool) -> str:
    """Restore only while current config still matches our exact owned phase."""
    current_raw, current_etag = client.get()
    if current_etag != owned_etag or not _same_config(current_raw, owned_raw):
        raise TlsIngressError("Rollback refused because Serve config is no longer this operation's transition")
    if remove_tcp443_first:
        phase_b = copy.deepcopy(_config_object(current_raw))
        tcp443 = phase_b["TCP"].get("443")
        if tcp443 != {"TCPForward": "127.0.0.1:18443"}:
            raise TlsIngressError("Rollback expected its exact raw TCP 443 mapping")
        del phase_b["TCP"]["443"]
        phase_b_raw = _canonical_config(phase_b)
        current_raw, current_etag = _set_cas(client, current_raw, current_etag, phase_b_raw)
    else:
        phase_b_raw = current_raw
    # Compare-and-swap with the fresh ETag, and restore the byte-exact original payload.
    if not _same_config(current_raw, phase_b_raw):
        raise TlsIngressError("Rollback state changed before original Serve config restore")
    restored, restored_etag = _set_cas(client, phase_b_raw, current_etag, original_raw)
    return restored_etag


def verify_tailscale_version(version_text: str) -> None:
    lines = version_text.splitlines()
    first_line = lines[0].strip() if lines else ""
    long_versions = [line.strip().removeprefix("long version: ")
                     for line in lines[1:] if line.strip().startswith("long version: ")]
    if first_line == TAILSCALE_BUILD and not long_versions:
        return
    if first_line == TAILSCALE_VERSION and long_versions == [TAILSCALE_BUILD]:
        return
    raise TlsIngressError("Tailscale LocalAPI client build differs from the pinned 1.104.5 build")
