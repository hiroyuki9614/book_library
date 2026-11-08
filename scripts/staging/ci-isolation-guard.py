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
EXPECTED_SOURCE_BUILDS = {
    "migrate": {
        "context": "./backend",
        "target": "migration",
    },
    "backend": {
        "context": "./backend",
        "target": "runtime",
        "args": {
            "VCS_REF": "${BELIB_STAGE_VCS_REF:?Set full reviewed staging SHA}",
        },
    },
    "frontend": {
        "context": "./frontend",
        "target": "runtime",
        "args": {
            "VCS_REF": "${BELIB_STAGE_VCS_REF:?Set full reviewed staging SHA}",
            "VITE_API_BASE_URL": "https://stg-bl.hiroyuki9614.com",
            "VITE_PUBLIC_DEMO_EMAIL": '""',
            "VITE_PUBLIC_DEMO_PASSWORD": '""',
            "VITE_PUBLIC_DEMO_ADMIN_EMAIL": '""',
            "VITE_PUBLIC_DEMO_ADMIN_PASSWORD": '""',
        },
    },
}
EXPECTED_BUILD_MODEL_KEYS = {
    name: set(build) | {"dockerfile"}
    for name, build in EXPECTED_SOURCE_BUILDS.items()
}
EXPECTED_SOURCE_ROOT_KEYS = {"name", "services", "networks", "volumes"}
EXPECTED_SOURCE_SERVICE_KEYS = {
    "db": {"image", "environment", "volumes", "healthcheck", "restart", "networks"},
    "migrate": {
        "profiles", "build", "user", "command", "environment", "depends_on",
        "restart", "networks",
    },
    "backend": {
        "build", "user", "environment", "volumes", "expose", "depends_on",
        "restart", "networks",
    },
    "frontend": {"build", "expose", "restart", "networks"},
    "nginx": {"image", "ports", "volumes", "depends_on", "restart", "networks"},
}
EXPECTED_SOURCE_ENV_KEYS = {
    "db": {
        "POSTGRES_USER", "POSTGRES_DB", "POSTGRES_PASSWORD",
        "BELIB_STAGE_DB_APP_PASSWORD", "BELIB_STAGE_DB_MIGRATE_PASSWORD",
    },
    "migrate": {"DATABASE_URL"},
    "backend": {
        "DATABASE_URL", "APP_PORT", "ENVIRONMENT", "FRONTEND_URL",
        "BETTER_AUTH_URL", "BETTER_AUTH_SECRET", "BOOK_FILE_STORAGE_DRIVER",
        "BOOK_FILE_STORAGE_ROOT", "NPM_CONFIG_CACHE",
    },
}
EXPECTED_SOURCE_MOUNTS = {
    "db": [
        "staging-postgres-data:/var/lib/postgresql/data",
        "./scripts/staging/10-belib-staging-roles.sh:/docker-entrypoint-initdb.d/10-belib-staging-roles.sh:ro",
    ],
    "backend": ["staging-book-files:/home/node"],
    "nginx": [
        "./nginx/nginx.conf:/etc/nginx/nginx.conf:ro",
        "./scripts/staging/nginx-default.conf:/etc/nginx/conf.d/default.conf:ro",
    ],
}
EXPECTED_SERVICE_MODEL_KEYS = {
    "db": {
        "command", "entrypoint", "environment", "healthcheck", "image",
        "networks", "restart", "volumes",
    },
    "migrate": {
        "build", "command", "depends_on", "entrypoint", "environment",
        "networks", "profiles", "restart", "user",
    },
    "backend": {
        "build", "command", "depends_on", "entrypoint", "environment",
        "expose", "networks", "restart", "user", "volumes",
    },
    "frontend": {
        "build", "command", "entrypoint", "expose", "networks", "restart",
    },
    "nginx": {
        "command", "depends_on", "entrypoint", "image", "networks", "ports",
        "restart", "volumes",
    },
}
EXPECTED_COMMANDS = {
    "db": None,
    "migrate": ["npx", "prisma", "migrate", "deploy"],
    "backend": None,
    "frontend": None,
    "nginx": None,
}
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


