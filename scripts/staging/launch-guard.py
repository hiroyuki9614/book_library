#!/usr/bin/env -S /usr/bin/python3 -I -B
"""Gate every i5 staging Compose operation on the same reviewed source and handoff."""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import pwd
import re
import socket
import stat
import subprocess
import sys
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
SOURCE_SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")

OPERATION_ARGS: dict[str, tuple[str, ...]] = {
    "start-db": ("up", "-d", "db"),
    "migrate": ("--profile", "migration", "run", "--rm", "migrate"),
    "start-app": ("up", "-d", "--build", "backend", "frontend", "nginx"),
    "status": ("ps",),
}


class LaunchBlocked(Exception):
    pass


def load_seed_module():
    path = Path(__file__).with_name("seed.py")
    spec = importlib.util.spec_from_file_location("belib_staging_seed", path)
    if spec is None or spec.loader is None:
        raise LaunchBlocked("The guarded staging seed wrapper is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    seed = load_seed_module()
    try:
        seed.reject_ambient_overrides(environment)
    except seed.SeedWrapperError as error:
        raise LaunchBlocked(str(error)) from error


def validate_handoff_receipt(receipt: object, source_sha: str) -> str:
    """Reject local claims until an independent supervisor verifier is configured."""
    if not SOURCE_SHA_PATTERN.fullmatch(source_sha):
        raise LaunchBlocked("Source revision must be a full lowercase Git SHA")
    _ = receipt
    raise LaunchBlocked(
        "No trusted independent staging supervisor verifier is configured; local receipt claims cannot authorize staging"
    )


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


def _checked_repo_branch(seed, repo_root: Path) -> None:
    result = _run(
        seed.git_command(repo_root, "branch", "--show-current"),
        environment=seed.git_environment(),
    )
    if result.returncode != 0 or (result.stdout or "").strip() != EXPECTED_BRANCH:
        raise LaunchBlocked("Checked-out staging branch differs from the existing PR #103 claim")


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
        verify_python_runtime()
        reject_caller_environment(os.environ)
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--source-sha", required=True)
        parser.add_argument("--env-file", type=Path, required=True)
        parser.add_argument("operation", choices=(*OPERATION_ARGS, "seed"))
        args = parser.parse_args(argv)

        account = pwd.getpwuid(os.getuid())
        if (
            account.pw_name != EXPECTED_USER
            or account.pw_uid != EXPECTED_UID
            or os.geteuid() != EXPECTED_UID
            or socket.gethostname() != EXPECTED_HOST
        ):
            raise LaunchBlocked("Run only as hiroyuki UID 1000 on ubuntu-worker")
        repo_root = Path(__file__).resolve(strict=True).parents[2]
        if repo_root != EXPECTED_REPO_ROOT:
            raise LaunchBlocked("Use only the existing i5 staging checkout")
        if args.env_file != EXPECTED_HOME / ".config/belib-staging/i5.env":
            raise LaunchBlocked("Use only the outside-repository i5 staging env file")

        validate_handoff_receipt(None, args.source_sha)

        seed = load_seed_module()
        _checked_repo_branch(seed, repo_root)

        with project_lock():
            env_snapshot_fd = -1
            docker_config_temporary = None
            try:
                source_guard = load_source_tree_guard(repo_root)
                try:
                    with source_guard.sha_derived_build_snapshot(repo_root, args.source_sha) as build_root:
                        try:
                            seed.validate_build_snapshot(build_root, repo_root, args.source_sha)
                        except seed.SeedWrapperError as error:
                            raise LaunchBlocked(str(error)) from error

                        _run_preflight(repo_root, args.env_file)
                        try:
                            env_snapshot_fd, values = seed.validate_staging_admission(
                                repo_root, args.env_file, EXPECTED_UID
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
                        try:
                            seed.verify_exact_source_context(repo_root, args.source_sha)
                            seed.assert_clean_source_worktree(repo_root)
                        except seed.SeedWrapperError as error:
                            raise LaunchBlocked(str(error)) from error

                        _validate_compose_model(
                            build_root,
                            env_snapshot_fd,
                            values,
                            docker_environment,
                            args.source_sha,
                        )
                        _checked_repo_branch(seed, repo_root)
                        try:
                            seed.verify_exact_source_context(repo_root, args.source_sha)
                            seed.assert_clean_source_worktree(repo_root)
                        except seed.SeedWrapperError as error:
                            raise LaunchBlocked(str(error)) from error

                        if args.operation == "seed":
                            return seed.main(
                                invoked_by_launcher=True,
                                build_root=build_root,
                                source_sha=args.source_sha,
                            )

                        env_path = Path(f"/proc/{os.getpid()}/fd/{env_snapshot_fd}")
                        command = build_compose_command(
                            args.operation,
                            repo_root=build_root,
                            env_file=env_path,
                        )
                        result = subprocess.run(
                            command,
                            cwd=build_root,
                            env=docker_environment,
                            stdin=subprocess.DEVNULL,
                            check=False,
                            pass_fds=(env_snapshot_fd,),
                        )
                        if result.returncode != 0:
                            print("BLOCKED: Guarded staging Compose operation failed", file=sys.stderr)
                        return result.returncode
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
