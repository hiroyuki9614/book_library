#!/usr/bin/env -S /usr/bin/python3 -I -B
"""Gate the bounded i5 staging deployment on one signed external authorization."""

from __future__ import annotations

import argparse
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
SOURCE_SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")

OPERATION_ARGS: dict[str, tuple[str, ...]] = {
    "start-db": ("up", "-d", "db"),
    "migrate": ("--profile", "migration", "run", "--rm", "migrate"),
    "start-app": ("up", "-d", "--build", "backend", "frontend", "nginx"),
    "status": ("ps",),
}


class LaunchBlocked(Exception):
    pass


def load_approval_module():
    path = Path(__file__).with_name("approval.py")
    spec = importlib.util.spec_from_file_location("belib_staging_approval", path)
    if spec is None or spec.loader is None:
        raise LaunchBlocked("The signed staging approval verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    def phase(name: str, command: list[str], timeout: int = 900) -> None:
        report(f"STAGING_PHASE_START {name}", file=sys.stderr)
        result = runner(command, cwd=build_root, env=dict(environment), stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                        pass_fds=(env_fd,), timeout=timeout)
        if result.returncode != 0:
            report(f"STAGING_PHASE_FAIL {name}", file=sys.stderr)
            raise LaunchBlocked(f"Staging phase failed: {name}")
        completed.append(name)
        report(f"STAGING_PHASE_PASS {name}", file=sys.stderr)

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
                    report("STAGING_PHASE_PASS db-healthy", file=sys.stderr)
                    return
            except (json.JSONDecodeError, AttributeError):
                pass
            sleep(2)
        report("STAGING_PHASE_FAIL db-health", file=sys.stderr)
        raise LaunchBlocked("Staging phase timed out: db-health")

    try:
        db_start_attempted = True
        phase("db-start", [*compose, "up", "-d", "db"], 300)
        wait_db()
        phase("migration", [*compose, "--profile", "migration", "run", "--rm", "migrate"])
        report("STAGING_PHASE_START synthetic-seed", file=sys.stderr)
        if seed.main(
            invoked_by_launcher=True, build_root=build_root, source_sha=source_sha,
            admitted_env_fd=env_fd, stage_values=stage_values, docker_cli=DOCKER_CLI,
            docker_endpoint=docker_endpoint, docker_environment=environment,
        ) != 0:
            report("STAGING_PHASE_FAIL synthetic-seed", file=sys.stderr)
            raise LaunchBlocked("Staging phase failed: synthetic-seed")
        completed.append("synthetic-seed")
        report("STAGING_PHASE_PASS synthetic-seed", file=sys.stderr)
        phase("app-start", [*compose, "up", "-d", "--build", "backend", "frontend", "nginx"], 1800)
        deadline = monotonic() + 120
        health_url = f"http://127.0.0.1:{port}/health"
        while monotonic() < deadline:
            result = runner(["/usr/bin/curl", "--fail", "--silent", "--show-error", "--max-time", "5", health_url],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            check=False, timeout=7, env={"PATH": SAFE_PATH, "HOME": str(EXPECTED_HOME)})
            if result.returncode == 0:
                completed.append("http-health")
                report("STAGING_PHASE_PASS http-health", file=sys.stderr)
                report("STAGING_TRANSACTION_PASS", file=sys.stderr)
                return 0
            sleep(2)
        report("STAGING_PHASE_FAIL http-health", file=sys.stderr)
        raise LaunchBlocked("Staging phase timed out: http-health")
    except (Exception, KeyboardInterrupt) as error:
        report("STAGING_TRANSACTION_FAILED phases=" + ",".join(completed), file=sys.stderr)
        if db_start_attempted:
            try:
                rollback = runner([*compose, "stop", "nginx", "frontend", "backend", "db"], cwd=build_root,
                                  env=dict(environment), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, check=False, pass_fds=(env_fd,), timeout=120)
                if rollback.returncode == 0:
                    report("STAGING_ROLLBACK_PASS stopped-project-services-volumes-preserved", file=sys.stderr)
                else:
                    report("STAGING_ROLLBACK_FAILED manual-review-required", file=sys.stderr)
            except (OSError, subprocess.SubprocessError):
                report("STAGING_ROLLBACK_FAILED manual-review-required", file=sys.stderr)
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


def _checked_repo_branch(seed, repo_root: Path) -> None:
    result = _run(
        seed.git_command(repo_root, "branch", "--show-current"),
        environment=seed.git_environment(),
    )
    if result.returncode != 0 or (result.stdout or "").strip() != EXPECTED_BRANCH:
        raise LaunchBlocked("Checked-out staging branch differs from the existing PR #103 claim")


def _admission_source_root(seed, checkout: Path, source_sha: str) -> Path:
    """Select only the fixed-name detached clean worktree sharing this checkout's Git store."""
    if not SOURCE_SHA_PATTERN.fullmatch(source_sha):
        raise LaunchBlocked("Source revision must be a full lowercase Git SHA")
    candidate = ADMISSION_WORKTREE_PREFIX.parent / f"{ADMISSION_WORKTREE_PREFIX.name}{source_sha[:7]}"
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
    try:
        head = seed.read_source_sha(candidate)
        candidate_common = _run(
            seed.git_command(candidate, "rev-parse", "--path-format=absolute", "--git-common-dir"),
            environment=seed.git_environment(), timeout=5,
        )
        checkout_common = _run(
            seed.git_command(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir"),
            environment=seed.git_environment(), timeout=5,
        )
        detached = _run(
            seed.git_command(candidate, "symbolic-ref", "-q", "HEAD"),
            environment=seed.git_environment(), timeout=5,
        )
        if head != source_sha or candidate_common.returncode != 0 or checkout_common.returncode != 0:
            raise LaunchBlocked("The detached admission worktree does not match the selected SHA and Git store")
        if candidate_common.stdout.strip() != checkout_common.stdout.strip() or detached.returncode == 0:
            raise LaunchBlocked("The admission source must be a detached worktree of the claimed repository")
    except seed.SeedWrapperError as error:
        raise LaunchBlocked("The detached admission worktree failed exact-source checks") from error
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
        verify_python_runtime()
        reject_caller_environment(os.environ)
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--source-sha", required=True)
        parser.add_argument("--env-file", type=Path, required=True)
        parser.add_argument("operation", choices=("deploy",))
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

        seed = load_seed_module()
        _checked_repo_branch(seed, repo_root)

        with project_lock():
            approval = load_approval_module()
            try:
                approval.request_and_wait(args.source_sha, "deploy", uid=EXPECTED_UID, output=sys.stdout)
            except approval.ApprovalError as error:
                raise LaunchBlocked(str(error)) from error
            source_root = _admission_source_root(seed, repo_root, args.source_sha)
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
