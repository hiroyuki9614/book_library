#!/usr/bin/env python3
"""Validate the owner-approved KAGOYA Compose identity without exposing its config."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path


EXPECTED_PROJECT = "belib-production"
EXPECTED_ACTIVE_SERVICES = {"db", "backend", "frontend", "nginx"}
ALLOWED_SERVICES = EXPECTED_ACTIVE_SERVICES | {"migrate"}
APPROVED_OVERRIDE = Path(".deployment/compose.production.yaml")
PROBE_SHA = "0" * 40


class ComposeIdentityError(RuntimeError):
    """The production Compose target or resolved contract is not approved."""


def _regular_file(path: Path, *, description: str) -> os.stat_result:
    try:
        result = path.lstat()
    except OSError:
        raise ComposeIdentityError(f"The {description} is unavailable") from None
    if not stat.S_ISREG(result.st_mode):
        raise ComposeIdentityError(f"The {description} must be a regular file, not a symlink")
    return result


def _private_directory(path: Path, owner_uid: int, *, description: str) -> os.stat_result:
    try:
        result = path.lstat()
    except OSError:
        raise ComposeIdentityError(f"The {description} is unavailable") from None
    if not stat.S_ISDIR(result.st_mode):
        raise ComposeIdentityError(f"The {description} must be a real directory, not a symlink")
    if result.st_uid != owner_uid or stat.S_IMODE(result.st_mode) & 0o022:
        raise ComposeIdentityError(f"The {description} must be agent-owned and not group/world writable")
    return result


def validate_private_owner_file(path: str | Path, owner_uid: int, *, description: str) -> Path:
    """Require an owner-readable regular file with no group/world access."""
    candidate = Path(path)
    result = _regular_file(candidate, description=description)
    mode = stat.S_IMODE(result.st_mode)
    if result.st_uid != owner_uid:
        raise ComposeIdentityError(f"The {description} must be owned by the production agent")
    if mode & 0o077 or not mode & stat.S_IRUSR or mode & 0o111:
        raise ComposeIdentityError(f"The {description} must be owner-readable and private")
    try:
        return candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ComposeIdentityError(f"The {description} path cannot be resolved") from None


def validate_live_compose_identity(
    *,
    project: str,
    working_dir: str,
    config_files: str,
    agent_uid: int,
) -> dict[str, str]:
    """Accept only the exact live base + protected production overlay pair."""
    if project != EXPECTED_PROJECT:
        raise ComposeIdentityError("The live Compose project is not the approved production project")
    if not Path(working_dir).is_absolute():
        raise ComposeIdentityError("The live Compose project directory must be an absolute path")
    try:
        project_dir = Path(working_dir).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ComposeIdentityError("The live Compose project directory is unavailable") from None
    if not project_dir.is_dir():
        raise ComposeIdentityError("The live Compose project directory is not a directory")
    _private_directory(project_dir, agent_uid, description="production Compose project directory")

    raw_files = [part.strip() for part in config_files.split(",")]
    if len(raw_files) != 2 or any(not part for part in raw_files):
        raise ComposeIdentityError("The live Compose identity must contain exactly two approved files")
    if any(not Path(part).is_absolute() for part in raw_files):
        raise ComposeIdentityError("The live Compose files must use absolute paths")

    base = project_dir / "compose.yaml"
    deployment_dir = project_dir / ".deployment"
    override = project_dir / APPROVED_OVERRIDE
    _private_directory(deployment_dir, agent_uid, description="approved production override directory")
    _regular_file(base, description="production base Compose file")
    override_stat = _regular_file(override, description="production Compose override")
    if override_stat.st_uid != agent_uid:
        raise ComposeIdentityError("The production Compose override must be owned by the production agent")
    mode = stat.S_IMODE(override_stat.st_mode)
    if mode & 0o077 or not mode & stat.S_IRUSR or mode & 0o111:
        raise ComposeIdentityError("The production Compose override must be owner-readable and private")

    try:
        expected = [base.resolve(strict=True), override.resolve(strict=True)]
        supplied = [Path(part).resolve(strict=True) for part in raw_files]
    except (OSError, RuntimeError):
        raise ComposeIdentityError("A live Compose file path cannot be resolved") from None
    if supplied != expected:
        raise ComposeIdentityError("The live Compose files do not match the approved base and override paths")
    return {
        "project_dir": str(project_dir),
        "compose_file": str(expected[0]),
        "compose_override_file": str(expected[1]),
    }


def validate_override_path(config: dict, agent_uid: int) -> Path:
    """Recheck the stored override path and ownership before every Compose call."""
    try:
        project_dir = Path(config["original_project_dir"]).resolve(strict=True)
        expected_path = project_dir / APPROVED_OVERRIDE
        provided_path = Path(config["production_compose_override"])
        expected = expected_path.resolve(strict=True)
        provided = provided_path.resolve(strict=True)
    except (KeyError, OSError, RuntimeError):
        raise ComposeIdentityError("The configured production override path is invalid") from None
    deployment_dir = project_dir / ".deployment"
    _private_directory(project_dir, agent_uid, description="production Compose project directory")
    _private_directory(deployment_dir, agent_uid, description="production override directory")
    if provided != expected:
        raise ComposeIdentityError("The configured production override is outside the approved project directory")
    validate_private_owner_file(provided_path, agent_uid, description="production Compose override")
    return provided


def validate_live_container_compose_labels(labels: dict, config: dict) -> None:
    """Allow the original base file or a SHA-pinned release checkout, always with the same overlay."""
    expected_project = config.get("expected_project")
    if labels.get("com.docker.compose.project") != expected_project:
        raise ComposeIdentityError("The live container belongs to a different Compose project")
    if labels.get("com.docker.compose.service") != "frontend":
        raise ComposeIdentityError("The live container is not the approved frontend service")

    uid = config.get("agent_uid")
    overlay = validate_override_path(config, uid)
    raw_files = [
        part.strip()
        for part in labels.get("com.docker.compose.project.config_files", "").split(",")
    ]
    if len(raw_files) != 2 or any(not part for part in raw_files):
        raise ComposeIdentityError("The live frontend must retain the exact two-file Compose identity")
    if any(not Path(part).is_absolute() for part in raw_files):
        raise ComposeIdentityError("The live frontend Compose files must use absolute paths")
    if not Path(labels.get("com.docker.compose.project.working_dir", "")).is_absolute():
        raise ComposeIdentityError("The live frontend Compose working directory must be absolute")
    try:
        _regular_file(Path(raw_files[0]), description="live frontend base Compose file")
    except ComposeIdentityError:
        raise
    try:
        base = Path(raw_files[0]).resolve(strict=True)
        actual_overlay = Path(raw_files[1]).resolve(strict=True)
        working_dir = Path(labels.get("com.docker.compose.project.working_dir", "")).resolve(strict=True)
        original_base = Path(config["original_compose_file"]).resolve(strict=True)
        original_dir = Path(config["original_project_dir"]).resolve(strict=True)
    except (KeyError, OSError, RuntimeError):
        raise ComposeIdentityError("The live frontend Compose paths are unavailable") from None
    if actual_overlay != overlay or working_dir != base.parent:
        raise ComposeIdentityError("The live frontend Compose paths differ from the approved overlay and project directory")
    _regular_file(base, description="live frontend base Compose file")
    if base != original_base:
        try:
            release_root = Path(config["release_root"]).resolve(strict=True)
            relative = base.relative_to(release_root)
        except (KeyError, OSError, RuntimeError, ValueError):
            raise ComposeIdentityError("The live frontend base file is outside the approved source locations") from None
        if (
            len(relative.parts) != 2
            or not re.fullmatch(r"[0-9a-f]{40}", relative.parts[0])
            or relative.name != "compose.yaml"
        ):
            raise ComposeIdentityError("The live frontend base file is not from an exact-SHA release checkout")
    elif working_dir != original_dir:
        raise ComposeIdentityError("The original live Compose file has an unexpected project directory")


def validate_compose_services(resolved: dict, *, require_migrate: bool = False) -> None:
    services = resolved.get("services") if isinstance(resolved, dict) else None
    if not isinstance(services, dict):
        raise ComposeIdentityError("The resolved production Compose services are missing")
    if any(not isinstance(service, dict) for service in services.values()):
        raise ComposeIdentityError("The resolved production Compose service definition is invalid")
    if not set(services) <= ALLOWED_SERVICES or not EXPECTED_ACTIVE_SERVICES <= set(services):
        raise ComposeIdentityError("The resolved Compose service set differs from the approved production project")
    migrate = services.get("migrate")
    if require_migrate and migrate is None:
        raise ComposeIdentityError("The profiled production migration service is missing")
    if migrate is not None:
        profiles = migrate.get("profiles") if isinstance(migrate, dict) else None
        if profiles == []:
            raise ComposeIdentityError("The migration service is unexpectedly active in the default Compose profile")
        if not isinstance(profiles, list) or any(not isinstance(p, str) or not p for p in profiles):
            raise ComposeIdentityError("The migration service must remain behind a non-active Compose profile")
    active = {
        name
        for name, service in services.items()
        if not isinstance(service, dict) or not service.get("profiles")
    }
    if active != EXPECTED_ACTIVE_SERVICES:
        if "migrate" in active:
            raise ComposeIdentityError("The migration service is unexpectedly active in the default Compose profile")
        raise ComposeIdentityError("The active production Compose services differ from the approved four services")


def _normalize_project_path(value: str, project_dir: Path) -> str:
    try:
        resolved = Path(value).resolve(strict=False)
        relative = resolved.relative_to(project_dir)
    except (OSError, RuntimeError, ValueError):
        return value
    return "$PROJECT/" + relative.as_posix()


def _canonical_bind_source(value: str, project_dir: Path) -> str:
    source = Path(value)
    if not source.is_absolute():
        source = project_dir / source
    return str(source.resolve(strict=False))


def _contract_projection(resolved: dict, project_directory: str | Path) -> dict:
    project_dir = Path(project_directory).resolve(strict=True)
    services = resolved["services"]
    # The migration service is only checked for its non-active profile boundary.
    # Frontend CD neither builds nor runs it, so its other fields are not pinned.
    protected_services = json.loads(
        json.dumps({name: services[name] for name in sorted(set(services) - {"frontend", "migrate"})})
    )
    for service in protected_services.values():
        build = service.get("build")
        if isinstance(build, dict):
            if isinstance(build.get("context"), str):
                build["context"] = _normalize_project_path(build["context"], project_dir)
            args = build.get("args")
            if isinstance(args, dict):
                # This provenance-only arg is dormant for protected services:
                # CD builds and starts only the frontend. Preserve every other
                # build arg and remove an empty map so absent and VCS_REF-only
                # build metadata have the same protected projection.
                args.pop("VCS_REF", None)
                if not args:
                    build.pop("args", None)
        for mount in service.get("volumes", []) or []:
            if isinstance(mount, dict) and mount.get("type") == "bind" and isinstance(mount.get("source"), str):
                mount["source"] = _canonical_bind_source(mount["source"], project_dir)
    return {
        "services": protected_services,
        "top_level": {
            key: resolved[key]
            for key in ("networks", "volumes", "configs", "secrets")
            if key in resolved
        },
    }


def compose_contract_sha256(resolved: dict, project_directory: str | Path) -> str:
    validate_compose_services(resolved)
    serialized = json.dumps(
        _contract_projection(resolved, project_directory), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def validate_candidate_protected_contract(
    resolved: dict,
    project_directory: str | Path,
    approved_effective_sha256: str,
) -> None:
    """Require a candidate's effective protected projection to match bootstrap approval."""
    if not re.fullmatch(r"[0-9a-f]{64}", approved_effective_sha256):
        raise ComposeIdentityError("The stored production Compose contract digest is invalid")
    if compose_contract_sha256(resolved, project_directory) != approved_effective_sha256:
        raise ComposeIdentityError(
            "The candidate changes protected services, database, mounts, or networks"
        )


