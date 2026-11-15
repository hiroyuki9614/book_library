#!/usr/bin/env -S /usr/bin/python3 -I -B
"""Create a pinned owner plan or perform the guarded rootless nginx TLS prepare."""
from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import importlib.util
import json
import os
import pwd
import re
import signal
import secrets
import socket
import stat
import ssl
import http.client
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
STATE = Path("/home/hiroyuki/.local/state/belib-staging")
PLAN_PATH = STATE / "tls-work-context/owner-plan.json"
TLS_CONTEXT = STATE / "tls-work-context"
PREPARED_ROOT = STATE / "retained-upgrades"
APP_SOURCE = Path("/home/hiroyuki/workspaces/belib-i5-admission-8125626")
ENV_PATH = Path("/home/hiroyuki/.config/belib-staging/i5.env")
APP812 = "81256268276d7ead95029849037f69ec6beec202"
PROJECT = "belib-isolated-i5"
APP_IMAGES = {
    "backend": "sha256:df3725ef307ebd11eb353e17aecb340c8b909c1451f9032d28fb5cb285a1218c",
    "frontend": "sha256:1ec82024965c504fe2c65d9e5cb3f0d3937b7bbaf4cc11ff843d2ae27d95382c",
}
TLS = None
GUARD = None

class PrepareError(Exception):
    pass


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PrepareError("Required staging helper could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_helpers(source: Path):
    global TLS, GUARD
    TLS = _module("belib_tls_ingress", source / "scripts/staging/tls-ingress.py")
    GUARD = _module("belib_launch_guard", source / "scripts/staging/launch-guard.py")
    return TLS, GUARD


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def validate_identity() -> None:
    try:
        account = pwd.getpwuid(os.getuid())
        if (socket.gethostname() != "ubuntu-worker" or account.pw_name != "hiroyuki"
                or account.pw_uid != 1000 or os.geteuid() != 1000):
            raise PrepareError("Run only as hiroyuki UID 1000 on ubuntu-worker")
    except (KeyError, OSError) as error:
        raise PrepareError("Host identity could not be verified") from error


def verify_cli_source(source: Path) -> None:
    if Path(__file__).resolve(strict=True) != source / "scripts/staging/tls-prepare.py":
        raise PrepareError("Run TLS prepare only from the exact detached admission source")


def _verify_path_parents(path: Path, uid: int = 1000) -> None:
    if not path.is_absolute():
        raise PrepareError("TLS input path must be absolute")
    for ancestor in reversed(path.parents):
        info = os.lstat(ancestor)
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid not in (0, uid) or stat.S_IMODE(info.st_mode) & 0o022):
            raise PrepareError("TLS input path contains an unsafe directory")


def _secure_read(path: Path, *, uid: int = 1000, modes=(0o600,), maximum=65536,
                 require_single_link: bool = False) -> bytes:
    _verify_path_parents(path, uid)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise PrepareError("Pinned TLS plan or staging input is unavailable or unsafe") from error
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) not in modes
                or info.st_size > maximum or (require_single_link and info.st_nlink != 1)):
            raise PrepareError("Pinned TLS plan or staging input has unsafe ownership, mode, or size")
        chunks = bytearray()
        while len(chunks) <= maximum:
            part = os.read(fd, min(8192, maximum + 1 - len(chunks)))
            if not part:
                break
            chunks.extend(part)
        after = os.fstat(fd)
        if (len(chunks) != info.st_size or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                or (require_single_link and after.st_nlink != 1)):
            raise PrepareError("Pinned TLS input changed while being read")
        return bytes(chunks)
    finally:
        os.close(fd)


def create_plan(source_sha: str, manifest: dict, serve_raw: bytes, serve_etag: str,
                certificate_sha: str, tls_config_sha: str, *, now: int | None = None,
                nonce: str | None = None) -> bytes:
    now = int(time.time()) if now is None else now
    if len(source_sha) != 40 or any(c not in "0123456789abcdef" for c in source_sha):
        raise PrepareError("Current source SHA is malformed")
    TLS.validate_original_serve_config(serve_raw)
    binding = TLS.build_tls_binding(source_sha=source_sha, prior_application_sha=TLS.PRIOR_APPLICATION_SHA,
        retained_manifest=manifest, serve_config=serve_raw, serve_etag=serve_etag,
        certificate_sha256=certificate_sha, tls_config=b"", owner_plan_sha256="0" * 64,
        tls_config_sha256=tls_config_sha)
    plan = {
        "schema": TLS.OWNER_PLAN_SCHEMA, "disposition": TLS.OWNER_PLAN_DISPOSITION,
        "source_sha": source_sha,
        "prior_application_sha": TLS.PRIOR_APPLICATION_SHA,
        "retained_state_sha256": binding["retained_state_sha256"],
        "serve_config_sha256": binding["serve_config_sha256"], "serve_etag": serve_etag,
        "certificate_sha256": certificate_sha, "tls_config_sha256": tls_config_sha,
        "nonce": nonce or secrets.token_hex(32), "expires_at": now + 1800,
    }
    return canonical(plan)


def validate_plan(raw: bytes, expected_sha256: str, *, now: int | None = None) -> dict:
    if digest(raw) != expected_sha256:
        raise PrepareError("Owner plan digest does not match the reviewed pinned digest")
    try:
        plan = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PrepareError("Owner plan is malformed") from error
    fields = {"schema", "disposition", "source_sha", "prior_application_sha", "retained_state_sha256", "serve_config_sha256",
              "serve_etag", "certificate_sha256", "tls_config_sha256", "nonce", "expires_at"}
    if not isinstance(plan, dict) or set(plan) != fields or canonical(plan) != raw:
        raise PrepareError("Owner plan fields or canonical encoding are invalid")
    if (plan["schema"] != TLS.OWNER_PLAN_SCHEMA or plan["disposition"] != TLS.OWNER_PLAN_DISPOSITION
            or plan["prior_application_sha"] != TLS.PRIOR_APPLICATION_SHA
            or not isinstance(plan["source_sha"], str) or len(plan["source_sha"]) != 40
            or any(c not in "0123456789abcdef" for c in plan["source_sha"])
            or plan["serve_etag"] != TLS.EXPECTED_SERVE_ETAG
            or plan["certificate_sha256"] != TLS.EXPECTED_CERT_SHA256):
        raise PrepareError("Owner plan does not target the reviewed app baseline")
    for name in ("retained_state_sha256", "serve_config_sha256", "serve_etag", "certificate_sha256", "tls_config_sha256", "nonce"):
        if not isinstance(plan[name], str) or len(plan[name]) != 64 or any(c not in "0123456789abcdef" for c in plan[name]):
            raise PrepareError("Owner plan contains a malformed digest or nonce")
    if plan["serve_etag"] != TLS.EXPECTED_SERVE_ETAG:
        raise PrepareError("Owner plan Serve ETag differs from the exact reviewed baseline")
    if type(plan["expires_at"]) is not int or plan["expires_at"] <= (int(time.time()) if now is None else now) or plan["expires_at"] > (int(time.time()) if now is None else now) + 1800:
        raise PrepareError("Owner plan is expired or exceeds its 30-minute lifetime")
    return plan


def write_plan_once(raw: bytes, path: Path = PLAN_PATH) -> None:
    if path != PLAN_PATH:
        raise PrepareError("Owner plan output path is fixed")
    # Refuse symlinked or non-private state directories; create only the leaf directory.
    state_parent = path.parent.parent
    for ancestor in (Path("/"), *reversed(state_parent.parents), state_parent):
        info = os.lstat(ancestor)
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or (info.st_uid not in (0, os.getuid())) or (stat.S_IMODE(info.st_mode) & 0o022)):
            raise PrepareError("TLS plan parent chain contains an unsafe directory")
    if not path.parent.exists():
        os.mkdir(path.parent, 0o700)
    info = os.lstat(path.parent)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise PrepareError("TLS state directory must be operator-owned mode 0700")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as error:
        raise PrepareError("Owner plan already exists; refusing overwrite") from error
    except OSError as error:
        raise PrepareError("Could not safely create the owner plan") from error
    try:
        with os.fdopen(fd, "wb", closefd=True) as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    except OSError as error:
        path.unlink(missing_ok=True)
        raise PrepareError("Could not persist the owner plan") from error


def _run(command: list[str], *, cwd: Path, env: dict, runner=subprocess.run, timeout=60, capture=False, pass_fds=()):
    try:
        return runner(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                      stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL, text=capture, check=False, timeout=timeout, pass_fds=pass_fds)
    except (OSError, subprocess.SubprocessError) as error:
        raise PrepareError("A guarded staging read or nginx operation failed") from error


def _read_only_rootless(seed, guard):
    """Verify and address the existing rootless socket without creating a Docker context."""
    seed.verify_trusted_docker_plugins()
    endpoint = seed.expected_docker_socket(1000)
    seed.validate_rootless_socket(endpoint, 1000)
    environment = {"PATH": guard.SAFE_PATH, "HOME": str(guard.EXPECTED_HOME),
                   "USER": guard.EXPECTED_USER, "LOGNAME": guard.EXPECTED_USER,
                   "DOCKER_CONFIG": str(guard.EXPECTED_HOME / ".docker")}
    prefix = ["/usr/bin/docker", "--host", f"unix://{endpoint}"]
    result = _run([*prefix, "info", "--format", "{{.Name}}|{{json .SecurityOptions}}"],
                  cwd=guard.EXPECTED_HOME, env=environment, capture=True, timeout=15)
    identity = (result.stdout or "").strip()
    if result.returncode or not identity.startswith("ubuntu-worker|") or '"name=rootless"' not in identity:
        raise PrepareError("Docker did not report the existing ubuntu-worker rootless daemon")
    return environment, endpoint, prefix


def transaction(*, before_manifest: dict, expected_manifest_sha: str, plan_sha: str,
                nginx_identity: str, effects: Any) -> dict:
    """Effect interface is deliberately injectable; changes only nginx and rolls back owned state."""
    if digest(canonical(before_manifest)) != expected_manifest_sha:
        raise PrepareError("Retained staging baseline changed before prepare")
    if (not re.fullmatch(r"[0-9a-f]{64}", getattr(effects, "approval_nonce", ""))
            or not re.fullmatch(r"[0-9a-f]{64}", getattr(effects, "evidence_sha256", ""))):
        raise PrepareError("Fresh signed approval receipt is missing")
    effects.syntax_check()
    mutation_attempted = False
    try:
        mutation_attempted = True
        effects.recreate_nginx()
        after = effects.manifest()
        effects.assert_retained_equal(before_manifest, after, allow_nginx_change=True)
        current_nginx = effects.nginx_identity()
        if (not current_nginx or current_nginx == nginx_identity
                or not getattr(effects, "owns_nginx_change", lambda: False)()):
            raise PrepareError("Nginx did not acquire a new reviewed container identity")
        effects.probe_http()
        effects.probe_https()
        effects.probe_unknown_sni()
        effects.probe_https()
        final = effects.manifest()
        effects.assert_retained_equal(before_manifest, final, allow_nginx_change=True)
        receipt = {"schema": "belib-staging-tls-prepare-receipt/v1", "plan_sha256": plan_sha,
                   "retained_state_sha256": expected_manifest_sha, "nginx_id": current_nginx,
                   "source_sha": effects.source_sha, "tls_config_sha256": effects.tls_config_sha256,
                   "certificate_sha256": effects.certificate_sha256,
                   "approval_nonce": getattr(effects, "approval_nonce", ""),
                   "evidence_sha256": getattr(effects, "evidence_sha256", ""), "prepared": True}
        return receipt
    except BaseException:
        if mutation_attempted:
            current_nginx = effects.nginx_identity()
            if current_nginx == nginx_identity:
                raise
            # Roll back only while the exact newly-created nginx identity remains owned.
            if getattr(effects, "owns_nginx_change", lambda: False)():
                effects.restore_nginx()
            else:
                raise PrepareError("Nginx ownership is uncertain; rollback refused")
        raise