def _source_mapping_entries(
    lines: list[str], indentation: int, label: str
) -> list[tuple[int, str, str]]:
    entries: list[tuple[int, str, str]] = []
    found: set[str] = set()
    pattern = re.compile(
        " " * indentation + r"([A-Za-z_][A-Za-z0-9_-]*)\s*:(?:\s*(.*))?"
    )
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        actual_indentation = len(line) - len(line.lstrip(" "))
        if actual_indentation != indentation:
            continue
        match = pattern.fullmatch(line)
        if match is None:
            raise GuardError(f"{label} uses unsupported YAML mapping syntax")
        key, value = match.groups()
        if key in found:
            raise GuardError(f"{label} contains a duplicate key")
        found.add(key)
        entries.append((index, key, (value or "").strip()))
    return entries


def _source_entry_end(
    entries: list[tuple[int, str, str]], entry_position: int, block_length: int
) -> int:
    if entry_position + 1 < len(entries):
        return entries[entry_position + 1][0]
    return block_length


def _source_shape_contract(source: str) -> None:
    """Allow only the checked-in raw Compose mappings, mounts, and env keys."""
    lines = _strip_yaml_comments(source).splitlines()
    root_entries = _source_mapping_entries(lines, 0, "Staging Compose root")
    if {key for _, key, _ in root_entries} != EXPECTED_SOURCE_ROOT_KEYS:
        raise GuardError("Staging Compose root keys are outside the allowlist")

    root_sections: dict[str, list[str]] = {}
    for position, (_, key, _) in enumerate(root_entries):
        start = root_entries[position][0] + 1
        end = _source_entry_end(root_entries, position, len(lines))
        root_sections[key] = lines[start:end]

    services_lines = root_sections["services"]
    service_entries = _source_mapping_entries(
        services_lines, 2, "Staging services mapping"
    )
    if {key for _, key, _ in service_entries} != EXPECTED_SERVICES:
        raise GuardError("Staging service keys are outside the allowlist")

    for position, (service_index, name, _) in enumerate(service_entries):
        service_end = _source_entry_end(service_entries, position, len(services_lines))
        service_lines = services_lines[service_index + 1 : service_end]
        properties = _source_mapping_entries(
            service_lines, 4, f"{name} service mapping"
        )
        property_keys = {key for _, key, _ in properties}
        if property_keys != EXPECTED_SOURCE_SERVICE_KEYS[name]:
            raise GuardError(f"{name} service keys are outside the allowlist")
        property_by_key = {key: (index, value) for index, key, value in properties}

        if property_by_key["networks"][1] != "[isolated]":
            raise GuardError(f"{name} must use only the isolated network")
        if name == "migrate":
            if (
                property_by_key["profiles"][1] != '["migration"]'
                or property_by_key["command"][1]
                != '["npx", "prisma", "migrate", "deploy"]'
            ):
                raise GuardError("Migration profile and command must remain allowlisted")

        if name in EXPECTED_SOURCE_ENV_KEYS:
            env_index, env_value = property_by_key["environment"]
            if env_value:
                raise GuardError(f"{name} environment must use an explicit mapping")
            property_position = next(
                position
                for position, (_, key, _) in enumerate(properties)
                if key == "environment"
            )
            env_end = _source_entry_end(properties, property_position, len(service_lines))
            env_lines = service_lines[env_index + 1 : env_end]
            env_entries = _source_mapping_entries(
                env_lines, 6, f"{name} environment mapping"
            )
            if {key for _, key, _ in env_entries} != EXPECTED_SOURCE_ENV_KEYS[name]:
                raise GuardError(f"{name} environment keys are outside the allowlist")
            if any(
                line.strip()
                and len(line) - len(line.lstrip(" ")) > 6
                for line in env_lines
            ):
                raise GuardError(f"{name} environment values must remain scalars")

        expected_mounts = EXPECTED_SOURCE_MOUNTS.get(name)
        if expected_mounts is not None:
            volumes_index, volumes_value = property_by_key["volumes"]
            if volumes_value:
                raise GuardError(f"{name} mounts must use the checked-in short syntax")
            property_position = next(
                position
                for position, (_, key, _) in enumerate(properties)
                if key == "volumes"
            )
            volumes_end = _source_entry_end(
                properties, property_position, len(service_lines)
            )
            volume_lines = service_lines[volumes_index + 1 : volumes_end]
            mounts: list[str] = []
            for line in volume_lines:
                if not line.strip():
                    continue
                if len(line) - len(line.lstrip(" ")) != 6:
                    raise GuardError(f"{name} mounts must use the checked-in short syntax")
                mount_match = re.fullmatch(r" {6}-\s+(.+)\s*", line)
                if mount_match is None:
                    raise GuardError(f"{name} mounts use unsupported YAML syntax")
                mounts.append(mount_match.group(1))
            if mounts != expected_mounts:
                raise GuardError(f"{name} mount sources are outside the allowlist")

    network_entries = _source_mapping_entries(
        root_sections["networks"], 2, "Staging networks mapping"
    )
    if len(network_entries) != 1 or network_entries[0][1:] != ("isolated", ""):
        raise GuardError("Staging network keys are outside the allowlist")
    network_index = network_entries[0][0]
    network_lines = root_sections["networks"][network_index + 1 :]
    network_properties = _source_mapping_entries(
        network_lines, 4, "Staging isolated network mapping"
    )
    if len(network_properties) != 1 or network_properties[0][1:] != ("internal", "true"):
        raise GuardError("Staging isolated network settings are outside the allowlist")
    if any(
        line.strip() and len(line) - len(line.lstrip(" ")) > 4
        for line in network_lines
    ):
        raise GuardError("Nested network settings are forbidden")

    volume_entries = _source_mapping_entries(
        root_sections["volumes"], 2, "Staging volumes mapping"
    )
    expected_volume_definitions = {
        "staging-postgres-data": "{}",
        "staging-book-files": "{}",
    }
    if {key: value for _, key, value in volume_entries} != expected_volume_definitions:
        raise GuardError("Named volume options are outside the allowlist")
    if any(
        line.strip() and len(line) - len(line.lstrip(" ")) > 2
        for line in root_sections["volumes"]
    ):
        raise GuardError("Named volume driver and option settings are forbidden")


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


