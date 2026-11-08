#!/usr/bin/env python3
"""Fail-closed static checks for the standalone i5 staging Compose model."""

from __future__ import annotations

import argparse
import json
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any


COMPOSE_PROJECT = "belib-isolated-i5"
EXPECTED_CI_PORT = 49199
EXPECTED_SERVICES = {"db", "migrate", "backend", "frontend", "nginx"}
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
HEX_SECRET_PATTERN = re.compile(r"[a-f0-9]{64}\Z")
MIGRATE_DATABASE_URL_SOURCE = (
    "postgresql://belib_staging_migrate:"
    "${BELIB_STAGE_DB_MIGRATE_PASSWORD:?Set staging-only migration password}"
    "@db:5432/belib_staging"
)
APP_DATABASE_URL_SOURCE = (
    "postgresql://belib_staging_app:"
    "${BELIB_STAGE_DB_APP_PASSWORD:?Set staging-only app password}"
    "@db:5432/belib_staging"
)
ROLE_INIT_SOURCE = "scripts/staging/10-belib-staging-roles.sh"
ROLE_INIT_TARGET = "/docker-entrypoint-initdb.d/10-belib-staging-roles.sh"


class GuardError(RuntimeError):
    """Raised when the staging Compose contract is not satisfied."""


def _strip_yaml_comments(source: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in source.splitlines())


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        result = value
    elif isinstance(value, list):
        result = {}
        for item in value:
            if not isinstance(item, str) or "=" not in item:
                raise GuardError(f"{label} contains an invalid entry")
            key, item_value = item.split("=", 1)
            result[key] = item_value
    else:
        raise GuardError(f"{label} must be a mapping")
    if any(not isinstance(key, str) for key in result):
        raise GuardError(f"{label} contains an invalid key")
    return result