class RuntimeEffects:
    def __init__(self, source: Path, build_root: Path, env_fd: int, docker_env: dict, env_values: dict,
                 plan: dict, plan_sha: str, binding: dict, evidence_sha: str, docker_prefix: list[str],
                 rollback_root: Path):
        self.source, self.build_root, self.env_fd = source, build_root, env_fd
        self.docker_env, self.values, self.plan, self.plan_sha = docker_env, env_values, plan, plan_sha
        self.binding, self.evidence_sha = binding, evidence_sha
        self.evidence_sha256 = evidence_sha
        self.docker_prefix = docker_prefix
        self.compose = [*docker_prefix, "compose", "-p", "belib-isolated-i5",
                        "--project-directory", str(build_root), "--env-file", f"/proc/{os.getpid()}/fd/{env_fd}",
                        "-f", str(build_root / "compose.staging.yaml"), "-f", str(build_root / "scripts/staging/compose-tls.yaml")]
        self.rollback_root = rollback_root
        self.base_compose = [*docker_prefix, "compose", "-p", "belib-isolated-i5", "--project-directory",
            str(rollback_root), "--env-file", f"/proc/{os.getpid()}/fd/{env_fd}", "-f", str(rollback_root / "compose.staging.yaml")]
        self.source_sha = plan["source_sha"]
        self.tls_config_sha256 = plan["tls_config_sha256"]
        self.certificate_sha256 = plan["certificate_sha256"]
        self._manifest_before = None
        self._prior_nginx = None
        self._new_nginx_id = None
        self._expected_db_image = None

    def _capture(self, command):
        result = _run(command, cwd=self.build_root, env=self.docker_env, capture=True, timeout=90)
        if result.returncode:
            raise PrepareError("Read-only staging resource inspection failed")
        return result.stdout

    def manifest(self):
        return GUARD.build_retained_manifest("6963103fd6bec332b4057457d07b890fe5c11e73", self.docker_env,
            docker_prefix=self.docker_prefix, validate=False)

    def nginx_identity(self):
        rows = self._capture([*self.docker_prefix, "ps", "-aq",
            "--filter", "label=com.docker.compose.project=belib-isolated-i5", "--filter", "label=com.docker.compose.service=nginx"])
        ids = rows.splitlines()
        return ids[0] if len(ids) == 1 and len(ids[0]) == 64 and all(c in "0123456789abcdef" for c in ids[0]) else ""

    def assert_retained_equal(self, before, after, *, allow_nginx_change):
        left = {**before, "containers": [c for c in before["containers"] if c["service"] != "nginx"]}
        right = {**after, "containers": [c for c in after["containers"] if c["service"] != "nginx"]}
        if left != right:
            raise PrepareError("A retained non-nginx container, volume, or network changed")
        if (len(after.get("containers", [])) != 4 or len(after.get("volumes", [])) != 2
                or {n.get("name") for n in after.get("networks", [])} != {"belib-isolated-i5_isolated", "belib-isolated-i5_ingress"}):
            raise PrepareError("TLS ingress resource identities are outside the fixed allowlist")
        nginx = [c for c in after["containers"] if c.get("service") == "nginx"]
        old_nginx = next(c for c in before["containers"] if c.get("service") == "nginx")
        if (len(nginx) != 1 or not re.fullmatch(r"[0-9a-f]{64}", nginx[0].get("id", ""))
                or nginx[0].get("id") == old_nginx.get("id") or nginx[0].get("project") != "belib-isolated-i5"
                or nginx[0].get("oneoff") != "false" or nginx[0].get("state") != "running"
                or nginx[0].get("image_id") != old_nginx.get("image_id")
                or nginx[0].get("image_ref") != old_nginx.get("image_ref")
                or nginx[0].get("networks") != ["belib-isolated-i5_ingress", "belib-isolated-i5_isolated"]):
            raise PrepareError("Recreated nginx image, labels, state, or network differs from the approved service")
        targets = {m.get("destination") for m in nginx[0].get("mounts", []) if isinstance(m, dict)}
        expected_mounts = {
            "/etc/nginx/nginx.conf": str(self.build_root / "scripts/staging/nginx-tls.conf"),
            "/etc/nginx/conf.d/default.conf": str(self.build_root / "scripts/staging/nginx-tls-default.conf"),
            "/etc/nginx/conf.d/belib-proxy-locations.conf": str(self.build_root / "scripts/staging/nginx-tls-locations.conf"),
            "/run/belib-tls/fullchain.pem": str(TLS.CERT_PATH),
            "/run/belib-tls/privkey.pem": str(TLS.KEY_PATH),
        }
        if targets != set(expected_mounts):
            raise PrepareError("Recreated nginx mount set differs from the reviewed TLS overlay")
        if any(m.get("read_write") is not False or m.get("type") != "bind"
               or m.get("source") != expected_mounts.get(m.get("destination")) for m in nginx[0]["mounts"]):
            raise PrepareError("Recreated nginx TLS/config mounts must all be readonly")
        ingress = [n for n in after["networks"] if n.get("name") == "belib-isolated-i5_ingress"]
        if (len(ingress) != 1 or ingress[0].get("driver") != "bridge" or ingress[0].get("internal") is not False
                or ingress[0].get("labels", {}).get("com.docker.compose.project") != "belib-isolated-i5"
                or ingress[0].get("labels", {}).get("com.docker.compose.network") != "ingress"):
            raise PrepareError("TLS ingress bridge labels or driver are outside the fixed allowlist")

    def syntax_check(self):
        image = next(c["image_ref"] for c in self._manifest_before["containers"] if c["service"] == "nginx")
        if "@sha256:" not in image:
            raise PrepareError("Existing nginx image is not pinned by digest")
        mounts = []
        for source, target in ((self.build_root / "scripts/staging/nginx-tls.conf", "/etc/nginx/nginx.conf"),
            (self.build_root / "scripts/staging/nginx-tls-default.conf", "/etc/nginx/conf.d/default.conf"),
            (self.build_root / "scripts/staging/nginx-tls-locations.conf", "/etc/nginx/conf.d/belib-proxy-locations.conf"),
            (TLS.CERT_PATH, "/run/belib-tls/fullchain.pem"), (TLS.KEY_PATH, "/run/belib-tls/privkey.pem")):
            mounts += ["--mount", f"type=bind,src={source},dst={target},readonly"]
        command = [*self.docker_prefix, "run", "--pull", "never", "--rm", "--network", "none",
                   "--read-only", "--cap-drop", "ALL", "--cap-add", "CHOWN",
                   "--security-opt", "no-new-privileges",
                   "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777",
                   "--tmpfs", "/var/cache/nginx:rw,nosuid,nodev,mode=0755",
                   "--add-host", "backend:127.0.0.1", "--add-host", "frontend:127.0.0.1", *mounts,
                   "--entrypoint", "nginx", image, "-t"]
        result = _run(command, cwd=self.build_root, env=self.docker_env, timeout=60)
        if result.returncode:
            raise PrepareError("Pinned nginx image rejected the TLS configuration")

    def recreate_nginx(self):
        self._prior_nginx = self.nginx_identity()
        try:
            result = _run([*self.compose, "up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", "nginx"],
                          cwd=self.build_root, env=self.docker_env, timeout=180, pass_fds=(self.env_fd,))
        finally:
            # Docker can apply the recreate and then lose its response; reacquire identity either way.
            self._new_nginx_id = self.nginx_identity()
        if result.returncode:
            raise PrepareError("Nginx-only TLS recreate failed")

    def owns_nginx_change(self):
        if not self._new_nginx_id or self._new_nginx_id == self._prior_nginx or self.nginx_identity() != self._new_nginx_id:
            return False
        try:
            self.assert_retained_equal(self._manifest_before, self.manifest(), allow_nginx_change=True)
            return True
        except (PrepareError, KeyError, TypeError):
            return False

    def restore_nginx(self):
        if not self.owns_nginx_change():
            raise PrepareError("Nginx identity changed; rollback refused")
        result = _run([*self.base_compose, "up", "-d", "--no-deps", "--no-build", "--pull", "never", "--force-recreate", "nginx"],
                      cwd=self.build_root, env=self.docker_env, timeout=180, pass_fds=(self.env_fd,))
        if result.returncode:
            raise PrepareError("Owned nginx rollback failed; retained resources were preserved")
        restored = self.manifest()
        GUARD.validate_retained_manifest(restored, "6963103fd6bec332b4057457d07b890fe5c11e73",
            allow_running=True, allow_ingress=True, expected_db_image=self._expected_db_image,
            current_source_sha=APP812)
        old = next(c for c in self._manifest_before["containers"] if c["service"] == "nginx")
        current = next(c for c in restored["containers"] if c["service"] == "nginx")
        retained_before = {**self._manifest_before,
            "containers": [c for c in self._manifest_before["containers"] if c["service"] != "nginx"]}
        retained_after = {**restored,
            "containers": [c for c in restored["containers"] if c["service"] != "nginx"]}
        if (retained_before != retained_after or current.get("id") == self._new_nginx_id
                or self.nginx_identity() != current.get("id")
                or current.get("image_ref") != old.get("image_ref")
                or current.get("image_id") != old.get("image_id")
                or current.get("mounts") != old.get("mounts")
                or current.get("networks") != old.get("networks")
                or current.get("state") != "running" or self._http("http://127.0.0.1:18083/health") != 200):
            raise PrepareError("Rollback readback did not verify the old nginx layout and healthy HTTP")

    def probe_http(self):
        if self._http("http://127.0.0.1:18083/health") != 200:
            raise PrepareError("Loopback HTTP health probe failed")

    def _http(self, url):
        result = _run(["/usr/bin/curl", "--noproxy", "*", "--silent", "--show-error", "--max-time", "8", "--output", "/dev/null", "--write-out", "%{http_code}", url],
                      cwd=self.build_root, env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"}, capture=True, timeout=12)
        return int(result.stdout) if result.returncode == 0 and result.stdout.isdigit() else 0

    def probe_https(self):
        context = ssl.create_default_context(cafile="/etc/ssl/certs/ca-certificates.crt")
        context.check_hostname = True
        try:
            with socket.create_connection(("127.0.0.1", 18443), timeout=8) as raw:
                with context.wrap_socket(raw, server_hostname="stg-bl.hiroyuki9614.com") as conn:
                    if digest(conn.getpeercert(binary_form=True)) != self.certificate_sha256:
                        raise PrepareError("TLS leaf differs from the pinned certificate")
                    request = http.client.HTTPSConnection("stg-bl.hiroyuki9614.com", 18443, timeout=8, context=context)
                    request.sock = conn
                    request.request("GET", "/health", headers={"Host": "stg-bl.hiroyuki9614.com"})
                    response = request.getresponse()
                    response.read(65536)
                    request.close()
                    if response.status != 200:
                        raise PrepareError("Loopback TLS HTTP health probe failed")
        except PrepareError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as error:
            raise PrepareError("Loopback TLS hostname, CA, pin, or HTTP probe failed") from error

    def probe_unknown_sni(self):
        # A refused/timed-out service is an error, never evidence that SNI was rejected.
        for server_name in ("unknown.invalid", None):
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            try:
                with socket.create_connection(("127.0.0.1", 18443), timeout=8) as raw:
                    with context.wrap_socket(raw, server_hostname=server_name):
                        raise PrepareError("TLS listener accepted unknown or absent SNI")
            except (ssl.SSLError, EOFError):
                continue
            except (OSError, TimeoutError) as error:
                raise PrepareError("TLS SNI rejection probe could not reach the loopback listener") from error


def prepare(plan_raw: bytes, plan_sha: str, *, evidence_sha256: str, owner_ready: bool,
            adapters: Any | None = None) -> dict:
    if not owner_ready:
        raise PrepareError("Prepare requires the explicit owner-ready flag")
    validate_plan(plan_raw, plan_sha)
    if not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256):
        raise PrepareError("External evidence digest is malformed")
    if adapters is None:
        raise PrepareError("Direct prepare is disabled without the verified owner loader")
    return adapters(plan_raw, plan_sha, evidence_sha256)


# The v2 path is additive. It is selected only by --upgrade-receipt and has a
# separate nonce-scoped journal; the legacy v1 helpers above remain unchanged.
V2_PLAN_FIELDS = {"schema", "disposition", "source_sha", "prior_application_sha", "retained_state_sha256",
    "serve_config_sha256", "serve_etag", "certificate_sha256", "tls_config_sha256", "nonce", "expires_at",
    "upgrade_receipt_nonce", "upgrade_receipt_sha256", "operation_id", "project"}
V2_MANIFEST_FIELDS = {"schema", "upgrade_receipt_nonce", "upgrade_receipt_sha256", "source_sha", "operation_id",
    "project", "expected_runtime", "observed_runtime"}
V2_RECEIPT_FIELDS = {"schema", "plan_sha256", "retained_state_sha256", "nginx_id", "source_sha", "tls_config_sha256",
    "certificate_sha256", "approval_nonce", "evidence_sha256", "prepared", "owner_nonce",
    "upgrade_receipt_nonce", "upgrade_receipt_sha256", "operation_id", "project", "previous_nginx_id",
    "nginx_configuration_sha256", "nginx_image_id", "nginx_network_ids", "unchanged_runtime_sha256"}
V2_INTENT_FIELDS = {"schema", "owner_nonce", "plan_sha256", "upgrade_receipt_nonce", "upgrade_receipt_sha256",
    "source_sha", "operation_id", "project", "before_manifest_sha256", "previous_nginx_id",
    "attempt_before_nginx_id", "intended_tls_model_sha256", "intended_http_model_sha256", "approval_nonce",
    "evidence_sha256", "ownership_label", "recovery_from_owner_nonce"}


def _v2_json(raw: bytes) -> Any:
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate JSON field")
            value[key] = item
        return value
    try:
        return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs,
                          parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite number")))
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise PrepareError("V2 TLS JSON is malformed") from error


def _v2_digest(value: Any) -> str:
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                             allow_nan=False).encode("utf-8"))


def _v2_context(owner_nonce: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", owner_nonce):
        raise PrepareError("Owner nonce is malformed")
    return TLS_CONTEXT / owner_nonce


def _v2_make_context(owner_nonce: str) -> Path:
    path = _v2_context(owner_nonce)
    for directory in (STATE, TLS_CONTEXT):
        try:
            info = os.lstat(directory)
        except FileNotFoundError:
            if directory == STATE:
                raise PrepareError("Staging state directory is unavailable")
            os.mkdir(directory, 0o700)
            info = os.lstat(directory)
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid not in (0, 1000) or stat.S_IMODE(info.st_mode) & 0o022
                or (directory == TLS_CONTEXT and (info.st_uid != 1000 or stat.S_IMODE(info.st_mode) != 0o700))):
            raise PrepareError("TLS work context parent chain is unsafe")
    os.mkdir(path, 0o700)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def _v2_create_once(directory: Path, name: str, raw: bytes, *, maximum: int = 2 * 1024 * 1024) -> None:
    if len(raw) > maximum:
        raise PrepareError("V2 TLS record exceeds its size limit")
    path = directory / name
    if path.exists() or path.is_symlink():
        raise PrepareError("V2 TLS record already exists; refusing overwrite")
    _v2_publish_once(directory, name, raw)


