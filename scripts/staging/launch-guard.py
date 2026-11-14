#!/usr/bin/env -S /usr/bin/python3 -I -B
"""Gate the bounded i5 staging deployment on one signed external authorization."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import fcntl
import json
import os
import pwd
import re
import socket
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping


EXPECTED_HOST = "ubuntu-worker"
EXPECTED_USER = "hiroyuki"
EXPECTED_UID = 1000
EXPECTED_HOME = Path("/home/hiroyuki")
EXPECTED_REPO_ROOT = Path("/home/hiroyuki/workspaces/belib-i5-isolated-staging-20261107")
EXPECTED_BRANCH = "codex/belib-i5-isolated-staging-20261107"
REPOSITORY = "hiroyuki9614/book_library"
PULL_REQUEST = 103
COMPOSE_PROJECT = "belib-isolated-i5"
LOCK_GROUP = "belib-staging-cd-belib-isolated-i5"
DOCKER_CLI = "/usr/bin/docker"
PYTHON_CLI = "/usr/bin/python3"
SAFE_PATH = "/usr/bin:/bin"
CONTEXT_NAME = "belib-i5-staging-rootless"
LOCK_PATH = Path("/tmp/belib-staging-cd-belib-isolated-i5.lock")
ADMISSION_WORKTREE_PREFIX = Path("/home/hiroyuki/workspaces/belib-i5-admission-")
OPERATOR_SOURCES = Path("/opt/belib-staging/sources")
SOURCE_SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
EVIDENCE_SHA_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
ADMITTED_SOURCE_ROOT: Path | None = None
RETAINED_PRIOR_SHA = "6963103fd6bec332b4057457d07b890fe5c11e73"
RETAINED_DB_CONTAINER_ID = "9ab9d141599e8b33f51a1e4e9bcd16d66e92b9d134889014d9d9bd618b42f73d"
SNAPSHOT_ROOT = Path("/home/hiroyuki/.local/state/belib-staging/snapshots")
RETAINED_SERVICES = {"db", "backend", "frontend", "nginx"}
RETAINED_VOLUMES = {
    "belib-isolated-i5_staging-postgres-data",
    "belib-isolated-i5_staging-book-files",
}
FRESH_PROJECT_VOLUMES = frozenset(f"{COMPOSE_PROJECT}_{name}" for name in ("staging-postgres-data", "staging-book-files"))
FRESH_PROJECT_NETWORKS = frozenset(f"{COMPOSE_PROJECT}_{name}" for name in ("isolated", "ingress"))

OPERATION_ARGS: dict[str, tuple[str, ...]] = {
    "start-db": ("up", "-d", "db"),
    "migrate": ("--profile", "migration", "run", "--build", "--rm", "-T", "migrate"),
    "start-app": ("up", "-d", "--build", "backend", "frontend", "nginx"),
    "status": ("ps",),
}


class LaunchBlocked(Exception):
    pass


OPERATOR_BOOTSTRAP_BLOCK_REASON = (
    "No independent operator-installed immutable bootstrap and verifier is available; "
    "staging operations are blocked"
)


ROOT_ADMISSION: dict | None = None


def _require_operator_bootstrap_admission() -> None:
    """Admit only a sealed root-created descriptor bound to this process/argv."""
    global ROOT_ADMISSION
    ROOT_ADMISSION = None
    value = os.environ.pop("BELIB_BOOTSTRAP_FD", "")
    if not re.fullmatch(r"[0-9]{1,4}", value) or not 3 <= int(value) <= 4096:
        raise LaunchBlocked(OPERATOR_BOOTSTRAP_BLOCK_REASON)
    fd = int(value)
    try:
        info = os.fstat(fd)
        seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
        if (info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_nlink != 0
                or info.st_size > 65536 or fcntl.fcntl(fd, fcntl.F_GET_SEALS) & seals != seals):
            raise LaunchBlocked("Operator admission descriptor is not root-owned and immutable")
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 65537)
        def unique(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise LaunchBlocked("Admission contains duplicate fields")
                result[key] = item
            return result
        admission = json.loads(raw, object_pairs_hook=unique)
        expected_fields = {"schema", "grant", "source_root", "pid", "argv_sha256"}
        if isinstance(admission, dict) and isinstance(admission.get("grant"), dict) and admission["grant"].get("operation") == "upgrade-retained":
            expected_fields.add("retained_config")
        if (not isinstance(admission, dict) or set(admission) != expected_fields
                or admission["schema"] != "belib-staging-operator-admission/v1"
                or type(admission["pid"]) is not int or admission["pid"] != os.getpid()
                or admission["argv_sha256"] != hashlib.sha256(json.dumps(sys.argv[1:], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()):
            raise LaunchBlocked("Operator admission process/arguments mismatch")
        grant = admission["grant"]
        if (not isinstance(grant, dict) or not isinstance(grant.get("source_sha"), str)
                or not SOURCE_SHA_PATTERN.fullmatch(grant["source_sha"])
                or admission["source_root"] != str(OPERATOR_SOURCES / grant["source_sha"])
                or grant.get("host") != socket.gethostname() or grant.get("uid") != os.getuid()
                or type(grant.get("expires_at")) is not int or int(time.time()) >= grant["expires_at"]):
            raise LaunchBlocked("Operator admission target/source/expiry mismatch")
        ROOT_ADMISSION = admission
    except (OSError, ValueError, TypeError, AttributeError) as error:
        raise LaunchBlocked("Operator admission could not be verified") from error
    finally:
        os.close(fd)


def _require_operation_approval(args) -> None:
    if ROOT_ADMISSION is None:
        raise LaunchBlocked(OPERATOR_BOOTSTRAP_BLOCK_REASON)
    grant = ROOT_ADMISSION["grant"]
    expected = {"source_sha": args.source_sha, "operation": args.operation,
                "evidence_sha256": args.evidence_sha256}
    if args.operation == "resume-ingress":
        expected.update(prior_source_sha=args.prior_source_sha, retained_state_sha256=args.retained_state_sha256)
    if args.operation == "upgrade-retained":
        expected.update({name: getattr(args, name) for name in
                         ("prior_application_sha", "retained_state_sha256", "backup_sha256", "restore_attestation_sha256")})
    if any(grant.get(name) != value for name, value in expected.items()) or int(time.time()) >= grant["expires_at"]:
        raise LaunchBlocked("Admitted operation differs from the one-use root-verified grant")


def load_approval_module():
    if ADMITTED_SOURCE_ROOT is None:
        raise LaunchBlocked("Exact detached staging source has not been admitted")
    path = ADMITTED_SOURCE_ROOT / "scripts/staging/approval.py"
    spec = importlib.util.spec_from_file_location("belib_staging_approval", path)
    if spec is None or spec.loader is None:
        raise LaunchBlocked("The signed staging approval verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_retained_executor(source_root):
    spec = importlib.util.spec_from_file_location("belib_retained_executor", source_root / "scripts/staging/retained-executor.py")
    if spec is None or spec.loader is None:
        raise LaunchBlocked("The fixed retained executor is unavailable")
    executor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(executor)
    return executor


def load_seed_module():
    if ADMITTED_SOURCE_ROOT is None:
        raise LaunchBlocked("Exact detached staging source has not been admitted")
    path = ADMITTED_SOURCE_ROOT / "scripts/staging/seed.py"
    spec = importlib.util.spec_from_file_location("belib_staging_seed", path)
    if spec is None or spec.loader is None:
        raise LaunchBlocked("The guarded staging seed wrapper is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _docker_read(command: list[str], environment: Mapping[str, str], *, runner=subprocess.run) -> str:
    try:
        result = runner(command, env=dict(environment), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, text=True, check=False, timeout=20)
    except (OSError, subprocess.SubprocessError) as error:
        raise LaunchBlocked("Read-only retained-state inspection failed") from error
    if result.returncode != 0 or len(result.stdout or "") > 2 * 1024 * 1024:
        raise LaunchBlocked("Read-only retained-state inspection failed")
    return result.stdout or ""


def reject_existing_fresh_project_resources(
    build_root: Path, environment: Mapping[str, str], *, runner=subprocess.run,
) -> None:
    """Reject pre-existing resources using exact Compose-resolved names, independent of labels."""
    docker = [DOCKER_CLI, "--context", CONTEXT_NAME]
    inventory = {}
    for kind in ("volume", "network"):
        try:
            result = runner(
                [*docker, kind, "ls", "--format", "{{.Name}}"], cwd=build_root,
                env=dict(environment), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, check=False, timeout=20,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise LaunchBlocked("Could not safely inventory staging project volumes and networks") from error
        if result.returncode != 0 or not isinstance(result.stdout, str) or len(result.stdout) > 2 * 1024 * 1024:
            raise LaunchBlocked("Could not safely inventory staging project volumes and networks")
        names = result.stdout.splitlines()
        if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}", name) for name in names):
            raise LaunchBlocked("Staging Docker resource inventory is malformed")
        inventory[kind] = set(names)

    collisions = (inventory["volume"] & FRESH_PROJECT_VOLUMES) | (inventory["network"] & FRESH_PROJECT_NETWORKS)
    if collisions:
        raise LaunchBlocked("Staging project named volumes or networks already exist; separate resume review is required")


def _lines_json(text: str) -> list[dict]:
    try:
        values = [json.loads(line) for line in text.splitlines() if line.strip()]
    except json.JSONDecodeError as error:
        raise LaunchBlocked("Retained-state inspection output is malformed") from error
    if any(not isinstance(value, dict) for value in values):
        raise LaunchBlocked("Retained-state inspection output is malformed")
    return values


def build_retained_manifest(
    prior_source_sha: str, environment: Mapping[str, str], *, runner=subprocess.run,
    docker_prefix: list[str] | None = None, validate: bool = True,
    expected_db_image: str | None = None,
) -> dict:
    if prior_source_sha != RETAINED_PRIOR_SHA:
        raise LaunchBlocked("Only the reviewed retained source revision can be resumed")
    docker = docker_prefix or [DOCKER_CLI, "--context", CONTEXT_NAME]
    project_filter = f"label=com.docker.compose.project={COMPOSE_PROJECT}"
    ids = _docker_read([*docker, "ps", "--all", "--no-trunc", "--filter", project_filter, "--format", "{{.ID}}"],
                       environment, runner=runner).splitlines()
    volume_names = _docker_read([*docker, "volume", "ls", "--filter", project_filter, "--format", "{{.Name}}"],
                                environment, runner=runner).splitlines()
    network_ids = _docker_read([*docker, "network", "ls", "--no-trunc", "--filter", project_filter, "--format", "{{.ID}}"],
                               environment, runner=runner).splitlines()
    if len(ids) > 16 or len(volume_names) > 8 or len(network_ids) > 8:
        raise LaunchBlocked("Retained staging inventory exceeds its fixed bounds")
    container_template = (
        '{{printf \"{\\\"id\\\":%q,\\\"service\\\":%q,\\\"project\\\":%q,\\\"oneoff\\\":%q,\\\"state\\\":%q,\\\"image_id\\\":%q,\\\"image_ref\\\":%q,\\\"mounts\\\":%s,\\\"networks\\\":%s}\" '
        '.Id (index .Config.Labels \"com.docker.compose.service\") '
        '(index .Config.Labels \"com.docker.compose.project\") '
        '(index .Config.Labels \"com.docker.compose.oneoff\") .State.Status .Image .Config.Image (json .Mounts) (json .NetworkSettings.Networks)}}'
    )
    containers = []
    for container_id in ids:
        if not re.fullmatch(r"[0-9a-f]{64}", container_id):
            raise LaunchBlocked("Retained container ID is malformed")
        inspected = _lines_json(_docker_read([*docker, "inspect", "--format", container_template, container_id],
                                             environment, runner=runner))
        if len(inspected) != 1:
            raise LaunchBlocked("Retained container inspection is ambiguous")
        row = inspected[0]
        raw_mounts = row.pop("mounts", None)
        raw_networks = row.get("networks")
        if not isinstance(raw_networks, dict) or len(raw_networks) > 4:
            raise LaunchBlocked("Retained container networks are malformed")
        row["networks"] = sorted(raw_networks)
        if not isinstance(raw_mounts, list) or len(raw_mounts) > 16:
            raise LaunchBlocked("Retained container mounts are malformed")
        mounts = []
        for mount in raw_mounts:
            if not isinstance(mount, dict):
                raise LaunchBlocked("Retained container mount is malformed")
            mounts.append({
                "type": mount.get("Type"), "name": mount.get("Name") or "",
                "source": mount.get("Source") or "", "destination": mount.get("Destination") or "",
                "read_write": mount.get("RW"),
            })
        row["mounts"] = sorted(mounts, key=lambda mount: (mount["destination"], mount["source"]))
        containers.append(row)
    volume_template = (
        '{{printf \"{\\\"name\\\":%q,\\\"driver\\\":%q,\\\"scope\\\":%q,\\\"created_at\\\":%q,\\\"mountpoint\\\":%q,\\\"labels\\\":%s,\\\"options\\\":%s}\" '
        '.Name .Driver .Scope .CreatedAt .Mountpoint (json .Labels) (json .Options)}}'
    )
    volumes = []
    for name in volume_names:
        if name not in RETAINED_VOLUMES:
            raise LaunchBlocked("Unexpected retained staging volume")
        rows = _lines_json(_docker_read([*docker, "volume", "inspect", "--format", volume_template, name],
                                        environment, runner=runner))
        if len(rows) != 1:
            raise LaunchBlocked("Retained volume inspection is ambiguous")
        volumes.extend(rows)
    network_template = (
        '{{printf \"{\\\"id\\\":%q,\\\"name\\\":%q,\\\"driver\\\":%q,\\\"internal\\\":%t,\\\"labels\\\":%s}\" '
        '.Id .Name .Driver .Internal (json .Labels)}}'
    )
    networks = []
    for network_id in network_ids:
        if not re.fullmatch(r"[0-9a-f]{64}", network_id):
            raise LaunchBlocked("Retained network ID is malformed")
        rows = _lines_json(_docker_read([*docker, "network", "inspect", "--format", network_template, network_id],
                                        environment, runner=runner))
        if len(rows) != 1:
            raise LaunchBlocked("Retained network inspection is ambiguous")
        networks.extend(rows)
    manifest = {
        "schema": "belib-staging-retained-state/v1",
        "project": COMPOSE_PROJECT,
        "prior_source_sha": prior_source_sha,
        "containers": sorted(containers, key=lambda item: item.get("id", "")),
        "volumes": sorted(volumes, key=lambda item: item.get("name", "")),
        "networks": sorted(networks, key=lambda item: item.get("id", "")),
    }
    if validate:
        validate_retained_manifest(manifest, prior_source_sha, expected_db_image=expected_db_image)
    return manifest


def retained_manifest_sha256(manifest: dict) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def validate_retained_manifest(
    manifest: dict, prior_source_sha: str, *, allow_running: bool = False,
    allow_ingress: bool = False, expected_db_image: str | None = None,
    current_source_sha: str | None = None,
) -> None:
    if not isinstance(manifest, dict) or manifest.get("schema") != "belib-staging-retained-state/v1" or manifest.get("project") != COMPOSE_PROJECT or manifest.get("prior_source_sha") != prior_source_sha:
        raise LaunchBlocked("Retained-state manifest identity is invalid")
    containers = manifest.get("containers")
    if not isinstance(containers, list) or len(containers) != 4:
        raise LaunchBlocked("Retained project must contain exactly four service containers")
    by_service = {item.get("service"): item for item in containers if isinstance(item, dict)}
    if set(by_service) != RETAINED_SERVICES or len(by_service) != 4:
        raise LaunchBlocked("Retained project services are outside the resume allowlist")
    current_source_sha = current_source_sha or prior_source_sha
    for service, item in by_service.items():
        expected_state = "running" if allow_running else "exited"
        if item.get("project") != COMPOSE_PROJECT or str(item.get("oneoff", "")).lower() != "false" or item.get("state") != expected_state:
            raise LaunchBlocked("Retained containers have an unexpected state or oneoff label")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("image_id", "")):
            raise LaunchBlocked("Retained container image identity is malformed")
        if not isinstance(item.get("image_ref"), str) or not item["image_ref"]:
            raise LaunchBlocked("Retained container image reference is missing")
        if service == "db" and expected_db_image is not None and item["image_ref"] != expected_db_image:
            raise LaunchBlocked("Retained database image reference differs from the verified source")
        if service == "db" and item.get("id") != RETAINED_DB_CONTAINER_ID:
            raise LaunchBlocked("Retained database container ID differs from the reviewed rollback")
        expected_mounts = {
            "db": {"/var/lib/postgresql/data", "/docker-entrypoint-initdb.d/10-belib-staging-roles.sh"},
            "backend": {"/home/node"},
            "frontend": set(),
            "nginx": {"/etc/nginx/nginx.conf", "/etc/nginx/conf.d/default.conf"},
        }[service]
        mounts = item.get("mounts")
        if not isinstance(mounts, list) or {mount.get("destination") for mount in mounts if isinstance(mount, dict)} != expected_mounts:
            raise LaunchBlocked("Retained container mounts differ from the reviewed staging layout")
        expected_networks = [f"{COMPOSE_PROJECT}_isolated", f"{COMPOSE_PROJECT}_ingress"] if allow_ingress and service == "nginx" else [f"{COMPOSE_PROJECT}_isolated"]
        if item.get("networks") != sorted(expected_networks):
            raise LaunchBlocked("Retained container network attachments are outside the resume allowlist")
        expected_volume_by_target = {
            ("/var/lib/postgresql/data", "db"): "belib-isolated-i5_staging-postgres-data",
            ("/home/node", "backend"): "belib-isolated-i5_staging-book-files",
        }
        expected_bind_by_target = {
            ("/docker-entrypoint-initdb.d/10-belib-staging-roles.sh", "db"):
                SNAPSHOT_ROOT / prior_source_sha / "scripts/staging/10-belib-staging-roles.sh",
            ("/etc/nginx/nginx.conf", "nginx"):
                SNAPSHOT_ROOT / (current_source_sha if allow_ingress else prior_source_sha) / "nginx/nginx.conf",
            ("/etc/nginx/conf.d/default.conf", "nginx"):
                SNAPSHOT_ROOT / (current_source_sha if allow_ingress else prior_source_sha) / "scripts/staging/nginx-default.conf",
        }
        for mount in mounts:
            if mount["type"] == "bind":
                expected_path = expected_bind_by_target.get((mount["destination"], service))
                if expected_path is None or mount["source"] != str(expected_path) or mount["read_write"] is not False:
                    raise LaunchBlocked("Retained bind mount does not point to the prior verified source snapshot")
            elif mount["type"] == "volume":
                expected_volume = expected_volume_by_target.get((mount["destination"], service))
                if expected_volume is None or mount["name"] != expected_volume or mount["read_write"] is not True:
                    raise LaunchBlocked("Retained named volume mount is outside the staging allowlist")
            else:
                raise LaunchBlocked("Retained container mount type is unsupported")
    volumes = manifest.get("volumes")
    if not isinstance(volumes, list) or {item.get("name") for item in volumes if isinstance(item, dict)} != RETAINED_VOLUMES or len(volumes) != 2:
        raise LaunchBlocked("Retained project must contain exactly its two named volumes")
    for volume in volumes:
        expected_label = volume["name"].removeprefix(COMPOSE_PROJECT + "_")
        labels = volume.get("labels")
        if (volume.get("driver") != "local" or volume.get("scope") != "local" or not volume.get("created_at")
                or not Path(volume.get("mountpoint", "")).is_absolute() or volume.get("options") not in (None, {})
                or not isinstance(labels, dict) or labels.get("com.docker.compose.project") != COMPOSE_PROJECT
                or labels.get("com.docker.compose.volume") != expected_label):
            raise LaunchBlocked("Retained volume driver, labels, or mountpoint are outside the allowlist")
    networks = manifest.get("networks")
    expected_network_count = 2 if allow_ingress else 1
    if not isinstance(networks, list) or len(networks) != expected_network_count:
        raise LaunchBlocked("Retained project must contain only its original isolated network")
    by_network = {item.get("name"): item for item in networks if isinstance(item, dict)}
    if set(by_network) != ({f"{COMPOSE_PROJECT}_isolated", f"{COMPOSE_PROJECT}_ingress"} if allow_ingress else {f"{COMPOSE_PROJECT}_isolated"}):
        raise LaunchBlocked("Retained project network set is outside the resume allowlist")
    network = by_network[f"{COMPOSE_PROJECT}_isolated"]
    labels = network.get("labels")
    if (network.get("name") != f"{COMPOSE_PROJECT}_isolated" or network.get("driver") != "bridge"
            or network.get("internal") is not True or not re.fullmatch(r"[0-9a-f]{64}", network.get("id", ""))
            or not isinstance(labels, dict) or labels.get("com.docker.compose.project") != COMPOSE_PROJECT
            or labels.get("com.docker.compose.network") != "isolated"):
        raise LaunchBlocked("Retained internal network identity or labels are outside the allowlist")
    if allow_ingress:
        ingress = by_network[f"{COMPOSE_PROJECT}_ingress"]
        ingress_labels = ingress.get("labels")
        if (ingress.get("driver") != "bridge" or ingress.get("internal") is not False
                or not re.fullmatch(r"[0-9a-f]{64}", ingress.get("id", ""))
                or not isinstance(ingress_labels, dict)
                or ingress_labels.get("com.docker.compose.project") != COMPOSE_PROJECT
                or ingress_labels.get("com.docker.compose.network") != "ingress"):
            raise LaunchBlocked("Resumed ingress bridge identity or labels are outside the allowlist")


def run_resume_ingress_transaction(
    *, compose: list[str], build_root: Path, environment: Mapping[str, str], port: str,
    prior_source_sha: str, signed_digest: str, manifest_provider, current_source_sha: str | None = None,
    expected_db_image: str | None = None, env_fd: int | None = None, runner=subprocess.run,
    sleep=time.sleep, monotonic=time.monotonic, report=print,
) -> int:
    """Restart the retained DB and build only the three web services; never migrates or seeds."""
    if not re.fullmatch(r"(?:[1-9][0-9]{3,4})", port) or not 1024 <= int(port) <= 65535:
        raise LaunchBlocked("Resume loopback port is invalid")
    original = manifest_provider(prior_source_sha)
    validate_retained_manifest(original, prior_source_sha, expected_db_image=expected_db_image)
    if retained_manifest_sha256(original) != signed_digest:
        raise LaunchBlocked("Signed retained-state digest does not match the current resource manifest")
    db_id = next(item["id"] for item in original["containers"] if item["service"] == "db")
    attempted = False
    def receipt(message: str) -> None:
        report(f"{message} retained_state_sha256={signed_digest}", file=sys.stderr)
    try:
        attempted = True
        result = runner([DOCKER_CLI, "--context", CONTEXT_NAME, "start", db_id], env=dict(environment),
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        check=False, timeout=60)
        if result.returncode != 0:
            raise LaunchBlocked("Retained database container did not start")
        deadline = monotonic() + 180
        while monotonic() < deadline:
            health = _docker_read([DOCKER_CLI, "--context", CONTEXT_NAME, "inspect", "--format",
                                   "{{.State.Health.Status}}", db_id], environment, runner=runner).strip()
            if health == "healthy":
                break
            sleep(2)
        else:
            raise LaunchBlocked("Retained database health timed out")
        web_services = [*compose, "up", "-d", "--no-deps", "--build", "backend", "frontend", "nginx"]
        result = runner(web_services, cwd=build_root, env=dict(environment), stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=1800,
                        pass_fds=() if env_fd is None else (env_fd,))
        if result.returncode != 0:
            raise LaunchBlocked("Staging ingress web services did not start")
        current = manifest_provider(prior_source_sha, validate=False)
        validate_retained_manifest(current, prior_source_sha, allow_running=True, allow_ingress=True,
                                   expected_db_image=expected_db_image, current_source_sha=current_source_sha)
        if (next(item["id"] for item in current["containers"] if item["service"] == "db") != db_id
                or current["volumes"] != original["volumes"]
                or next(item for item in current["containers"] if item["service"] == "db")
                   != next(item for item in original["containers"] if item["service"] == "db") | {"state": "running"}
                or next(item for item in current["networks"] if item["name"] == f"{COMPOSE_PROJECT}_isolated")
                   != original["networks"][0]):
            raise LaunchBlocked("Resume changed the retained database, volumes, or isolated network")
        if _docker_read([DOCKER_CLI, "--context", CONTEXT_NAME, "inspect", "--format",
                         "{{.State.Health.Status}}", db_id], environment, runner=runner).strip() != "healthy":
            raise LaunchBlocked("Retained database health was lost during ingress resume")
        nginx_id = next(item["id"] for item in current["containers"] if item["service"] == "nginx")
        published = _docker_read([DOCKER_CLI, "--context", CONTEXT_NAME, "port", nginx_id, "8080/tcp"],
                                 environment, runner=runner).splitlines()
        if published != [f"127.0.0.1:{port}"]:
            raise LaunchBlocked("Resumed nginx does not have the exact loopback port binding")
        health_url = f"http://127.0.0.1:{port}/health"
        deadline = monotonic() + 120
        while monotonic() < deadline:
            response = runner(["/usr/bin/curl", "-q", "--noproxy", "*", "--fail", "--silent",
                               "--show-error", "--max-time", "5", "--output", "/dev/null",
                               "--write-out", "%{http_code}", health_url],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              text=True, check=False, timeout=7,
                              env={"PATH": SAFE_PATH, "HOME": str(EXPECTED_HOME), "NO_PROXY": "*", "no_proxy": "*"})
            if response.returncode == 0 and response.stdout == "200":
                receipt("STAGING_RESUME_INGRESS_PASS")
                return 0
            sleep(2)
        raise LaunchBlocked("Resumed staging HTTP health timed out")
    except (Exception, KeyboardInterrupt) as error:
        receipt("STAGING_RESUME_INGRESS_FAILED")
        if attempted:
            try:
                stopped = runner([*compose, "stop", "nginx", "frontend", "backend", "db"],
                                 cwd=build_root, env=dict(environment), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=120,
                                 pass_fds=() if env_fd is None else (env_fd,))
                after = manifest_provider(prior_source_sha, validate=False)
                running = _docker_read([DOCKER_CLI, "--context", CONTEXT_NAME, "ps", "--filter",
                                        f"label=com.docker.compose.project={COMPOSE_PROJECT}", "--format", "{{.ID}}"],
                                       environment, runner=runner)
                if stopped.returncode == 0 and not running.strip() and after.get("volumes") == original["volumes"]:
                    receipt("STAGING_ROLLBACK_PASS stopped-retained-services-volumes-preserved")
                else:
                    receipt("STAGING_ROLLBACK_FAILED manual-review-required")
            except Exception:
                receipt("STAGING_ROLLBACK_FAILED manual-review-required")
        if isinstance(error, LaunchBlocked):
            raise
        if isinstance(error, KeyboardInterrupt):
            raise LaunchBlocked("Staging ingress resume was interrupted") from error
        raise LaunchBlocked("Staging ingress resume failed") from error


def verify_resume_snapshot_compatibility(source_root: Path, new_source_sha: str, prior_source_sha: str) -> str:
    guard = load_source_tree_guard(source_root)
    try:
        object_format = guard.git(source_root, "rev-parse", "--show-object-format").decode("ascii").strip()
        manifests = {}
        for revision in (prior_source_sha, new_source_sha):
            tree = guard.parse_tree(source_root, revision)
            entries = guard._snapshot_entries(source_root, tree)
            snapshot = SNAPSHOT_ROOT / revision
            guard._verify_persistent_snapshot(snapshot, revision, entries, object_format)
            manifest = json.loads((snapshot / ".belib-staging-manifest.json").read_text(encoding="utf-8"))
            manifests[revision] = {item["path"]: item["blob"] for item in manifest["files"]}
        old, new = manifests[prior_source_sha], manifests[new_source_sha]
        hook = "scripts/staging/10-belib-staging-roles.sh"
        old_prisma = {path: oid for path, oid in old.items() if path.startswith("backend/prisma/")}
        new_prisma = {path: oid for path, oid in new.items() if path.startswith("backend/prisma/")}
        if hook not in old or hook not in new or old[hook] != new[hook] or not old_prisma or old_prisma != new_prisma:
            raise LaunchBlocked("Retained bootstrap hook or Prisma migration tree changed")
        def db_image(revision: str) -> str:
            raw = guard.git(source_root, "show", f"{revision}:compose.staging.yaml").decode("utf-8")
            match = re.search(r"(?ms)^  db:\n(?:(?!^  \w).)*?^    image: ([^\n]+)$", raw)
            if not match:
                raise LaunchBlocked("Database image identity could not be read from the verified source tree")
            return match.group(1)
        old_db_image, new_db_image = db_image(prior_source_sha), db_image(new_source_sha)
        if old_db_image != new_db_image:
            raise LaunchBlocked("The retained database image differs from the reviewed current source")
        return new_db_image
    except (OSError, ValueError, UnicodeError, guard.SourceTreeBlocked) as error:
        if isinstance(error, LaunchBlocked):
            raise
        raise LaunchBlocked("Retained and current source snapshots could not be verified") from error


def verify_python_runtime() -> None:
    if (
        sys.executable != PYTHON_CLI
        or not sys.flags.isolated
        or not sys.flags.dont_write_bytecode
    ):
        raise LaunchBlocked("Use the trusted isolated /usr/bin/python3 -I -B launcher")


def reject_caller_environment(environment: Mapping[str, str]) -> None:
    if environment.get("PATH") != SAFE_PATH:
        raise LaunchBlocked("PATH must be the fixed trusted /usr/bin:/bin value")
    if environment.get("HOME") != str(EXPECTED_HOME):
        raise LaunchBlocked("HOME must be /home/hiroyuki")
    python_overrides = {
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONUSERBASE",
        "PYTHONSTARTUP",
        "PYTHONINSPECT",
        "PYTHONWARNINGS",
    }
    shell_and_git_overrides = {
        "BASH_ENV",
        "ENV",
        "CDPATH",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG_COUNT",
    }
    if any(name in environment for name in python_overrides | shell_and_git_overrides):
        raise LaunchBlocked("Caller Python, shell, or Git startup overrides are forbidden")
    forbidden_prefixes = (
        "DOCKER_", "COMPOSE_", "BUILDX_", "BUILDKIT_", "BELIB_STAGE_", "POSTGRES_",
        "R2_", "AWS_", "S3_", "PROD_", "PRODUCTION_", "VITE_PUBLIC_DEMO_",
    )
    forbidden_names = {"DATABASE_URL", "DIRECT_URL", "BETTER_AUTH_SECRET", "SSH_AUTH_SOCK", "SSH_AGENT_PID", "GIT_SSH_COMMAND"}
    if any(name.upper().startswith(forbidden_prefixes) or name.upper() in forbidden_names for name in environment):
        raise LaunchBlocked("Remove inherited staging, database, Docker, production, and secret overrides")


def validate_handoff_receipt(receipt: object, source_sha: str) -> str:
    """Legacy handoff JSON remains non-authoritative; bootstrap uses signed challenge response."""
    if not SOURCE_SHA_PATTERN.fullmatch(source_sha):
        raise LaunchBlocked("Source revision must be a full lowercase Git SHA")
    _ = receipt
    raise LaunchBlocked(
        "No trusted independent staging supervisor verifier is configured; local receipt claims cannot authorize staging"
    )


def run_deploy_transaction(
    *,
    compose,
    env_file: Path,
    build_root: Path,
    environment: Mapping[str, str],
    env_fd: int,
    seed,
    source_sha: str,
    port: str,
    stage_values: Mapping[str, str],
    docker_endpoint: Path,
    evidence_sha256: str = "0" * 64,
    runner=subprocess.run,
    sleep=time.sleep,
    monotonic=time.monotonic,
    report=print,
) -> int:
    """Run ordered phases; on failure stop only this project's services, preserving volumes."""
    if len(compose) < 6 or compose[0:5] != [DOCKER_CLI, "--context", CONTEXT_NAME, "compose", "-p"] or compose[5] != COMPOSE_PROJECT:
        raise LaunchBlocked("Deployment transaction Compose target is not the fixed i5 staging project")
    if not re.fullmatch(r"(?:[1-9][0-9]{3,4})", port) or not 1024 <= int(port) <= 65535:
        raise LaunchBlocked("Deployment transaction loopback port is invalid")
    completed: list[str] = []
    db_start_attempted = False

    def receipt(message: str) -> None:
        report(f"{message} evidence_sha256={evidence_sha256}", file=sys.stderr)

    def phase(name: str, command: list[str], timeout: int = 900) -> None:
        receipt(f"STAGING_PHASE_START {name}")
        result = runner(command, cwd=build_root, env=dict(environment), stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                        pass_fds=(env_fd,), timeout=timeout)
        if result.returncode != 0:
            receipt(f"STAGING_PHASE_FAIL {name}")
            raise LaunchBlocked(f"Staging phase failed: {name}")
        completed.append(name)
        receipt(f"STAGING_PHASE_PASS {name}")

    def wait_db() -> None:
        deadline = monotonic() + 180
        while monotonic() < deadline:
            result = runner([*compose, "ps", "db", "--format", "json"], cwd=build_root,
                            env=dict(environment), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, check=False, pass_fds=(env_fd,), timeout=15)
            try:
                rows = []
                for line in result.stdout.splitlines():
                    if line.strip():
                        decoded = json.loads(line)
                        rows.extend(decoded if isinstance(decoded, list) else [decoded])
                if result.returncode == 0 and any(
                    isinstance(row, dict) and row.get("Service", "db") == "db" and row.get("Health") == "healthy"
                    for row in rows
                ):
                    completed.append("db-healthy")
                    receipt("STAGING_PHASE_PASS db-healthy")
                    return
            except (json.JSONDecodeError, AttributeError):
                pass
            sleep(2)
        receipt("STAGING_PHASE_FAIL db-health")
        raise LaunchBlocked("Staging phase timed out: db-health")

    try:
        inventory = runner([*compose, "ps", "--all", "--format", "json"], cwd=build_root,
                           env=dict(environment), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, text=True, check=False, pass_fds=(env_fd,), timeout=20)
        volumes = runner([DOCKER_CLI, "--context", CONTEXT_NAME, "volume", "ls", "--filter",
                          f"label=com.docker.compose.project={COMPOSE_PROJECT}", "--format", "{{.Name}}"],
                         cwd=build_root, env=dict(environment), stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False, timeout=20)
        if inventory.returncode != 0 or volumes.returncode != 0:
            raise LaunchBlocked("Could not safely inventory the staging project before first Docker mutation")
        reject_existing_fresh_project_resources(build_root, environment, runner=runner)
        if (inventory.stdout or "").strip() or (volumes.stdout or "").strip():
            raise LaunchBlocked("Staging project containers or volumes already exist; separate resume review is required")
        receipt("STAGING_RESOURCE_INVENTORY_EMPTY")
        db_start_attempted = True
        phase("db-start", [*compose, "up", "-d", "db"], 300)
        wait_db()
        phase("migration", [*compose, *OPERATION_ARGS["migrate"]])
        receipt("STAGING_PHASE_START synthetic-seed")
        if seed.main(
            invoked_by_launcher=True, build_root=build_root, source_sha=source_sha,
            admitted_env_fd=env_fd, stage_values=stage_values, docker_cli=DOCKER_CLI,
            docker_endpoint=docker_endpoint, docker_environment=environment,
        ) != 0:
            receipt("STAGING_PHASE_FAIL synthetic-seed")
            raise LaunchBlocked("Staging phase failed: synthetic-seed")
        completed.append("synthetic-seed")
        receipt("STAGING_PHASE_PASS synthetic-seed")
        phase("app-start", [*compose, "up", "-d", "--build", "backend", "frontend", "nginx"], 1800)
        deadline = monotonic() + 120
        health_url = f"http://127.0.0.1:{port}/health"
        while monotonic() < deadline:
            result = runner(["/usr/bin/curl", "-q", "--noproxy", "*", "--fail", "--silent", "--show-error",
                             "--max-time", "5", "--output", "/dev/null", "--write-out", "%{http_code}", health_url],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                            check=False, timeout=7, env={"PATH": SAFE_PATH, "HOME": str(EXPECTED_HOME), "NO_PROXY": "*", "no_proxy": "*"})
            if result.returncode == 0 and getattr(result, "stdout", "") == "200":
                completed.append("http-health")
                receipt("STAGING_PHASE_PASS http-health")
                receipt("STAGING_TRANSACTION_PASS")
                return 0
            sleep(2)
        receipt("STAGING_PHASE_FAIL http-health")
        raise LaunchBlocked("Staging phase timed out: http-health")
    except (Exception, KeyboardInterrupt) as error:
        receipt("STAGING_TRANSACTION_FAILED phases=" + ",".join(completed))
        if db_start_attempted:
            try:
                rollback = runner([*compose, "stop", "nginx", "frontend", "backend", "db"], cwd=build_root,
                                  env=dict(environment), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, check=False, pass_fds=(env_fd,), timeout=120)
                remaining = runner(
                    [DOCKER_CLI, "--context", CONTEXT_NAME, "ps", "--filter",
                     f"label=com.docker.compose.project={COMPOSE_PROJECT}", "--format", "{{.ID}}"],
                    cwd=build_root, env=dict(environment), stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                    check=False, timeout=20,
                )
                if rollback.returncode == 0 and remaining.returncode == 0 and not remaining.stdout.strip():
                    receipt("STAGING_ROLLBACK_PASS stopped-project-services-volumes-preserved")
                else:
                    receipt("STAGING_ROLLBACK_FAILED manual-review-required")
            except (OSError, subprocess.SubprocessError):
                receipt("STAGING_ROLLBACK_FAILED manual-review-required")
        if isinstance(error, LaunchBlocked):
            raise
        if isinstance(error, KeyboardInterrupt):
            raise LaunchBlocked("Staging transaction was interrupted") from error
        raise LaunchBlocked("Staging transaction could not be completed") from error