def validate_frontend_service(
    resolved: dict,
    *,
    checkout: str | Path,
    source_sha: str,
    api_base_url: str,
    expected_networks: list[str],
    expected_contract_sha256: str | None = None,
    legacy_image_ref: str | None = None,
    public_demo_credentials: tuple[str, str] = ("", ""),
) -> None:
    """Keep the frontend build and runtime within its approved no-mount contract."""
    validate_compose_services(resolved)
    if resolved.get("name") not in (None, EXPECTED_PROJECT):
        raise ComposeIdentityError("The resolved Compose project name changed")
    services = resolved["services"]
    frontend = services.get("frontend")
    expected_frontend_fields = {
        "build", "command", "entrypoint", "expose", "image", "logging", "networks", "restart"
    }
    if not isinstance(frontend, dict) or set(frontend) != expected_frontend_fields:
        raise ComposeIdentityError("The frontend Compose service adds unsupported runtime capabilities")
    exact_image = frontend.get("image") == f"belib-frontend:{source_sha}"
    legacy_image = (
        isinstance(legacy_image_ref, str)
        and re.fullmatch(r"belib-frontend:[0-9a-f]{40}", legacy_image_ref) is not None
        and frontend.get("image") == legacy_image_ref
    )
    if not exact_image and not legacy_image:
        raise ComposeIdentityError("The frontend Compose image is not tagged with the exact source SHA")
    if frontend.get("command") is not None or frontend.get("entrypoint") is not None:
        raise ComposeIdentityError("The frontend Compose service may not override the reviewed image command")
    if frontend.get("expose") != ["8080"] or frontend.get("restart") != "unless-stopped":
        raise ComposeIdentityError("The frontend Compose runtime contract changed")
    if frontend.get("logging") != {
        "driver": "json-file",
        "options": {"max-file": "3", "max-size": "10m"},
    }:
        raise ComposeIdentityError("The frontend Compose logging contract changed")

    build = frontend.get("build")
    if not isinstance(build, dict) or set(build) != {"args", "context", "dockerfile", "target"}:
        raise ComposeIdentityError("The frontend build adds unsupported build capabilities")
    checkout_root = Path(checkout).resolve(strict=True)
    frontend_context = checkout_root / "frontend"
    if frontend_context.is_symlink() or not frontend_context.is_dir():
        raise ComposeIdentityError("The frontend build context must be a real directory inside the isolated checkout")
    dockerfile_path = frontend_context / "Dockerfile"
    if dockerfile_path.is_symlink() or not dockerfile_path.is_file():
        raise ComposeIdentityError("The frontend Dockerfile must be a regular file inside the isolated checkout")
    try:
        resolved_context = Path(str(build.get("context", ""))).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ComposeIdentityError("The frontend build context could not be resolved inside the isolated checkout") from None
    if (
        resolved_context != frontend_context
        or build.get("dockerfile") != "Dockerfile"
        or build.get("target") != "runtime"
    ):
        raise ComposeIdentityError("The frontend build must use only the reviewed frontend runtime context")
    args = build.get("args")
    if legacy_image:
        if not isinstance(args, dict) or set(args) != {"VITE_API_BASE_URL"}:
            raise ComposeIdentityError("The legacy frontend override build arguments differ from the approved bootstrap shape")
        if args.get("VITE_API_BASE_URL") != api_base_url:
            raise ComposeIdentityError("The frontend build arguments differ from the operator-approved values")
    else:
        if not isinstance(args, dict) or set(args) != {
            "VCS_REF", "VITE_API_BASE_URL", "VITE_PUBLIC_DEMO_EMAIL", "VITE_PUBLIC_DEMO_PASSWORD"
        }:
            raise ComposeIdentityError("The frontend build argument set differs from the approved contract")
        if (
            not isinstance(public_demo_credentials, tuple)
            or len(public_demo_credentials) != 2
            or any(type(value) is not str for value in public_demo_credentials)
        ):
            raise ComposeIdentityError("The frontend public demo credential shape is invalid")
        actual_email = args.get("VITE_PUBLIC_DEMO_EMAIL")
        actual_password = args.get("VITE_PUBLIC_DEMO_PASSWORD")
        if public_demo_credentials[0] == "" and actual_email is None:
            actual_email = ""
        if public_demo_credentials[1] == "" and actual_password is None:
            actual_password = ""
        if (
            args.get("VCS_REF") != source_sha
            or args.get("VITE_API_BASE_URL") != api_base_url
            or actual_email != public_demo_credentials[0]
            or actual_password != public_demo_credentials[1]
        ):
            raise ComposeIdentityError("The frontend build arguments differ from the operator-approved values")

    network_refs = frontend.get("networks")
    declared_networks = resolved.get("networks")
    if not isinstance(network_refs, dict) or not isinstance(declared_networks, dict):
        raise ComposeIdentityError("The resolved frontend network identity is missing")
    if any(value not in (None, {}) for value in network_refs.values()):
        raise ComposeIdentityError("The frontend Compose network options may not change")
    actual_networks = []
    for name in network_refs:
        network = declared_networks.get(name)
        if not isinstance(network, dict) or not isinstance(network.get("name"), str):
            raise ComposeIdentityError("A frontend Compose network has no resolved identity")
        actual_networks.append(network["name"])
    if sorted(actual_networks) != sorted(expected_networks):
        raise ComposeIdentityError("The candidate frontend networks differ from the approved production target")
    if expected_contract_sha256 is not None:
        validate_candidate_protected_contract(resolved, checkout, expected_contract_sha256)


