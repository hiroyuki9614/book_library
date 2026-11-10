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
EXPECTED_PLATFORM = "linux/amd64"
POSTGRES_IMAGE = (
    "postgres:16.14-bookworm@sha256:"
    "714b08dd70c05160db032e4321033ac4e5b6328935b2d03ed419c3a898aa8219"
)
NGINX_IMAGE = (
    "nginx:1.27-alpine@sha256:"
    "62223d644fa234c3a1cc785ee14242ec47a77364226f1c811d2f669f96dc2ac8"
)
CI_POSTGRES_IMAGE = POSTGRES_IMAGE
NODE_BUILD_IMAGE = (
    "node:22-bookworm@sha256:"
    "539d4d5b9c0df96bb9d1229264d358dc816e8592cb8867828a580b527e7ad0fa"
)
NODE_RUNTIME_IMAGE = (
    "node:22-bookworm-slim@sha256:"
    "aaa4b5baf1ecff3b283486bb1f90405cdda225a3e6f79660d2c559fa510a7292"
)
NGINX_UNPRIVILEGED_IMAGE = (
    "nginxinc/nginx-unprivileged:1.27-alpine@sha256:"
    "28d91bdce70ad09025ea901458fdd149259d8e05982ade79d4ef2c0d9470eb48"
)
EXPECTED_SOURCE_IMAGES = {"db": POSTGRES_IMAGE, "nginx": NGINX_IMAGE}
EXPECTED_DOCKERFILE_REFERENCES = {
    "migrate": "../scripts/staging/Dockerfile.backend.staging",
    "backend": "../scripts/staging/Dockerfile.backend.staging",
    "frontend": "../scripts/staging/Dockerfile.frontend.staging",
}
EXPECTED_DOCKERFILE_PATHS = {
    "migrate": "scripts/staging/Dockerfile.backend.staging",
    "backend": "scripts/staging/Dockerfile.backend.staging",
    "frontend": "scripts/staging/Dockerfile.frontend.staging",
}
EXPECTED_DOCKERFILE_FROM_LINES = {
    "scripts/staging/Dockerfile.backend.staging": [
        f"FROM {NODE_BUILD_IMAGE} AS build",
        "FROM build AS migration",
        f"FROM {NODE_RUNTIME_IMAGE} AS runtime",
    ],
    "scripts/staging/Dockerfile.frontend.staging": [
        f"FROM {NODE_BUILD_IMAGE} AS build",
        f"FROM {NGINX_UNPRIVILEGED_IMAGE} AS runtime",
    ],
}
EXPECTED_SOURCE_BUILDS = {
    "migrate": {
        "context": "./backend",
        "dockerfile": EXPECTED_DOCKERFILE_REFERENCES["migrate"],
        "target": "migration",
    },
    "backend": {
        "context": "./backend",
        "dockerfile": EXPECTED_DOCKERFILE_REFERENCES["backend"],
        "target": "runtime",
        "args": {
            "VCS_REF": "${BELIB_STAGE_VCS_REF:?Set full reviewed staging SHA}",
        },
    },
    "frontend": {
        "context": "./frontend",
        "dockerfile": EXPECTED_DOCKERFILE_REFERENCES["frontend"],
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
    "db": {"image", "platform", "environment", "volumes", "healthcheck", "restart", "networks"},
    "migrate": {
        "profiles", "platform", "build", "user", "command", "environment", "depends_on",
        "restart", "networks",
    },
    "backend": {
        "platform", "build", "user", "environment", "volumes", "expose", "depends_on",
        "restart", "networks",
    },
    "frontend": {"platform", "build", "expose", "restart", "networks"},
    "nginx": {"image", "platform", "ports", "volumes", "depends_on", "restart", "networks"},
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
EXPECTED_SOURCE_ENV_VALUES = {
    "db": {
        "POSTGRES_USER": "belib_staging_bootstrap",
        "POSTGRES_DB": "belib_staging",
        "POSTGRES_PASSWORD": "${BELIB_STAGE_DB_BOOTSTRAP_PASSWORD:?Set staging-only bootstrap password}",
        "BELIB_STAGE_DB_APP_PASSWORD": "${BELIB_STAGE_DB_APP_PASSWORD:?Set staging-only app password}",
        "BELIB_STAGE_DB_MIGRATE_PASSWORD": "${BELIB_STAGE_DB_MIGRATE_PASSWORD:?Set staging-only migration password}",
    },
    "migrate": {"DATABASE_URL": MIGRATE_DATABASE_URL_SOURCE},
    "backend": {
        "DATABASE_URL": APP_DATABASE_URL_SOURCE,
        "APP_PORT": '"3000"',
        "ENVIRONMENT": "test",
        "FRONTEND_URL": "https://stg-bl.hiroyuki9614.com",
        "BETTER_AUTH_URL": "https://stg-bl.hiroyuki9614.com",
        "BETTER_AUTH_SECRET": "${BELIB_STAGE_AUTH_SECRET:?Set staging-only Better Auth secret}",
        "BOOK_FILE_STORAGE_DRIVER": "local",
        "BOOK_FILE_STORAGE_ROOT": "/home/node/books",
        "NPM_CONFIG_CACHE": "/tmp/npm-cache",
    },
}
DB_HEALTHCHECK_SQL = (
    "SELECT 1 WHERE current_user = 'belib_staging_app'",
    "AND shobj_description((SELECT oid FROM pg_database WHERE datname = current_database()), 'pg_database') = 'belib-staging-role-bootstrap-v1'",
    "AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'belib_staging_app' AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls AND NOT rolinherit)",
    "AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'belib_staging_migrate' AND rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls AND NOT rolinherit)",
    "AND has_database_privilege(current_user, current_database(), 'CONNECT')",
    "AND NOT has_database_privilege(current_user, current_database(), 'CREATE')",
    "AND NOT has_database_privilege(current_user, current_database(), 'TEMPORARY')",
    "AND has_schema_privilege(current_user, 'public', 'USAGE')",
    "AND NOT has_schema_privilege(current_user, 'public', 'CREATE')",
    "AND (SELECT nspowner = (SELECT oid FROM pg_roles WHERE rolname = 'belib_staging_migrate') FROM pg_namespace WHERE nspname = 'public')",
)
EXPECTED_DB_HEALTHCHECK_COMMAND = (
    'test "$$(psql -X -w -U belib_staging_app -d belib_staging -tAc '
    '"'
    + " ".join(DB_HEALTHCHECK_SQL)
    + '")" = "1"'
)
EXPECTED_DB_HEALTHCHECK_SOURCE = "\n".join(
    [
        "    healthcheck:",
        "      test:",
        "        - CMD-SHELL",
        "        - >-",
        '          test "$$(psql -X -w -U belib_staging_app -d belib_staging -tAc',
        f'          "{DB_HEALTHCHECK_SQL[0]}',
        *(f"          {line}" for line in DB_HEALTHCHECK_SQL[1:-1]),
        f'          {DB_HEALTHCHECK_SQL[-1]}\")\" = \"1\"',
        "      interval: 5s",
        "      timeout: 5s",
        "      retries: 12",
    ]
)
EXPECTED_DB_HEALTHCHECK_MODEL = {
    "test": ["CMD-SHELL", EXPECTED_DB_HEALTHCHECK_COMMAND],
    "interval": "5s",
    "timeout": "5s",
    "retries": 12,
}
EXPECTED_SOURCE_MOUNTS = {
    "db": [
        "      - staging-postgres-data:/var/lib/postgresql/data",
        "      - type: bind",
        "        source: ./scripts/staging/10-belib-staging-roles.sh",
        "        target: /docker-entrypoint-initdb.d/10-belib-staging-roles.sh",
        "        read_only: true",
        "        bind:",
        "          create_host_path: false",
    ],
    "backend": ["      - staging-book-files:/home/node"],
    "nginx": [
        "      - type: bind",
        "        source: ./nginx/nginx.conf",
        "        target: /etc/nginx/nginx.conf",
        "        read_only: true",
        "        bind:",
        "          create_host_path: false",
        "      - type: bind",
        "        source: ./scripts/staging/nginx-default.conf",
        "        target: /etc/nginx/conf.d/default.conf",
        "        read_only: true",
        "        bind:",
        "          create_host_path: false",
    ],
}
EXPECTED_SERVICE_MODEL_KEYS = {
    "db": {
        "command", "entrypoint", "environment", "healthcheck", "image", "platform",
        "networks", "restart", "volumes",
    },
    "migrate": {
        "build", "command", "depends_on", "entrypoint", "environment",
        "networks", "platform", "profiles", "restart", "user",
    },
    "backend": {
        "build", "command", "depends_on", "entrypoint", "environment",
        "expose", "networks", "platform", "restart", "user", "volumes",
    },
    "frontend": {
        "build", "command", "entrypoint", "expose", "networks", "platform", "restart",
    },
    "nginx": {
        "command", "depends_on", "entrypoint", "image", "networks", "ports",
        "platform", "restart", "volumes",
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
ROLE_INIT_SOURCE = "scripts/staging/10-belib-staging-roles.sh"
ROLE_INIT_TARGET = "/docker-entrypoint-initdb.d/10-belib-staging-roles.sh"
_YAML_HORIZONTAL_WHITESPACE = " \t"


class GuardError(RuntimeError):
    """Raised when the staging Compose contract is not satisfied."""


def _strip_yaml_comments(source: str) -> str:
    """Remove YAML comments without truncating hashes inside scalar values."""
    uncommented: list[str] = []
    for line in source.splitlines():
        quote: str | None = None
        token_start = True
        index = 0
        while index < len(line):
            character = line[index]
            if quote == "'":
                if character == "'":
                    if index + 1 < len(line) and line[index + 1] == "'":
                        index += 2
                        continue
                    quote = None
            elif quote == '"':
                if character == "\\":
                    index += 2
                    continue
                if character == '"':
                    quote = None
            else:
                if character == "#" and (
                    index == 0 or line[index - 1] in _YAML_HORIZONTAL_WHITESPACE
                ):
                    line = line[:index]
                    break
                if character in "'\"" and token_start:
                    quote = character
                    token_start = False
                    index += 1
                    continue
                if character in ":,[]{}" or (character == "-" and token_start):
                    token_start = True
                elif character not in _YAML_HORIZONTAL_WHITESPACE:
                    token_start = False
            index += 1
        uncommented.append(line)
    return "\n".join(uncommented)


def _source_mapping_entries(
    lines: list[str], indentation: int, label: str
) -> list[tuple[int, str, str]]:
    entries: list[tuple[int, str, str]] = []
    found: set[str] = set()
    pattern = re.compile(
        " " * indentation
        + r"([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:(?:[ \t]*(.*))?"
    )
    for index, line in enumerate(lines):
        if not line.strip(_YAML_HORIZONTAL_WHITESPACE):
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
        entries.append(
            (index, key, (value or "").strip(_YAML_HORIZONTAL_WHITESPACE))
        )
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
        if property_by_key["platform"][1] != EXPECTED_PLATFORM:
            raise GuardError(f"{name} must target the reviewed {EXPECTED_PLATFORM} platform")
        if (
            name in EXPECTED_SOURCE_IMAGES
            and property_by_key["image"][1] != EXPECTED_SOURCE_IMAGES[name]
        ):
            raise GuardError(f"{name} image reference must match its reviewed immutable digest")

        expected_network_source = "[isolated, ingress]" if name == "nginx" else "[isolated]"
        if property_by_key["networks"][1] != expected_network_source:
            raise GuardError(f"{name} network attachments are outside the allowlist")
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
            if {
                key: value for _, key, value in env_entries
            } != EXPECTED_SOURCE_ENV_VALUES[name]:
                raise GuardError(f"{name} environment values are outside the allowlist")
            if any(
                line.strip(_YAML_HORIZONTAL_WHITESPACE)
                and len(line) - len(line.lstrip(" ")) > 6
                for line in env_lines
            ):
                raise GuardError(f"{name} environment values must remain scalars")

        if name == "db":
            healthcheck_index, healthcheck_value = property_by_key["healthcheck"]
            healthcheck_position = next(
                position
                for position, (_, key, _) in enumerate(properties)
                if key == "healthcheck"
            )
            healthcheck_end = _source_entry_end(
                properties, healthcheck_position, len(service_lines)
            )
            if (
                healthcheck_value
                or tuple(service_lines[healthcheck_index:healthcheck_end])
                != tuple(EXPECTED_DB_HEALTHCHECK_SOURCE.splitlines())
            ):
                raise GuardError("PostgreSQL healthcheck source is outside the allowlist")

        expected_mounts = EXPECTED_SOURCE_MOUNTS.get(name)
        if expected_mounts is not None:
            volumes_index, volumes_value = property_by_key["volumes"]
            if volumes_value:
                raise GuardError(f"{name} mounts must use the checked-in list syntax")
            property_position = next(
                position
                for position, (_, key, _) in enumerate(properties)
                if key == "volumes"
            )
            volumes_end = _source_entry_end(
                properties, property_position, len(service_lines)
            )
            volume_lines = service_lines[volumes_index + 1 : volumes_end]
            mounts = [
                line.rstrip(_YAML_HORIZONTAL_WHITESPACE)
                for line in volume_lines
                if line.strip(_YAML_HORIZONTAL_WHITESPACE)
            ]
            if mounts != expected_mounts:
                raise GuardError(f"{name} mount sources are outside the allowlist")

    network_entries = _source_mapping_entries(
        root_sections["networks"], 2, "Staging networks mapping"
    )
    if {key: value for _, key, value in network_entries} != {"isolated": "", "ingress": ""}:
        raise GuardError("Staging network keys are outside the allowlist")
    for position, (network_index, network_name, _) in enumerate(network_entries):
        network_end = _source_entry_end(network_entries, position, len(root_sections["networks"]))
        network_lines = root_sections["networks"][network_index + 1 : network_end]
        network_properties = _source_mapping_entries(
            network_lines, 4, f"Staging {network_name} network mapping"
        )
        observed = [(key, value) for _, key, value in network_properties]
        expected = [("internal", "true")] if network_name == "isolated" else [("driver", "bridge")]
        if observed != expected:
            raise GuardError(f"Staging {network_name} network settings are outside the allowlist")
        if any(
            line.strip(_YAML_HORIZONTAL_WHITESPACE)
            and len(line) - len(line.lstrip(" ")) > 4
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
        line.strip(_YAML_HORIZONTAL_WHITESPACE)
        and len(line) - len(line.lstrip(" ")) > 2
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
    if re.search(r"(?m)^[ \t]*<<[ \t]*:", code) or re.search(
        r"(?:^|[ \t\r\n:[{,])[&*][A-Za-z0-9_-]+(?=$|[ \t\r\n,}\]])", code
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
        leading_whitespace = line[
            : len(line) - len(line.lstrip(_YAML_HORIZONTAL_WHITESPACE))
        ]
        if "\t" in leading_whitespace:
            raise GuardError("Tabs are forbidden in staging Compose indentation")
        stripped = line.strip(_YAML_HORIZONTAL_WHITESPACE)
        indent = len(line) - len(line.lstrip(" "))
        if not stripped:
            index += 1
            continue
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
            service_match = re.fullmatch(
                r"  ([A-Za-z0-9][A-Za-z0-9_-]*):[ \t]*", line
            )
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
            r" {4}(?:build|'build'|\"build\")[ \t]*:(.*)", line
        )
        if build_match is None:
            index += 1
            continue
        if current_service not in EXPECTED_SOURCE_BUILDS:
            raise GuardError("Build is forbidden for this staging service")
        if build_match.group(1).strip(_YAML_HORIZONTAL_WHITESPACE):
            raise GuardError("Staging build must use an explicit allowlisted mapping")

        actual_build: dict[str, Any] = {}
        actual_args: dict[str, str] = {}
        in_args = False
        child_index = index + 1
        while child_index < len(lines):
            child = lines[child_index]
            child_leading_whitespace = child[
                : len(child) - len(child.lstrip(_YAML_HORIZONTAL_WHITESPACE))
            ]
            if "\t" in child_leading_whitespace:
                raise GuardError("Tabs are forbidden in staging build indentation")
            if not child.strip(_YAML_HORIZONTAL_WHITESPACE):
                child_index += 1
                continue
            child_indent = len(child) - len(child.lstrip(" "))
            if child_indent <= 4:
                break
            if child_indent == 6:
                child_match = re.fullmatch(
                    r" {6}([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:(.*)", child
                )
                if child_match is None:
                    raise GuardError("Staging build keys must use the checked-in YAML form")
                key, raw_value = child_match.groups()
                if key in actual_build:
                    raise GuardError("Duplicate staging build key")
                if key == "args":
                    if raw_value.strip(_YAML_HORIZONTAL_WHITESPACE):
                        raise GuardError("Staging build args must use an explicit allowlisted mapping")
                    actual_build[key] = actual_args
                    in_args = True
                else:
                    value = raw_value.strip(_YAML_HORIZONTAL_WHITESPACE)
                    if not value:
                        raise GuardError("Staging build values must be explicit scalars")
                    actual_build[key] = value
                    in_args = False
            elif child_indent == 8 and in_args:
                arg_match = re.fullmatch(
                    r" {8}([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:(.*)", child
                )
                if arg_match is None:
                    raise GuardError("Staging build args must use the checked-in YAML form")
                arg_name, raw_value = arg_match.groups()
                if arg_name in actual_args:
                    raise GuardError("Duplicate staging build argument")
                value = raw_value.strip(_YAML_HORIZONTAL_WHITESPACE)
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
    if not source.isascii():
        raise GuardError("Staging Compose source must contain ASCII characters only")
    if any(
        (ord(character) < 0x20 and character not in "\t\r\n")
        or ord(character) == 0x7F
        for character in source
    ):
        raise GuardError("Staging Compose source contains unsupported control characters")

    code = _strip_yaml_comments(source)
    _source_build_contract(source)
    _source_shape_contract(source)
    for pattern, description in (
        (
            r"(?im)^[ \t]*(?:env_file|extends|include)[ \t]*:",
            "external Compose input",
        ),
        (r"compose\.ya?ml", "production Compose reference"),
        (r"(?<![\w])\.env(?:\b|$)", "environment file reference"),
        (r"\bR2_[A-Z0-9_]*\b", "R2 setting"),
        (r"\bBELIB_FRONTEND_IMAGE\b", "production image override"),
    ):
        if re.search(pattern, code):
            raise GuardError(f"Staging Compose contains a forbidden {description}")

    if re.search(r"(?m)^name:[ \t]*belib-isolated-i5[ \t]*$", code) is None:
        raise GuardError("Staging Compose project name must be belib-isolated-i5")
    if re.search(r"(?m)^name:[ \t]*(?!belib-isolated-i5[ \t]*$)\S+", code):
        raise GuardError("Staging Compose project name must be belib-isolated-i5")

    if "BELIB_STAGE_DB_PASSWORD" in code:
        raise GuardError("The legacy single-password database identity is forbidden")
    if "shobj_description(" not in code or re.search(
        r"(?<!sh)obj_description[ \t]*\(", code
    ):
        raise GuardError("The database bootstrap marker must use shared-object description lookup")
    required_role_lines = (
        "POSTGRES_USER: belib_staging_bootstrap",
        "POSTGRES_DB: belib_staging",
        "POSTGRES_PASSWORD: ${BELIB_STAGE_DB_BOOTSTRAP_PASSWORD:?Set staging-only bootstrap password}",
        "BELIB_STAGE_DB_APP_PASSWORD: ${BELIB_STAGE_DB_APP_PASSWORD:?Set staging-only app password}",
        "BELIB_STAGE_DB_MIGRATE_PASSWORD: ${BELIB_STAGE_DB_MIGRATE_PASSWORD:?Set staging-only migration password}",
    )
    if any(code.count(line) != 1 for line in required_role_lines):
        raise GuardError("PostgreSQL must use the distinct bootstrap identity and read-only role initializer")
    if (
        code.count(f"source: ./{ROLE_INIT_SOURCE}") != 1
        or code.count(f"target: {ROLE_INIT_TARGET}") != 1
    ):
        raise GuardError("PostgreSQL role initializer mount must remain unique and isolated")

    expected_vcs_line = "VCS_REF: ${BELIB_STAGE_VCS_REF:?Set full reviewed staging SHA}"
    vcs_lines = [
        line.strip(_YAML_HORIZONTAL_WHITESPACE)
        for line in code.splitlines()
        if re.search(r"(?<![A-Za-z0-9_])VCS_REF[ \t]*:", line)
    ]
    if vcs_lines != [expected_vcs_line, expected_vcs_line]:
        raise GuardError("Both built services must use the required staging SHA")
    if not SHA_PATTERN.fullmatch(source_sha):
        raise GuardError("Source revision must be a full lowercase Git SHA")

    db_lines = [
        line.strip(_YAML_HORIZONTAL_WHITESPACE)
        for line in code.splitlines()
        if re.search(r"(?<![A-Za-z0-9_])DATABASE_URL[ \t]*:", line)
    ]
    expected_db_lines = [
        f"DATABASE_URL: {MIGRATE_DATABASE_URL_SOURCE}",
        f"DATABASE_URL: {APP_DATABASE_URL_SOURCE}",
    ]
    if db_lines != expected_db_lines:
        raise GuardError("Database URLs must use the isolated migration and app roles")

    port_lines = [
        line.strip(_YAML_HORIZONTAL_WHITESPACE)
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


def _checked_repo_path(value: Any, compose_root: Path) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    raw_path = Path(value)
    if ".." in raw_path.parts:
        return None
    try:
        root = Path(compose_root).resolve(strict=True)
        candidate = raw_path if raw_path.is_absolute() else root / raw_path
        relative = candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    if not relative.parts:
        return None

    current = root
    for part in relative.parts:
        if part in {".", ".."}:
            return None
        current = current / part
        try:
            component_stat = current.lstat()
            resolved = current.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            return None
        if stat.S_ISLNK(component_stat.st_mode) or resolved != current:
            return None
    return current


def _validate_staging_dockerfiles(services: dict[str, Any], compose_root: Path) -> None:
    for name, relative_reference in EXPECTED_DOCKERFILE_REFERENCES.items():
        service = services.get(name)
        build = service.get("build") if isinstance(service, dict) else None
        if not isinstance(build, dict):
            raise GuardError(f"{name} must use its dedicated staging Dockerfile")
        if build.get("dockerfile") != relative_reference:
            raise GuardError(f"{name} Dockerfile path must use the staging-only recipe")

        context_path = _checked_repo_path(build.get("context"), compose_root)
        expected_path = _checked_repo_path(
            EXPECTED_DOCKERFILE_PATHS[name], compose_root
        )
        if context_path is None or expected_path is None:
            raise GuardError(f"{name} staging Dockerfile must stay inside the checkout")
        try:
            resolved_reference = (context_path / relative_reference).resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            raise GuardError(f"{name} staging Dockerfile path cannot be resolved") from None
        if resolved_reference != expected_path:
            raise GuardError(f"{name} Dockerfile path resolves outside its staging allowlist")
        try:
            file_stat = expected_path.lstat()
            dockerfile_source = expected_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise GuardError(f"{name} staging Dockerfile is unavailable") from None
        if not stat.S_ISREG(file_stat.st_mode):
            raise GuardError(f"{name} staging Dockerfile must be a regular file")
        from_lines = [
            line.strip()
            for line in dockerfile_source.splitlines()
            if re.match(r"(?i)^\s*FROM(?:\s|$)", line)
        ]
        if from_lines != EXPECTED_DOCKERFILE_FROM_LINES[EXPECTED_DOCKERFILE_PATHS[name]]:
            raise GuardError(f"{name} staging Dockerfile has an unreviewed base image or stage")


def _validate_staging_ci_image(compose_root: Path) -> None:
    workflow_path = _checked_repo_path(
        ".github/workflows/staging-ci.yml", compose_root
    )
    if workflow_path is None:
        raise GuardError("Staging CI workflow path is missing or unsafe")
    try:
        file_stat = workflow_path.lstat()
        workflow_source = workflow_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise GuardError("Staging CI workflow is unavailable") from None
    if not stat.S_ISREG(file_stat.st_mode):
        raise GuardError("Staging CI workflow must be a regular file")
    image_values = [
        match.group(1)
        for line in _strip_yaml_comments(workflow_source).splitlines()
        if (
            match := re.fullmatch(
                r"[ \t]*image[ \t]*:[ \t]*(\S+)[ \t]*", line
            )
        )
    ]
    if image_values != [CI_POSTGRES_IMAGE]:
        raise GuardError("Staging CI container images must use the reviewed immutable digest")


def _repo_path_matches(value: Any, expected: str, compose_root: Path) -> bool:
    actual_path = _checked_repo_path(value, compose_root)
    expected_path = _checked_repo_path(expected, compose_root)
    return actual_path is not None and actual_path == expected_path


def _safe_mount_shape_summary(mount: dict[str, Any], option_key: str) -> str:
    """Describe only mount structure and known boolean flags in CI diagnostics."""
    mount_keys = sorted(key for key in mount if isinstance(key, str))
    options = mount.get(option_key)
    option_keys = (
        sorted(key for key in options if isinstance(key, str))
        if isinstance(options, dict)
        else []
    )
    boolean_flags: dict[str, bool] = {}
    if type(mount.get("read_only")) is bool:
        boolean_flags["read_only"] = mount["read_only"]
    if isinstance(options, dict):
        for key in ("create_host_path", "nocopy"):
            if type(options.get(key)) is bool:
                boolean_flags[f"{option_key}.{key}"] = options[key]
    return (
        f"(mount keys={mount_keys}; {option_key} option keys={option_keys}; "
        f"boolean flags={boolean_flags})"
    )


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
    actual_keys = set(mount)
    keys_match = actual_keys == expected_keys
    if (
        mount_type == "volume"
        and actual_keys == expected_keys | {"read_only"}
        and mount.get("read_only") is False
    ):
        keys_match = True
    allowed_options = (
        ({}, {"create_host_path": False})
        if mount_type == "bind"
        else (
            {},
            {"nocopy": False},
            {"subpath": ""},
            {"nocopy": False, "subpath": ""},
        )
    )
    options = mount.get(option_key)
    options_match = isinstance(options, dict) and any(
        set(options) == set(allowed)
        and all(
            type(options[key]) is type(value) and options[key] == value
            for key, value in allowed.items()
        )
        for allowed in allowed_options
    )
    if not keys_match or not options_match:
        raise GuardError(
            "Staging volume options are outside the allowlist "
            f"{_safe_mount_shape_summary(mount, option_key)}"
        )
    actual_source = mount.get("source")
    source_matches = False
    if isinstance(actual_source, str):
        if mount_type == "bind":
            source_matches = _repo_path_matches(actual_source, source, compose_root)
        else:
            source_matches = actual_source == source
    if mount.get("type") != mount_type or not source_matches:
        raise GuardError("Staging volume mapping is not isolated")
    if read_only is not None and mount.get("read_only") is not read_only:
        raise GuardError("Staging bind mount must be read-only")


def _context_matches(value: Any, expected: str, compose_root: Path) -> bool:
    return _repo_path_matches(value, expected, compose_root)


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
        if service.get("platform") != EXPECTED_PLATFORM:
            raise GuardError(f"{name} must target the reviewed {EXPECTED_PLATFORM} platform")
        if service.get("command") != EXPECTED_COMMANDS[name] or service.get("entrypoint") is not None:
            raise GuardError(f"{name} command and entrypoint must remain allowlisted")
        if name == "migrate" and service.get("profiles") != ["migration"]:
            raise GuardError("Migration service must use only the migration profile")
    _validate_staging_dockerfiles(services, compose_root)
    networks = model.get("networks")
    if (
        not isinstance(networks, dict)
        or set(networks) != {"isolated", "ingress"}
        or not isinstance(networks["isolated"], dict)
        or set(networks["isolated"]) != {"internal", "ipam", "name"}
        or networks["isolated"].get("internal") is not True
        or networks["isolated"].get("ipam") != {}
        or networks["isolated"].get("name") != f"{COMPOSE_PROJECT}_isolated"
        or not isinstance(networks["ingress"], dict)
        or set(networks["ingress"]) != {"driver", "ipam", "name"}
        or networks["ingress"].get("ipam") != {}
        or networks["ingress"].get("driver") != "bridge"
        or networks["ingress"].get("name") != f"{COMPOSE_PROJECT}_ingress"
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
        expected_networks = {"isolated": None, "ingress": None} if name == "nginx" else {"isolated": None}
        if service.get("networks") != expected_networks:
            raise GuardError(f"{name} network attachments are outside the allowlist")
        if service.get("network_mode") is not None:
            raise GuardError("Host or external network modes are forbidden")
        if service.get("privileged") is True or service.get("devices") or service.get("cap_add"):
            raise GuardError("Privileged container settings are forbidden")
        if name != "nginx" and service.get("ports"):
            raise GuardError("Only nginx may publish a staging port")

    db = services["db"]
    db_env = _env(db, "db")
    if (
        db.get("image") != POSTGRES_IMAGE
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
    if db.get("healthcheck") != EXPECTED_DB_HEALTHCHECK_MODEL:
        raise GuardError("PostgreSQL healthcheck is outside the staging allowlist")
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
        or migrate_build.get("dockerfile") != EXPECTED_DOCKERFILE_REFERENCES["migrate"]
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
        or backend_build.get("dockerfile") != EXPECTED_DOCKERFILE_REFERENCES["backend"]
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
        or frontend_build.get("dockerfile") != EXPECTED_DOCKERFILE_REFERENCES["frontend"]
        or frontend_build.get("target") != "runtime"
        or frontend_build.get("args") != expected_frontend_args
        or frontend.get("expose") != ["8080"]
        or frontend.get("volumes")
    ):
        raise GuardError("Frontend image must be staging-only and built from the exact PR head")

    nginx = services["nginx"]
    if (
        nginx.get("image") != NGINX_IMAGE
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
    _validate_staging_ci_image(root)
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