def build_compose_command(
    operation: str,
    *,
    repo_root: Path,
    env_file: Path,
    docker_context: str = CONTEXT_NAME,
) -> list[str]:
    if operation not in OPERATION_ARGS:
        raise LaunchBlocked("Unsupported staging Compose operation")
    if docker_context != CONTEXT_NAME:
        raise LaunchBlocked("Only the private i5 rootless Docker context is allowed")
    return [
        DOCKER_CLI,
        "--context",
        CONTEXT_NAME,
        "compose",
        "-p",
        COMPOSE_PROJECT,
        "--project-directory",
        str(repo_root),
        "--env-file",
        str(env_file),
        "-f",
        str(repo_root / "compose.staging.yaml"),
        *OPERATION_ARGS[operation],
    ]


def build_compose_config_command(repo_root: Path, env_file: Path) -> list[str]:
    return [
        DOCKER_CLI,
        "--context",
        CONTEXT_NAME,
        "compose",
        "-p",
        COMPOSE_PROJECT,
        "--project-directory",
        str(repo_root),
        "--env-file",
        str(env_file),
        "-f",
        str(repo_root / "compose.staging.yaml"),
        "--profile",
        "migration",
        "config",
        "--format",
        "json",
    ]


def validate_context_endpoint(
    context_name: str,
    observed_endpoint: str,
    expected_endpoint: str,
) -> None:
    if context_name != CONTEXT_NAME or observed_endpoint != expected_endpoint:
        raise LaunchBlocked("Docker context must resolve only to this user's validated rootless socket")