def _source_build_contract(source: str) -> None:
    """Check the original YAML build blocks before Compose can normalize them."""
    code = _strip_yaml_comments(source)
    if re.search(r"(?m)^\s*<<\s*:", code) or re.search(
        r"(?:^|[\s:[{,])[&*][A-Za-z0-9_-]+(?=$|[\s,}\]])", code
    ):
        raise GuardError("YAML aliases and merge keys are forbidden in staging Compose")

    lines = code.splitlines()
    in_services = False
    current_service: str | None = None
    found_services: set[str] = set()
    build_counts = {name: 0 for name in EXPECTED_SOURCE_BUILDS}
    services_sections = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if not stripped:
            index += 1
            continue
        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise GuardError("Tabs are forbidden in staging Compose indentation")
        if indent == 0:
            if stripped == "services:":
                services_sections += 1
                if services_sections != 1:
                    raise GuardError("Staging Compose must have one services mapping")
                in_services = True
                current_service = None
            else:
                in_services = False
                current_service = None
            index += 1
            continue
        if not in_services:
            index += 1
            continue
        if indent == 2:
            service_match = re.fullmatch(r"  ([A-Za-z0-9][A-Za-z0-9_-]*):\s*", line)
            if service_match is None:
                raise GuardError("Staging service keys must use the checked-in YAML form")
            current_service = service_match.group(1)
            found_services.add(current_service)
            index += 1
            continue
        if indent != 4:
            index += 1
            continue

        build_match = re.fullmatch(
            r" {4}(?:build|'build'|\"build\")\s*:(.*)", line
        )
        if build_match is None:
            index += 1
            continue
        if current_service not in EXPECTED_SOURCE_BUILDS:
            raise GuardError("Build is forbidden for this staging service")
        if build_match.group(1).strip():
            raise GuardError("Staging build must use an explicit allowlisted mapping")

        actual_build: dict[str, Any] = {}
        actual_args: dict[str, str] = {}
        in_args = False
        child_index = index + 1
        while child_index < len(lines):
            child = lines[child_index]
            if not child.strip():
                child_index += 1
                continue
            if "\t" in child[: len(child) - len(child.lstrip())]:
                raise GuardError("Tabs are forbidden in staging build indentation")
            child_indent = len(child) - len(child.lstrip(" "))
            if child_indent <= 4:
                break
            if child_indent == 6:
                child_match = re.fullmatch(
                    r" {6}([A-Za-z_][A-Za-z0-9_-]*)\s*:(.*)", child
                )
                if child_match is None:
                    raise GuardError("Staging build keys must use the checked-in YAML form")
                key, raw_value = child_match.groups()
                if key in actual_build:
                    raise GuardError("Duplicate staging build key")
                if key == "args":
                    if raw_value.strip():
                        raise GuardError("Staging build args must use an explicit allowlisted mapping")
                    actual_build[key] = actual_args
                    in_args = True
                else:
                    value = raw_value.strip()
                    if not value:
                        raise GuardError("Staging build values must be explicit scalars")
                    actual_build[key] = value
                    in_args = False
            elif child_indent == 8 and in_args:
                arg_match = re.fullmatch(
                    r" {8}([A-Za-z_][A-Za-z0-9_-]*)\s*:(.*)", child
                )
                if arg_match is None:
                    raise GuardError("Staging build args must use the checked-in YAML form")
                arg_name, raw_value = arg_match.groups()
                if arg_name in actual_args:
                    raise GuardError("Duplicate staging build argument")
                value = raw_value.strip()
                if not value:
                    raise GuardError("Staging build arguments must be explicit scalars")
                actual_args[arg_name] = value
            else:
                raise GuardError("Nested or unsupported staging build syntax is forbidden")
            child_index += 1

        if actual_build != EXPECTED_SOURCE_BUILDS[current_service]:
            raise GuardError(f"{current_service} build keys or values are outside the staging allowlist")
        build_counts[current_service] += 1
        index = child_index

    if services_sections != 1 or found_services != EXPECTED_SERVICES:
        raise GuardError("Staging Compose must declare exactly the five expected services")
    if build_counts != {name: 1 for name in EXPECTED_SOURCE_BUILDS}:
        raise GuardError("Each staging build service must have exactly one allowlisted build")


