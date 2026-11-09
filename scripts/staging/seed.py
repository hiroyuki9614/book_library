#!/usr/bin/env -S /usr/bin/python3 -I -B
"""Run the candidate staging seed with its private credential file on stdin."""

from __future__ import annotations

import fcntl
import json
import os
import pwd
import selectors
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Mapping


EXPECTED_HOSTNAME = "ubuntu-worker"
COMPOSE_PROJECT = "belib-isolated-i5"
GIT_CLI = "/usr/bin/git"
DOCKER_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
DOCKER_INFO_TIMEOUT_SECONDS = 10
DOCKER_INFO_MAX_OUTPUT_BYTES = 4096
SOURCE_CHECK_TIMEOUT_SECONDS = 5
ENV_FILE_MAX_BYTES = 1024
DOCKER_CLI = "/usr/bin/docker"
DOCKER_PLUGIN_DIR = Path("/usr/libexec/docker/cli-plugins")
DOCKER_PLUGIN_SEARCH_DIRS = (
    Path("/usr/local/lib/docker/cli-plugins"),
    Path("/usr/local/libexec/docker/cli-plugins"),
    Path("/usr/lib/docker/cli-plugins"),
    DOCKER_PLUGIN_DIR,
)
DOCKER_PLUGIN_PARENTS = (
    Path("/usr"),
    Path("/usr/bin"),
    Path("/usr/local"),
    Path("/usr/local/lib"),
    Path("/usr/local/lib/docker"),
    Path("/usr/local/lib/docker/cli-plugins"),
    Path("/usr/local/libexec"),
    Path("/usr/local/libexec/docker"),
    Path("/usr/local/libexec/docker/cli-plugins"),
    Path("/usr/lib"),
    Path("/usr/lib/docker"),
    Path("/usr/lib/docker/cli-plugins"),
    Path("/usr/libexec"),
    Path("/usr/libexec/docker"),
    DOCKER_PLUGIN_DIR,
)
STAGING_ENV_KEYS = frozenset(
    {
        "BELIB_STAGE_DB_BOOTSTRAP_PASSWORD",
        "BELIB_STAGE_DB_APP_PASSWORD",
        "BELIB_STAGE_DB_MIGRATE_PASSWORD",
        "BELIB_STAGE_AUTH_SECRET",
        "BELIB_STAGE_VCS_REF",
        "BELIB_STAGE_PORT",
    }
)
PYTHON_STARTUP_OVERRIDES = frozenset(
    {
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONUSERBASE",
        "PYTHONSTARTUP",
        "PYTHONINSPECT",
        "PYTHONWARNINGS",
    }
)


class SeedWrapperError(Exception):
    pass