def parse_buildx_inspect(output: str) -> str:
    driver_lines = [line.split() for line in output.splitlines() if line.startswith("Driver:")]
    endpoint_lines = [line.split() for line in output.splitlines() if line.startswith("Endpoint:")]
    if (
        len(driver_lines) != 1
        or len(driver_lines[0]) != 2
        or driver_lines[0][1] != "docker"
        or len(endpoint_lines) != 1
        or len(endpoint_lines[0]) != 2
    ):
        raise LaunchBlocked("Selected Buildx builder must use the local i5 Docker driver")
    return endpoint_lines[0][1]


@contextmanager
def project_lock(path: Path = LOCK_PATH, *, expected_uid: int = EXPECTED_UID) -> Iterator[None]:
    if not path.is_absolute():
        raise LaunchBlocked("The staging project lock path must be absolute")
    try:
        tmp_metadata = os.lstat("/tmp")
    except OSError as error:
        raise LaunchBlocked("The staging project lock directory is unavailable") from error
    if (
        not stat.S_ISDIR(tmp_metadata.st_mode)
        or tmp_metadata.st_uid != 0
        or stat.S_IMODE(tmp_metadata.st_mode) != 0o1777
    ):
        raise LaunchBlocked("The staging project lock directory must be root-owned mode 1777")
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError as error:
        raise LaunchBlocked("Could not acquire the existing staging project lock") from error
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise LaunchBlocked("The staging project lock must be a mode-0600 file owned by hiroyuki")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise LaunchBlocked("Another staging operation holds the project lock") from error
        yield
    finally:
        os.close(fd)


