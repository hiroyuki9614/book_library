#!/usr/bin/env -S /usr/bin/python3 -I -B
"""Create a pinned owner plan or perform the guarded rootless nginx TLS prepare."""
from __future__ import annotations

import argparse
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
APP_SOURCE = Path("/home/hiroyuki/workspaces/belib-i5-admission-8125626")
ENV_PATH = Path("/home/hiroyuki/.config/belib-staging/i5.env")
APP812 = "81256268276d7ead95029849037f69ec6beec202"
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


def _secure_read(path: Path, *, uid: int = 1000, modes=(0o600,), maximum=65536) -> bytes:
    _verify_path_parents(path, uid)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise PrepareError("Pinned TLS plan or staging input is unavailable or unsafe") from error
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) not in modes or info.st_size > maximum:
            raise PrepareError("Pinned TLS plan or staging input has unsafe ownership, mode, or size")
        chunks = bytearray()
        while len(chunks) <= maximum:
            part = os.read(fd, min(8192, maximum + 1 - len(chunks)))
            if not part:
                break
            chunks.extend(part)
        after = os.fstat(fd)
        if len(chunks) != info.st_size or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns):
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
        "schema": "belib-tls-owner-plan/v1", "source_sha": source_sha,
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
    fields = {"schema", "source_sha", "prior_application_sha", "retained_state_sha256", "serve_config_sha256",
              "serve_etag", "certificate_sha256", "tls_config_sha256", "nonce", "expires_at"}
    if not isinstance(plan, dict) or set(plan) != fields or canonical(plan) != raw:
        raise PrepareError("Owner plan fields or canonical encoding are invalid")
    if (plan["schema"] != "belib-tls-owner-plan/v1"
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
                   "--add-host", "backend:127.0.0.1", "--add-host", "frontend:127.0.0.1",
                   "--add-host", "ubuntu-worker.tail63cd2d.ts.net:100.109.171.70", *mounts,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "prepare"))
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--evidence-sha256")
    parser.add_argument("--plan-sha256")
    parser.add_argument("--owner-ready", action="store_true")
    args = parser.parse_args(argv)
    try:
        validate_identity()
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