def _v2_publish_once(directory: Path, name: str, raw: bytes) -> None:
    temp_name = "." + name + "." + secrets.token_hex(12) + ".tmp"
    dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            renameat2 = libc.renameat2
            renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
            renameat2.restype = ctypes.c_int
            result = renameat2(dir_fd, os.fsencode(temp_name), dir_fd, os.fsencode(name), 1)  # RENAME_NOREPLACE
            if result != 0:
                code = ctypes.get_errno()
                if code != errno.EEXIST:
                    raise OSError(code, os.strerror(code))
                existing = _v2_read_private(directory, name, maximum=65536)
                if existing != raw:
                    raise PrepareError("A conflicting V2 TLS receipt already exists")
        except AttributeError as error:
            raise PrepareError("Atomic no-replace publication is unavailable") from error
        os.fsync(dir_fd)
    finally:
        try:
            os.unlink(temp_name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        os.fsync(dir_fd)
        os.close(dir_fd)


def _v2_load_executor(source: Path):
    return _module("belib_tls_cd_executor", source / "scripts/staging/cd-handoff-executor.py")


def _v2_source_sha(source: Path) -> str:
    if source.parent != Path("/opt/belib-staging/sources") or not source.name:
        raise PrepareError("V2 must run from the fixed root-admitted source directory")
    _v2_verify_installed_source(source)
    try:
        result = subprocess.run(["/usr/bin/git", "-c", f"safe.directory={source}", "-C", str(source),
            "rev-parse", "--verify", "HEAD"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, check=False, timeout=8,
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C",
                 "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_NO_REPLACE_OBJECTS": "1"})
    except (OSError, subprocess.SubprocessError) as error:
        raise PrepareError("Admitted source revision could not be read") from error
    value = (result.stdout or "").strip()
    if result.returncode or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise PrepareError("Admitted source revision is not a full lowercase Git SHA")
    return value


def _v2_verify_installed_source(source: Path) -> None:
    """Check the installed source boundary without invoking the legacy worktree-only gate."""
    if (source != Path(__file__).resolve(strict=True).parents[2]
            or source.parent != Path("/opt/belib-staging/sources")
            or not re.fullmatch(r"[0-9a-f]{40}", source.name)):
        raise PrepareError("V2 source path is not the exact installed source revision")
    for path in [*reversed(source.parents), source]:
        try:
            info = os.lstat(path)
        except OSError as error:
            raise PrepareError("Installed V2 source ancestry is unavailable") from error
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022):
            raise PrepareError("Installed V2 source ancestry must be root-owned and non-writable")
    count = 0
    for directory, subdirs, files in os.walk(source, followlinks=False):
        for name in (*subdirs, *files):
            path = Path(directory) / name
            try:
                info = os.lstat(path)
            except OSError as error:
                raise PrepareError("Installed V2 source changed during validation") from error
            if (stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022
                    or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))):
                raise PrepareError("Installed V2 source contains an unsafe path")
            count += 1
            if count > 50000:
                raise PrepareError("Installed V2 source exceeds its path bound")
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1"}
    prefix = ["/usr/bin/git", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
              "-c", f"safe.directory={source}", "-C", str(source)]
    try:
        head = subprocess.run([*prefix, "rev-parse", "--verify", "HEAD"], env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False, timeout=10)
        status = subprocess.run([*prefix, "status", "--porcelain=v1", "--untracked-files=all"], env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        raise PrepareError("Installed source Git identity could not be verified") from error
    if (head.returncode or head.stdout.strip() != source.name or status.returncode or status.stdout):
        raise PrepareError("Installed source is not the exact clean admitted revision")


def _v2_read_upgrade_receipt(source: Path, nonce: str, runner=subprocess.run,
                             source_sha: str | None = None) -> tuple[dict, str]:
    if not re.fullmatch(r"[0-9a-f]{64}", nonce):
        raise PrepareError("Upgrade receipt nonce is malformed")
    executor = _v2_load_executor(source)
    source_sha = source_sha or _v2_source_sha(source)
    receipt = executor.read_upgrade_receipt(nonce, source_sha, runner=runner)
    if not isinstance(receipt, dict):
        raise PrepareError("Root upgrade receipt is malformed")
    expected = receipt.get("expected_runtime")
    executor.validate_expected_runtime(expected)
    plan = expected["plan"]["cutover_plan"]
    if (receipt.get("source_sha") != source_sha or plan.get("source_sha") != source_sha
            or expected.get("project") != PROJECT + "-upgrade-" + expected.get("operation_id", "")):
        raise PrepareError("Root upgrade receipt does not bind this operation source")
    return receipt, _v2_digest(receipt)


def _v2_unchanged_runtime_sha256(expected: dict) -> str:
    value = json.loads(json.dumps(expected))
    value["services"].pop("nginx", None)
    value["plan"]["cutover_plan"]["new"]["nginx"].pop("id", None)
    value["plan"]["cutover_plan"]["new"]["nginx"].pop("configuration_sha256", None)
    value.pop("plan_sha256", None)
    return _v2_digest(value)


def _v2_validate_plan(raw: bytes, plan_sha: str, source_sha: str, receipt_nonce: str,
                      receipt_sha: str, expected: dict, *, now: int | None = None,
                      allow_expired_readonly: bool = False) -> dict:
    plan = _v2_json(raw)
    fields = V2_PLAN_FIELDS
    operation_id = expected.get("operation_id")
    project = PROJECT + "-upgrade-" + str(operation_id)
    instant = int(time.time()) if now is None else now
    if (digest(raw) != plan_sha or _v2_digest(plan) != plan_sha or not isinstance(plan, dict)
            or set(plan) != fields or plan.get("schema") != "belib-tls-owner-retirement-plan/v2"
            or plan.get("disposition") != TLS.OWNER_PLAN_DISPOSITION
            or plan.get("source_sha") != source_sha
            or plan.get("prior_application_sha") != source_sha or plan.get("operation_id") != operation_id
            or plan.get("project") != project or plan.get("upgrade_receipt_nonce") != receipt_nonce
            or plan.get("upgrade_receipt_sha256") != receipt_sha
            or plan.get("serve_etag") != TLS.EXPECTED_SERVE_ETAG
            or plan.get("certificate_sha256") != TLS.EXPECTED_CERT_SHA256
            or any(not re.fullmatch(r"[0-9a-f]{64}", plan.get(key, "")) for key in (
                "retained_state_sha256", "serve_config_sha256", "certificate_sha256", "tls_config_sha256", "nonce",
                "upgrade_receipt_nonce", "upgrade_receipt_sha256"))
            or type(plan.get("expires_at")) is not int
            or (not allow_expired_readonly and not instant < plan["expires_at"] <= instant + 1800)
            or (allow_expired_readonly and plan["expires_at"] > instant + 1800)):
        raise PrepareError("V2 owner plan does not match the exact fresh root receipt")
    return plan


def _v2_request_fresh_approval(request, *, plan_raw: bytes, plan_sha: str, source_sha: str,
                               receipt: dict, receipt_sha: str) -> str:
    approval_nonce = request()
    if not re.fullmatch(r"[0-9a-f]{64}", approval_nonce or ""):
        raise PrepareError("Fresh signed TLS approval is malformed")
    _v2_validate_plan(plan_raw, plan_sha, source_sha, receipt["nonce"], receipt_sha,
                      receipt["expected_runtime"])
    return approval_nonce


def _v2_revalidate_pre_effect_runtime(source_sha: str, port: int, expected: dict, executor, *,
                                      attempt_before: str, initial_configuration: str,
                                      recovery: dict | None, runner=subprocess.run,
                                      endpoint_check=None) -> dict:
    if isinstance(recovery, dict) and recovery.get("schema") == "belib-staging-upgrade-tls-recovery-readback/v1":
        info = _v2_nginx_info(executor, runner, expected["project"])
        transition = {"previous_nginx_id": expected["services"]["nginx"], "nginx_id": info["Id"],
            "nginx_configuration_sha256": _v2_configuration_digest(executor, info),
            "nginx_image_id": info.get("Image"),
            "nginx_network_ids": {name: row.get("NetworkID") for name, row in
                sorted((info.get("NetworkSettings") or {}).get("Networks", {}).items())}}
        mode = recovery.get("mode")
        owner_nonce = recovery.get("prior_owner_nonce") if mode == "tls" else None
        observed = executor.collect_tls_candidate_readback(source_sha, port, expected_runtime=expected,
            nginx_transition=transition, runner=runner,
            endpoint_check=endpoint_check or executor._verify_local_docker_endpoint,
            mode=mode, owner_nonce=owner_nonce)
    else:
        observed = executor.collect_runtime_readback(source_sha, port, expected_runtime=expected,
            runner=runner, endpoint_check=endpoint_check or executor._verify_local_docker_endpoint)
    if not isinstance(observed, dict) or observed.get("status") not in {"verified", "candidate-verified"}:
        raise PrepareError("Candidate runtime changed before the TLS nginx effect")
    current = _v2_nginx_info(executor, runner, expected["project"])
    if (current.get("Id") != attempt_before
            or _v2_configuration_digest(executor, current) != initial_configuration):
        raise PrepareError("Candidate nginx changed after model rendering and before TLS mutation")
    return observed


def _v2_collect_runtime(executor, expected: dict, runner=subprocess.run) -> dict:
    source_sha = expected["plan"]["cutover_plan"]["source_sha"]
    result = executor.collect_runtime_readback(source_sha, expected["port"], expected_runtime=expected, runner=runner)
    if not isinstance(result, dict) or result.get("status") != "verified":
        raise PrepareError("Candidate upgrade runtime is not fully verified")
    return result


def _v2_collect_candidate_runtime(source_sha: str, expected: dict, executor, *, runner=subprocess.run,
                                 endpoint_check=None, prior_owner_nonce: str,
                                 prior_intent_sha256: str) -> dict:
    info = _v2_nginx_info(executor, runner, expected["project"])
    transition = {"previous_nginx_id": expected["services"]["nginx"], "nginx_id": info["Id"],
        "nginx_configuration_sha256": executor._configuration_digest(info), "nginx_image_id": info.get("Image"),
        "nginx_network_ids": {name: row.get("NetworkID") for name, row in
            sorted((info.get("NetworkSettings") or {}).get("Networks", {}).items())}}
    labels = (info.get("Config") or {}).get("Labels") or {}
    mounts = {row.get("Destination") for row in info.get("Mounts", []) if isinstance(row, dict)}
    mode = "tls" if "/run/belib-tls/fullchain.pem" in mounts else "http"
    result = executor.collect_tls_candidate_readback(source_sha, expected["port"], expected_runtime=expected,
        nginx_transition=transition, runner=runner, mode=mode,
        owner_nonce=prior_owner_nonce if labels.get("belib.tls-owner-nonce") == prior_owner_nonce else None,
        endpoint_check=endpoint_check or executor._verify_local_docker_endpoint)
    if not isinstance(result, dict) or result.get("status") != "candidate-verified":
        raise PrepareError("Prior TLS intent no longer matches the exact candidate runtime")
    if labels.get("belib.tls-owner-nonce") not in (None, prior_owner_nonce):
        raise PrepareError("Prior nginx carries an unexpected TLS owner nonce")
    if mode == "tls" and labels.get("belib.tls-owner-nonce") != prior_owner_nonce:
        raise PrepareError("Prior TLS nginx does not carry its immutable owner nonce")
    if mode == "http":
        prior_context = _v2_context(prior_owner_nonce)
        prior_intent = _v2_json(_v2_read_private(prior_context, "prepare-intent.json", maximum=65536))
        progress_path = prior_context / "prepare-progress.jsonl"
        if progress_path.exists() or progress_path.is_symlink():
            progress_raw = _v2_read_private(prior_context, "prepare-progress.jsonl", maximum=65536)
        else:
            progress_raw = b""
        rows = []
        for line in progress_raw.splitlines(keepends=True):
            if not line.endswith(b"\n"):
                break
            row = _v2_json(line[:-1])
            if (not isinstance(row, dict) or set(row) != {"schema", "event", "owner_nonce", "nginx_id", "configuration_sha256"}
                    or row.get("schema") != "belib-staging-upgrade-tls-progress/v1"
                    or row.get("owner_nonce") != prior_owner_nonce):
                raise PrepareError("Prior TLS progress log is malformed")
            rows.append(row)
        restored = bool(labels.get("belib.tls-owner-nonce") == prior_owner_nonce
            and rows and rows[-1].get("event") == "rolled_back"
            and rows[-1].get("nginx_id") == info["Id"]
            and rows[-1].get("configuration_sha256") == transition["nginx_configuration_sha256"])
        interrupted_before_effect = bool(info["Id"] == prior_intent.get("attempt_before_nginx_id")
            and transition["nginx_configuration_sha256"] == expected["plan"]["cutover_plan"]["new"]["nginx"]["configuration_sha256"]
            and labels.get("belib.tls-owner-nonce") in (None, prior_owner_nonce)
            and (not rows or (rows[-1].get("event") == "attempting"
                and rows[-1].get("nginx_id") == info["Id"]
                and rows[-1].get("configuration_sha256") == transition["nginx_configuration_sha256"])))
        if not (restored or interrupted_before_effect):
            raise PrepareError("HTTP candidate is not the exact recorded rollback or pre-effect state")
    return {"schema": "belib-staging-upgrade-tls-recovery-readback/v1",
        "prior_owner_nonce": prior_owner_nonce, "prior_intent_sha256": prior_intent_sha256,
        "mode": mode, "nginx_transition": transition, "candidate_readback": result}


def _v2_validate_recovery_context(source: Path, expected: dict, executor, recovery: dict, *,
                                 receipt_nonce: str, receipt_sha: str,
                                 runner=subprocess.run, endpoint_check=None) -> dict:
    fields = {"schema", "prior_owner_nonce", "prior_intent_sha256", "mode", "nginx_transition", "candidate_readback"}
    if (not isinstance(recovery, dict) or set(recovery) != fields
            or recovery.get("schema") != "belib-staging-upgrade-tls-recovery-readback/v1"
            or not re.fullmatch(r"[0-9a-f]{64}", recovery.get("prior_owner_nonce", ""))):
        raise PrepareError("Recovery readback wrapper is malformed")
    prior_nonce = recovery["prior_owner_nonce"]
    context = _v2_context(prior_nonce)
    plan_raw = _v2_read_private(context, "owner-plan.json", maximum=16384)
    plan = _v2_json(plan_raw)
    intent_raw = _v2_read_private(context, "prepare-intent.json", maximum=65536)
    intent = _v2_json(intent_raw)
    before_raw = _v2_read_private(context, "before-manifest.json")
    before_manifest = _v2_json(before_raw)
    if (digest(intent_raw) != recovery["prior_intent_sha256"]
            or not isinstance(intent, dict) or set(intent) != V2_INTENT_FIELDS
            or not isinstance(plan, dict) or set(plan) != V2_PLAN_FIELDS
            or intent.get("owner_nonce") != prior_nonce or intent.get("plan_sha256") != digest(plan_raw)
            or intent.get("source_sha") != plan.get("source_sha")
            or intent.get("project") != expected["project"]
            or intent.get("operation_id") != expected["operation_id"]
            or intent.get("upgrade_receipt_sha256") != plan.get("upgrade_receipt_sha256")
            or intent.get("before_manifest_sha256") != digest(before_raw)
            or plan.get("upgrade_receipt_nonce") != receipt_nonce
            or plan.get("upgrade_receipt_sha256") != receipt_sha
            or plan.get("retained_state_sha256") != digest(before_raw)
            or not isinstance(before_manifest, dict) or set(before_manifest) != V2_MANIFEST_FIELDS
            or before_manifest.get("expected_runtime") != expected
            or before_manifest.get("source_sha") != plan.get("source_sha")
            or before_manifest.get("upgrade_receipt_nonce") != receipt_nonce
            or before_manifest.get("upgrade_receipt_sha256") != receipt_sha
            or (isinstance(before_manifest.get("observed_runtime"), dict)
                and before_manifest["observed_runtime"].get("schema") == "belib-staging-upgrade-tls-recovery-readback/v1")
            or plan.get("project") != expected["project"]
            or plan.get("operation_id") != expected["operation_id"]):
        raise PrepareError("Recovery context is not linked to a prior immutable owner intent")
    observed = _v2_collect_candidate_runtime(plan["source_sha"], expected, executor, runner=runner,
        endpoint_check=endpoint_check, prior_owner_nonce=prior_nonce,
        prior_intent_sha256=digest(intent_raw))
    if observed != recovery:
        raise PrepareError("Recovery runtime differs from the exact signed readback")
    old_context = _v2_context(prior_nonce)
    overlay_raw = _v2_read_private(old_context, "tls-compose-overlay.json", maximum=65536)
    overlay = _v2_json(overlay_raw)
    try:
        tls_port = int(overlay["services"]["nginx"]["ports"][0]["published"])
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise PrepareError("Prior TLS overlay does not carry one pinned loopback port") from error
    if not 1024 <= tls_port <= 65535:
        raise PrepareError("Prior TLS loopback port is outside the allowed range")
    seed = GUARD.load_seed_module()
    GUARD.ADMITTED_SOURCE_ROOT = source
    env_fd, values = seed.validate_staging_admission(source, ENV_PATH, 1000)
    try:
        if values.get("BELIB_STAGE_VCS_REF") != plan["source_sha"] or int(values["BELIB_STAGE_PORT"]) != expected["port"]:
            raise PrepareError("Prior operation env does not match the recovery source and port")
        docker_env, _endpoint, docker_prefix = _read_only_rootless(seed, GUARD)
        _compose, _model, hashes = _v2_build_models(source,
            PREPARED_ROOT / expected["operation_id"],
            old_context, prior_nonce, expected["port"], tls_port, env_fd, docker_env, docker_prefix, runner)
    finally:
        os.close(env_fd)
    if (hashes["tls_model_sha256"] != intent["intended_tls_model_sha256"]
            or hashes["base_model_sha256"] != intent["intended_http_model_sha256"]):
        raise PrepareError("Prior source Compose model differs from its durable effect intent")
    return observed


def v2_create_plan(source: Path, receipt_nonce: str, *, runner=subprocess.run,
                   serve: Any | None = None, certificate_sha: str | None = None,
                   tls_config_sha: str | None = None, observed_runtime: dict | None = None,
                   owner_nonce: str | None = None, recovery_owner_nonce: str | None = None,
                   endpoint_check=None) -> tuple[bytes, Path]:
    """Read-only v2 plan builder; optional seams exist only for isolated tests."""
    source_sha = _v2_source_sha(source)
    receipt, receipt_sha = _v2_read_upgrade_receipt(source, receipt_nonce, runner=runner, source_sha=source_sha)
    expected = receipt["expected_runtime"]
    executor = _v2_load_executor(source)
    if recovery_owner_nonce is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", recovery_owner_nonce):
            raise PrepareError("Recovery owner nonce is malformed")
        previous_context = _v2_context(recovery_owner_nonce)
        previous_intent_raw = _v2_read_private(previous_context, "prepare-intent.json", maximum=65536)
        previous_intent = _v2_json(previous_intent_raw)
        if (not isinstance(previous_intent, dict) or set(previous_intent) != V2_INTENT_FIELDS
                or previous_intent.get("owner_nonce") != recovery_owner_nonce):
            raise PrepareError("Recovery link does not match a prior exact TLS operation context")
        provisional = _v2_collect_candidate_runtime(source_sha, expected, executor, runner=runner,
            endpoint_check=endpoint_check, prior_owner_nonce=recovery_owner_nonce,
            prior_intent_sha256=digest(previous_intent_raw))
        observed = _v2_validate_recovery_context(source, expected, executor, provisional,
            receipt_nonce=receipt_nonce, receipt_sha=receipt_sha, runner=runner, endpoint_check=endpoint_check)
    else:
        observed = observed_runtime if observed_runtime is not None else _v2_collect_runtime(executor, expected, runner)
    if serve is None:
        client = TLS.TailscaleServeClient()
        serve_raw, etag = client.get()
    else:
        serve_raw, etag = serve
    TLS.validate_original_serve_config(serve_raw)
    if etag != TLS.EXPECTED_SERVE_ETAG:
        raise PrepareError("Tailscale Serve ETag differs from the fixed owner-reviewed baseline")
    if certificate_sha is None:
        certificate_sha = TLS.inspect_certificate(TLS.CERT_PATH, TLS.KEY_PATH)
    if certificate_sha != TLS.EXPECTED_CERT_SHA256:
        raise PrepareError("TLS leaf certificate differs from the fixed owner-issued leaf")
    config_sha = tls_config_sha or bundle_digest(source)
    if not re.fullmatch(r"[0-9a-f]{64}", config_sha):
        raise PrepareError("TLS configuration digest is malformed")
    manifest = {"schema": "belib-staging-upgrade-tls-manifest/v2",
        "upgrade_receipt_nonce": receipt_nonce, "upgrade_receipt_sha256": receipt_sha,
        "source_sha": source_sha, "operation_id": expected["operation_id"], "project": expected["project"],
        "expected_runtime": expected, "observed_runtime": observed}
    manifest_raw = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                              allow_nan=False).encode("utf-8")
    manifest_sha = digest(manifest_raw)
    binding = {"prior_application_sha": source_sha, "retained_state_sha256": manifest_sha,
        "serve_config_sha256": digest(serve_raw), "serve_etag": etag,
        "certificate_sha256": certificate_sha, "tls_config_sha256": config_sha, "owner_plan_sha256": "0" * 64}
    plan = {"schema": "belib-tls-owner-retirement-plan/v2", "disposition": TLS.OWNER_PLAN_DISPOSITION,
        "source_sha": source_sha, "prior_application_sha": source_sha, "retained_state_sha256": manifest_sha,
        "serve_config_sha256": binding["serve_config_sha256"], "serve_etag": etag,
        "certificate_sha256": certificate_sha, "tls_config_sha256": config_sha,
        "nonce": owner_nonce or secrets.token_hex(32), "expires_at": int(time.time()) + 1800,
        "upgrade_receipt_nonce": receipt_nonce, "upgrade_receipt_sha256": receipt_sha,
        "operation_id": expected["operation_id"], "project": expected["project"]}
    plan_raw = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
    context = _v2_make_context(plan["nonce"])
    _v2_create_once(context, "owner-plan.json", plan_raw)
    _v2_create_once(context, "before-manifest.json", manifest_raw)
    return plan_raw, context