def _run(
    command: list[str],
    *,
    environment: Mapping[str, str],
    timeout: int = 20,
    input_text: str | None = None,
    pass_fds: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL if input_text is None else subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            input=input_text,
            timeout=timeout,
            env=dict(environment),
            pass_fds=pass_fds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise LaunchBlocked("A bounded staging guard command could not be completed") from error


def _docker_environment(home: Path, config: Path) -> dict[str, str]:
    return {
        "PATH": SAFE_PATH,
        "HOME": str(home),
        "USER": EXPECTED_USER,
        "LOGNAME": EXPECTED_USER,
        "DOCKER_CONFIG": str(config),
    }


def _trusted_rootless_context(seed, uid: int, home: Path, config: Path) -> tuple[dict[str, str], Path]:
    try:
        seed.verify_trusted_docker_plugins()
        endpoint = seed.expected_docker_socket(uid)
        seed.validate_rootless_socket(endpoint, uid)
        seed.verify_private_docker_config(config, uid)
    except seed.SeedWrapperError as error:
        raise LaunchBlocked(str(error)) from error
    environment = _docker_environment(home, config)
    socket_url = f"unix://{endpoint}"
    create = _run(
        [DOCKER_CLI, "context", "create", CONTEXT_NAME, "--docker", f"host={socket_url}"],
        environment=environment,
    )
    if create.returncode != 0:
        raise LaunchBlocked("Could not create the private rootless Docker context")
    inspected = _run(
        [DOCKER_CLI, "context", "inspect", CONTEXT_NAME, "--format", '{{ (index .Endpoints "docker").Host }}'],
        environment=environment,
    )
    observed_endpoint = (inspected.stdout or "").strip()
    if inspected.returncode != 0:
        raise LaunchBlocked("Could not inspect the private rootless Docker context")
    validate_context_endpoint(CONTEXT_NAME, observed_endpoint, socket_url)

    identity = _run(
        [DOCKER_CLI, "--context", CONTEXT_NAME, "info", "--format", "{{.Name}}|{{json .SecurityOptions}}"],
        environment=environment,
    )
    actual_identity = (identity.stdout or "").strip()
    if (
        identity.returncode != 0
        or not actual_identity.startswith(f"{EXPECTED_HOST}|")
        or '"name=rootless"' not in actual_identity
    ):
        raise LaunchBlocked("Docker did not report the local ubuntu-worker rootless daemon")

    version = _run([DOCKER_CLI, "--context", CONTEXT_NAME, "compose", "version"], environment=environment)
    if version.returncode != 0:
        raise LaunchBlocked("The trusted Docker Compose plugin is unavailable")

    builder = _run(
        [DOCKER_CLI, "--context", CONTEXT_NAME, "buildx", "inspect"],
        environment=environment,
    )
    if builder.returncode != 0:
        raise LaunchBlocked("Could not inspect the selected local Buildx builder")
    builder_endpoint = parse_buildx_inspect(builder.stdout or "")
    if builder_endpoint == socket_url:
        return environment, endpoint
    if "://" in builder_endpoint:
        raise LaunchBlocked("Selected Buildx builder is not bound to this user's rootless socket")
    resolved = _run(
        [DOCKER_CLI, "--context", CONTEXT_NAME, "context", "inspect", builder_endpoint,
         "--format", '{{ (index .Endpoints "docker").Host }}'],
        environment=environment,
    )
    if resolved.returncode != 0 or (resolved.stdout or "").strip() != socket_url:
        raise LaunchBlocked("Selected Buildx builder is not bound to this user's rootless socket")
    return environment, endpoint


def _validate_compose_model(
    repo_root: Path,
    env_snapshot_fd: int,
    values: Mapping[str, str],
    environment: Mapping[str, str],
    source_sha: str,
) -> None:
    env_path = Path(f"/proc/{os.getpid()}/fd/{env_snapshot_fd}")
    config = _run(
        build_compose_config_command(repo_root, env_path),
        environment=environment,
        pass_fds=(env_snapshot_fd,),
    )
    if config.returncode != 0:
        raise LaunchBlocked("Staging Compose configuration failed closed")
    try:
        model = json.loads(config.stdout or "")
        validate_compose_contract(
            repo_root,
            source_sha=source_sha,
            port=int(values["BELIB_STAGE_PORT"]),
            model=model,
        )
    except LaunchBlocked:
        raise
    except Exception as error:
        raise LaunchBlocked("Staging image digest, Compose project, or build context was rejected") from error


def validate_compose_contract(
    repo_root: Path,
    *,
    source_sha: str,
    port: int,
    model: object,
) -> None:
    guard_path = repo_root / "scripts/staging/ci-isolation-guard.py"
    spec = importlib.util.spec_from_file_location("belib_staging_compose_guard", guard_path)
    if spec is None or spec.loader is None:
        raise LaunchBlocked("The staging image and Compose guard is unavailable")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    try:
        guard.validate(
            source_sha=source_sha,
            checkout_sha=source_sha,
            compose_source=(repo_root / "compose.staging.yaml").read_text(encoding="utf-8"),
            compose_model=model,
            expected_port=port,
            compose_root=repo_root,
        )
    except Exception as error:
        raise LaunchBlocked("Staging image digest, Compose project, or build context was rejected") from error


def load_source_tree_guard(repo_root: Path):
    guard_path = repo_root / "scripts/staging/source-tree-guard.py"
    spec = importlib.util.spec_from_file_location("belib_staging_source_tree_guard", guard_path)
    if spec is None or spec.loader is None:
        raise LaunchBlocked("The SHA-derived staging build snapshot helper is unavailable")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    return guard


def _trusted_git(repo_root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = {
        "HOME": str(EXPECTED_HOME), "PATH": SAFE_PATH, "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }
    return _run(["/usr/bin/git", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "-c", f"safe.directory={repo_root}",
                 "-C", str(repo_root), *arguments], environment=environment, timeout=10)


def _admission_source_root(checkout: Path, source_sha: str) -> Path:
    """Admit the fixed detached tree and its bytes before importing any privileged helper."""
    if not SOURCE_SHA_PATTERN.fullmatch(source_sha):
        raise LaunchBlocked("Source revision must be a full lowercase Git SHA")
    if ROOT_ADMISSION is not None:
        candidate = Path(ROOT_ADMISSION["source_root"])
        if (ROOT_ADMISSION["grant"]["source_sha"] != source_sha
                or Path(__file__).resolve(strict=True) != candidate / "scripts/staging/launch-guard.py"):
            raise LaunchBlocked("Installed source does not match the admitted launcher")
        for path in [*reversed(candidate.parents), candidate]:
            metadata = path.lstat()
            if metadata.st_uid != 0 or stat.S_ISLNK(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o022:
                raise LaunchBlocked("Installed source ancestry is not root-owned")
        head = _trusted_git(candidate, "rev-parse", "--verify", "HEAD")
        if head.returncode != 0 or head.stdout.strip() != source_sha:
            raise LaunchBlocked("Installed Git revision differs from the admitted source")
        return candidate
    candidate = ADMISSION_WORKTREE_PREFIX.parent / f"{ADMISSION_WORKTREE_PREFIX.name}{source_sha[:7]}"
    expected_launcher = candidate / "scripts/staging/launch-guard.py"
    if Path(__file__).resolve(strict=True) != expected_launcher:
        raise LaunchBlocked("Invoke launch-guard.py from the exact-SHA detached admission worktree")
    try:
        metadata = os.lstat(candidate)
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise LaunchBlocked("The exact-SHA detached staging admission worktree is unavailable") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != EXPECTED_UID
        or resolved != candidate
        or candidate.parent != ADMISSION_WORKTREE_PREFIX.parent
    ):
        raise LaunchBlocked("The staging admission worktree path or ownership is unsafe")
    branch = _trusted_git(checkout, "branch", "--show-current")
    head = _trusted_git(candidate, "rev-parse", "--verify", "HEAD")
    candidate_common = _trusted_git(candidate, "rev-parse", "--path-format=absolute", "--git-common-dir")
    checkout_common = _trusted_git(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir")
    detached = _trusted_git(candidate, "symbolic-ref", "-q", "HEAD")
    status = _trusted_git(candidate, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none")
    worktree_diff = _trusted_git(candidate, "diff", "--no-ext-diff", "--quiet", "HEAD", "--")
    index_diff = _trusted_git(candidate, "diff", "--cached", "--no-ext-diff", "--quiet", "HEAD", "--")
    if branch.returncode != 0 or branch.stdout.strip() != EXPECTED_BRANCH:
        raise LaunchBlocked("Original checkout branch differs from the existing PR #103 claim")
    if head.returncode != 0 or head.stdout.strip() != source_sha or candidate_common.returncode != 0 or checkout_common.returncode != 0:
        raise LaunchBlocked("The detached admission worktree does not match the selected SHA and Git store")
    if candidate_common.stdout.strip() != checkout_common.stdout.strip() or detached.returncode == 0:
        raise LaunchBlocked("The admission source must be a detached worktree of the claimed repository")
    if status.returncode != 0 or status.stdout or worktree_diff.returncode != 0 or index_diff.returncode != 0:
        raise LaunchBlocked("The exact-SHA admission worktree must be clean with verified source bytes")
    # Git status/diff may trust index flags or cached stat data. Hash every tracked
    # staging helper directly before importing any of them.
    tree = _trusted_git(candidate, "ls-tree", "-r", "-z", source_sha, "--", "scripts/staging")
    if tree.returncode != 0 or not tree.stdout:
        raise LaunchBlocked("Could not verify detached staging helper bytes")
    for entry in tree.stdout.rstrip("\0").split("\0"):
        metadata, relative = entry.split("\t", 1)
        mode, kind, oid = metadata.split()
        path = candidate / relative
        info = os.lstat(path)
        if kind != "blob" or mode not in {"100644", "100755"} or not stat.S_ISREG(info.st_mode):
            raise LaunchBlocked("Detached staging helper path or mode is unsafe")
        data = path.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        expected_mode = 0o755 if mode == "100755" else 0o644
        if actual != oid or stat.S_IMODE(info.st_mode) != expected_mode:
            raise LaunchBlocked("Detached staging helper bytes differ from selected SHA")
    return candidate


def _run_preflight(repo_root: Path, env_file: Path) -> None:
    result = _run(
        [str(repo_root / "scripts/staging/preflight.sh"), str(env_file)],
        environment={"HOME": str(EXPECTED_HOME), "PATH": SAFE_PATH},
        timeout=120,
    )
    if result.returncode != 0:
        raise LaunchBlocked("Read-only i5 preflight did not admit this exact staging source")


def main(argv: list[str] | None = None) -> int:
    try:
        # Keep this as the first action in main: candidate helpers, argument-driven
        # operations, shell files, and Docker must stay unreachable without an
        # independently installed operator bootstrap.
        _require_operator_bootstrap_admission()
        verify_python_runtime()
        reject_caller_environment(os.environ)
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--source-sha", required=True)
        parser.add_argument("--evidence-sha256")
        parser.add_argument("--prior-source-sha")
        parser.add_argument("--retained-state-sha256")
        parser.add_argument("--prior-application-sha")
        parser.add_argument("--backup-sha256")
        parser.add_argument("--restore-attestation-sha256")
        parser.add_argument("--env-file", type=Path, required=True)
        parser.add_argument("operation", choices=("deploy", "resume-ingress", "manifest-ingress", "upgrade-retained"))
        args = parser.parse_args(argv)

        account = pwd.getpwuid(os.getuid())
        if (
            account.pw_name != EXPECTED_USER
            or account.pw_uid != EXPECTED_UID
            or os.geteuid() != EXPECTED_UID
            or socket.gethostname() != EXPECTED_HOST
        ):
            raise LaunchBlocked("Run only as hiroyuki UID 1000 on ubuntu-worker")
        repo_root = EXPECTED_REPO_ROOT
        if args.env_file != EXPECTED_HOME / ".config/belib-staging/i5.env":
            raise LaunchBlocked("Use only the outside-repository i5 staging env file")
        if args.operation != "manifest-ingress" and not EVIDENCE_SHA_PATTERN.fullmatch(args.evidence_sha256 or ""):
            raise LaunchBlocked("External evidence digest must be 64 lowercase hexadecimal characters")
        if args.operation in ("resume-ingress", "manifest-ingress") and args.prior_source_sha != RETAINED_PRIOR_SHA:
            raise LaunchBlocked("Only the reviewed retained source revision can be resumed")
        if args.operation == "resume-ingress" and not re.fullmatch(r"[0-9a-f]{64}", args.retained_state_sha256 or ""):
            raise LaunchBlocked("Resume requires the signed retained-state manifest digest")
        source_root = _admission_source_root(repo_root, args.source_sha)
        global ADMITTED_SOURCE_ROOT
        ADMITTED_SOURCE_ROOT = source_root
        seed = load_seed_module()

        with project_lock():
            if args.operation == "upgrade-retained":
                _require_operation_approval(args)
                if ROOT_ADMISSION.get("retained_config") is None:
                    raise LaunchBlocked("Retained topology and independent evidence authorities are not adopted")
                executor = load_retained_executor(source_root)
                try:
                    result = executor.execute_admitted(ROOT_ADMISSION, source_root, args.env_file)
                except Exception as error:
                    raise LaunchBlocked("Retained upgrade admission, execution or recovery failed; preserve its operation journal") from error
                print(json.dumps(result, sort_keys=True, separators=(",", ":")))
                return 0
            if args.operation == "manifest-ingress":
                try:
                    source_guard = load_source_tree_guard(source_root)
                    with source_guard.sha_derived_build_snapshot(source_root, args.source_sha):
                        expected_db_image = verify_resume_snapshot_compatibility(
                            source_root, args.source_sha, args.prior_source_sha
                        )
                    seed.verify_trusted_docker_plugins()
                    endpoint = seed.expected_docker_socket(EXPECTED_UID)
                    seed.validate_rootless_socket(endpoint, EXPECTED_UID)
                    inspect_env = {"HOME": "/nonexistent", "PATH": SAFE_PATH, "DOCKER_CONFIG": "/nonexistent"}
                    prefix = [DOCKER_CLI, "--host", f"unix://{endpoint}"]
                    manifest = build_retained_manifest(args.prior_source_sha, inspect_env, docker_prefix=prefix,
                                                       expected_db_image=expected_db_image)
                    response = {"manifest": manifest, "retained_state_sha256": retained_manifest_sha256(manifest)}
                    print(json.dumps(response, sort_keys=True, separators=(",", ":")))
                    return 0
                except (LaunchBlocked, seed.SeedWrapperError) as error:
                    raise LaunchBlocked(str(error)) from error
            if args.operation == "resume-ingress":
                try:
                    source_guard = load_source_tree_guard(source_root)
                    with source_guard.sha_derived_build_snapshot(source_root, args.source_sha) as build_root:
                        expected_db_image = verify_resume_snapshot_compatibility(
                            source_root, args.source_sha, args.prior_source_sha
                        )
                        seed.verify_trusted_docker_plugins()
                        endpoint = seed.expected_docker_socket(EXPECTED_UID)
                        seed.validate_rootless_socket(endpoint, EXPECTED_UID)
                        inspect_env = {"HOME": "/nonexistent", "PATH": SAFE_PATH, "DOCKER_CONFIG": "/nonexistent"}
                        inspect_prefix = [DOCKER_CLI, "--host", f"unix://{endpoint}"]
                        before = build_retained_manifest(args.prior_source_sha, inspect_env, docker_prefix=inspect_prefix,
                                                         expected_db_image=expected_db_image)
                        if retained_manifest_sha256(before) != args.retained_state_sha256:
                            raise LaunchBlocked("Signed retained-state digest does not match the stopped project")
                        _require_operation_approval(args)
                        after_approval = build_retained_manifest(args.prior_source_sha, inspect_env,
                                                                 docker_prefix=inspect_prefix,
                                                                 expected_db_image=expected_db_image)
                        if retained_manifest_sha256(after_approval) != args.retained_state_sha256:
                            raise LaunchBlocked("Retained staging resources changed while awaiting resume approval")
                        _run_preflight(source_root, args.env_file)
                        env_fd, values = seed.validate_staging_admission(source_root, args.env_file, EXPECTED_UID)
                        try:
                            if values["BELIB_STAGE_VCS_REF"] != args.source_sha:
                                raise LaunchBlocked("Staging env revision differs from the selected source SHA")
                            docker_config_temp = seed.create_private_docker_config(EXPECTED_UID)
                            try:
                                docker_environment, actual_endpoint = _trusted_rootless_context(
                                    seed, EXPECTED_UID, EXPECTED_HOME, Path(docker_config_temp.name)
                                )
                                if actual_endpoint != endpoint:
                                    raise LaunchBlocked("Rootless Docker endpoint changed during resume admission")
                                os.set_inheritable(env_fd, True)
                                _validate_compose_model(build_root, env_fd, values, docker_environment, args.source_sha)
                                env_path = Path(f"/proc/{os.getpid()}/fd/{env_fd}")
                                compose = [
                                    DOCKER_CLI, "--context", CONTEXT_NAME, "compose", "-p", COMPOSE_PROJECT,
                                    "--project-directory", str(build_root), "--env-file", str(env_path),
                                    "-f", str(build_root / "compose.staging.yaml"),
                                ]
                                provider = lambda revision, validate=True: build_retained_manifest(
                                    revision, docker_environment, validate=validate,
                                    expected_db_image=expected_db_image,
                                )
                                current = provider(args.prior_source_sha)
                                if retained_manifest_sha256(current) != args.retained_state_sha256:
                                    raise LaunchBlocked("Retained resources changed before resume mutation")
                                return run_resume_ingress_transaction(
                                    compose=compose, build_root=build_root, environment=docker_environment,
                                    port=values["BELIB_STAGE_PORT"], prior_source_sha=args.prior_source_sha,
                                    signed_digest=args.retained_state_sha256, manifest_provider=provider,
                                    current_source_sha=args.source_sha, expected_db_image=expected_db_image,
                                    env_fd=env_fd,
                                )
                            finally:
                                docker_config_temp.cleanup()
                        finally:
                            os.close(env_fd)
                except (LaunchBlocked, seed.SeedWrapperError) as error:
                    raise LaunchBlocked(str(error)) from error
                except Exception as error:
                    raise LaunchBlocked("Retained source or resume admission failed") from error
            _require_operation_approval(args)
            env_snapshot_fd = -1
            docker_config_temporary = None
            try:
                source_guard = load_source_tree_guard(source_root)
                try:
                    with source_guard.sha_derived_build_snapshot(source_root, args.source_sha) as build_root:
                        try:
                            seed.validate_build_snapshot(build_root, source_root, args.source_sha)
                        except seed.SeedWrapperError as error:
                            raise LaunchBlocked(str(error)) from error

                        _run_preflight(source_root, args.env_file)
                        try:
                            env_snapshot_fd, values = seed.validate_staging_admission(
                                source_root, args.env_file, EXPECTED_UID
                            )
                        except seed.SeedWrapperError as error:
                            raise LaunchBlocked(str(error)) from error
                        if values["BELIB_STAGE_VCS_REF"] != args.source_sha:
                            raise LaunchBlocked("Staging env revision differs from the selected source SHA")

                        try:
                            docker_config_temporary = seed.create_private_docker_config(EXPECTED_UID)
                        except seed.SeedWrapperError as error:
                            raise LaunchBlocked(str(error)) from error
                        docker_config = Path(docker_config_temporary.name)
                        docker_environment, endpoint = _trusted_rootless_context(
                            seed, EXPECTED_UID, EXPECTED_HOME, docker_config
                        )
                        os.set_inheritable(env_snapshot_fd, True)
                        _validate_compose_model(
                            build_root,
                            env_snapshot_fd,
                            values,
                            docker_environment,
                            args.source_sha,
                        )
                        env_path = Path(f"/proc/{os.getpid()}/fd/{env_snapshot_fd}")
                        compose = [
                            DOCKER_CLI, "--context", CONTEXT_NAME, "compose", "-p", COMPOSE_PROJECT,
                            "--project-directory", str(build_root), "--env-file", str(env_path),
                            "-f", str(build_root / "compose.staging.yaml"),
                        ]
                        return run_deploy_transaction(
                            compose=compose, env_file=args.env_file, build_root=build_root,
                            environment=docker_environment, env_fd=env_snapshot_fd, seed=seed,
                            source_sha=args.source_sha, port=values["BELIB_STAGE_PORT"],
                            stage_values=values, docker_endpoint=endpoint,
                            evidence_sha256=args.evidence_sha256,
                        )
                except source_guard.SourceTreeBlocked as error:
                    raise LaunchBlocked("The fixed-SHA staging build snapshot could not be verified") from error
            finally:
                if env_snapshot_fd >= 0:
                    os.close(env_snapshot_fd)
                if docker_config_temporary is not None:
                    docker_config_temporary.cleanup()
    except LaunchBlocked as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError) as error:
        print("BLOCKED: Guarded staging operation could not be completed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