def _run_json(argv: list[str], env: dict[str, str]) -> dict | list:
    try:
        result = subprocess.run(argv, check=False, capture_output=True, text=True, env=env)
    except OSError:
        raise ComposeIdentityError("A required Docker command could not start") from None
    if result.returncode:
        raise ComposeIdentityError("The approved production Compose configuration could not be resolved")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise ComposeIdentityError("The resolved production Compose config is invalid") from None


def _compose_config(
    *,
    project: str,
    project_dir: Path,
    compose_files: list[Path],
    env_file: Path,
    env: dict[str, str],
    include_all_profiles: bool = False,
) -> dict:
    command = ["docker", "compose", "--project-name", project, "--project-directory", str(project_dir)]
    for compose_file in compose_files:
        command.extend(["--file", str(compose_file)])
    command.extend(["--env-file", str(env_file)])
    if include_all_profiles:
        command.extend(["--profile", "*"])
    command.extend(["config", "--format", "json"])
    resolved = _run_json(command, env)
    if not isinstance(resolved, dict):
        raise ComposeIdentityError("The resolved production Compose config has an invalid shape")
    return resolved


def bootstrap_preflight(container_name: str, production_env_file: str, *, agent_uid: int | None = None) -> dict:
    """Read live labels and validate the exact protected two-file config without printing it."""
    uid = os.getuid() if agent_uid is None else agent_uid
    env_file = validate_private_owner_file(
        production_env_file, uid, description="production env file"
    )
    try:
        inspected = _run_json(["docker", "inspect", container_name], os.environ.copy())
    except ComposeIdentityError:
        raise
    if not isinstance(inspected, list) or len(inspected) != 1:
        raise ComposeIdentityError("The approved production frontend container is not unique")
    item = inspected[0]
    labels = item.get("Config", {}).get("Labels") or {}
    if (
        item.get("Name", "").lstrip("/") != container_name
        or container_name != "belib-production-frontend-1"
        or labels.get("com.docker.compose.service") != "frontend"
    ):
        raise ComposeIdentityError("The container name/service does not match the approved production frontend")
    identity = validate_live_compose_identity(
        project=labels.get("com.docker.compose.project", ""),
        working_dir=labels.get("com.docker.compose.project.working_dir", ""),
        config_files=labels.get("com.docker.compose.project.config_files", ""),
        agent_uid=uid,
    )
    if item.get("State", {}).get("Running") is not True:
        raise ComposeIdentityError("The production frontend is stopped")
    networks = sorted((item.get("NetworkSettings", {}).get("Networks") or {}).keys())
    if (
        not networks
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("Image", ""))
        or not item.get("Config", {}).get("Image")
    ):
        raise ComposeIdentityError("The production frontend network/image identity is incomplete")

    clean_env = os.environ.copy()
    clean_env.pop("VITE_API_BASE_URL", None)
    clean_env.pop("COMPOSE_PROFILES", None)
    clean_env.update(
        {
            "COMPOSE_PROFILES": "",
            "VCS_REF": PROBE_SHA,
            "BELIB_FRONTEND_IMAGE": f"belib-frontend:{PROBE_SHA}",
            "VITE_PUBLIC_DEMO_EMAIL": "",
            "VITE_PUBLIC_DEMO_PASSWORD": "",
        }
    )
    project_dir = Path(identity["project_dir"])
    compose_file = Path(identity["compose_file"])
    override_file = Path(identity["compose_override_file"])
    base = _compose_config(
        project=EXPECTED_PROJECT,
        project_dir=project_dir,
        compose_files=[compose_file],
        env_file=env_file,
        env=clean_env,
    )
    effective = _compose_config(
        project=EXPECTED_PROJECT,
        project_dir=project_dir,
        compose_files=[compose_file, override_file],
        env_file=env_file,
        env=clean_env,
    )
    effective_all_profiles = _compose_config(
        project=EXPECTED_PROJECT,
        project_dir=project_dir,
        compose_files=[compose_file, override_file],
        env_file=env_file,
        env=clean_env,
        include_all_profiles=True,
    )
    validate_compose_services(base)
    validate_compose_services(effective)
    validate_compose_services(effective_all_profiles, require_migrate=True)
    # The owner-approved two-file result is the contract. The base-only config
    # is checked for supported services, but it is not a protected baseline:
    # the private overlay may legitimately define the production services.
    contract_digest = compose_contract_sha256(effective, project_dir)
    try:
        api_base_url = effective["services"]["frontend"]["build"]["args"]["VITE_API_BASE_URL"]
    except (KeyError, TypeError):
        raise ComposeIdentityError("The approved production API base URL is missing") from None
    if not isinstance(api_base_url, str) or not api_base_url:
        raise ComposeIdentityError("The approved production API base URL is missing")
    base_api = base.get("services", {}).get("frontend", {}).get("build", {}).get("args", {}).get("VITE_API_BASE_URL")
    if base_api != api_base_url:
        raise ComposeIdentityError("The production override may not change the approved API base URL")
    validate_frontend_service(
        effective,
        checkout=project_dir,
        source_sha=PROBE_SHA,
        api_base_url=api_base_url,
        expected_networks=networks,
        expected_contract_sha256=contract_digest,
        # A legacy tag is accepted only as a match for the currently running
        # container's mutable reference. It is never treated as source proof;
        # the image ID above remains the bootstrap rollback identity.
        legacy_image_ref=item.get("Config", {}).get("Image"),
    )
    frontend_args = effective["services"]["frontend"]["build"]["args"]
    bootstrap_mode = (
        "legacy-override-update-required"
        if "VCS_REF" not in frontend_args
        else "exact-sha"
    )
    return {
        **identity,
        "project": EXPECTED_PROJECT,
        "networks": networks,
        "image_id": item["Image"],
        "expected_api_base_url": api_base_url,
        "compose_contract_sha256": contract_digest,
        "frontend_bootstrap_mode": bootstrap_mode,
    }


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: kagoya_compose.py CONTAINER_NAME PRODUCTION_ENV_FILE", file=sys.stderr)
        return 2
    try:
        safe_metadata = bootstrap_preflight(sys.argv[1], sys.argv[2])
    except (ComposeIdentityError, OSError, KeyError, ValueError) as error:
        print(f"BeLib KAGOYA preflight failed closed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(safe_metadata, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