def _v2_read_private(directory: Path, name: str, *, maximum: int = 2 * 1024 * 1024) -> bytes:
    return _secure_read(directory / name, maximum=maximum, require_single_link=True)


def _v2_append_progress(directory: Path, owner_nonce: str, event: str, nginx_id: str, config_sha: str) -> None:
    if event not in {"attempting", "replaced", "verified", "rolled_back"}:
        raise PrepareError("V2 TLS progress event is invalid")
    row = {"schema": "belib-staging-upgrade-tls-progress/v1", "event": event,
           "owner_nonce": owner_nonce, "nginx_id": nginx_id, "configuration_sha256": config_sha}
    raw = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode() + b"\n"
    path = directory / "prepare-progress.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 1000 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size + len(raw) > 65536:
            raise PrepareError("V2 TLS progress log is unsafe or full")
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)


def v2_prepare_live(plan_raw: bytes, plan_sha: str, evidence_sha256: str, *,
                    owner_ready: bool, source: Path, runner=subprocess.run,
                    approval_callback=None, endpoint_check=None, tls_port: int = 18443,
                    tls_ca_file: Path = Path("/etc/ssl/certs/ca-certificates.crt")) -> dict:
    with GUARD.project_lock():
        return _v2_prepare_live_locked(plan_raw, plan_sha, evidence_sha256, owner_ready=owner_ready,
            source=source, runner=runner, approval_callback=approval_callback,
            endpoint_check=endpoint_check, tls_port=tls_port, tls_ca_file=tls_ca_file)


