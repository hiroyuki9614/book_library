#!/usr/bin/env python3
"""Prepare an isolated retained-upgrade candidate from trusted root admission.

This module is an operation helper, not a trust root. Its caller must supply the
already authenticated admission descriptor. It never stops an old service,
changes the retained database, installs evidence keys, or enables cutover.
"""
from __future__ import annotations

import hashlib
import fcntl
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import secrets
import time
from pathlib import Path

PROJECT = "belib-isolated-i5"
SERVICES = ("backend", "frontend", "nginx")
OLD_DB_ID = "9ab9d141599e8b33f51a1e4e9bcd16d66e92b9d134889014d9d9bd618b42f73d"
OLD_DB_IMAGE = "postgres:16.14-bookworm@sha256:714b08dd70c05160db032e4321033ac4e5b6328935b2d03ed419c3a898aa8219"
OLD_APP_SHA = "81256268276d7ead95029849037f69ec6beec202"
OLD_BACKEND_IMAGE = "sha256:df3725ef307ebd11eb353e17aecb340c8b909c1451f9032d28fb5cb285a1218c"
OLD_FRONTEND_IMAGE = "sha256:1ec82024965c504fe2c65d9e5cb3f0d3937b7bbaf4cc11ff843d2ae27d95382c"
BOOK_VOLUME = PROJECT + "_staging-book-files"
DB_VOLUME = PROJECT + "_staging-postgres-data"
MAX_BOOK_FILES = 100_000
MAX_BOOK_BYTES = 64 * 1024**3
MAX_HELPER_OUTPUT = 16 * 1024 * 1024
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX32 = re.compile(r"[0-9a-f]{32}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
F_ADD_SEALS = getattr(fcntl, "F_ADD_SEALS", 1033)
F_GET_SEALS = getattr(fcntl, "F_GET_SEALS", 1034)
REQUIRED_SEALS = (getattr(fcntl, "F_SEAL_WRITE", 0x0008) | getattr(fcntl, "F_SEAL_GROW", 0x0004)
                  | getattr(fcntl, "F_SEAL_SHRINK", 0x0002) | getattr(fcntl, "F_SEAL_SEAL", 0x0001))


class PreparationBlocked(RuntimeError):
    pass


class _FDCloser:
    def __init__(self, fd: int):
        self.fd = fd

    def close(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            os.close(fd)

    def __del__(self):
        try:
            self.close()
        except OSError:
            pass


def _load(path: Path, name: str):
    if not path.is_file() or path.is_symlink():
        raise PreparationBlocked("Required retained helper is unavailable")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PreparationBlocked("Required retained helper is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _atomic(directory: Path, name: str, value: object, mode: int = 0o600) -> None:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    if len(raw) > 2 * 1024 * 1024:
        raise PreparationBlocked("Preparation evidence exceeds its size limit")
    path = directory / name
    pending = directory / (name + ".pending")
    if pending.exists() or pending.is_symlink():
        info = pending.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != mode or info.st_nlink != 1):
            raise PreparationBlocked("Pending preparation record is unsafe")
        pending.unlink()
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
        os.chmod(path, mode, follow_symlinks=False)
        dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        pending.unlink(missing_ok=True)


def _read_private_file(path: Path, maximum: int = 2 * 1024 * 1024) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise PreparationBlocked("Private operation evidence file is unavailable") from exc
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o600 or before.st_size > maximum):
            raise PreparationBlocked("Private operation evidence file is unsafe")
        raw = os.read(fd, maximum + 1)
        after = os.fstat(fd)
        if (len(raw) > maximum or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise PreparationBlocked("Private operation evidence changed while read")
        return raw
    finally:
        os.close(fd)


def _read_journal(directory: Path, source_sha: str, operation_id: str) -> dict:
    path = directory / "preparation-journal.json"
    if not path.exists():
        return {"schema":"belib-retained-preparation-journal/v1","source_sha":source_sha,
                "operation_id":operation_id,"phase":"initial","image_ids":{},"container_ids":{},"container_pins":{},"intent":None}
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 65536):
            raise PreparationBlocked("Preparation journal is unsafe")
        value = json.loads(os.read(fd, 65537))
    finally:
        os.close(fd)
    fields = {"schema","source_sha","operation_id","phase","image_ids","container_ids","container_pins","intent"}
    if (not isinstance(value, dict) or set(value) != fields or value["schema"] != "belib-retained-preparation-journal/v1"
            or value["source_sha"] != source_sha or value["operation_id"] != operation_id
            or value["phase"] not in ("initial","building","built","creating","created","prepared")
            or not isinstance(value["image_ids"], dict) or not isinstance(value["container_ids"], dict)
            or not isinstance(value["container_pins"], dict)):
        raise PreparationBlocked("Preparation journal does not match this exact operation")
    if (value["phase"] == "initial" and value["intent"] is not None
            or value["phase"] in ("building", "built", "creating", "created", "prepared")
                and not isinstance(value["intent"], str)
            or value["intent"] is not None and not re.fullmatch(r"[0-9a-f]{64}", value["intent"])):
        raise PreparationBlocked("Preparation intent binding is malformed")
    return value


def _write_journal(directory: Path, value: dict, **changes) -> dict:
    next_value = dict(value)
    next_value.update(changes)
    _atomic(directory, "preparation-journal.json", next_value)
    return next_value


def _namespace_containers(docker: Docker, project: str):
    raw = docker.run("ps", "--all", "--no-trunc", "--filter", "label=com.docker.compose.project=" + project,
                     "--format", "{{.ID}}")
    ids = [line for line in raw.decode().splitlines() if line]
    if len(ids) > 4 or any(not HEX64.fullmatch(value) for value in ids):
        raise PreparationBlocked("Candidate container namespace is ambiguous")
    result = {}
    for identity in ids:
        info = docker.inspect(identity)
        labels = info.get("Config", {}).get("Labels") or {}
        service = labels.get("com.docker.compose.service")
        if service not in ("db", *SERVICES) or service in result or labels.get("com.docker.compose.project") != project:
            raise PreparationBlocked("Candidate namespace contains an unexpected container")
        result[service] = identity
    return result


def _assert_operation_namespace_empty(docker: Docker, project: str, tags):
    if _namespace_containers(docker, project):
        raise PreparationBlocked("Unjournaled candidate containers already occupy this operation namespace")
    volumes = docker.run("volume", "ls", "--format", "{{.Name}}", maximum=1024 * 1024).decode().splitlines()
    networks = docker.run("network", "ls", "--format", "{{.Name}}", maximum=1024 * 1024).decode().splitlines()
    if (project + "_staging-postgres-data" in volumes
            or project + "_isolated" in networks or project + "_ingress" in networks):
        raise PreparationBlocked("Unjournaled Docker resources already occupy this operation namespace")
    for tag in tags.values():
        found = docker.run("image", "ls", "--filter", "reference=" + tag, "--format", "{{.Repository}}:{{.Tag}}", maximum=1024 * 1024).decode().splitlines()
        if found:
            raise PreparationBlocked("Unjournaled operation image tag already exists")


def _private_dir(path: Path) -> None:
    info = path.lstat()
    if (path != path.resolve() or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise PreparationBlocked("Prepared directory must be private, owned, and symlink-free")


def _ensure_prepared_dir(path: Path) -> None:
    if not path.is_absolute() or path != Path(os.path.abspath(path)):
        raise PreparationBlocked("Prepared directory path must be absolute and canonical")
    home = Path("/home/hiroyuki")
    try:
        home_info = home.lstat()
    except OSError as exc:
        raise PreparationBlocked("Prepared state root is unavailable") from exc
    if not stat.S_ISDIR(home_info.st_mode) or stat.S_ISLNK(home_info.st_mode) or home_info.st_uid != 1000:
        raise PreparationBlocked("Prepared state root has unsafe ownership")
    try:
        relative = path.relative_to(home)
    except ValueError as exc:
        raise PreparationBlocked("Prepared directory must stay inside the fixed user state root") from exc
    current = home
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            info = current.lstat()
            os.chmod(current, 0o700, follow_symlinks=False)
        except OSError as exc:
            raise PreparationBlocked("Prepared directory path could not be inspected") from exc
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid():
            raise PreparationBlocked("Prepared directory path contains an unsafe directory")
    _private_dir(path)


class Docker:
    def __init__(self):
        self.prefix = ["/usr/bin/docker", "--host", f"unix:///run/user/{os.getuid()}/docker.sock"]
        self.env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "DOCKER_CONFIG": "/nonexistent"}

    def run(self, *args, timeout=300, input=None, maximum=MAX_HELPER_OUTPUT):
        try:
            result = subprocess.run([*self.prefix, *args], input=input, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env=self.env, stdin=subprocess.DEVNULL if input is None else None,
                timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PreparationBlocked("Fixed Docker operation failed") from exc
        if result.returncode or len(result.stdout) > maximum:
            raise PreparationBlocked("Fixed Docker operation failed or exceeded its bound")
        return result.stdout

    def inspect(self, identity):
        try:
            return json.loads(self.run("inspect", identity, maximum=4 * 1024 * 1024))[0]
        except (ValueError, IndexError, TypeError) as exc:
            raise PreparationBlocked("Docker identity inspection was malformed") from exc


def _volume_identity(info):
    fixed = {key: info.get(key) for key in ("Name", "Driver", "Scope", "CreatedAt", "Mountpoint", "Labels", "Options")}
    return hashlib.sha256(json.dumps(fixed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _source_db_pin(info, cutover):
    networks = info.get("NetworkSettings", {}).get("Networks", {})
    ids = {name: entry.get("NetworkID") for name, entry in networks.items()}
    mounts = [m for m in info.get("Mounts", []) if m.get("Destination") == "/var/lib/postgresql/data"]
    labels = info.get("Config", {}).get("Labels") or {}
    if (info.get("Id") != OLD_DB_ID or info.get("Config", {}).get("Image") != OLD_DB_IMAGE
            or labels.get("com.docker.compose.project") != PROJECT or labels.get("com.docker.compose.service") != "db"
            or len(mounts) != 1 or mounts[0].get("Type") != "volume" or mounts[0].get("Name") != DB_VOLUME
            or set(ids) != {PROJECT + "_isolated"} or not ids[PROJECT + "_isolated"]
            or info.get("HostConfig", {}).get("PortBindings")):
        raise PreparationBlocked("Retained database identity differs from the admitted baseline")
    return {"id": info["Id"], "configuration_sha256": cutover.configuration_digest(info),
            "network_ids": ids, "volume_name": DB_VOLUME}


def _container_pin(docker, identity, project, service, cutover):
    info = docker.inspect(identity)
    labels = info.get("Config", {}).get("Labels") or {}
    if info.get("Id") != identity or labels.get("com.docker.compose.project") != project or labels.get("com.docker.compose.service") != service:
        raise PreparationBlocked("Compose container labels differ from the bounded project")
    networks = info.get("NetworkSettings", {}).get("Networks", {})
    ids = {}
    for name, entry in networks.items():
        observed = json.loads(docker.run("network", "inspect", name, maximum=1024 * 1024))[0]
        net_labels = observed.get("Labels") or {}
        if (observed.get("Id") is None or not HEX64.fullmatch(observed["Id"])
                or net_labels.get("com.docker.compose.project") != project
                or net_labels.get("com.docker.compose.network") != name.removeprefix(project + "_")
                or entry.get("NetworkID") not in (None, "", observed["Id"])):
            raise PreparationBlocked("Compose network identity is not operation-owned")
        ids[name] = observed["Id"]
    if not ids:
        raise PreparationBlocked("Compose network identity is incomplete")
    return {"id": identity, "configuration_sha256": cutover.configuration_digest(info), "network_ids": ids}, info


def _container_contract(docker, info, service, project, intent, image_id, source, values):
    config = info.get("Config", {})
    labels = config.get("Labels") or {}
    if (info.get("Image") != image_id or labels.get("com.docker.compose.project") != project
            or labels.get("com.docker.compose.service") != service
            or labels.get("belib.retained-prepare-intent") != intent
            or info.get("Name") != "/" + project + "-" + service + "-1"):
        raise PreparationBlocked("Candidate container identity does not match its durable intent")
    image = json.loads(docker.run("image", "inspect", image_id, maximum=4 * 1024 * 1024))[0].get("Config", {})
    expected_user = "node" if service == "backend" else image.get("User", "")
    expected_runtime = {key: image.get(key) for key in ("Entrypoint", "Cmd", "WorkingDir")}
    expected_runtime["User"] = expected_user
    actual_runtime = {key: config.get(key) for key in ("Entrypoint", "Cmd", "WorkingDir", "User")}
    for key in ("WorkingDir", "User"):
        if expected_runtime[key] is None:
            expected_runtime[key] = ""
        if actual_runtime[key] is None:
            actual_runtime[key] = ""
    if actual_runtime != expected_runtime:
        raise PreparationBlocked("Candidate command, entrypoint, working directory, or user differs from the image")
    env_expected = dict(item.split("=", 1) for item in image.get("Env", []) if "=" in item)
    service_env = {}
    if service == "db":
        service_env = {"POSTGRES_USER":"belib_staging_bootstrap","POSTGRES_DB":"belib_staging",
            "POSTGRES_PASSWORD":values["BELIB_STAGE_DB_BOOTSTRAP_PASSWORD"],
            "BELIB_STAGE_DB_APP_PASSWORD":values["BELIB_STAGE_DB_APP_PASSWORD"],
            "BELIB_STAGE_DB_MIGRATE_PASSWORD":values["BELIB_STAGE_DB_MIGRATE_PASSWORD"]}
    elif service == "backend":
        service_env = {"DATABASE_URL":"postgresql://belib_staging_app:"+values["BELIB_STAGE_DB_APP_PASSWORD"]+"@db:5432/belib_staging",
            "APP_PORT":"3000","ENVIRONMENT":"test","FRONTEND_URL":"https://stg-bl.hiroyuki9614.com",
            "BETTER_AUTH_URL":"https://stg-bl.hiroyuki9614.com","BETTER_AUTH_SECRET":values["BELIB_STAGE_AUTH_SECRET"],
            "BOOK_FILE_STORAGE_DRIVER":"local","BOOK_FILE_STORAGE_ROOT":"/home/node/books","NPM_CONFIG_CACHE":"/tmp/npm-cache"}
    env_expected.update(service_env)
    env_items = config.get("Env") or []
    env_actual = dict(item.split("=", 1) for item in env_items if "=" in item)
    if len(env_actual) != len(env_items) or env_actual != env_expected:
        raise PreparationBlocked("Candidate environment differs from the fixed Compose contract")
    host = info.get("HostConfig", {})
    if (host.get("Privileged") is not False or host.get("CapAdd") or host.get("Devices") or host.get("DeviceRequests")
            or host.get("VolumesFrom") or host.get("Links") or host.get("ExtraHosts") or host.get("PublishAllPorts") is True
            or host.get("PidMode") == "host" or host.get("IpcMode") == "host" or host.get("UTSMode") == "host"
            or host.get("UsernsMode") == "host"):
        raise PreparationBlocked("Candidate container has an unapproved host privilege or namespace setting")
    expected_ports = {"8080/tcp":[{"HostIp":"127.0.0.1","HostPort":values["BELIB_STAGE_PORT"]}]} if service == "nginx" else {}
    if (host.get("PortBindings") or {}) != expected_ports:
        raise PreparationBlocked("Candidate port mapping differs from the fixed loopback policy")
    mounts = info.get("Mounts", [])
    expected_mounts = {
        "db": {"/var/lib/postgresql/data": ("volume", project + "_staging-postgres-data", True),
               "/docker-entrypoint-initdb.d/10-belib-staging-roles.sh": ("bind", str(source/"scripts/staging/10-belib-staging-roles.sh"), False)},
        "backend": {"/home/node": ("volume", BOOK_VOLUME, True)},
        "frontend": {},
        "nginx": {"/etc/nginx/nginx.conf": ("bind", str(source/"nginx/nginx.conf"), False),
                  "/etc/nginx/conf.d/default.conf": ("bind", str(source/"scripts/staging/nginx-default.conf"), False)},
    }[service]
    if len(mounts) != len(expected_mounts):
        raise PreparationBlocked("Candidate container mount set differs from the fixed Compose contract")
    for mount in mounts:
        expected = expected_mounts.get(mount.get("Destination"))
        actual_identity = mount.get("Name") if expected and expected[0] == "volume" else mount.get("Source")
        if (expected is None or mount.get("Type") != expected[0] or actual_identity != expected[1]
                or mount.get("RW") is not expected[2]):
            raise PreparationBlocked("Candidate container includes an unexpected mount")


def _book_inventory(docker: Docker, backend_image: str, operation: str):
    # A short lived read-only, no-network helper sees only the retained books volume.
    js = r'''const fs=require('fs'),p=require('path'),c=require('crypto');let out=[],total=0;function walk(d,rel=''){for(const e of fs.readdirSync(d,{withFileTypes:true}).sort((a,b)=>a.name<b.name?-1:a.name>b.name?1:0)){if(e.name==='.'||e.name==='..'||e.name.includes('/')||e.name.includes('\\')||e.name.startsWith('.'))throw Error();let q=p.join(d,e.name),r=rel?rel+'/'+e.name:e.name;if(Buffer.byteLength(r,'utf8')>1024||/[\x00-\x1f\x7f]/.test(r))throw Error();let s=fs.lstatSync(q);if(s.isSymbolicLink())throw Error();if(s.isDirectory())walk(q,r);else if(s.isFile()){if(out.length>=100000||s.size>68719476736-total)throw Error();let h=c.createHash('sha256'),fd=fs.openSync(q,'r');try{let b=Buffer.alloc(1048576),n;while((n=fs.readSync(fd,b,0,b.length,null))>0)h.update(b.subarray(0,n));}finally{fs.closeSync(fd)}let a=fs.lstatSync(q);if(a.size!==s.size||a.mtimeMs!==s.mtimeMs||a.ino!==s.ino)throw Error();total+=s.size;out.push({path:r,size:s.size,sha256:h.digest('hex')})}else throw Error()}}walk('/home/node/books');process.stdout.write(JSON.stringify(out))'''
    helper_name = "belib-retained-books-" + operation
    prior = docker.run("ps", "--all", "--no-trunc", "--filter", "name=^/" + helper_name + "$", "--format", "{{.ID}}").decode().splitlines()
    if prior:
        if len(prior) != 1 or not HEX64.fullmatch(prior[0]):
            raise PreparationBlocked("Book inventory helper identity is ambiguous")
        info = docker.inspect(prior[0])
        labels = info.get("Config", {}).get("Labels") or {}
        mounts = [m for m in info.get("Mounts", []) if m.get("Name") == BOOK_VOLUME and m.get("Destination") == "/home/node"]
        if (info.get("Name") != "/" + helper_name or info.get("Image") != backend_image
                or labels.get("belib.retained-book-inventory") != operation or len(mounts) != 1
                or mounts[0].get("RW") is not False or info.get("HostConfig", {}).get("NetworkMode") != "none"):
            raise PreparationBlocked("Refusing to remove an unowned book inventory helper")
        docker.run("rm", "--force", prior[0])
    raw = docker.run("run", "--rm", "--pull=never", "--name", helper_name,
        "--label", "belib.retained-book-inventory=" + operation, "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--volume", BOOK_VOLUME + ":/home/node:ro",
        "--entrypoint", "node", backend_image, "-e", js, timeout=900, maximum=MAX_HELPER_OUTPUT)
    try:
        entries = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise PreparationBlocked("Retained book inventory helper returned malformed data") from exc
    if not isinstance(entries, list) or len(entries) > MAX_BOOK_FILES:
        raise PreparationBlocked("Retained book inventory exceeds its bound")
    entries.sort(key=lambda row: row.get("path", ""))
    return entries


def _compose(directory: Path, source: Path, env_file: Path, project: str, override: Path, docker: Docker, *args, timeout=1800):
    # The env file is opened by Compose only after seed admission has validated its provenance.
    return docker.run("compose", "-p", project, "--project-directory", str(source), "--env-file", str(env_file),
        "-f", str(source / "compose.staging.yaml"), "-f", str(override), *args, timeout=timeout)


def _prepare_admitted(admission: dict, *, docker: Docker | None = None) -> dict:
    """Build and stage one operation from a trusted admission handed off by bootstrap."""
    required = {"source_root", "source_sha", "operation_id", "prepared_directory", "env_file"}
    if not isinstance(admission, dict) or set(admission) != required:
        raise PreparationBlocked("Trusted admission descriptor has invalid fields")
    source = Path(admission["source_root"])
    prepared = Path(admission["prepared_directory"])
    env_file = Path(admission["env_file"])
    sha, operation = admission["source_sha"], admission["operation_id"]
    if not HEX40.fullmatch(sha or "") or not HEX32.fullmatch(operation or ""):
        raise PreparationBlocked("Source and operation identities are malformed")
    if source != source.resolve(strict=True) or not source.is_dir():
        raise PreparationBlocked("Admitted source root must be an absolute real directory")
    if prepared.exists() or prepared.is_symlink():
        _private_dir(prepared)
    else:
        fixed_prepared = Path("/home/hiroyuki/.local/state/belib-staging/retained-upgrades") / operation
        if prepared != fixed_prepared:
            raise PreparationBlocked("A missing prepared directory may be created only at the fixed operation path")
        _ensure_prepared_dir(prepared)
    stage = source / "scripts/staging"
    seed = _load(stage / "seed.py", "prepare_seed")
    pg = _load(stage / "retained-postgres.py", "prepare_pg")
    cutover = _load(stage / "retained-cutover.py", "prepare_cutover")
    # This validation is mandatory and happens before credentials or Docker are used.
    env_fd, values = seed.validate_staging_admission(source, env_file, os.getuid())
    env_closer = _FDCloser(env_fd)
    os.set_inheritable(env_fd, True)
    compose_env = Path(f"/proc/{os.getpid()}/fd/{env_fd}")
    if values.get("BELIB_STAGE_VCS_REF") != sha:
        raise PreparationBlocked("Admitted source differs from the staging env source")
    if int(values.get("BELIB_STAGE_PORT", "0")) not in range(1024, 65536):
        raise PreparationBlocked("Staging loopback port is invalid")
    journal = _read_journal(prepared, sha, operation)
    if not (prepared / "preparation-journal.json").exists():
        _atomic(prepared, "preparation-journal.json", journal)
    docker = docker or Docker()
    project = PROJECT + "-upgrade-" + operation
    old_db = docker.inspect(OLD_DB_ID)
    source_pin = _source_db_pin(old_db, cutover)
    if (not old_db.get("State", {}).get("Running") or old_db.get("State", {}).get("Health", {}).get("Status") != "healthy"
            or old_db.get("State", {}).get("Restarting") or old_db.get("State", {}).get("Paused")):
        raise PreparationBlocked("Retained database is not in its exact healthy baseline state")
    old_app_ids = {}
    old_pins = {}
    old_info = {}
    for service in SERVICES:
        # Resolve exactly one currently retained Compose service by fixed project/service labels.
        rows = docker.run("ps", "--all", "--no-trunc", "--filter", "label=com.docker.compose.project=" + PROJECT,
                          "--filter", "label=com.docker.compose.service=" + service, "--format", "{{.ID}}")
        ids = [line for line in rows.decode().splitlines() if line]
        if len(ids) != 1 or not HEX64.fullmatch(ids[0]):
            raise PreparationBlocked("Retained application service identity is ambiguous")
        pin, info = _container_pin(docker, ids[0], PROJECT, service, cutover)
        if (not info.get("State", {}).get("Running") or info.get("State", {}).get("Restarting")
                or info.get("State", {}).get("Paused")):
            raise PreparationBlocked("A retained application service is not in the expected running state")
        image = docker.run("image", "inspect", info["Image"], maximum=4 * 1024 * 1024)
        image_info = json.loads(image)[0]
        image_revision = (image_info.get("Config", {}).get("Labels") or {}).get("org.opencontainers.image.revision")
        expected = OLD_BACKEND_IMAGE if service == "backend" else OLD_FRONTEND_IMAGE if service == "frontend" else None
        if service in ("backend", "frontend") and (info.get("Image") != expected or image_revision != OLD_APP_SHA):
            raise PreparationBlocked("Retained application OCI identity differs from the approved baseline")
        if service == "nginx" and info.get("Config", {}).get("Image") != "nginx:1.27-alpine@sha256:62223d644fa234c3a1cc785ee14242ec47a77364226f1c811d2f669f96dc2ac8":
            raise PreparationBlocked("Retained nginx image differs from the approved baseline")
        expected_ports = {"8080/tcp":[{"HostIp":"127.0.0.1","HostPort":values["BELIB_STAGE_PORT"]}]} if service == "nginx" else {}
        if (info.get("HostConfig", {}).get("PortBindings") or {}) != expected_ports:
            raise PreparationBlocked("Retained application endpoint differs from the exact loopback baseline")
        if service == "backend":
            books = [m for m in info.get("Mounts", []) if m.get("Destination") == "/home/node"]
            if len(books) != 1 or books[0].get("Name") != BOOK_VOLUME:
                raise PreparationBlocked("Retained backend book-volume identity differs from the baseline")
        old_app_ids[service], old_pins[service], old_info[service] = ids[0], pin, info
    volume_infos = {}
    for name in (BOOK_VOLUME, DB_VOLUME):
        rows = json.loads(docker.run("volume", "inspect", name, maximum=1024 * 1024))
        if len(rows) != 1 or rows[0].get("Name") != name:
            raise PreparationBlocked("Retained volume identity is ambiguous")
        labels = rows[0].get("Labels") or {}
        expected_compose_volume = name.removeprefix(PROJECT + "_")
        if (labels.get("com.docker.compose.project") != PROJECT
                or labels.get("com.docker.compose.volume") != expected_compose_volume):
            raise PreparationBlocked("Retained volume labels differ from the exact Compose baseline")
        volume_infos[name] = rows[0]
    # Write a fixed JSON Compose override: operation project, external original books,
    # private candidate DB volume, and operation-scoped build tags.
    tags = {service: "belib-retained:" + operation + "-" + service for service in ("backend", "frontend", "migrate")}
    if journal["phase"] == "initial":
        _assert_operation_namespace_empty(docker, project, tags)
        # Persist ownership before BuildKit can create any tagged image. A lost
        # response can then resume only images carrying this nonce and source.
        journal = _write_journal(prepared, journal, phase="building", intent=secrets.token_hex(32))
    if journal["phase"] == "building":
        if _namespace_containers(docker, project):
            raise PreparationBlocked("Candidate containers exist before the durable image build")
        volumes = docker.run("volume", "ls", "--format", "{{.Name}}", maximum=1024 * 1024).decode().splitlines()
        networks = docker.run("network", "ls", "--format", "{{.Name}}", maximum=1024 * 1024).decode().splitlines()
        if (project + "_staging-postgres-data" in volumes or project + "_isolated" in networks
                or project + "_ingress" in networks):
            raise PreparationBlocked("Candidate Docker resources exist before the durable image build")
        # Existing operation tags are recoverable only when their image config
        # proves that this journaled build intent created them.
        for service, tag in tags.items():
            found = docker.run("image", "ls", "--filter", "reference=" + tag,
                               "--format", "{{.Repository}}:{{.Tag}}", maximum=1024 * 1024).decode().splitlines()
            if found:
                if found != [tag]:
                    raise PreparationBlocked("Operation image tag namespace is ambiguous")
                tagged = json.loads(docker.run("image", "inspect", tag, maximum=4 * 1024 * 1024))[0]
                labels = tagged.get("Config", {}).get("Labels") or {}
                if (labels.get("belib.retained-prepare-intent") != journal["intent"]
                        or labels.get("belib.retained-prepare-source") != sha):
                    raise PreparationBlocked("Existing operation image tag is not owned by this build intent")
    override_value = {"name": project, "services": {
        "backend": {"build": {"tags": [tags["backend"]], "labels":{"belib.retained-prepare-intent":journal["intent"],"belib.retained-prepare-source":sha}, "context":"./backend",
                    "dockerfile":"../scripts/staging/Dockerfile.backend.staging", "target":"runtime", "args":{"VCS_REF":sha}},},
        "frontend": {"build": {"tags": [tags["frontend"]], "labels":{"belib.retained-prepare-intent":journal["intent"],"belib.retained-prepare-source":sha}}},
        "migrate": {"build": {"tags": [tags["migrate"]], "labels":{"belib.retained-prepare-intent":journal["intent"],"belib.retained-prepare-source":sha}}},
        "db": {"volumes": [{"type":"bind","source":str(source/"scripts/staging/10-belib-staging-roles.sh"),"target":"/docker-entrypoint-initdb.d/10-belib-staging-roles.sh","read_only":True,"bind":{"create_host_path":False}}]},
        },
        "volumes": {"staging-postgres-data": {"name": project + "_staging-postgres-data"},
                    "staging-book-files": {"external": True, "name": BOOK_VOLUME}},
        "networks": {"isolated": {"name": project + "_isolated", "internal": True}, "ingress": {"name": project + "_ingress", "driver":"bridge"}}}
    override_path = prepared / "compose.override.json"
    _atomic(prepared, override_path.name, override_value)
    image_ids = journal["image_ids"]
    if journal["phase"] == "building":
        # The durable building intent makes this safe to repeat after a lost
        # Docker response, while the checks above rule out unrelated resources.
        _compose(prepared, source, compose_env, project, override_path, docker, "build", "backend", "frontend", "migrate", timeout=7200)
        image_ids = {}
        for service, tag in tags.items():
            image = json.loads(docker.run("image", "inspect", tag, maximum=4 * 1024 * 1024))[0]
            image_id = image.get("Id")
            if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
                raise PreparationBlocked("Built image did not resolve to an immutable image ID")
            if service in ("backend", "frontend") and (image.get("Config", {}).get("Labels") or {}).get("org.opencontainers.image.revision") != sha:
                raise PreparationBlocked("Built application image does not embed the admitted source SHA")
            labels = image.get("Config", {}).get("Labels") or {}
            if (labels.get("belib.retained-prepare-intent") != journal["intent"]
                    or labels.get("belib.retained-prepare-source") != sha):
                raise PreparationBlocked("Built image does not bind the durable operation intent and source")
            image_ids[service] = image_id
        journal = _write_journal(prepared, journal, phase="built", image_ids=image_ids)
    elif set(image_ids) != set(tags) or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", value or "") for value in image_ids.values()):
        raise PreparationBlocked("Durable operation journal lacks immutable image identities")
    # Immutable image IDs bind create/resume to exactly the recorded build outputs.
    for service in tags:
        image_info = json.loads(docker.run("image", "inspect", image_ids[service], maximum=4 * 1024 * 1024))[0]
        if image_info.get("Id") != image_ids[service]:
            raise PreparationBlocked("Journaled build image disappeared or changed")
        if service in ("backend", "frontend") and (image_info.get("Config", {}).get("Labels") or {}).get("org.opencontainers.image.revision") != sha:
            raise PreparationBlocked("Journaled app image does not bind the admitted source SHA")
        image_labels = image_info.get("Config", {}).get("Labels") or {}
        if (image_labels.get("belib.retained-prepare-intent") != journal["intent"]
                or image_labels.get("belib.retained-prepare-source") != sha):
            raise PreparationBlocked("Journaled build image lost its durable operation intent")
        # Compose needs a name:tag reference in its model. The tag is operation
        # scoped and local-only; create uses pull never and every resulting
        # container is checked against this journaled immutable image ID.
        override_value["services"].setdefault(service, {})["image"] = tags[service]
    expected_support_images = {}
    for service, image_ref in (("db", OLD_DB_IMAGE), ("nginx", "nginx:1.27-alpine@sha256:62223d644fa234c3a1cc785ee14242ec47a77364226f1c811d2f669f96dc2ac8")):
        image_info = json.loads(docker.run("image", "inspect", image_ref, maximum=4 * 1024 * 1024))[0]
        expected_support_images[service] = image_info.get("Id")
        if not isinstance(expected_support_images[service], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_support_images[service]):
            raise PreparationBlocked("Pinned PostgreSQL or nginx image is unavailable")
    if journal["phase"] == "built":
        if journal["phase"] == "built":
            if _namespace_containers(docker, project):
                raise PreparationBlocked("Candidate containers exist before the durable create intent")
            volumes = docker.run("volume", "ls", "--format", "{{.Name}}", maximum=1024 * 1024).decode().splitlines()
            networks = docker.run("network", "ls", "--format", "{{.Name}}", maximum=1024 * 1024).decode().splitlines()
            if (project + "_staging-postgres-data" in volumes or project + "_isolated" in networks
                    or project + "_ingress" in networks):
                raise PreparationBlocked("Candidate volume/network exists before the durable create intent")
        intent = journal["intent"]
        journal = _write_journal(prepared, journal, phase="creating", intent=intent)
    else:
        intent = journal["intent"]
    for service in ("db", *SERVICES):
        override_value["services"].setdefault(service, {}).setdefault("labels", {})["belib.retained-prepare-intent"] = intent
    override_value["volumes"]["staging-postgres-data"]["labels"] = {"belib.retained-prepare-intent": intent}
    for network in ("isolated", "ingress"):
        override_value["networks"][network].setdefault("labels", {})["belib.retained-prepare-intent"] = intent
    _atomic(prepared, override_path.name, override_value)
    candidate = _namespace_containers(docker, project)
    if journal["phase"] in ("created", "prepared") and (candidate != journal["container_ids"]
            or set(journal["container_pins"]) != {"db", *SERVICES}):
        raise PreparationBlocked("Journaled candidate containers are missing")
    for service, identity in candidate.items():
        info = docker.inspect(identity)
        labels = info.get("Config", {}).get("Labels") or {}
        expected_image = image_ids.get(service) or expected_support_images.get(service)
        if (info.get("Image") != expected_image or labels.get("belib.retained-prepare-intent") != intent
                or info.get("Name") != "/" + project + "-" + service + "-1"):
            raise PreparationBlocked("Existing candidate container does not match the durable create intent")
        if service != "db" and info.get("State", {}).get("Running"):
            raise PreparationBlocked("Candidate application unexpectedly started during a previous attempt")
    if journal["phase"] in ("created", "prepared") and not candidate:
        raise PreparationBlocked("Journaled candidate containers are missing")
    if journal["phase"] == "creating":
        existing_volumes = docker.run("volume", "ls", "--format", "{{.Name}}", maximum=1024 * 1024).decode().splitlines()
        existing_networks = docker.run("network", "ls", "--format", "{{.Name}}", maximum=1024 * 1024).decode().splitlines()
        for kind, names, expected_names in (("volume", existing_volumes, [project + "_staging-postgres-data"]),
                                            ("network", existing_networks, [project + "_isolated", project + "_ingress"])):
            for name in expected_names:
                if name not in names:
                    continue
                resource = json.loads(docker.run(kind, "inspect", name, maximum=1024 * 1024))[0]
                labels = resource.get("Labels") or {}
                if labels.get("belib.retained-prepare-intent") != intent or labels.get("com.docker.compose.project") != project:
                    raise PreparationBlocked("Existing candidate volume/network lacks this operation create intent")
        if set(candidate) != {"db", *SERVICES}:
            _compose(prepared, source, compose_env, project, override_path, docker, "create", "--no-recreate",
                    "--no-build", "--pull", "never", "db", "backend", "frontend", "nginx", timeout=900)
            candidate = _namespace_containers(docker, project)
        if set(candidate) != {"db", *SERVICES}:
            raise PreparationBlocked("Operation intent did not produce the exact four candidate containers")
    env_closer.close()
    candidate_infos = {service: docker.inspect(identity) for service, identity in candidate.items()}
    candidate_container_pins = {service: cutover.configuration_digest(info) for service, info in candidate_infos.items()}
    if journal["phase"] in ("created", "prepared") and candidate_container_pins != journal["container_pins"]:
        raise PreparationBlocked("Candidate container configuration changed since its durable journal")
    journal = _write_journal(prepared, journal, phase="created", container_ids=candidate,
                             container_pins=candidate_container_pins)
    if any(info.get("State", {}).get("Running") for name, info in candidate_infos.items() if name != "db"):
        raise PreparationBlocked("A candidate application service started unexpectedly")
    for service in ("db", *SERVICES):
        image_id = image_ids.get(service) or expected_support_images.get(service)
        _container_contract(docker, candidate_infos[service], service, project, intent, image_id, source, values)
    pre_db_networks = candidate_infos["db"].get("NetworkSettings", {}).get("Networks", {})
    if set(pre_db_networks) != {project + "_isolated"}:
        raise PreparationBlocked("Candidate DB must use only the operation isolated network")
    for service, expected_names in (("backend", {project + "_isolated"}), ("frontend", {project + "_isolated"}),
                                    ("nginx", {project + "_isolated", project + "_ingress"})):
        if set(candidate_infos[service].get("NetworkSettings", {}).get("Networks", {})) != expected_names:
            raise PreparationBlocked("Candidate app network set differs from the fixed topology")
    for network_name in (project + "_isolated", project + "_ingress"):
        network_info = json.loads(docker.run("network", "inspect", network_name, maximum=1024 * 1024))[0]
        network_labels = network_info.get("Labels") or {}
        short_name = network_name.removeprefix(project + "_")
        if (network_labels.get("com.docker.compose.project") != project
                or network_labels.get("com.docker.compose.network") != short_name
                or network_labels.get("belib.retained-prepare-intent") != intent
                or network_info.get("Driver") != "bridge"
                or (short_name == "isolated") != (network_info.get("Internal") is True)):
            raise PreparationBlocked("Candidate network identity, intent, or policy is invalid")
    pre_db_volume = json.loads(docker.run("volume", "inspect", project + "_staging-postgres-data", maximum=1024 * 1024))[0]
    pre_db_volume_labels = pre_db_volume.get("Labels") or {}
    if (pre_db_volume.get("Name") != project + "_staging-postgres-data"
            or pre_db_volume_labels.get("com.docker.compose.project") != project
            or pre_db_volume_labels.get("com.docker.compose.volume") != "staging-postgres-data"
            or pre_db_volume_labels.get("belib.retained-prepare-intent") != intent):
        raise PreparationBlocked("Candidate database volume lacks the durable operation ownership labels")
    for service in SERVICES:
        expected_image = image_ids.get(service, expected_support_images.get(service))
        info = candidate_infos[service]
        if info.get("Image") != expected_image:
            raise PreparationBlocked("Candidate service does not use its immutable operation image")
        expected_name = "/" + project + "-" + service + "-1"
        if info.get("Name") != expected_name:
            raise PreparationBlocked("Candidate container name differs from the fixed operation namespace")
        ports = info.get("HostConfig", {}).get("PortBindings") or {}
        expected_ports = {"8080/tcp":[{"HostIp":"127.0.0.1","HostPort":values["BELIB_STAGE_PORT"]}]} if service == "nginx" else {}
        if ports != expected_ports:
            raise PreparationBlocked("Candidate service port binding differs from the fixed loopback endpoint")
    backend_book_mounts = [m for m in candidate_infos["backend"].get("Mounts", []) if m.get("Destination") == "/home/node"]
    if len(backend_book_mounts) != 1 or backend_book_mounts[0].get("Name") != BOOK_VOLUME or backend_book_mounts[0].get("RW") is not True:
        raise PreparationBlocked("Candidate backend does not mount the retained book volume at its fixed application path")
    db_info = candidate_infos["db"]
    db_mounts = [m for m in db_info.get("Mounts", []) if m.get("Destination") == "/var/lib/postgresql/data"]
    if (len(db_mounts) != 1 or db_mounts[0].get("Name") != project + "_staging-postgres-data"
            or db_mounts[0].get("Name") == DB_VOLUME):
        raise PreparationBlocked("Candidate database volume is not operation-owned")
    if not candidate_infos["db"].get("State", {}).get("Running"):
        docker.run("start", candidate["db"], timeout=120)
    # Confirm the retained DB has not changed during candidate build and preparation.
    source_port = pg.RetainedPostgres.for_retained(prepared, operation)
    try:
        dump_sha = source_port.backup()
        source_port.verify_restore(dump_sha)
        report_path = prepared / "restore-report.json"
        report = json.loads(_read_private_file(report_path, 1024 * 1024))
    finally:
        source_port.cleanup_restore(missing_ok=True)
    query_report = json.dumps({"schema_sha256": report["schema_sha256"], "data": report["data"]}, sort_keys=True, separators=(",", ":")).encode()
    if len(query_report) > 1024 * 1024:
        raise PreparationBlocked("Restore fingerprint report exceeds the evidence size limit")
    _atomic(prepared, "query-report.json", {"schema_sha256": report["schema_sha256"], "data": report["data"]})
    old_network_names = set()
    for info in [old_db, *old_info.values()]:
        old_network_names.update(info.get("NetworkSettings", {}).get("Networks", {}))
    network_ids = []
    for name in sorted(old_network_names):
        observed = json.loads(docker.run("network", "inspect", name, maximum=1024 * 1024))[0]
        labels = observed.get("Labels") or {}
        expected_network_name = name.removeprefix(PROJECT + "_")
        if (observed.get("Id") not in [entry.get("NetworkID") for item in [old_db, *old_info.values()] for entry in item.get("NetworkSettings", {}).get("Networks", {}).values()]
                or labels.get("com.docker.compose.project") != PROJECT
                or labels.get("com.docker.compose.network") != expected_network_name
                or observed.get("Driver") != "bridge"
                or (expected_network_name == "isolated") != (observed.get("Internal") is True)):
            raise PreparationBlocked("Retained network identity changed during observation")
        network_ids.append(observed["Id"])
    if old_network_names != {PROJECT + "_isolated", PROJECT + "_ingress"}:
        raise PreparationBlocked("Retained application network set differs from the exact baseline")
    book_files = _book_inventory(docker, image_ids["backend"], operation)
    if _book_inventory(docker, image_ids["backend"], operation) != book_files:
        raise PreparationBlocked("Retained book volume changed during inventory")
    # The old database remains live; compare its current fingerprint with the
    # exact archive again after the complete inventory window.
    try:
        source_port.verify_restore(dump_sha)
        report = json.loads(_read_private_file(report_path, 1024 * 1024))
    finally:
        source_port.cleanup_restore(missing_ok=True)
    old_volumes = [{"name": name, "identity_sha256": _volume_identity(volume_infos[name])} for name in (BOOK_VOLUME, DB_VOLUME)]
    manifest = {"schema":"belib-retained-upgrade-manifest/v2", "repository":"hiroyuki9614/book_library",
        "pull_request":103,"branch":"codex/belib-i5-isolated-staging-20261107","host":"ubuntu-worker","principal":"hiroyuki","uid":1000,
        "project":PROJECT,"source_sha":sha,"prior_application_sha":OLD_APP_SHA,"prior_backend_image_digest":OLD_BACKEND_IMAGE,
        "prior_frontend_image_digest":OLD_FRONTEND_IMAGE,"database_lineage":"6963103fd6bec332b4057457d07b890fe5c11e73",
        "db_container_id":OLD_DB_ID,"db_image_digest":OLD_DB_IMAGE,"volumes":old_volumes,"network_ids":sorted(network_ids),"book_files":book_files}
    manifest_sha = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    _atomic(prepared, "manifest.json", manifest)
    old_plan = {service: old_pins[service] for service in SERVICES}
    new_plan = {}
    for service in SERVICES:
        pin, _info = _container_pin(docker, candidate[service], project, service, cutover)
        new_plan[service] = pin
    port = int(values["BELIB_STAGE_PORT"])
    cutover_plan = {"schema":"belib-isolated-copy-cutover/v1","source_sha":sha,"operation_id":operation,"port":port,"old":old_plan,"new":new_plan}
    candidate_db_info = docker.inspect(candidate["db"])
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        state = candidate_db_info.get("State", {})
        health = state.get("Health", {}).get("Status")
        if state.get("Running") and health == "healthy":
            break
        time.sleep(.5)
        candidate_db_info = docker.inspect(candidate["db"])
    else:
        raise PreparationBlocked("Candidate database did not become healthy")
    candidate_infos["db"] = candidate_db_info
    db_info = candidate_db_info
    db_networks = db_info.get("NetworkSettings", {}).get("Networks", {})
    isolated_name = project + "_isolated"
    ingress_name = project + "_ingress"
    if set(db_networks) != {isolated_name}:
        raise PreparationBlocked("Candidate DB must use only its private isolated network")
    candidate_network = json.loads(docker.run("network", "inspect", isolated_name, maximum=1024 * 1024))[0]
    candidate_network_labels = candidate_network.get("Labels") or {}
    if (candidate_network.get("Id") != next(iter(db_networks.values())).get("NetworkID")
            or candidate_network.get("Driver") != "bridge" or candidate_network.get("Internal") is not True
            or candidate_network_labels.get("com.docker.compose.project") != project
            or candidate_network_labels.get("com.docker.compose.network") != "isolated"):
        raise PreparationBlocked("Candidate isolated network identity or policy is invalid")
    for service in ("backend", "frontend"):
        nets = candidate_infos[service].get("NetworkSettings", {}).get("Networks", {})
        if set(nets) != {isolated_name} or next(iter(nets.values())).get("NetworkID") not in (None, "", candidate_network["Id"]):
            raise PreparationBlocked("Candidate app service shares an unexpected network")
    nginx_nets = candidate_infos["nginx"].get("NetworkSettings", {}).get("Networks", {})
    if set(nginx_nets) != {isolated_name, ingress_name}:
        raise PreparationBlocked("Candidate nginx network set differs from the fixed topology")
    ingress = json.loads(docker.run("network", "inspect", ingress_name, maximum=1024 * 1024))[0]
    ingress_labels = ingress.get("Labels") or {}
    if (ingress.get("Driver") != "bridge" or ingress.get("Internal") is True
            or ingress_labels.get("com.docker.compose.project") != project
            or ingress_labels.get("com.docker.compose.network") != "ingress"):
        raise PreparationBlocked("Candidate ingress network identity or policy is invalid")
    candidate_db_volume = json.loads(docker.run("volume", "inspect", project + "_staging-postgres-data", maximum=1024 * 1024))[0]
    candidate_volume_labels = candidate_db_volume.get("Labels") or {}
    if (candidate_db_volume.get("Name") != project + "_staging-postgres-data"
            or candidate_volume_labels.get("com.docker.compose.project") != project
            or candidate_volume_labels.get("com.docker.compose.volume") != "staging-postgres-data"):
        raise PreparationBlocked("Candidate DB volume identity or ownership labels are invalid")
    candidate_db = {"id":candidate["db"],"configuration_sha256":cutover.configuration_digest(db_info),
        "volume_name":candidate_db_volume["Name"],"network_id":next(iter(db_info["NetworkSettings"]["Networks"].values()))["NetworkID"]}
    _atomic(prepared, "restore-attestation-request.json", {"status":"UNSIGNED_NOT_ADOPTED","payload_template":{
        "schema":"belib-retained-upgrade-restore-attestation/v1","backup_sha256":dump_sha,"manifest_sha256":manifest_sha,
        "postgresql_major_version":16,"restore_target_identity":{"disposable":True,"identity":report["restore_container_id"]},
        "integrity_query_sha256":hashlib.sha256(query_report).hexdigest(),"restore_success":True}})
    stage_identity = {"repository":"hiroyuki9614/book_library","pull_request":103,"branch":"codex/belib-i5-isolated-staging-20261107",
        "host":"ubuntu-worker","principal":"hiroyuki","uid":1000,"project":PROJECT}
    _atomic(prepared, "network-inventory-request.json", {"status":"UNSIGNED_NOT_ADOPTED","payload_template":{
        "schema":"belib-retained-upgrade-network-inventory/v1",**stage_identity,"prior_application_sha":OLD_APP_SHA,
        "network_ids":sorted(network_ids)}})
    _atomic(prepared, "backup-roots-request.json", {"status":"UNSIGNED_NOT_ADOPTED","payload_template":{
        "schema":"belib-retained-upgrade-backup-roots/v1",**stage_identity,"repository_root":str(source),
        "retained_db_root":volume_infos[DB_VOLUME].get("Mountpoint"),"retained_books_root":volume_infos[BOOK_VOLUME].get("Mountpoint")}})
    config = {"schema":"belib-retained-operator/v1","enabled":False,"source_sha":sha,"topology":"isolated-copy",
        "prepared_directory":str(prepared),"authorities":{},"cutover_plan":cutover_plan,"candidate_database":candidate_db,
        "source_database":source_pin,"migration_image":image_ids["migrate"]}
    _atomic(prepared, "candidate-config.json", config)
    receipt = {"schema":"belib-retained-preparation-receipt/v1","source_sha":sha,"operation_id":operation,
        "image_tags":tags,"image_ids":image_ids,
        "build_inputs_sha256":{name:hashlib.sha256((source/name).read_bytes()).hexdigest()
            for name in ("compose.staging.yaml","scripts/staging/Dockerfile.backend.staging","scripts/staging/Dockerfile.frontend.staging",
                         "backend/.dockerignore","frontend/.dockerignore")},
        "candidate_container_ids":candidate,"old_container_pins":old_pins,
        "old_container_oci":{"db":{"image_id":old_db.get("Image"),"image_ref":old_db.get("Config",{}).get("Image")},
            **{service:{"image_id":old_info[service].get("Image"),"image_ref":old_info[service].get("Config",{}).get("Image")}
               for service in SERVICES}},
        "old_database_pin":source_pin,"old_volume_sha256":{name:row["identity_sha256"] for name,row in zip((BOOK_VOLUME,DB_VOLUME),old_volumes)},
        "manifest_sha256":manifest_sha,"backup_sha256":dump_sha,
        "candidate_apps_stopped":True,"candidate_database_started":True,"original_database_mutated":False}
    _atomic(prepared, "preparation-receipt.json", receipt)
    journal = _write_journal(prepared, journal, phase="prepared", image_ids=image_ids, container_ids=candidate)
    return receipt


def prepare(admission: dict, *, docker: Docker | None = None) -> dict:
    fields = {"source_root", "source_sha", "operation_id", "prepared_directory", "env_file"}
    if (not isinstance(admission, dict) or set(admission) != fields
            or not isinstance(admission.get("source_root"), str)
            or not isinstance(admission.get("source_sha"), str) or not HEX40.fullmatch(admission["source_sha"])
            or not isinstance(admission.get("operation_id"), str) or not HEX32.fullmatch(admission["operation_id"])):
        raise PreparationBlocked("Trusted root admission is required")
    source = Path(admission["source_root"])
    if not source.is_absolute() or source != source.resolve(strict=True) or not source.is_dir():
        raise PreparationBlocked("Trusted source root must be absolute and symlink-free")
    guard = _load(Path(admission["source_root"]) / "scripts/staging/launch-guard.py", "prepare_launch_guard")
    with guard.project_lock():
        return _prepare_admitted(admission, docker=docker)


def finalize(prepared_directory: str | os.PathLike[str], authorities: dict) -> dict:
    """Validate externally adopted envelopes and write a candidate config only.

    The caller supplies authority pins from the installed trusted bootstrap. This
    function never installs keys, changes root config, or performs cutover.
    """
    directory = Path(prepared_directory)
    _private_dir(directory)
    if not isinstance(authorities, dict) or set(authorities) != {"restore", "network", "roots"}:
        raise PreparationBlocked("Three independently adopted authorities are required")
    upgrade = _load(Path(__file__).resolve().with_name("retained-upgrade.py"), "finalize_upgrade")
    def load_json(name):
        return upgrade.strict_json(_read_private_file(directory / name))
    pins = {}
    for name, pin in authorities.items():
        if (not isinstance(pin, dict) or set(pin) != {"issuer", "public_key_pem", "fingerprint"}
                or not isinstance(pin["issuer"], str) or not isinstance(pin["public_key_pem"], str)
                or not HEX64.fullmatch(pin["fingerprint"] or "")):
            raise PreparationBlocked("Adopted authority pin is malformed")
        pins[name] = pin
    manifest = load_json("manifest.json")
    source_sha = manifest.get("source_sha") if isinstance(manifest, dict) else None
    if not isinstance(source_sha, str) or not HEX40.fullmatch(source_sha):
        raise PreparationBlocked("Prepared manifest source identity is malformed")
    network = load_json("network-inventory.json")
    roots = load_json("backup-roots.json")
    attestation = load_json("restore-attestation.json")
    report_value = load_json("query-report.json")
    report = json.dumps(report_value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    try:
        manifest_sha = upgrade.validate_manifest(manifest, source_sha, trusted_network_inventory=network,
            trusted_network_inventory_issuer=pins["network"]["issuer"], network_inventory_public_key_pem=pins["network"]["public_key_pem"].encode(),
            trusted_network_inventory_fingerprint=pins["network"]["fingerprint"])
        backup_sha = upgrade.validate_backup_artifact(directory / "backup.dump", uid=os.getuid(), trusted_root_contract=roots,
            trusted_root_contract_issuer=pins["roots"]["issuer"], root_contract_public_key_pem=pins["roots"]["public_key_pem"].encode(),
            trusted_root_contract_fingerprint=pins["roots"]["fingerprint"])
        attestation_sha = upgrade.validate_restore_attestation(attestation, backup_sha256=backup_sha, manifest_sha256=manifest_sha,
            query_report=report, trusted_issuer=pins["restore"]["issuer"], verifier_public_key_pem=pins["restore"]["public_key_pem"].encode(),
            trusted_verifier_fingerprint=pins["restore"]["fingerprint"])
    except upgrade.UpgradePreflightError as exc:
        raise PreparationBlocked("External retained evidence did not validate") from exc
    config = load_json("candidate-config.json")
    required_config = {"schema","enabled","source_sha","topology","prepared_directory","authorities","cutover_plan","candidate_database","source_database","migration_image"}
    if (not isinstance(config, dict) or set(config) != required_config or config.get("schema") != "belib-retained-operator/v1"
            or config.get("enabled") is not False or config.get("source_sha") != source_sha
            or config.get("prepared_directory") != str(directory) or config.get("topology") != "isolated-copy"):
        raise PreparationBlocked("Candidate retained configuration differs from the prepared operation")
    cutover = _load(Path(__file__).resolve().with_name("retained-cutover.py"), "finalize_cutover")
    controller = cutover.RetainedCutover(config["cutover_plan"])
    plan = controller.plan
    if plan["source_sha"] != source_sha or plan["operation_id"] != directory.name or manifest.get("db_container_id") != OLD_DB_ID:
        raise PreparationBlocked("Candidate cutover plan is not bound to this operation and retained baseline")
    source_db = config["source_database"]
    candidate_db = config["candidate_database"]
    if (not isinstance(source_db, dict) or set(source_db) != {"id","configuration_sha256","network_ids","volume_name"}
            or source_db["id"] != manifest["db_container_id"] or source_db["volume_name"] != DB_VOLUME
            or not HEX64.fullmatch(source_db["configuration_sha256"] or "")
            or not isinstance(source_db["network_ids"], dict) or set(source_db["network_ids"]) != {PROJECT + "_isolated"}
            or any(not isinstance(value, str) or not HEX64.fullmatch(value) for value in source_db["network_ids"].values())
            or not isinstance(candidate_db, dict) or set(candidate_db) != {"id","configuration_sha256","volume_name","network_id"}
            or not HEX64.fullmatch(candidate_db["id"] or "") or not HEX64.fullmatch(candidate_db["configuration_sha256"] or "")
            or candidate_db["volume_name"] != PROJECT + "-upgrade-" + plan["operation_id"] + "_staging-postgres-data"
            or not HEX64.fullmatch(candidate_db["network_id"] or "")
            or not isinstance(config["migration_image"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", config["migration_image"])):
        raise PreparationBlocked("Candidate database/config identity is malformed")
    candidate_app_networks = [set(plan["new"][service]["network_ids"].values()) for service in ("backend", "frontend")]
    if any(networks != {candidate_db["network_id"]} for networks in candidate_app_networks):
        raise PreparationBlocked("Candidate app and database network identities differ")
    config["authorities"] = pins
    # Deliberately remains disabled: only external adoption may enable it.
    config["enabled"] = False
    _atomic(directory, "candidate-config.json", config)
    _atomic(directory, "finalize-candidate.json", {"schema":"belib-retained-finalize-candidate/v1","source_sha":source_sha,
        "manifest_sha256":manifest_sha,"backup_sha256":backup_sha,"restore_attestation_sha256":attestation_sha,
        "public_pins_used_for_validation_only":True,"requires_root_adoption":True})
    return {"config":str(directory / "candidate-config.json"),"enabled":False,"manifest_sha256":manifest_sha,"backup_sha256":backup_sha}


def _read_root_admission() -> dict:
    raw_fd = os.environ.pop("BELIB_PREPARATION_FD", "")
    if not re.fullmatch(r"[0-9]{1,4}", raw_fd) or not 3 <= int(raw_fd) <= 4096:
        raise PreparationBlocked("Root preparation admission is required")
    fd = int(raw_fd)
    try:
        info = os.fstat(fd)
        if (info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_nlink != 0 or info.st_size > 65536
                or fcntl.fcntl(fd, F_GET_SEALS) & REQUIRED_SEALS != REQUIRED_SEALS):
            raise PreparationBlocked("Preparation admission is not root-owned and immutable")
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 65537)
    finally:
        os.close(fd)
    try:
        def unique(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise PreparationBlocked("Admission contains duplicate fields")
                result[key] = item
            return result
        admission = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as exc:
        raise PreparationBlocked("Preparation admission is malformed") from exc
    required = {"schema", "source_root", "source_sha", "operation_id", "prepared_directory", "env_file", "pid"}
    if (not isinstance(admission, dict) or set(admission) != required
            or admission.get("schema") != "belib-retained-preparation-admission/v1"
            or type(admission.get("pid")) is not int or admission["pid"] != os.getpid()):
        raise PreparationBlocked("Preparation admission process or schema mismatch")
    return {key: admission[key] for key in required if key != "schema" and key != "pid"}


def main() -> int:
    try:
        if sys.argv[1:] and len(sys.argv) == 5 and sys.argv[1] == "--finalize-candidate" and sys.argv[3] == "--public-pins-file":
            operation = sys.argv[2]
            if not HEX32.fullmatch(operation or ""):
                raise PreparationBlocked("Operation id is malformed")
            pin_path = Path(sys.argv[4])
            fd = os.open(pin_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 65536):
                    raise PreparationBlocked("Public pin input must be a bounded private regular file")
                pins = json.loads(os.read(fd, 65537))
            finally:
                os.close(fd)
            if not isinstance(pins, dict):
                raise PreparationBlocked("Public pin input is malformed")
            prepared = Path("/home/hiroyuki/.local/state/belib-staging/retained-upgrades") / operation
            guard = _load(Path(__file__).resolve().with_name("launch-guard.py"), "finalize_launch_guard")
            with guard.project_lock():
                result = finalize(prepared, pins)
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            return 0
        if sys.argv[1:]:
            raise PreparationBlocked("Unsupported preparation command")
        if (os.getuid() != 1000 or os.geteuid() != 1000 or os.getgid() != 1000
                or sys.executable != "/usr/bin/python3" or not sys.flags.isolated or not sys.flags.dont_write_bytecode):
            raise PreparationBlocked("Preparation requires the fixed isolated UID 1000 runtime")
        admission = _read_root_admission()
        # The trusted bootstrap supplies only fixed paths, never CLI path overrides.
        if (admission["source_root"] != str(Path(__file__).resolve().parents[2])
                or admission["prepared_directory"] != "/home/hiroyuki/.local/state/belib-staging/retained-upgrades/" + admission["operation_id"]
                or admission["env_file"] != "/home/hiroyuki/.config/belib-staging/i5.env"):
            raise PreparationBlocked("Root admission paths differ from the installed operation layout")
        if Path(__file__).resolve() != Path(admission["source_root"]) / "scripts/staging/prepare-retained.py":
            raise PreparationBlocked("Preparation code is not the exact admitted source file")
        print(json.dumps(prepare(admission), sort_keys=True, separators=(",", ":")))
        return 0
    except Exception:
        # Avoid leaking subprocess stderr, credential values, or book/database data.
        print("BLOCKED: retained preparation failed closed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