def _walk(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _source_contract(source: str, source_sha: str) -> None:
    code = _strip_yaml_comments(source)
    for pattern, description in (
        (r"(?im)^\s*(?:env_file|extends|include)\s*:", "external Compose input"),
        (r"compose\.ya?ml", "production Compose reference"),
        (r"(?<![\w])\.env(?:\b|$)", "environment file reference"),
        (r"\bR2_[A-Z0-9_]*\b", "R2 setting"),
        (r"\bBELIB_FRONTEND_IMAGE\b", "production image override"),
    ):
        if re.search(pattern, code):
            raise GuardError(f"Staging Compose contains a forbidden {description}")

    if re.search(r"(?m)^name:\s*belib-isolated-i5\s*$", code) is None:
        raise GuardError("Staging Compose project name must be belib-isolated-i5")
    if re.search(r"(?m)^name:\s*(?!belib-isolated-i5\s*$)\S+", code):
        raise GuardError("Staging Compose project name must be belib-isolated-i5")

    if "BELIB_STAGE_DB_PASSWORD" in code:
        raise GuardError("The legacy single-password database identity is forbidden")
    required_role_lines = (
        "POSTGRES_USER: belib_staging_bootstrap",
        "POSTGRES_DB: belib_staging",
        "POSTGRES_PASSWORD: ${BELIB_STAGE_DB_BOOTSTRAP_PASSWORD:?Set staging-only bootstrap password}",
        "BELIB_STAGE_DB_APP_PASSWORD: ${BELIB_STAGE_DB_APP_PASSWORD:?Set staging-only app password}",
        "BELIB_STAGE_DB_MIGRATE_PASSWORD: ${BELIB_STAGE_DB_MIGRATE_PASSWORD:?Set staging-only migration password}",
        f"./{ROLE_INIT_SOURCE}:{ROLE_INIT_TARGET}:ro",
    )
    if any(code.count(line) != 1 for line in required_role_lines):
        raise GuardError("PostgreSQL must use the distinct bootstrap identity and read-only role initializer")

    expected_vcs_line = "VCS_REF: ${BELIB_STAGE_VCS_REF:?Set full reviewed staging SHA}"
    vcs_lines = [
        line.strip()
        for line in code.splitlines()
        if re.search(r"(?<![A-Za-z0-9_])VCS_REF\s*:", line)
    ]
    if vcs_lines != [expected_vcs_line, expected_vcs_line]:
        raise GuardError("Both built services must use the required staging SHA")
    if not SHA_PATTERN.fullmatch(source_sha):
        raise GuardError("Source revision must be a full lowercase Git SHA")

    db_lines = [
        line.strip()
        for line in code.splitlines()
        if re.search(r"(?<![A-Za-z0-9_])DATABASE_URL\s*:", line)
    ]
    expected_db_lines = [
        f"DATABASE_URL: {MIGRATE_DATABASE_URL_SOURCE}",
        f"DATABASE_URL: {APP_DATABASE_URL_SOURCE}",
    ]
    if db_lines != expected_db_lines:
        raise GuardError("Database URLs must use the isolated migration and app roles")

    port_lines = [
        line.strip()
        for line in code.splitlines()
        if "BELIB_STAGE_PORT" in line and "8080" in line
    ]
    if port_lines != [
        '- "127.0.0.1:${BELIB_STAGE_PORT:?Set free i5 loopback port}:8080"'
    ]:
        raise GuardError("Staging nginx must publish only the required loopback port")


def _env(service: dict[str, Any], name: str) -> dict[str, Any]:
    return _mapping(service.get("environment", {}), f"{name} environment")


def _mounts(service: dict[str, Any], name: str) -> list[dict[str, Any]]:
    value = service.get("volumes", [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise GuardError(f"{name} volume mounts are invalid")
    return value


def _check_mount(
    mounts: list[dict[str, Any]],
    *,
    target: str,
    mount_type: str,
    source: str,
    compose_root: Path,
    read_only: bool | None = None,
) -> None:
    matches = [mount for mount in mounts if mount.get("target") == target]
    if len(matches) != 1:
        raise GuardError("Staging volume mapping is missing or duplicated")
    mount = matches[0]
    actual_source = mount.get("source")
    source_matches = False
    if isinstance(actual_source, str):
        if mount_type == "bind":
            actual_path = Path(actual_source)
            if not actual_path.is_absolute():
                actual_path = compose_root / actual_path
            source_matches = actual_path.resolve() == (compose_root / source).resolve()
        else:
            source_matches = actual_source == source
    if mount.get("type") != mount_type or not source_matches:
        raise GuardError("Staging volume mapping is not isolated")
    if read_only is not None and mount.get("read_only") is not read_only:
        raise GuardError("Staging bind mount must be read-only")


def _context_matches(value: Any, expected: str, compose_root: Path) -> bool:
    if not isinstance(value, str):
        return False
    context = Path(value)
    if not context.is_absolute():
        context = compose_root / context
    return context.resolve() == (compose_root / expected).resolve()


def _validate_model(
    model: Any, source_sha: str, expected_port: int, compose_root: Path
) -> None:
    if not isinstance(model, dict):
        raise GuardError("Compose output must be a JSON object")
    if set(model) != {"name", "services", "networks", "volumes"}:
        raise GuardError("Compose contains unsupported top-level configuration")
    if model.get("name") != COMPOSE_PROJECT:
        raise GuardError("Compose project name must be belib-isolated-i5")

    services = model.get("services")
    if not isinstance(services, dict) or set(services) != EXPECTED_SERVICES:
        raise GuardError("Compose must contain only the five isolated staging services")
    networks = model.get("networks")
    if (
        not isinstance(networks, dict)
        or set(networks) != {"isolated"}
        or not isinstance(networks["isolated"], dict)
        or networks["isolated"].get("internal") is not True
        or networks["isolated"].get("name") != f"{COMPOSE_PROJECT}_isolated"
        or networks["isolated"].get("external") is True
    ):
        raise GuardError("Staging services must use the isolated internal network")

    volumes = model.get("volumes")
    if not isinstance(volumes, dict) or set(volumes) != {
        "staging-postgres-data",
        "staging-book-files",
    }:
        raise GuardError("Compose must use only the two staging-owned named volumes")
    if any(not isinstance(value, dict) or value.get("external") is True for value in volumes.values()):
        raise GuardError("External volumes are forbidden")
    expected_volume_names = {
        "staging-postgres-data": f"{COMPOSE_PROJECT}_staging-postgres-data",
        "staging-book-files": f"{COMPOSE_PROJECT}_staging-book-files",
    }
    if any(
        volumes[key].get("name") != expected_name
        for key, expected_name in expected_volume_names.items()
    ):
        raise GuardError("Named volumes must remain inside the staging Compose project")

    for key, value in _walk(model):
        if key.upper().startswith("R2_"):
            raise GuardError("R2 settings are forbidden in staging Compose")
        if isinstance(value, str):
            lowered = value.lower()
            if (
                "compose.yaml" in lowered
                or ".env" in lowered
                or "production" in lowered
                or "r2_" in lowered
            ):
                raise GuardError("Production configuration is forbidden in staging Compose")
        if key in {
            "env_file", "extends", "include", "network_mode", "privileged", "devices",
            "cap_add", "container_name", "volumes_from", "links", "external_links",
            "extra_hosts", "dns", "dns_search", "pid", "ipc", "userns_mode", "runtime",
            "secrets", "configs",
        }:
            raise GuardError("Unsupported or unsafe Compose setting")

    for name, service in services.items():
        if not isinstance(service, dict):
            raise GuardError(f"{name} service configuration is invalid")
        if set(service.get("networks", {})) != {"isolated"}:
            raise GuardError("Every service must use only the isolated network")
        if service.get("network_mode") is not None:
            raise GuardError("Host or external network modes are forbidden")
        if service.get("privileged") is True or service.get("devices") or service.get("cap_add"):
            raise GuardError("Privileged container settings are forbidden")
        if name != "nginx" and service.get("ports"):
            raise GuardError("Only nginx may publish a staging port")

    db = services["db"]
    db_env = _env(db, "db")
    if (
        db.get("image") != "postgres:16.14-bookworm"
        or "build" in db
        or db.get("command") is not None
        or db.get("entrypoint") is not None
        or set(db_env) != {
            "POSTGRES_USER",
            "POSTGRES_DB",
            "POSTGRES_PASSWORD",
            "BELIB_STAGE_DB_APP_PASSWORD",
            "BELIB_STAGE_DB_MIGRATE_PASSWORD",
        }
        or db_env.get("POSTGRES_USER") != "belib_staging_bootstrap"
        or db_env.get("POSTGRES_DB") != "belib_staging"
    ):
        raise GuardError("PostgreSQL must use the isolated bootstrap identity and default init entrypoint")
    bootstrap_password = db_env.get("POSTGRES_PASSWORD")
    app_password = db_env.get("BELIB_STAGE_DB_APP_PASSWORD")
    migrate_password = db_env.get("BELIB_STAGE_DB_MIGRATE_PASSWORD")
    database_passwords = (bootstrap_password, app_password, migrate_password)
    if any(
        not isinstance(password, str) or not HEX_SECRET_PATTERN.fullmatch(password)
        for password in database_passwords
    ):
        raise GuardError("Staging database password fixtures are invalid")
    if len(set(database_passwords)) != len(database_passwords):
        raise GuardError("Bootstrap, app, and migration database passwords must be distinct")
    db_mounts = _mounts(db, "db")
    if len(db_mounts) != 2:
        raise GuardError("PostgreSQL must use only its staging data volume and role initializer")
    _check_mount(
        db_mounts,
        target="/var/lib/postgresql/data",
        mount_type="volume",
        source="staging-postgres-data",
        compose_root=compose_root,
    )
    _check_mount(
        db_mounts,
        target=ROLE_INIT_TARGET,
        mount_type="bind",
        source=ROLE_INIT_SOURCE,
        compose_root=compose_root,
        read_only=True,
    )
    try:
        role_init_stat = (compose_root / ROLE_INIT_SOURCE).lstat()
    except OSError:
        raise GuardError("The PostgreSQL role initializer must be a checked-in regular file") from None
    if not stat.S_ISREG(role_init_stat.st_mode) or stat.S_IMODE(role_init_stat.st_mode) & 0o111:
        raise GuardError("The PostgreSQL role initializer must be a non-executable regular file")

    expected_migrate_database_url = (
        f"postgresql://belib_staging_migrate:{migrate_password}@db:5432/belib_staging"
    )
    expected_app_database_url = (
        f"postgresql://belib_staging_app:{app_password}@db:5432/belib_staging"
    )
    migrate = services["migrate"]
    migrate_env = _env(migrate, "migrate")
    if (
        migrate_env != {"DATABASE_URL": expected_migrate_database_url}
        or migrate.get("user") != "node"
        or "image" in migrate
        or not _context_matches(
            migrate.get("build", {}).get("context"), "backend", compose_root
        )
        or migrate.get("build", {}).get("target") != "migration"
        or _mounts(migrate, "migrate")
    ):
        raise GuardError("Migration service must use only the isolated staging database")

    backend = services["backend"]
    backend_env = _env(backend, "backend")
    expected_port_text = str(expected_port)
    expected_backend_env = {
        "DATABASE_URL": expected_app_database_url,
        "APP_PORT": "3000",
        "ENVIRONMENT": "test",
        "FRONTEND_URL": f"http://127.0.0.1:{expected_port_text}",
        "BETTER_AUTH_URL": f"http://127.0.0.1:{expected_port_text}",
        "BETTER_AUTH_SECRET": None,
        "BOOK_FILE_STORAGE_DRIVER": "local",
        "BOOK_FILE_STORAGE_ROOT": "/home/node/books",
        "NPM_CONFIG_CACHE": "/tmp/npm-cache",
    }
    if set(backend_env) != set(expected_backend_env):
        raise GuardError("Backend environment contains an unexpected setting")
    auth_secret = backend_env.get("BETTER_AUTH_SECRET")
    if not isinstance(auth_secret, str) or not HEX_SECRET_PATTERN.fullmatch(auth_secret):
        raise GuardError("Staging authentication secret fixture is invalid")
    if auth_secret in database_passwords:
        raise GuardError("Bootstrap, app, migration, and auth secrets must all be distinct")
    expected_backend_env["BETTER_AUTH_SECRET"] = auth_secret
    if backend_env != expected_backend_env:
        raise GuardError("Backend environment must remain staging-local")
    backend_build = backend.get("build", {})
    if (
        backend.get("user") != "node"
        or "image" in backend
        or not _context_matches(backend_build.get("context"), "backend", compose_root)
        or backend_build.get("target") != "runtime"
        or backend_build.get("args", {}).get("VCS_REF") != source_sha
    ):
        raise GuardError("Backend image must be built from the exact PR head SHA")
    backend_mounts = _mounts(backend, "backend")
    if len(backend_mounts) != 1 or backend.get("expose") != ["3000"]:
        raise GuardError("Backend must expose only its private API port and book volume")
    _check_mount(
        backend_mounts,
        target="/home/node",
        mount_type="volume",
        source="staging-book-files",
        compose_root=compose_root,
    )

    frontend = services["frontend"]
    frontend_build = frontend.get("build", {})
    expected_frontend_args = {
        "VCS_REF": source_sha,
        "VITE_API_BASE_URL": f"http://127.0.0.1:{expected_port_text}",
        "VITE_PUBLIC_DEMO_EMAIL": "",
        "VITE_PUBLIC_DEMO_PASSWORD": "",
        "VITE_PUBLIC_DEMO_ADMIN_EMAIL": "",
        "VITE_PUBLIC_DEMO_ADMIN_PASSWORD": "",
    }
    if (
        frontend.get("environment") not in (None, {})
        or "image" in frontend
        or not _context_matches(
            frontend_build.get("context"), "frontend", compose_root
        )
        or frontend_build.get("target") != "runtime"
        or frontend_build.get("args") != expected_frontend_args
        or frontend.get("expose") != ["8080"]
        or frontend.get("volumes")
    ):
        raise GuardError("Frontend image must be staging-only and built from the exact PR head")

    nginx = services["nginx"]
    if (
        nginx.get("image") != "nginx:1.27-alpine"
        or "build" in nginx
        or nginx.get("environment") not in (None, {})
    ):
        raise GuardError("Staging nginx image must remain pinned to its reviewed version")
    ports = nginx.get("ports")
    if not isinstance(ports, list) or len(ports) != 1:
        raise GuardError("Staging nginx must publish exactly one loopback port")
    binding = ports[0]
    if (
        binding.get("host_ip") != "127.0.0.1"
        or str(binding.get("published")) != str(expected_port)
        or str(binding.get("target")) != "8080"
        or binding.get("protocol", "tcp") != "tcp"
    ):
        raise GuardError("Staging nginx port or host binding is incorrect")
    nginx_mounts = _mounts(nginx, "nginx")
    _check_mount(
        nginx_mounts,
        target="/etc/nginx/nginx.conf",
        mount_type="bind",
        source="nginx/nginx.conf",
        compose_root=compose_root,
        read_only=True,
    )
    _check_mount(
        nginx_mounts,
        target="/etc/nginx/conf.d/default.conf",
        mount_type="bind",
        source="nginx/conf.d/default.conf",
        compose_root=compose_root,
        read_only=True,
    )
    if len(nginx_mounts) != 2:
        raise GuardError("Staging nginx must not mount any other host paths")


def validate(
    *,
    source_sha: str,
    checkout_sha: str,
    compose_source: str,
    compose_model: Any,
    expected_port: int = EXPECTED_CI_PORT,
    compose_root: Path | None = None,
) -> None:
    if not isinstance(source_sha, str) or not SHA_PATTERN.fullmatch(source_sha):
        raise GuardError("PR source revision must be a full lowercase Git SHA")
    if not isinstance(checkout_sha, str) or checkout_sha != source_sha:
        raise GuardError("Checked-out revision does not match the exact PR head SHA")
    if type(expected_port) is not int or not 1024 <= expected_port <= 65535:
        raise GuardError("Staging CI port is outside the safe loopback range")
    root = Path.cwd() if compose_root is None else compose_root
    _source_contract(compose_source, source_sha)
    _validate_model(compose_model, source_sha, expected_port, root)


def _checkout_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        raise GuardError("Could not read the checked-out Git revision") from None
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--compose-source", type=Path, required=True)
    parser.add_argument("--compose-json", type=Path, required=True)
    parser.add_argument("--port", type=int, default=EXPECTED_CI_PORT)
    args = parser.parse_args()
    try:
        source = args.compose_source.read_text(encoding="utf-8")
        model = json.loads(args.compose_json.read_text(encoding="utf-8"))
        validate(
            source_sha=args.source_sha,
            checkout_sha=_checkout_sha(),
            compose_source=source,
            compose_model=model,
            expected_port=args.port,
            compose_root=args.compose_source.resolve().parent,
        )
    except (GuardError, OSError, json.JSONDecodeError, ValueError) as error:
        print(f"Staging isolation guard rejected Compose: {error}", file=sys.stderr)
        return 1
    print(
        "STAGING_COMPOSE_GUARD_PASS "
        f"source_sha={args.source_sha} project={COMPOSE_PROJECT} "
        f"loopback_port={args.port} deployment=not_started"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