def _v2_prepare_live_locked(plan_raw: bytes, plan_sha: str, evidence_sha256: str, *,
                    owner_ready: bool, source: Path, runner=subprocess.run,
                    approval_callback=None, endpoint_check=None, tls_port: int = 18443,
                    tls_ca_file: Path = Path("/etc/ssl/certs/ca-certificates.crt")) -> dict:
    """Run a nonce-bound nginx-only candidate continuation after fresh signed approval."""
    if owner_ready is not True or not re.fullmatch(r"[0-9a-f]{64}", evidence_sha256):
        raise PrepareError("V2 prepare requires owner-ready and a bounded evidence digest")
    if type(tls_port) is not int or not 1024 <= tls_port <= 65535:
        raise PrepareError("TLS listener port must be an exact integer in the non-privileged range")
    plan = _v2_json(plan_raw)
    source_sha = _v2_source_sha(source)
    receipt, receipt_sha = _v2_read_upgrade_receipt(source, plan.get("upgrade_receipt_nonce", ""),
                                                     runner=runner, source_sha=source_sha)
    expected = receipt["expected_runtime"]
    context = _v2_context(plan["nonce"])
    has_intent = (context / "prepare-intent.json").exists()
    _v2_validate_plan(plan_raw, plan_sha, source_sha, plan["upgrade_receipt_nonce"], receipt_sha, expected,
                      allow_expired_readonly=has_intent)
    manifest_raw = _v2_read_private(context, "before-manifest.json")
    manifest = _v2_json(manifest_raw)
    if (not isinstance(manifest, dict) or set(manifest) != V2_MANIFEST_FIELDS
            or manifest.get("schema") != "belib-staging-upgrade-tls-manifest/v2"
            or manifest.get("expected_runtime") != expected
            or manifest.get("upgrade_receipt_sha256") != receipt_sha
            or digest(manifest_raw) != plan["retained_state_sha256"]):
        raise PrepareError("V2 before-manifest does not match the independent root receipt")
    executor = _v2_load_executor(source)
    recovery = manifest.get("observed_runtime")
    if has_intent:
        intent = _v2_json(_v2_read_private(context, "prepare-intent.json", maximum=65536))
        linked = {"owner_nonce": plan["nonce"], "plan_sha256": plan_sha,
            "upgrade_receipt_nonce": plan["upgrade_receipt_nonce"], "upgrade_receipt_sha256": receipt_sha,
            "source_sha": source_sha, "operation_id": plan["operation_id"], "project": plan["project"],
            "before_manifest_sha256": digest(manifest_raw)}
        if (not isinstance(intent, dict) or set(intent) != V2_INTENT_FIELDS
                or any(intent.get(key) != value for key, value in linked.items())
                or intent.get("ownership_label") != plan["nonce"]):
            raise PrepareError("Existing TLS intent does not bind this exact operation")
        if (intent.get("previous_nginx_id") != expected["services"]["nginx"]
                or not re.fullmatch(r"[0-9a-f]{64}", intent.get("attempt_before_nginx_id", ""))
                or not re.fullmatch(r"[0-9a-f]{64}", intent.get("approval_nonce", ""))
                or not re.fullmatch(r"[0-9a-f]{64}", intent.get("evidence_sha256", ""))):
            raise PrepareError("Existing TLS intent has invalid pre-effect identity or grant pins")
        prior_owner = recovery.get("prior_owner_nonce") if isinstance(recovery, dict) and recovery.get("schema") == "belib-staging-upgrade-tls-recovery-readback/v1" else None
        if intent.get("recovery_from_owner_nonce") != prior_owner:
            raise PrepareError("Existing TLS intent recovery link differs from the signed manifest")
        if bundle_digest(source) != plan["tls_config_sha256"]:
            raise PrepareError("TLS configuration changed since the durable candidate intent")
        prepared = PREPARED_ROOT / plan["operation_id"]
        seed = GUARD.load_seed_module()
        GUARD.ADMITTED_SOURCE_ROOT = source
        env_fd, values = seed.validate_staging_admission(source, ENV_PATH, 1000)
        try:
            if (values.get("BELIB_STAGE_VCS_REF") != source_sha
                    or int(values["BELIB_STAGE_PORT"]) != expected["port"]):
                raise PrepareError("Recovery source or operation port differs from the admitted root receipt")
            docker_env, _endpoint, docker_prefix = _read_only_rootless(seed, GUARD)
            _compose, _model, hashes = _v2_build_models(source, prepared, context, plan["nonce"],
                expected["port"], tls_port, env_fd, docker_env, docker_prefix, runner)
            if (intent.get("intended_tls_model_sha256") != hashes["tls_model_sha256"]
                    or intent.get("intended_http_model_sha256") != hashes["base_model_sha256"]):
                raise PrepareError("Recovered TLS model differs from the immutable pre-effect intent")
        finally:
            os.close(env_fd)
        return _v2_apply_candidate_nginx(plan, plan_sha, manifest, receipt, receipt_sha,
            intent["evidence_sha256"], intent["approval_nonce"], source, executor,
            runner=runner, endpoint_check=endpoint_check, tls_port=tls_port, tls_ca_file=tls_ca_file)
    is_recovery = isinstance(recovery, dict) and recovery.get("schema") == "belib-staging-upgrade-tls-recovery-readback/v1"
    if is_recovery:
        before = _v2_validate_recovery_context(source, expected, executor, recovery,
            receipt_nonce=plan["upgrade_receipt_nonce"], receipt_sha=receipt_sha,
            runner=runner, endpoint_check=endpoint_check)
    else:
        before = _v2_collect_runtime(executor, expected, runner)
        if before != recovery:
            raise PrepareError("Candidate runtime changed after the v2 owner plan was created")
    serve = TLS.TailscaleServeClient()
    serve_raw, etag = serve.get()
    TLS.validate_original_serve_config(serve_raw)
    certificate_sha = TLS.inspect_certificate(TLS.CERT_PATH, TLS.KEY_PATH)
    config_sha = bundle_digest(source)
    if (digest(serve_raw) != plan["serve_config_sha256"] or etag != plan["serve_etag"]
            or certificate_sha != plan["certificate_sha256"] or config_sha != plan["tls_config_sha256"]):
        raise PrepareError("V2 Serve, certificate, or configuration inputs changed after planning")
    binding = {"prior_application_sha": source_sha, "retained_state_sha256": plan["retained_state_sha256"],
        "serve_config_sha256": digest(serve_raw), "serve_etag": etag, "certificate_sha256": certificate_sha,
        "tls_config_sha256": config_sha, "owner_plan_sha256": plan_sha}
    tls_binding = TLS.signed_tls_binding_v2(binding, source_sha, project=plan["project"],
        upgrade_receipt_nonce=plan["upgrade_receipt_nonce"], upgrade_receipt_sha256=receipt_sha,
        operation_id=plan["operation_id"])
    approval = GUARD.load_approval_module()
    if approval_callback is None:
        request_approval = lambda: approval.request_and_wait(source_sha, "tls-ingress", evidence_sha256,
            uid=1000, output=sys.stdout, tls_binding=tls_binding)
    else:
        request_approval = lambda: approval_callback(tls_binding)
    approval_nonce = _v2_request_fresh_approval(request_approval, plan_raw=plan_raw, plan_sha=plan_sha,
        source_sha=source_sha, receipt=receipt, receipt_sha=receipt_sha)
    # The fresh signature wait is followed by independent root and runtime readback.
    receipt_after, receipt_sha_after = _v2_read_upgrade_receipt(source, plan["upgrade_receipt_nonce"],
        runner=runner, source_sha=source_sha)
    if receipt_sha_after != receipt_sha or receipt_after != receipt:
        raise PrepareError("Root upgrade receipt changed after TLS approval")
    if is_recovery:
        attempt_before = _v2_validate_recovery_context(source, expected, executor, recovery,
            receipt_nonce=plan["upgrade_receipt_nonce"], receipt_sha=receipt_sha,
            runner=runner, endpoint_check=endpoint_check)
    else:
        attempt_before = _v2_collect_runtime(executor, expected, runner)
    if attempt_before != before:
        raise PrepareError("Candidate runtime changed while waiting for TLS approval")
    serve_after, etag_after = TLS.TailscaleServeClient().get()
    if (serve_after != serve_raw or etag_after != etag
            or TLS.inspect_certificate(TLS.CERT_PATH, TLS.KEY_PATH) != certificate_sha
            or bundle_digest(source) != config_sha):
        raise PrepareError("TLS/Serve inputs changed while waiting for signed approval")
    # The operation-specific Compose model and overlay are constructed below by the
    # effect adapter; absence of an adapter is deliberately not a successful prepare.
    return _v2_apply_candidate_nginx(plan, plan_sha, manifest, receipt, receipt_sha, evidence_sha256,
        approval_nonce, source, executor, runner=runner, endpoint_check=endpoint_check, tls_port=tls_port,
        tls_ca_file=tls_ca_file)


def _v2_docker_json(executor, runner, command: list[str]) -> Any:
    result = runner([executor.DOCKER_CLI, "--host", executor.DOCKER_ENDPOINT, *command],
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "DOCKER_CONFIG": "/nonexistent",
             "DOCKER_HOST": executor.DOCKER_ENDPOINT, "LANG": "C"}, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=30, check=False)
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise PrepareError("Candidate nginx Docker readback failed")
    try:
        return json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise PrepareError("Candidate nginx Docker readback is malformed") from error


def _v2_nginx_info(executor, runner, project: str) -> dict:
    result = runner([executor.DOCKER_CLI, "--host", executor.DOCKER_ENDPOINT, "ps", "--all", "--no-trunc",
        "--filter", "label=com.docker.compose.project=" + project,
        "--filter", "label=com.docker.compose.service=nginx", "--format", "{{.ID}}"],
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "DOCKER_CONFIG": "/nonexistent",
             "DOCKER_HOST": executor.DOCKER_ENDPOINT, "LANG": "C"}, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=20, check=False)
    ids = result.stdout.splitlines() if result.returncode == 0 else []
    if len(ids) != 1 or not re.fullmatch(r"[0-9a-f]{64}", ids[0]):
        raise PrepareError("Candidate project nginx identity is ambiguous")
    value = _v2_docker_json(executor, runner, ["inspect", "--format", "{{json .}}", ids[0]])
    if not isinstance(value, dict) or value.get("Id") != ids[0]:
        raise PrepareError("Candidate nginx inspect identity differs")
    labels = (value.get("Config") or {}).get("Labels") or {}
    if labels.get("com.docker.compose.project") != project or labels.get("com.docker.compose.service") != "nginx":
        raise PrepareError("Candidate nginx Compose identity differs")
    return value


def _v2_configuration_digest(executor, info: dict) -> str:
    return executor._configuration_digest(info)


def _v2_atomic_overlay(path: Path, source: Path, context: Path, owner_nonce: str, tls_port: int = 18443) -> dict:
    values = {"/etc/nginx/nginx.conf": source / "scripts/staging/nginx-tls.conf",
        "/etc/nginx/conf.d/default.conf": source / "scripts/staging/nginx-tls-default.conf",
        "/etc/nginx/conf.d/belib-proxy-locations.conf": source / "scripts/staging/nginx-tls-locations.conf",
        "/run/belib-tls/fullchain.pem": TLS.CERT_PATH, "/run/belib-tls/privkey.pem": TLS.KEY_PATH}
    mounts = [{"type": "bind", "source": str(path), "target": target, "read_only": True,
               "bind": {"create_host_path": False}} for target, path in values.items()]
    model = {"services": {"nginx": {"ports": [
        {"host_ip": "127.0.0.1", "target": 8443, "published": str(tls_port), "protocol": "tcp", "mode": "ingress"}],
        "volumes": mounts, "labels": {"belib.tls-owner-nonce": owner_nonce}}}}
    raw = json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    existing = context / "tls-compose-overlay.json"
    if existing.exists():
        if _v2_read_private(context, "tls-compose-overlay.json", maximum=65536) != raw:
            raise PrepareError("Immutable TLS Compose overlay differs from the derived owner model")
    else:
        _v2_create_once(context, "tls-compose-overlay.json", raw)
    return model


def _v2_http_owner_overlay(context: Path, owner_nonce: str) -> dict:
    model = {"services": {"nginx": {"labels": {"belib.tls-owner-nonce": owner_nonce}}}}
    raw = json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    existing = context / "http-compose-overlay.json"
    if existing.exists():
        if _v2_read_private(context, "http-compose-overlay.json", maximum=65536) != raw:
            raise PrepareError("Immutable HTTP recovery overlay differs from the exact owner nonce")
    else:
        _v2_create_once(context, "http-compose-overlay.json", raw)
    return model


def _v2_strip_owner_label(model: dict, owner_nonce: str) -> dict:
    value = json.loads(json.dumps(model))
    labels = value.get("services", {}).get("nginx", {}).get("labels")
    if not isinstance(labels, dict) or labels.get("belib.tls-owner-nonce") != owner_nonce:
        raise PrepareError("Rendered owner overlay label is missing or changed")
    labels.pop("belib.tls-owner-nonce")
    if not labels:
        value["services"]["nginx"].pop("labels", None)
    return value