def _build(service: dict[str, Any], name: str) -> dict[str, Any]:
    build = service.get("build")
    if not isinstance(build, dict) or set(build) != EXPECTED_BUILD_MODEL_KEYS[name]:
        raise GuardError(f"{name} build contains unsupported or unsafe build settings")
    return build


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
    _source_build_contract(source)
    _source_shape_contract(source)
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
    if "shobj_description(" not in code or re.search(r"(?<!sh)obj_description\s*\(", code):
        raise GuardError("The database bootstrap marker must use shared-object description lookup")
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
    value = service.get("environment", {})
    if not isinstance(value, dict):
        raise GuardError(f"{name} environment must be a mapping")
    return _mapping(value, f"{name} environment")


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
    expected_keys = (
        {"type", "source", "target", "read_only", "bind"}
        if mount_type == "bind"
        else {"type", "source", "target", "volume"}
    )
    option_key = "bind" if mount_type == "bind" else "volume"
    if set(mount) != expected_keys or mount.get(option_key) != {}:
        raise GuardError("Staging volume options are outside the allowlist")
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
    for name, service in services.items():
        if not isinstance(service, dict) or set(service) != EXPECTED_SERVICE_MODEL_KEYS[name]:
            raise GuardError(f"{name} service settings are outside the allowlist")
        if service.get("command") != EXPECTED_COMMANDS[name] or service.get("entrypoint") is not None:
            raise GuardError(f"{name} command and entrypoint must remain allowlisted")
        if name == "migrate" and service.get("profiles") != ["migration"]:
            raise GuardError("Migration service must use only the migration profile")
    networks = model.get("networks")
    if (
        not isinstance(networks, dict)
        or set(networks) != {"isolated"}
        or not isinstance(networks["isolated"], dict)
        or set(networks["isolated"]) != {"internal", "ipam", "name"}
        or networks["isolated"].get("internal") is not True
        or networks["isolated"].get("ipam") != {}
        or networks["isolated"].get("name") != f"{COMPOSE_PROJECT}_isolated"
    ):
        raise GuardError("Staging services must use the isolated internal network")

    volumes = model.get("volumes")
    if not isinstance(volumes, dict) or set(volumes) != {
        "staging-postgres-data",
        "staging-book-files",
    }:
        raise GuardError("Compose must use only the two staging-owned named volumes")
    expected_volume_names = {
        "staging-postgres-data": f"{COMPOSE_PROJECT}_staging-postgres-data",
        "staging-book-files": f"{COMPOSE_PROJECT}_staging-book-files",
    }
    if any(
        not isinstance(volumes[key], dict)
        or set(volumes[key]) != {"name"}
        or volumes[key].get("name") != expected_name
        for key, expected_name in expected_volume_names.items()
    ):
        raise GuardError("Named volume drivers and options are outside the allowlist")

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
        if service.get("networks") != {"isolated": None}:
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
    migrate_build = _build(migrate, "migrate")
    migrate_env = _env(migrate, "migrate")
    if (
        migrate_env != {"DATABASE_URL": expected_migrate_database_url}
        or migrate.get("user") != "node"
        or "image" in migrate
        or not _context_matches(
            migrate_build.get("context"), "backend", compose_root
        )
        or migrate_build.get("dockerfile") != "Dockerfile"
        or migrate_build.get("target") != "migration"
        or _mounts(migrate, "migrate")
    ):
        raise GuardError("Migration service must use only the isolated staging database")

    backend = services["backend"]
    backend_build = _build(backend, "backend")
    backend_env = _env(backend, "backend")
    expected_port_text = str(expected_port)
    expected_backend_env = {
        "DATABASE_URL": expected_app_database_url,
        "APP_PORT": "3000",
        "ENVIRONMENT": "test",
        "FRONTEND_URL": "https://stg-bl.hiroyuki9614.com",
        "BETTER_AUTH_URL": "https://stg-bl.hiroyuki9614.com",
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
    if (
        backend.get("user") != "node"
        or "image" in backend
        or not _context_matches(backend_build.get("context"), "backend", compose_root)
        or backend_build.get("dockerfile") != "Dockerfile"
        or backend_build.get("target") != "runtime"
        or backend_build.get("args") != {"VCS_REF": source_sha}
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
    frontend_build = _build(frontend, "frontend")
    expected_frontend_args = {
        "VCS_REF": source_sha,
        "VITE_API_BASE_URL": "https://stg-bl.hiroyuki9614.com",
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
        or frontend_build.get("dockerfile") != "Dockerfile"
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
        not isinstance(binding, dict)
        or set(binding) != {"mode", "host_ip", "target", "published", "protocol"}
        or binding.get("mode") != "ingress"
        or binding.get("host_ip") != "127.0.0.1"
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
        source="scripts/staging/nginx-default.conf",
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


def _checkout_sha(compose_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(compose_root), "rev-parse", "HEAD"],
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
            checkout_sha=_checkout_sha(args.compose_source.resolve().parent),
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