def _open_regular_file_without_symlinks(
    path: str | os.PathLike[str],
    expected_uid: int,
    *,
    allowed_modes: frozenset[int],
    maximum_size: int,
    description: str,
    mode_error: str,
) -> int:
    """Open an owner-controlled regular file without following any path symlink."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise SeedWrapperError(f"This host cannot securely open the {description}")

    file_path = Path(path)
    parts = file_path.parts
    if not file_path.is_absolute() or len(parts) < 2 or any(part in (".", "..") for part in parts[1:]):
        raise SeedWrapperError(f"The {description} path must be absolute and contain no traversal")

    directory_fd = -1
    file_fd = -1
    try:
        directory_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        for component in parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise SeedWrapperError(f"The {description} path must be a regular file")
        if metadata.st_uid != expected_uid:
            raise SeedWrapperError(f"The {description} must be owned by hiroyuki")
        if stat.S_IMODE(metadata.st_mode) not in allowed_modes:
            raise SeedWrapperError(mode_error)
        if metadata.st_size > maximum_size:
            raise SeedWrapperError(f"The {description} is too large")
        return file_fd
    except SeedWrapperError:
        if file_fd >= 0:
            os.close(file_fd)
        raise
    except OSError as error:
        if file_fd >= 0:
            os.close(file_fd)
        raise SeedWrapperError(f"The {description} path must be a real file with no symlinks") from error
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)


def open_private_regular_file(path: str | os.PathLike[str], expected_uid: int) -> int:
    """Open the seed credentials as a mode-0600 regular file without symlinks."""
    return _open_regular_file_without_symlinks(
        path,
        expected_uid,
        allowed_modes=frozenset({0o600}),
        maximum_size=256,
        description="staging seed credentials file",
        mode_error="The staging seed credentials file must have mode 0600",
    )


def open_staging_env_file(path: str | os.PathLike[str], expected_uid: int) -> int:
    """Open the host-only staging env file under the committed preflight policy."""
    return _open_regular_file_without_symlinks(
        path,
        expected_uid,
        allowed_modes=frozenset({0o400, 0o600}),
        maximum_size=ENV_FILE_MAX_BYTES,
        description="staging env file",
        mode_error="The staging env file must have mode 0600 or 0400",
    )


def read_staging_env_file(env_fd: int) -> tuple[bytes, dict[str, str]]:
    """Read and validate the exact bounded staging env format without logging values."""
    try:
        before = os.fstat(env_fd)
        os.lseek(env_fd, 0, os.SEEK_SET)
        chunks = []
        total_bytes = 0
        while total_bytes <= ENV_FILE_MAX_BYTES:
            chunk = os.read(env_fd, ENV_FILE_MAX_BYTES + 1 - total_bytes)
            if not chunk:
                break
            chunks.append(chunk)
            total_bytes += len(chunk)
        contents = b"".join(chunks)
        after = os.fstat(env_fd)
        os.lseek(env_fd, 0, os.SEEK_SET)
    except OSError as error:
        raise SeedWrapperError("Could not safely read the staging env file") from error

    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_uid,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_uid,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if before_identity != after_identity or len(contents) != after.st_size:
        raise SeedWrapperError("The staging env file changed while it was being checked")
    if len(contents) > ENV_FILE_MAX_BYTES:
        raise SeedWrapperError("The staging env file is too large")

    try:
        text = contents.decode("ascii")
    except UnicodeDecodeError as error:
        raise SeedWrapperError("The staging env file has an invalid format") from error

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) != len(STAGING_ENV_KEYS) or any(not line or "\r" in line for line in lines):
        raise SeedWrapperError("The staging env file must contain exactly the six staging keys")

    values: dict[str, str] = {}
    for line in lines:
        if line.count("=") != 1:
            raise SeedWrapperError("The staging env file has an invalid format")
        name, value = line.split("=", 1)
        if name not in STAGING_ENV_KEYS or name in values or not value:
            raise SeedWrapperError("The staging env file contains an unsupported or duplicate key")
        values[name] = value

    if set(values) != STAGING_ENV_KEYS:
        raise SeedWrapperError("The staging env file must contain exactly the six staging keys")

    database_passwords = (
        values["BELIB_STAGE_DB_BOOTSTRAP_PASSWORD"],
        values["BELIB_STAGE_DB_APP_PASSWORD"],
        values["BELIB_STAGE_DB_MIGRATE_PASSWORD"],
    )
    auth_secret = values["BELIB_STAGE_AUTH_SECRET"]
    revision = values["BELIB_STAGE_VCS_REF"]
    port = values["BELIB_STAGE_PORT"]
    if (
        any(
            len(password) != 64 or any(character not in "0123456789abcdef" for character in password)
            for password in database_passwords
        )
        or len(auth_secret) != 64
        or any(character not in "0123456789abcdef" for character in auth_secret)
        or len(revision) != 40
        or any(character not in "0123456789abcdef" for character in revision)
        or len(port) not in (4, 5)
        or not port.isascii()
        or not port.isdigit()
        or not 1024 <= int(port, 10) <= 65535
    ):
        raise SeedWrapperError("The staging env file contains an invalid staging value")
    if len(set((*database_passwords, auth_secret))) != 4:
        raise SeedWrapperError("Bootstrap, app, migration, and Better Auth secrets must all be distinct")

    return contents, values


def create_sealed_env_snapshot(contents: bytes) -> int:
    """Create an immutable descriptor for Compose so path replacement cannot change values."""
    required_names = (
        "memfd_create",
        "MFD_ALLOW_SEALING",
    )
    seal_names = (
        "F_ADD_SEALS",
        "F_DUPFD_CLOEXEC",
        "F_GET_SEALS",
        "F_SEAL_SEAL",
        "F_SEAL_SHRINK",
        "F_SEAL_GROW",
        "F_SEAL_WRITE",
    )
    if any(not hasattr(os, name) for name in required_names) or any(not hasattr(fcntl, name) for name in seal_names):
        raise SeedWrapperError("This host cannot safely snapshot the staging env file")

    snapshot_fd = -1
    try:
        flags = getattr(os, "MFD_CLOEXEC", 0) | os.MFD_ALLOW_SEALING
        snapshot_fd = os.memfd_create("belib-i5-staging-env", flags=flags)
        written = 0
        while written < len(contents):
            byte_count = os.write(snapshot_fd, contents[written:])
            if byte_count <= 0:
                raise SeedWrapperError("Could not safely snapshot the staging env file")
            written += byte_count

        required_seals = fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE
        fcntl.fcntl(snapshot_fd, fcntl.F_ADD_SEALS, required_seals)
        if fcntl.fcntl(snapshot_fd, fcntl.F_GET_SEALS) & required_seals != required_seals:
            raise SeedWrapperError("The staging env snapshot could not be sealed")
        if snapshot_fd <= 2:
            duplicated_fd = fcntl.fcntl(snapshot_fd, fcntl.F_DUPFD_CLOEXEC, 3)
            os.close(snapshot_fd)
            snapshot_fd = duplicated_fd
        os.lseek(snapshot_fd, 0, os.SEEK_SET)
        return snapshot_fd
    except SeedWrapperError:
        if snapshot_fd >= 0:
            os.close(snapshot_fd)
        raise
    except OSError as error:
        if snapshot_fd >= 0:
            os.close(snapshot_fd)
        raise SeedWrapperError("Could not safely snapshot the staging env file") from error


def git_environment() -> dict[str, str]:
    """Keep Git admission checks independent of ambient Git configuration and overrides."""
    return {
        "PATH": DOCKER_PATH,
        "HOME": "/nonexistent",
        "LC_ALL": "C",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
    }


def verify_trusted_git_cli(*, lstat=None) -> None:
    """Pin Git admission to the root-owned system binary and its protected parents."""
    inspect = os.lstat if lstat is None else lstat
    for directory in (Path("/usr"), Path("/usr/bin")):
        try:
            metadata = inspect(directory)
        except OSError as error:
            raise SeedWrapperError("Trusted Git directories are unavailable") from error
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o7022
        ):
            raise SeedWrapperError("Trusted Git directories must be root-owned and non-writable")

    try:
        metadata = inspect(GIT_CLI)
    except OSError as error:
        raise SeedWrapperError("Trusted /usr/bin/git is unavailable") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o7022
        or not stat.S_IMODE(metadata.st_mode) & 0o111
    ):
        raise SeedWrapperError("Trusted /usr/bin/git must be a root-owned non-writable executable")


def git_command(repo_root: Path, *arguments: str) -> list[str]:
    return [
        GIT_CLI,
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "core.splitIndex=false",
        "-C",
        str(repo_root),
        *arguments,
    ]


def read_source_sha(repo_root: Path, *, runner=None) -> str:
    """Ask Git for the commit currently checked out in the source directory."""
    verify_trusted_git_cli()
    run = subprocess.run if runner is None else runner
    try:
        result = run(
            git_command(repo_root, "rev-parse", "--verify", "HEAD^{commit}"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=SOURCE_CHECK_TIMEOUT_SECONDS,
            env=git_environment(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SeedWrapperError("Could not independently verify the staging source revision") from error

    source_sha = (result.stdout or "").strip()
    if (
        result.returncode != 0
        or len(source_sha) != 40
        or any(character not in "0123456789abcdef" for character in source_sha)
    ):
        raise SeedWrapperError("Could not independently verify the staging source revision")
    return source_sha


def verify_exact_source_context(repo_root: Path, source_sha: str, *, runner=None) -> None:
    """Reject source bytes or effective Docker inputs outside the reviewed Git tree."""
    run = subprocess.run if runner is None else runner
    guard = Path(__file__).with_name("source-tree-guard.py")
    environment = {
        "HOME": "/nonexistent",
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "LC_ALL": "C",
    }
    try:
        result = run(
            [
                sys.executable,
                "-I",
                "-B",
                str(guard),
                "--repo-root",
                str(repo_root),
                "--source-sha",
                source_sha,
            ],
            cwd=repo_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=SOURCE_CHECK_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SeedWrapperError("Could not verify exact staging build inputs") from error
    if result.returncode != 0:
        raise SeedWrapperError("Staging source or Docker build inputs differ from the reviewed Git tree")


def assert_clean_source_worktree(repo_root: Path, *, popen=None, selector_factory=None) -> None:
    """Reject tracked and untracked changes while reading at most one status byte."""
    verify_trusted_git_cli()
    spawn = subprocess.Popen if popen is None else popen
    make_selector = selectors.DefaultSelector if selector_factory is None else selector_factory
    process = None
    selector = None
    try:
        process = spawn(
            git_command(
                repo_root,
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--ignore-submodules=none",
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=git_environment(),
            close_fds=True,
        )
        if process.stdout is None:
            raise SeedWrapperError("Could not verify that the staging source worktree is clean")

        selector = make_selector()
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(SOURCE_CHECK_TIMEOUT_SECONDS):
            raise SeedWrapperError("Could not verify that the staging source worktree is clean")
        if process.stdout.read(1):
            raise SeedWrapperError("The staging source worktree must be clean")
        try:
            return_code = process.wait(timeout=SOURCE_CHECK_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            raise SeedWrapperError("Could not verify that the staging source worktree is clean") from error
        if return_code != 0:
            raise SeedWrapperError("Could not verify that the staging source worktree is clean")
    except SeedWrapperError:
        raise
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise SeedWrapperError("Could not verify that the staging source worktree is clean") from error
    finally:
        if selector is not None:
            selector.close()
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=SOURCE_CHECK_TIMEOUT_SECONDS)
            except (OSError, subprocess.SubprocessError):
                pass
            if process.stdout is not None:
                process.stdout.close()


def verify_reviewed_source(
    repo_root: Path,
    reviewed_sha: str,
    *,
    git_runner=None,
    popen=None,
    selector_factory=None,
) -> None:
    source_sha = read_source_sha(repo_root, runner=git_runner)
    if source_sha != reviewed_sha:
        raise SeedWrapperError("Checked-out Git HEAD differs from the staging env revision")
    verify_exact_source_context(repo_root, reviewed_sha)
    assert_clean_source_worktree(repo_root, popen=popen, selector_factory=selector_factory)


def validate_staging_admission(repo_root: Path, env_file: Path, expected_uid: int) -> tuple[int, dict[str, str]]:
    """Validate source and host-only env provenance before credentials may be opened."""
    env_fd = open_staging_env_file(env_file, expected_uid)
    try:
        contents, values = read_staging_env_file(env_fd)
        verify_reviewed_source(repo_root, values["BELIB_STAGE_VCS_REF"])
        return create_sealed_env_snapshot(contents), values
    finally:
        os.close(env_fd)


def build_compose_command(repo_root: Path, env_file: Path, endpoint: Path) -> list[str]:
    return [
        "docker",
        "--host",
        f"unix://{endpoint}",
        "compose",
        "-p",
        COMPOSE_PROJECT,
        "--project-directory",
        str(repo_root),
        "--env-file",
        str(env_file),
        "-f",
        str(repo_root / "compose.staging.yaml"),
        "run",
        "--rm",
        "--no-deps",
        "--build",
        "--user",
        "node",
        "-i",
        "-T",
        "backend",
        "node",
        "dist/scripts/seed-staging.js",
    ]


def reject_ambient_overrides(environment: Mapping[str, str]) -> None:
    """Reject inherited settings that can redirect Compose or leak runtime credentials."""
    forbidden_prefixes = (
        "DOCKER_",
        "COMPOSE_",
        "BUILDX_",
        "BUILDKIT_",
        "BELIB_STAGE_",
        "POSTGRES_",
        "R2_",
        "AWS_",
        "S3_",
        "PROD_",
        "PRODUCTION_",
        "VITE_PUBLIC_DEMO_",
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "GIT_SSH_COMMAND",
    )
    forbidden_names = {"DATABASE_URL", "DIRECT_URL", "BETTER_AUTH_SECRET", *PYTHON_STARTUP_OVERRIDES}
    if any(name.upper().startswith(forbidden_prefixes) or name.upper() in forbidden_names for name in environment):
        raise SeedWrapperError(
            "Remove inherited Python startup, staging, database, Docker, Compose, production, and secret overrides before running the staging seed"
        )


def expected_docker_socket(uid: int) -> Path:
    return Path(f"/run/user/{uid}/docker.sock")


def validate_rootless_socket(path: Path, expected_uid: int) -> None:
    """Require the expected socket under a private runtime directory without following symlinks."""
    if not path.is_absolute() or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise SeedWrapperError("This host cannot securely verify the local rootless Docker socket")

    parts = path.parts
    directory_fd = -1
    try:
        directory_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        for component in parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd

        runtime_metadata = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(runtime_metadata.st_mode)
            or runtime_metadata.st_uid != expected_uid
            or stat.S_IMODE(runtime_metadata.st_mode) != 0o700
        ):
            raise SeedWrapperError("The local Docker runtime directory must be private and owned by hiroyuki")

        socket_metadata = os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISSOCK(socket_metadata.st_mode) or socket_metadata.st_uid != expected_uid:
            raise SeedWrapperError("The expected local rootless Docker endpoint is not this user's socket")
    except SeedWrapperError:
        raise
    except OSError as error:
        raise SeedWrapperError("The expected local rootless Docker socket is unavailable") from error
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)


def _trusted_system_directory(path: Path, *, lstat=None) -> bool:
    inspect = os.lstat if lstat is None else lstat
    try:
        metadata = inspect(path)
    except OSError as error:
        if error.errno == 2:
            return False
        raise SeedWrapperError("Could not verify Docker CLI plugin search directories") from error
    mode = metadata.st_mode
    if (
        not stat.S_ISDIR(mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(mode) & 0o7022
    ):
        raise SeedWrapperError("Docker CLI plugin search directories must be root-owned and non-writable")
    return True


def verify_trusted_docker_plugins(*, lstat=None) -> None:
    """Require the package-owned CLI and plugins before invoking Docker Compose."""
    inspect = os.lstat if lstat is None else lstat
    for directory in DOCKER_PLUGIN_PARENTS:
        _trusted_system_directory(directory, lstat=inspect)

    try:
        docker_metadata = inspect(DOCKER_CLI)
    except OSError as error:
        raise SeedWrapperError("Trusted /usr/bin/docker is unavailable") from error
    if (
        not stat.S_ISREG(docker_metadata.st_mode)
        or docker_metadata.st_uid != 0
        or stat.S_IMODE(docker_metadata.st_mode) & 0o7022
        or not stat.S_IMODE(docker_metadata.st_mode) & 0o111
    ):
        raise SeedWrapperError("Trusted /usr/bin/docker must be a root-owned non-writable executable")

    for plugin_name in ("docker-compose", "docker-buildx"):
        trusted_plugin = DOCKER_PLUGIN_DIR / plugin_name
        try:
            metadata = inspect(trusted_plugin)
        except OSError as error:
            raise SeedWrapperError("Trusted Docker Compose and Buildx plugins are required") from error
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o755
        ):
            raise SeedWrapperError("Docker Compose and Buildx plugins must be root-owned regular 0755 executables")

        for search_directory in DOCKER_PLUGIN_SEARCH_DIRS:
            if search_directory == DOCKER_PLUGIN_DIR:
                continue
            candidate = search_directory / plugin_name
            try:
                inspect(candidate)
            except OSError as error:
                if error.errno == 2:
                    continue
                raise SeedWrapperError("Could not verify alternate Docker plugin paths") from error
            raise SeedWrapperError("An unapproved alternate Docker CLI plugin is present")


def verify_private_docker_config(path: Path, expected_uid: int) -> None:
    """Require an empty, private Docker config directory before every CLI invocation."""
    try:
        metadata = os.lstat(path)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise SeedWrapperError("The isolated Docker config directory must be empty and mode 0700")
        with os.scandir(path) as entries:
            if next(entries, None) is not None:
                raise SeedWrapperError("The isolated Docker config directory must be empty and mode 0700")
    except SeedWrapperError:
        raise
    except OSError as error:
        raise SeedWrapperError("The isolated Docker config directory is unavailable") from error


def create_private_docker_config(expected_uid: int) -> tempfile.TemporaryDirectory[str]:
    """Create an empty config under the trusted system /tmp, independent of caller settings."""
    tmp_metadata = os.lstat("/tmp")
    if (
        not stat.S_ISDIR(tmp_metadata.st_mode)
        or tmp_metadata.st_uid != 0
        or stat.S_IMODE(tmp_metadata.st_mode) != 0o1777
    ):
        raise SeedWrapperError("The system temporary directory must be root-owned mode 1777")
    temporary = None
    try:
        temporary = tempfile.TemporaryDirectory(prefix="belib-staging-docker.", dir="/tmp")
        verify_private_docker_config(Path(temporary.name), expected_uid)
        return temporary
    except (OSError, SeedWrapperError) as error:
        if temporary is not None:
            temporary.cleanup()
        if isinstance(error, SeedWrapperError):
            raise
        raise SeedWrapperError("Could not create a private Docker config directory") from error


def docker_environment(home: Path, docker_config: Path) -> dict[str, str]:
    """Return the small, fixed environment passed to Docker CLI processes."""
    return {
        "PATH": DOCKER_PATH,
        "HOME": str(home),
        "USER": "hiroyuki",
        "LOGNAME": "hiroyuki",
        "DOCKER_CONFIG": str(docker_config),
    }


def resolve_docker_cli() -> str:
    return DOCKER_CLI


def verify_rootless_docker(
    docker_cli: str,
    endpoint: Path,
    environment: Mapping[str, str],
    *,
    runner=None,
) -> None:
    """Check the daemon identity with a timed query that returns no credentials or config."""
    run = subprocess.run if runner is None else runner
    endpoint_url = f"unix://{endpoint}"
    try:
        result = run(
            [
                docker_cli,
                "--host",
                endpoint_url,
                "info",
                "--format",
                "{{.Name}}|{{json .SecurityOptions}}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=DOCKER_INFO_TIMEOUT_SECONDS,
            env=dict(environment),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SeedWrapperError("The local rootless Docker daemon did not answer the bounded identity check") from error

    output = result.stdout or ""
    if result.returncode != 0 or len(output.encode("utf-8", errors="replace")) > DOCKER_INFO_MAX_OUTPUT_BYTES:
        raise SeedWrapperError("The local rootless Docker daemon did not answer the bounded identity check")

    daemon_name, separator, security_json = output.strip().partition("|")
    try:
        security_options = json.loads(security_json) if separator else None
    except json.JSONDecodeError:
        security_options = None
    if (
        daemon_name != EXPECTED_HOSTNAME
        or not isinstance(security_options, list)
        or "name=rootless" not in security_options
    ):
        raise SeedWrapperError("Docker daemon identity is not the local ubuntu-worker rootless daemon")


def validate_docker_runtime(
    uid: int,
    home: Path,
    environment: Mapping[str, str],
    docker_config: Path,
) -> tuple[str, Path, dict[str, str]]:
    reject_ambient_overrides(environment)
    verify_private_docker_config(docker_config, uid)
    verify_trusted_docker_plugins()
    endpoint = expected_docker_socket(uid)
    validate_rootless_socket(endpoint, uid)
    docker_env = docker_environment(home, docker_config)
    docker_cli = resolve_docker_cli()
    verify_rootless_docker(docker_cli, endpoint, docker_env)
    return docker_cli, endpoint, docker_env


def main(*, invoked_by_launcher: bool = False) -> int:
    env_snapshot_fd = -1
    credential_fd = -1
    docker_config_temporary = None
    try:
        if (
            sys.executable != "/usr/bin/python3"
            or not sys.flags.isolated
            or not sys.flags.dont_write_bytecode
        ):
            raise SeedWrapperError("Use trusted /usr/bin/python3 -I -B for the staging seed wrapper")
        reject_ambient_overrides(os.environ)
        if not invoked_by_launcher:
            raise SeedWrapperError("Invoke the staging seed only through scripts/staging/launch-guard.py")
        account = pwd.getpwnam("hiroyuki")
        if account.pw_uid == 0 or os.getuid() != account.pw_uid or os.geteuid() != account.pw_uid:
            raise SeedWrapperError("Run the staging seed wrapper as host user hiroyuki")
        if socket.gethostname() != EXPECTED_HOSTNAME:
            raise SeedWrapperError("Run the staging seed wrapper only on i5 host ubuntu-worker")

        home = Path(account.pw_dir)
        credentials_path = home / ".config" / "belib-staging" / "seed-credentials"
        env_file = home / ".config" / "belib-staging" / "i5.env"
        repo_root = Path(__file__).resolve(strict=True).parents[2]
        env_snapshot_fd, _stage_values = validate_staging_admission(repo_root, env_file, account.pw_uid)
        docker_config_temporary = create_private_docker_config(account.pw_uid)
        docker_config = Path(docker_config_temporary.name)
        docker_cli, endpoint, docker_env = validate_docker_runtime(
            account.pw_uid,
            home,
            os.environ,
            docker_config,
        )
        if docker_env.get("DOCKER_CONFIG") != str(docker_config):
            raise SeedWrapperError("Docker CLI config is not the isolated private directory")

        # Seed credentials are opened only after source, env-file, and Docker guards pass.
        credential_fd = open_private_regular_file(credentials_path, account.pw_uid)

        os.dup2(credential_fd, 0, inheritable=True)
        if credential_fd != 0:
            os.close(credential_fd)
            credential_fd = -1

        os.chdir(repo_root)
        os.set_inheritable(env_snapshot_fd, True)
        snapshot_path = Path(f"/proc/{os.getpid()}/fd/{env_snapshot_fd}")
        command = build_compose_command(repo_root, snapshot_path, endpoint)
        command[0] = docker_cli
        verify_private_docker_config(docker_config, account.pw_uid)
        verify_trusted_docker_plugins()
        result = subprocess.run(command, env=docker_env, check=False)
        return result.returncode
    except SeedWrapperError as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError):
        print("BLOCKED: Could not start the named staging Compose seed command", file=sys.stderr)
        return 1
    finally:
        if credential_fd >= 0:
            os.close(credential_fd)
        if env_snapshot_fd >= 0:
            os.close(env_snapshot_fd)
        if docker_config_temporary is not None:
            docker_config_temporary.cleanup()

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