def _v2_build_models(source: Path, prepared: Path, context: Path, owner_nonce: str, loopback_port: int,
                     tls_port: int,
                     env_fd: int, docker_env: dict, docker_prefix: list[str], runner) -> tuple[list[str], dict, dict]:
    compose = [*docker_prefix, "compose", "-p", PROJECT + "-upgrade-" + prepared.name,
        "--project-directory", str(source), "--env-file", f"/proc/{os.getpid()}/fd/{env_fd}",
        "-f", str(source / "compose.staging.yaml"), "-f", str(prepared / "compose.override.json")]
    base_cmd = [*compose, "--profile", "migration", "config", "--format", "json"]
    result = _run(base_cmd, cwd=source, env=docker_env, runner=runner, timeout=40, capture=True, pass_fds=(env_fd,))
    if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
        raise PrepareError("Operation-scoped Compose base model is unavailable")
    base = _v2_json(result.stdout.encode())
    _v2_atomic_overlay(context / "tls-compose-overlay.json", source, context, owner_nonce, tls_port)
    combined_cmd = [*compose, "-f", str(context / "tls-compose-overlay.json"), "--profile", "migration", "config", "--format", "json"]
    result = _run(combined_cmd, cwd=source, env=docker_env, runner=runner, timeout=40, capture=True, pass_fds=(env_fd,))
    if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
        raise PrepareError("Operation-scoped Compose TLS model is unavailable")
    combined = _v2_json(result.stdout.encode())
    try:
        standard_overlay = json.loads(json.dumps(combined))
        labels = standard_overlay.get("services", {}).get("nginx", {}).get("labels") or {}
        if labels.get("belib.tls-owner-nonce") != owner_nonce:
            raise PrepareError("TLS model does not carry the exact owner nonce label")
        labels.pop("belib.tls-owner-nonce", None)
        if not labels:
            standard_overlay["services"]["nginx"].pop("labels", None)
        TLS.validate_tls_compose_overlay(base, standard_overlay, source, loopback_port, tls_port=tls_port)
    except Exception as error:
        raise PrepareError("Operation TLS model changed more than the nginx ingress overlay") from error
    nginx = combined.get("services", {}).get("nginx", {})
    if nginx.get("labels", {}).get("belib.tls-owner-nonce") != owner_nonce:
        raise PrepareError("TLS model does not carry the exact owner nonce label")
    model_raw = json.dumps(combined, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    http_owner_overlay = _v2_http_owner_overlay(context, owner_nonce)
    http_cmd = [*compose, "-f", str(context / "http-compose-overlay.json"),
                "--profile", "migration", "config", "--format", "json"]
    result = _run(http_cmd, cwd=source, env=docker_env, runner=runner, timeout=40,
                  capture=True, pass_fds=(env_fd,))
    if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
        raise PrepareError("Nonce-marked HTTP rollback model is unavailable")
    http_model = _v2_json(result.stdout.encode())
    if _v2_strip_owner_label(http_model, owner_nonce) != base:
        raise PrepareError("HTTP rollback overlay changed more than the exact owner marker")
    tls_compose = [*compose, "-f", str(context / "tls-compose-overlay.json")]
    return tls_compose, combined, {"tls_model_sha256": digest(model_raw),
        "base_model_sha256": _v2_digest(http_model)}


def _v2_validate_prepared_inputs(source: Path, prepared: Path, expected: dict) -> dict:
    if (prepared != PREPARED_ROOT / expected["operation_id"] or prepared.is_symlink()
            or not prepared.is_dir()):
        raise PrepareError("Retained prepared directory is not the exact operation path")
    directory_info = os.lstat(prepared)
    if directory_info.st_uid != 1000 or stat.S_IMODE(directory_info.st_mode) != 0o700:
        raise PrepareError("Retained prepared directory must be operator-owned mode 0700")
    config_raw = _secure_read(prepared / "candidate-config.json", maximum=1024 * 1024)
    receipt_raw = _secure_read(prepared / "preparation-receipt.json", maximum=2 * 1024 * 1024)
    journal_raw = _secure_read(prepared / "preparation-journal.json", maximum=1024 * 1024)
    override_raw = _secure_read(prepared / "compose.override.json", maximum=1024 * 1024)
    config, receipt, journal, override = map(_v2_json, (config_raw, receipt_raw, journal_raw, override_raw))
    operation, source_sha = expected["operation_id"], expected["plan"]["cutover_plan"]["source_sha"]
    project = expected["project"]
    plan = expected["plan"]
    authorities = config.get("authorities") if isinstance(config, dict) else None
    if (not isinstance(authorities, dict) or set(authorities) != {"restore", "network", "roots"}
            or any(not isinstance(pin, dict) or set(pin) != {"issuer", "public_key_pem", "fingerprint"}
                   or not isinstance(pin.get("issuer"), str) or not pin["issuer"]
                   or not isinstance(pin.get("public_key_pem"), str) or not pin["public_key_pem"]
                   or not isinstance(pin.get("fingerprint"), str)
                   or not re.fullmatch(r"[0-9a-f]{64}", pin["fingerprint"])
                   for pin in authorities.values())
            or _v2_digest(authorities) != expected.get("authority_pins_sha256")):
        raise PrepareError("Disabled candidate authority pins differ from the root-adopted configuration")
    if (not isinstance(config, dict)
            or set(config) != {"schema", "enabled", "source_sha", "topology", "prepared_directory", "authorities",
                               "cutover_plan", "candidate_database", "source_database", "migration_image"}
            or config.get("schema") != "belib-retained-operator/v1"
            or config.get("enabled") is not False or config.get("source_sha") != source_sha
            or config.get("topology") != "isolated-copy" or config.get("prepared_directory") != str(prepared)
            or config.get("cutover_plan") != plan["cutover_plan"]
            or config.get("candidate_database") != plan["candidate_database"]
            or config.get("source_database") != plan["source_database"]):
        raise PrepareError("Disabled candidate configuration differs from the root-admitted plan")
    receipt_fields = {"schema", "source_sha", "operation_id", "image_tags", "image_ids", "build_inputs_sha256",
        "candidate_container_ids", "old_container_pins", "old_container_oci", "old_database_pin",
        "old_volume_sha256", "manifest_sha256", "backup_sha256", "candidate_apps_stopped",
        "candidate_database_started", "original_database_mutated"}
    image_names = {"backend", "frontend", "migrate"}
    build_inputs = ("compose.staging.yaml", "scripts/staging/Dockerfile.backend.staging",
                    "scripts/staging/Dockerfile.frontend.staging", "backend/.dockerignore", "frontend/.dockerignore")
    expected_build_inputs = {name: digest((source / name).read_bytes()) for name in build_inputs}
    candidate_db = plan["candidate_database"]
    expected_container_pins = {service: pin["configuration_sha256"]
                               for service, pin in plan["cutover_plan"]["new"].items()}
    expected_container_pins["db"] = candidate_db["configuration_sha256"]
    if (not isinstance(receipt, dict) or set(receipt) != receipt_fields
            or not isinstance(journal, dict)
            or receipt.get("schema") != "belib-retained-preparation-receipt/v1"
            or receipt.get("source_sha") != source_sha or receipt.get("operation_id") != operation
            or receipt.get("candidate_container_ids") != expected["services"] | {"db": expected["services"]["db"]}
            or receipt.get("image_ids") != journal.get("image_ids")
            or not isinstance(receipt.get("image_ids"), dict) or set(receipt["image_ids"]) != image_names
            or any(not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value)
                   for value in receipt["image_ids"].values())
            or receipt.get("image_tags") != {name: "belib-retained:" + operation + "-" + name for name in image_names}
            or receipt.get("build_inputs_sha256") != expected_build_inputs
            or receipt.get("old_container_pins") != plan["cutover_plan"]["old"]
            or receipt.get("old_database_pin") != plan["source_database"]
            or not isinstance(receipt.get("old_container_oci"), dict)
            or set(receipt["old_container_oci"]) != {"db", "backend", "frontend", "nginx"}
            or any(not isinstance(pin, dict) or set(pin) != {"image_id", "image_ref"}
                   or not isinstance(pin.get("image_id"), str)
                   or not re.fullmatch(r"sha256:[0-9a-f]{64}", pin["image_id"])
                   or not isinstance(pin.get("image_ref"), str) or not pin["image_ref"]
                   for pin in receipt["old_container_oci"].values())
            or not isinstance(receipt.get("old_volume_sha256"), dict)
            or set(receipt["old_volume_sha256"]) != {PROJECT + "_staging-book-files", PROJECT + "_staging-postgres-data"}
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                   for value in receipt["old_volume_sha256"].values())
            or not isinstance(receipt.get("manifest_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", receipt["manifest_sha256"])
            or not isinstance(receipt.get("backup_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", receipt["backup_sha256"])
            or receipt.get("candidate_apps_stopped") is not True
            or receipt.get("candidate_database_started") is not True
            or receipt.get("original_database_mutated") is not False):
        raise PrepareError("Retained preparation receipt differs from the root-admitted operation")
    if (not isinstance(journal, dict)
            or set(journal) != {"schema", "source_sha", "operation_id", "phase", "image_ids", "container_ids", "container_pins", "intent"}
            or journal.get("schema") != "belib-retained-preparation-journal/v1"
            or journal.get("source_sha") != source_sha or journal.get("operation_id") != operation
            or journal.get("phase") != "prepared" or journal.get("container_ids") != receipt.get("candidate_container_ids")
            or journal.get("image_ids") != receipt.get("image_ids")
            or journal.get("container_pins") != expected_container_pins
            or not isinstance(journal.get("intent"), str) or not re.fullmatch(r"[0-9a-f]{64}", journal["intent"])):
        raise PrepareError("Retained preparation journal is not the exact completed build record")
    tags = {name: "belib-retained:" + operation + "-" + name for name in ("backend", "frontend", "migrate")}
    intent = journal["intent"]
    override_expected = {"name": project, "services": {
        "backend": {"build": {"tags": [tags["backend"]],
            "labels": {"belib.retained-prepare-intent": intent, "belib.retained-prepare-source": source_sha},
            "context": "./backend", "dockerfile": "../scripts/staging/Dockerfile.backend.staging",
            "target": "runtime", "args": {"VCS_REF": source_sha}}},
        "frontend": {"build": {"tags": [tags["frontend"]],
            "labels": {"belib.retained-prepare-intent": intent, "belib.retained-prepare-source": source_sha}}},
        "migrate": {"build": {"tags": [tags["migrate"]],
            "labels": {"belib.retained-prepare-intent": intent, "belib.retained-prepare-source": source_sha}}},
        "db": {"volumes": [{"type": "bind", "source": str(source / "scripts/staging/10-belib-staging-roles.sh"),
            "target": "/docker-entrypoint-initdb.d/10-belib-staging-roles.sh", "read_only": True,
            "bind": {"create_host_path": False}}]}},
        "volumes": {"staging-postgres-data": {"name": project + "_staging-postgres-data"},
            "staging-book-files": {"external": True, "name": PROJECT + "_staging-book-files"}},
        "networks": {"isolated": {"name": project + "_isolated", "internal": True},
            "ingress": {"name": project + "_ingress", "driver": "bridge"}}}
    for service, tag in tags.items():
        service_model = override_expected["services"].setdefault(service, {})
        service_model["image"] = tag
    for service in ("db", "backend", "frontend", "nginx"):
        override_expected["services"].setdefault(service, {}).setdefault("labels", {})[
            "belib.retained-prepare-intent"] = intent
    override_expected["volumes"]["staging-postgres-data"]["labels"] = {
        "belib.retained-prepare-intent": intent}
    for network in ("isolated", "ingress"):
        override_expected["networks"][network].setdefault("labels", {})[
            "belib.retained-prepare-intent"] = intent
    if (not isinstance(override, dict)
            or json.dumps(override, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode() != override_raw
            or override != override_expected
            or receipt.get("image_tags") != tags
            or config.get("migration_image") != receipt.get("image_ids", {}).get("migrate")):
        raise PrepareError("Prepared Compose override differs from the deterministic admitted operation model")
    return config


def _v2_atomic_receipt(directory: Path, receipt: dict) -> bytes:
    raw = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    if set(receipt) != V2_RECEIPT_FIELDS or receipt.get("schema") != "belib-staging-tls-prepare-receipt/v2":
        raise PrepareError("V2 prepare receipt fields are incomplete")
    _v2_publish_once(directory, "prepare-receipt.json", raw)
    return raw


def _v2_apply_candidate_nginx(plan: dict, plan_sha: str, manifest: dict, root_receipt: dict,
                              root_receipt_sha: str, evidence_sha: str, approval_nonce: str,
                              source: Path, executor, *, runner=subprocess.run, endpoint_check=None,
                              tls_port: int = 18443,
                              tls_ca_file: Path = Path("/etc/ssl/certs/ca-certificates.crt")) -> dict:
    operation_id, project, owner_nonce = plan["operation_id"], plan["project"], plan["nonce"]
    prepared = PREPARED_ROOT / operation_id
    if prepared.resolve(strict=True) != prepared or prepared.name != operation_id:
        raise PrepareError("Retained operation directory is unavailable or unsafe")
    _v2_validate_prepared_inputs(source, prepared, root_receipt["expected_runtime"])
    context = _v2_context(owner_nonce)
    receipt_path = context / "prepare-receipt.json"
    # Same-process response-loss recovery is read-only: only an exact nonce-owned
    # nginx plus unchanged resource readback can regenerate the same receipt.
    intent_path = context / "prepare-intent.json"
    if intent_path.exists():
        intent_raw = _v2_read_private(context, "prepare-intent.json", maximum=65536)
        intent = _v2_json(intent_raw)
        if not isinstance(intent, dict) or set(intent) != V2_INTENT_FIELDS or intent.get("owner_nonce") != owner_nonce:
            raise PrepareError("Existing TLS intent is malformed or belongs to another owner nonce")
        info = _v2_nginx_info(executor, runner, project)
        labels = (info.get("Config") or {}).get("Labels") or {}
        current_id = info["Id"]
        current_config = _v2_configuration_digest(executor, info)
        if labels.get("belib.tls-owner-nonce") != owner_nonce:
            raise PrepareError("Interrupted TLS operation does not own the current candidate nginx")
        transition = {"previous_nginx_id": intent["previous_nginx_id"], "nginx_id": current_id,
            "nginx_configuration_sha256": current_config, "nginx_image_id": info.get("Image"),
            "nginx_network_ids": {name: item.get("NetworkID") for name, item in
                sorted((info.get("NetworkSettings") or {}).get("Networks", {}).items())}}
        observed = executor.collect_tls_candidate_readback(plan["source_sha"], root_receipt["expected_runtime"]["port"],
            expected_runtime=root_receipt["expected_runtime"], nginx_transition=transition, runner=runner,
            endpoint_check=endpoint_check or executor._verify_local_docker_endpoint,
            mode="tls", owner_nonce=owner_nonce)
        if observed.get("status") != "candidate-verified":
            raise PrepareError("Interrupted TLS runtime could not be reconciled exactly")
        _v2_probe_tls(source, plan["certificate_sha256"], root_receipt["expected_runtime"]["port"], tls_port, tls_ca_file)
        receipt = _v2_receipt_values(plan, plan_sha, manifest, root_receipt, root_receipt_sha, evidence_sha,
                                     approval_nonce, intent["previous_nginx_id"], transition, expected= root_receipt["expected_runtime"])
        _v2_atomic_receipt(context, receipt)
        return receipt
    plan_raw = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
    _v2_validate_plan(plan_raw, plan_sha, plan["source_sha"], plan["upgrade_receipt_nonce"],
                      root_receipt_sha, root_receipt["expected_runtime"])
    previous_nginx = root_receipt["expected_runtime"]["services"]["nginx"]
    initial = _v2_nginx_info(executor, runner, project)
    attempt_before = initial["Id"]
    initial_configuration = _v2_configuration_digest(executor, initial)
    recovery = manifest.get("observed_runtime")
    recovery_from = None
    if isinstance(recovery, dict) and recovery.get("schema") == "belib-staging-upgrade-tls-recovery-readback/v1":
        recovery_from = recovery.get("prior_owner_nonce")
        transition_before = recovery.get("nginx_transition")
        if (not isinstance(transition_before, dict) or transition_before.get("previous_nginx_id") != previous_nginx
                or transition_before.get("nginx_id") != attempt_before
                or transition_before.get("nginx_configuration_sha256") != initial_configuration):
            raise PrepareError("Recovery nginx changed after the signed readback")
    elif (attempt_before != previous_nginx or initial_configuration !=
            root_receipt["expected_runtime"]["plan"]["cutover_plan"]["new"]["nginx"]["configuration_sha256"]):
        raise PrepareError("Candidate nginx ID or configuration differs from the independently admitted root plan")
    seed = GUARD.load_seed_module()
    GUARD.ADMITTED_SOURCE_ROOT = source
    env_fd, values = seed.validate_staging_admission(source, ENV_PATH, 1000)
    try:
        if values.get("BELIB_STAGE_VCS_REF") != plan["source_sha"] or int(values["BELIB_STAGE_PORT"]) != root_receipt["expected_runtime"]["port"]:
            raise PrepareError("Fixed operation environment does not match the root receipt source and port")
        docker_env, _endpoint, docker_prefix = _read_only_rootless(seed, GUARD)
        compose, combined_model, model_hashes = _v2_build_models(source, prepared, context, owner_nonce,
            int(values["BELIB_STAGE_PORT"]), tls_port, env_fd, docker_env, docker_prefix, runner)
        expected_tls_port = int(values["BELIB_STAGE_PORT"])
        if expected_tls_port != root_receipt["expected_runtime"]["port"]:
            raise PrepareError("TLS operation port differs from the root-admitted operation")
        # The intent is immutable and durable before Compose can replace nginx.
        before_manifest_sha = digest(json.dumps(manifest, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode())
        intent = {"schema": "belib-staging-upgrade-tls-intent/v1", "owner_nonce": owner_nonce,
            "plan_sha256": plan_sha, "upgrade_receipt_nonce": plan["upgrade_receipt_nonce"],
            "upgrade_receipt_sha256": root_receipt_sha, "source_sha": plan["source_sha"], "operation_id": operation_id,
            "project": project, "before_manifest_sha256": before_manifest_sha, "previous_nginx_id": previous_nginx,
            "attempt_before_nginx_id": attempt_before, "intended_tls_model_sha256": model_hashes["tls_model_sha256"],
            "intended_http_model_sha256": model_hashes["base_model_sha256"], "approval_nonce": approval_nonce,
            "evidence_sha256": evidence_sha, "ownership_label": owner_nonce,
            "recovery_from_owner_nonce": recovery_from}
        intent_raw = json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        if set(intent) != V2_INTENT_FIELDS:
            raise PrepareError("V2 TLS intent fields are invalid")
        _v2_create_once(context, "prepare-intent.json", intent_raw, maximum=65536)
        _v2_append_progress(context, owner_nonce, "attempting", attempt_before, _v2_configuration_digest(executor, initial))
        try:
            _v2_validate_plan(plan_raw, plan_sha, plan["source_sha"], plan["upgrade_receipt_nonce"],
                              root_receipt_sha, root_receipt["expected_runtime"])
            _v2_revalidate_pre_effect_runtime(plan["source_sha"], expected_tls_port,
                root_receipt["expected_runtime"], executor, attempt_before=attempt_before,
                initial_configuration=initial_configuration, recovery=recovery, runner=runner,
                endpoint_check=endpoint_check)
            result = _run([*compose, "up", "-d", "--no-deps", "--no-build", "--pull", "never",
                           "--force-recreate", "nginx"], cwd=source, env=docker_env, runner=runner,
                          timeout=180, pass_fds=(env_fd,))
            # Re-read even on a failed subprocess response; Docker may have applied it.
            info = _v2_nginx_info(executor, runner, project)
            if result.returncode:
                raise PrepareError("Candidate nginx recreate command failed")
            labels = (info.get("Config") or {}).get("Labels") or {}
            if labels.get("belib.tls-owner-nonce") != owner_nonce or info["Id"] == attempt_before:
                raise PrepareError("Recreated candidate nginx is not a new nonce-owned container")
            transition = {"previous_nginx_id": previous_nginx, "nginx_id": info["Id"],
                "nginx_configuration_sha256": _v2_configuration_digest(executor, info),
                "nginx_image_id": info.get("Image"),
                "nginx_network_ids": {name: item.get("NetworkID") for name, item in
                    sorted((info.get("NetworkSettings") or {}).get("Networks", {}).items())}}
            observed = executor.collect_tls_candidate_readback(plan["source_sha"], expected_tls_port,
                expected_runtime=root_receipt["expected_runtime"], nginx_transition=transition, runner=runner,
                endpoint_check=endpoint_check or executor._verify_local_docker_endpoint,
                mode="tls", owner_nonce=owner_nonce)
            if observed.get("status") != "candidate-verified":
                raise PrepareError("Only the candidate nginx may change during TLS continuation")
            _v2_probe_tls(source, plan["certificate_sha256"], expected_tls_port, tls_port, tls_ca_file)
            _v2_append_progress(context, owner_nonce, "verified", info["Id"], transition["nginx_configuration_sha256"])
            receipt = _v2_receipt_values(plan, plan_sha, manifest, root_receipt, root_receipt_sha,
                                         evidence_sha, approval_nonce, previous_nginx, transition,
                                         expected=root_receipt["expected_runtime"])
            _v2_atomic_receipt(context, receipt)
            return receipt
        except Exception:
            # Never remove an uncertain container. A rollback is attempted only
            # for the exact nonce-owned nginx that was just created.
            try:
                current = _v2_nginx_info(executor, runner, project)
                labels = (current.get("Config") or {}).get("Labels") or {}
                if labels.get("belib.tls-owner-nonce") == owner_nonce and current["Id"] != attempt_before:
                    transition_current = {"previous_nginx_id": previous_nginx, "nginx_id": current["Id"],
                        "nginx_configuration_sha256": _v2_configuration_digest(executor, current),
                        "nginx_image_id": current.get("Image"),
                        "nginx_network_ids": {name: item.get("NetworkID") for name, item in
                            sorted((current.get("NetworkSettings") or {}).get("Networks", {}).items())}}
                    owned = executor.collect_tls_candidate_readback(plan["source_sha"], expected_tls_port,
                        expected_runtime=root_receipt["expected_runtime"], nginx_transition=transition_current,
                        runner=runner, endpoint_check=endpoint_check or executor._verify_local_docker_endpoint,
                        mode="tls", owner_nonce=owner_nonce)
                    if owned.get("status") != "candidate-verified":
                        raise PrepareError("TLS rollback target is not the exact nonce-owned candidate nginx")
                    # The base command drops only the v2 TLS override and uses the
                    # immutable preparation override to restore the HTTP nginx model.
                    base_compose = [*docker_prefix, "compose", "-p", project, "--project-directory", str(source),
                        "--env-file", f"/proc/{os.getpid()}/fd/{env_fd}", "-f", str(source / "compose.staging.yaml"),
                        "-f", str(prepared / "compose.override.json"),
                        "-f", str(context / "http-compose-overlay.json")]
                    _run([*base_compose, "up", "-d", "--no-deps", "--no-build", "--pull", "never",
                          "--force-recreate", "nginx"], cwd=source, env=docker_env, runner=runner,
                         timeout=180, pass_fds=(env_fd,))
                    restored = _v2_nginx_info(executor, runner, project)
                    restored_transition = {"previous_nginx_id": previous_nginx, "nginx_id": restored["Id"],
                        "nginx_configuration_sha256": _v2_configuration_digest(executor, restored),
                        "nginx_image_id": restored.get("Image"),
                        "nginx_network_ids": {name: item.get("NetworkID") for name, item in
                            sorted((restored.get("NetworkSettings") or {}).get("Networks", {}).items())}}
                    restored_result = executor.collect_tls_candidate_readback(plan["source_sha"], expected_tls_port,
                        expected_runtime=root_receipt["expected_runtime"], nginx_transition=restored_transition,
                        runner=runner, endpoint_check=endpoint_check or executor._verify_local_docker_endpoint,
                        mode="http", owner_nonce=owner_nonce)
                    if (restored["Id"] == current["Id"] or restored_result.get("status") != "candidate-verified"
                            or (restored.get("Config", {}).get("Labels") or {}).get("belib.tls-owner-nonce") != owner_nonce):
                        raise PrepareError("Owned nginx HTTP rollback did not produce the expected replacement")
                    _v2_probe_http(source, expected_tls_port)
                    _v2_append_progress(context, owner_nonce, "rolled_back", restored["Id"],
                                        _v2_configuration_digest(executor, restored))
            except Exception as rollback_error:
                raise PrepareError("TLS prepare failed; nginx ownership or rollback state is uncertain") from rollback_error
            raise
    finally:
        os.close(env_fd)


def _v2_probe_tls(source: Path, certificate_sha: str, port: int, tls_port: int = 18443,
                  tls_ca_file: Path = Path("/etc/ssl/certs/ca-certificates.crt")) -> None:
    _v2_probe_http(source, port)
    context = ssl.create_default_context(cafile=str(tls_ca_file))
    try:
        with socket.create_connection(("127.0.0.1", tls_port), timeout=8) as raw:
            with context.wrap_socket(raw, server_hostname=TLS.CUSTOM_HOST) as connection:
                if digest(connection.getpeercert(binary_form=True)) != certificate_sha:
                    raise PrepareError("Candidate TLS leaf differs from the owner-pinned certificate")
                request = http.client.HTTPSConnection(TLS.CUSTOM_HOST, tls_port, timeout=8, context=context)
                request.sock = connection
                request.request("GET", "/health", headers={"Host": TLS.CUSTOM_HOST})
                response = request.getresponse(); response.read(65536); request.close()
                if response.status != 200:
                    raise PrepareError("Candidate TLS health probe failed")
    except PrepareError:
        raise
    except (OSError, ssl.SSLError, http.client.HTTPException) as error:
        raise PrepareError("Candidate TLS hostname, CA, leaf, or health check failed") from error
    for name in ("unknown.invalid", None):
        test_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        test_context.check_hostname = False
        test_context.verify_mode = ssl.CERT_NONE
        try:
            with socket.create_connection(("127.0.0.1", tls_port), timeout=8) as raw:
                with test_context.wrap_socket(raw, server_hostname=name):
                    raise PrepareError("Candidate TLS listener accepted unknown or absent SNI")
        except (ssl.SSLError, EOFError):
            continue
        except (OSError, TimeoutError) as error:
            raise PrepareError("Candidate TLS SNI rejection probe could not reach the listener") from error


def _v2_probe_http(source: Path, port: int) -> None:
    result = _run(["/usr/bin/curl", "--noproxy", "*", "--silent", "--show-error", "--max-time", "8",
        "--output", "/dev/null", "--write-out", "%{http_code}", f"http://127.0.0.1:{port}/health"],
        cwd=source, env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"}, capture=True, timeout=12)
    if result.returncode or result.stdout.strip() != "200":
        raise PrepareError("Candidate HTTP health probe failed")


def _v2_receipt_values(plan: dict, plan_sha: str, manifest: dict, root_receipt: dict,
                       root_receipt_sha: str, evidence_sha: str, approval_nonce: str,
                       previous_nginx_id: str, transition: dict, *, expected: dict) -> dict:
    receipt = {"schema": "belib-staging-tls-prepare-receipt/v2", "plan_sha256": plan_sha,
        "retained_state_sha256": plan["retained_state_sha256"], "nginx_id": transition["nginx_id"],
        "source_sha": plan["source_sha"], "tls_config_sha256": plan["tls_config_sha256"],
        "certificate_sha256": plan["certificate_sha256"], "approval_nonce": approval_nonce,
        "evidence_sha256": evidence_sha, "prepared": True, "owner_nonce": plan["nonce"],
        "upgrade_receipt_nonce": plan["upgrade_receipt_nonce"], "upgrade_receipt_sha256": root_receipt_sha,
        "operation_id": plan["operation_id"], "project": plan["project"],
        "previous_nginx_id": previous_nginx_id,
        "nginx_configuration_sha256": transition["nginx_configuration_sha256"],
        "nginx_image_id": transition["nginx_image_id"], "nginx_network_ids": transition["nginx_network_ids"],
        "unchanged_runtime_sha256": _v2_unchanged_runtime_sha256(expected)}
    if (set(receipt) != V2_RECEIPT_FIELDS or transition.get("previous_nginx_id") != previous_nginx_id
            or root_receipt.get("expected_runtime") != expected):
        raise PrepareError("V2 owner receipt linkage is inconsistent")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "prepare"))
    parser.add_argument("--source-sha")
    parser.add_argument("--upgrade-receipt")
    parser.add_argument("--owner-nonce")
    parser.add_argument("--recovery-owner-nonce")
    parser.add_argument("--evidence-sha256")
    parser.add_argument("--plan-sha256")
    parser.add_argument("--owner-ready", action="store_true")
    args = parser.parse_args(argv)
    try:
        validate_identity()
        if args.upgrade_receipt is not None:
            if args.source_sha is not None:
                raise PrepareError("V2 source SHA is derived from the exact admitted source, not a caller override")
            if args.mode == "plan" and (args.owner_nonce or args.plan_sha256 or args.owner_ready or args.evidence_sha256):
                raise PrepareError("V2 plan mode accepts only the root receipt and optional prior owner nonce")
            if args.mode == "prepare" and (not args.owner_nonce or not args.owner_ready
                    or not args.plan_sha256 or not args.evidence_sha256 or args.recovery_owner_nonce):
                raise PrepareError("V2 prepare requires owner nonce, owner-ready, and signed evidence pins")
            source = Path(__file__).resolve(strict=True).parents[2]
            verify_cli_source(source)
            _v2_verify_installed_source(source)
            _load_helpers(source)
            GUARD.verify_python_runtime()
            GUARD.reject_caller_environment(os.environ)
            source_sha = _v2_source_sha(source)
            if source.name != source_sha:
                raise PrepareError("V2 installed source directory differs from the Git revision")
            if args.mode == "plan":
                raw, context = v2_create_plan(source, args.upgrade_receipt,
                                              recovery_owner_nonce=args.recovery_owner_nonce)
                plan = _v2_json(raw)
                print(json.dumps({"owner_nonce": plan["nonce"], "context_path": str(context),
                                  "plan_sha256": digest(raw)}, sort_keys=True, separators=(",", ":")))
                return 0
            context = _v2_context(args.owner_nonce)
            plan_raw = _v2_read_private(context, "owner-plan.json", maximum=16384)
            plan = _v2_json(plan_raw)
            if plan.get("nonce") != args.owner_nonce or plan.get("upgrade_receipt_nonce") != args.upgrade_receipt:
                raise PrepareError("V2 owner nonce does not select the requested receipt-bound plan")
            result = v2_prepare_live(plan_raw, args.plan_sha256, args.evidence_sha256,
                owner_ready=True, source=source)
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            return 0
        if args.source_sha is None:
            raise PrepareError("Legacy v1 requires its pinned source SHA")
        if args.owner_nonce or args.recovery_owner_nonce:
            raise PrepareError("V2 owner selectors require an explicit root upgrade receipt")
        source = Path(f"/home/hiroyuki/workspaces/belib-i5-admission-{args.source_sha[:7]}")
        verify_cli_source(source)
        _load_helpers(source)
        GUARD.verify_python_runtime()
        GUARD.reject_caller_environment(os.environ)
        if args.mode == "plan":
            if args.plan_sha256 or args.owner_ready:
                raise PrepareError("Plan mode accepts no prepare flags")
            data = collect_plan_inputs(source, args.source_sha)
            raw = create_plan(args.source_sha, **data)
            write_plan_once(raw)
            print(json.dumps({"plan_path": str(PLAN_PATH), "plan_sha256": digest(raw)}, sort_keys=True))
            return 0
        if not args.owner_ready or not args.plan_sha256 or not args.evidence_sha256:
            raise PrepareError("Prepare requires --owner-ready, pinned plan digest, and external evidence digest")
        plan_raw = _secure_read(PLAN_PATH)
        receipt = prepare(plan_raw, args.plan_sha256, evidence_sha256=args.evidence_sha256,
                          owner_ready=args.owner_ready, adapters=prepare_live)
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as error:
        if not isinstance(error, PrepareError):
            error = PrepareError("TLS prepare admission or runtime checks failed")
        print(f"TLS prepare blocked: {error}", file=sys.stderr)
        return 2


def verify_tls_runtime_contract(source_root: Path, source_sha: str) -> str:
    """Keep the live app812 DB contract while admitting only a new nginx overlay."""
    expected_db_image = GUARD.verify_resume_snapshot_compatibility(
        source_root, APP812, "6963103fd6bec332b4057457d07b890fe5c11e73")
    source_guard = GUARD.load_source_tree_guard(source_root)
    for path in ("compose.staging.yaml", "scripts/staging/10-belib-staging-roles.sh",
                 "nginx/nginx.conf", "scripts/staging/nginx-default.conf"):
        previous = source_guard.git(source_root, "rev-parse", f"{APP812}:{path}").strip()
        current = source_guard.git(source_root, "rev-parse", f"{source_sha}:{path}").strip()
        if not previous or previous != current:
            raise PrepareError("TLS-only continuation cannot change the live base Compose or DB contract")
    return expected_db_image


def open_live_app_env(seed):
    """Verify the unchanged private env against the clean live application source."""
    fd, values = seed.validate_staging_admission(APP_SOURCE, ENV_PATH, 1000)
    if values.get("BELIB_STAGE_VCS_REF") != APP812:
        os.close(fd)
        raise PrepareError("TLS continuation must retain the live application environment")
    return fd, values


def collect_plan_inputs(source: Path, source_sha: str) -> dict:
    """Collect the precomputable plan state without changing Docker or Serve."""
    guard = GUARD
    admitted = guard._admission_source_root(guard.EXPECTED_REPO_ROOT, source_sha)
    guard.ADMITTED_SOURCE_ROOT = admitted
    seed = guard.load_seed_module()
    source_guard = guard.load_source_tree_guard(admitted)
    try:
        with source_guard.sha_derived_build_snapshot(admitted, source_sha) as build_root, \
                source_guard.sha_derived_build_snapshot(APP_SOURCE, APP812) as rollback_root:
            seed.validate_build_snapshot(build_root, admitted, source_sha)
            expected_db_image = verify_tls_runtime_contract(admitted, source_sha)
            docker_env, endpoint, docker_prefix = _read_only_rootless(seed, guard)
            env_fd, env_values = open_live_app_env(seed)
            try:
                base_model = _compose_model(build_root, env_fd, docker_env, docker_prefix, tls=False)
                tls_model = _compose_model(build_root, env_fd, docker_env, docker_prefix, tls=True)
                TLS.validate_tls_compose_overlay(base_model, tls_model, build_root, int(env_values["BELIB_STAGE_PORT"]))
                manifest = retained_live_manifest(guard, docker_env, docker_prefix, expected_db_image)
                validate_app812(manifest, docker_env, docker_prefix)
                serve = TLS.TailscaleServeClient()
                serve_raw, etag = serve.get()
                TLS.validate_original_serve_config(serve_raw)
                version = subprocess.run(["/usr/local/bin/tailscale", "version"], stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False, timeout=8,
                    env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"})
                TLS.verify_tailscale_version(version.stdout if version.returncode == 0 else "")
                cert_sha = TLS.inspect_certificate(TLS.CERT_PATH, TLS.KEY_PATH)
                cfg_sha = bundle_digest(build_root)
                return {"manifest": manifest, "serve_raw": serve_raw, "serve_etag": etag,
                        "certificate_sha": cert_sha, "tls_config_sha": cfg_sha}
            finally:
                os.close(env_fd)
    except (guard.LaunchBlocked, seed.SeedWrapperError, TLS.TlsIngressError) as error:
        raise PrepareError("Read-only staging TLS plan collection failed") from error


def bundle_digest(source: Path) -> str:
    inventory = []
    paths = [source / "compose.staging.yaml", source / "scripts/staging/tls-prepare.py",
             source / "scripts/staging/tls-ingress.py", source / "scripts/staging/owner-serve-cas.py",
             source / "scripts/staging/owner-command.py", source / "scripts/staging/nginx-default.conf",
             *(source / name for name in TLS.TLS_CONFIG_PATHS)]
    for path in paths:
        try:
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode):
                raise PrepareError("TLS bundle contains a non-regular input")
            raw = path.read_bytes()
            inventory.append({"path": path.relative_to(source).as_posix(), "sha256": digest(raw)})
        except OSError as error:
            raise PrepareError("TLS configuration bundle is incomplete") from error
    return digest(canonical(inventory))


def validate_app812(manifest: dict, docker_env: dict, docker_prefix: list[str]) -> None:
    rows = {row.get("service"): row for row in manifest.get("containers", []) if isinstance(row, dict)}
    for service, image_id in APP_IMAGES.items():
        row = rows.get(service)
        if not row or row.get("image_id") != image_id:
            raise PrepareError("Retained backend/frontend image differs from the live 812 baseline")
        labels_raw = GUARD._docker_read([*docker_prefix, "inspect", "--format", "{{json .Config.Labels}}", row["id"]], docker_env)
        try:
            labels = json.loads(labels_raw)
        except json.JSONDecodeError as error:
            raise PrepareError("Retained app OCI labels are malformed") from error
        if not isinstance(labels, dict) or labels.get("org.opencontainers.image.revision") != APP812:
            raise PrepareError("Retained app OCI revision label differs from live 812")


def retained_live_manifest(guard, docker_env, docker_prefix, expected_db_image):
    """Collect without the exited-baseline default, then validate the running 812 retention set."""
    manifest = guard.build_retained_manifest("6963103fd6bec332b4057457d07b890fe5c11e73", docker_env,
        docker_prefix=docker_prefix, validate=False, expected_db_image=expected_db_image)
    guard.validate_retained_manifest(manifest, "6963103fd6bec332b4057457d07b890fe5c11e73",
        allow_running=True, allow_ingress=True, current_source_sha=APP812,
        expected_db_image=expected_db_image)
    TLS._validate_retained_identities(manifest)
    return manifest


def prepare_live(plan_raw: bytes, plan_sha: str, evidence_sha256: str) -> dict:
    """Run the guarded rootless transaction after a fresh external signature."""
    plan = validate_plan(plan_raw, plan_sha)
    if len(evidence_sha256) != 64 or any(c not in "0123456789abcdef" for c in evidence_sha256):
        raise PrepareError("External evidence digest is malformed")
    guard = GUARD
    source_root = guard._admission_source_root(guard.EXPECTED_REPO_ROOT, plan["source_sha"])
    guard.ADMITTED_SOURCE_ROOT = source_root
    seed = guard.load_seed_module()
    source_guard = guard.load_source_tree_guard(source_root)
    with guard.project_lock():
        try:
            with source_guard.sha_derived_build_snapshot(source_root, plan["source_sha"]) as build_root, \
                    source_guard.sha_derived_build_snapshot(APP_SOURCE, APP812) as rollback_root:
                seed.validate_build_snapshot(build_root, source_root, plan["source_sha"])
                expected_db_image = verify_tls_runtime_contract(source_root, plan["source_sha"])
                docker_env, endpoint, docker_prefix = _read_only_rootless(seed, guard)
                env_fd, values = open_live_app_env(seed)
                try:
                    base = _compose_model(build_root, env_fd, docker_env, docker_prefix, tls=False)
                    combined = _compose_model(build_root, env_fd, docker_env, docker_prefix, tls=True)
                    TLS.validate_tls_compose_overlay(base, combined, build_root, int(values["BELIB_STAGE_PORT"]))
                    before = retained_live_manifest(guard, docker_env, docker_prefix, expected_db_image)
                    validate_app812(before, docker_env, docker_prefix)
                    serve = TLS.TailscaleServeClient()
                    serve_raw, etag = serve.get()
                    TLS.validate_original_serve_config(serve_raw)
                    cert_sha = TLS.inspect_certificate(TLS.CERT_PATH, TLS.KEY_PATH)
                    current_bundle = bundle_digest(build_root)
                    binding = TLS.build_tls_binding(source_sha=plan["source_sha"],
                        prior_application_sha=TLS.PRIOR_APPLICATION_SHA, retained_manifest=before,
                        serve_config=serve_raw, serve_etag=etag, certificate_sha256=cert_sha,
                        tls_config=b"", owner_plan_sha256=plan_sha, tls_config_sha256=current_bundle)
                    actual_plan = {"retained_state_sha256": binding["retained_state_sha256"],
                        "serve_config_sha256": binding["serve_config_sha256"], "serve_etag": etag,
                        "certificate_sha256": cert_sha, "tls_config_sha256": current_bundle}
                    if any(plan[key] != value for key, value in actual_plan.items()):
                        raise PrepareError("Owner plan is stale or live TLS state changed")
                    approval = guard.load_approval_module()
                    approval_nonce = approval.request_and_wait(plan["source_sha"], "tls-ingress", evidence_sha256,
                        uid=1000, output=sys.stdout, tls_binding=TLS.signed_tls_binding(binding, plan["source_sha"]))
                    validate_plan(plan_raw, plan_sha)
                    after = retained_live_manifest(guard, docker_env, docker_prefix, expected_db_image)
                    validate_app812(after, docker_env, docker_prefix)
                    serve_after, etag_after = serve.get()
                    cert_after = TLS.inspect_certificate(TLS.CERT_PATH, TLS.KEY_PATH)
                    bundle_after = bundle_digest(build_root)
                    if (digest(canonical(after)) != plan["retained_state_sha256"] or serve_after != serve_raw
                            or etag_after != etag or cert_after != plan["certificate_sha256"]
                            or bundle_after != plan["tls_config_sha256"]):
                        raise PrepareError("Approved retained or Serve baseline changed before mutation")
                    nginx_id = next(c["id"] for c in before["containers"] if c["service"] == "nginx")
                    effects = RuntimeEffects(source_root, build_root, env_fd, docker_env, values, plan, plan_sha,
                                             binding, evidence_sha256, docker_prefix, rollback_root)
                    effects._manifest_before = before
                    effects._expected_db_image = expected_db_image
                    effects.approval_nonce = approval_nonce
                    old_handler = signal.getsignal(signal.SIGTERM)
                    def handle_sigterm(_signum, _frame):
                        raise PrepareError("TLS prepare received SIGTERM")
                    signal.signal(signal.SIGTERM, handle_sigterm)
                    try:
                        return transaction(before_manifest=before, expected_manifest_sha=plan["retained_state_sha256"],
                                           plan_sha=plan_sha, nginx_identity=nginx_id, effects=effects)
                    finally:
                        signal.signal(signal.SIGTERM, old_handler)
                finally:
                    os.close(env_fd)
        except (guard.LaunchBlocked, seed.SeedWrapperError, TLS.TlsIngressError, Exception) as error:
            if isinstance(error, PrepareError):
                raise
            raise PrepareError(f"Guarded rootless TLS prepare failed ({type(error).__name__})") from error


def _compose_model(build_root: Path, env_fd: int, environment: dict, docker_prefix: list[str], *, tls: bool):
    command = [*docker_prefix, "compose", "-p", "belib-isolated-i5",
        "--project-directory", str(build_root), "--env-file", f"/proc/{os.getpid()}/fd/{env_fd}", "-f", str(build_root / "compose.staging.yaml")]
    if tls:
        command += ["-f", str(build_root / "scripts/staging/compose-tls.yaml")]
    command += ["--profile", "migration", "config", "--format", "json"]
    result = _run(command, cwd=build_root, env=environment, capture=True, timeout=30, pass_fds=(env_fd,))
    if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
        raise PrepareError("Compose model could not be read safely")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise PrepareError("Compose model is malformed") from error


if __name__ == "__main__":
    raise SystemExit(main())
