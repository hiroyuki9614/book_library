#!/usr/bin/env python3
"""Bounded KAGOYA pull dispatcher for reviewed BeLib frontend releases."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
from http.cookiejar import CookieJar
import json
import os
import re
import stat
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    HTTPCookieProcessor,
    ProxyHandler,
    Request,
    build_opener,
)

from release_gate import GateError, GitHubAPI, TransientGitHubAPIError, validate_release
from kagoya_compose import (
    ComposeIdentityError,
    validate_frontend_service,
    validate_compose_services,
    validate_live_container_compose_labels,
    validate_override_path,
    validate_private_owner_file,
)


REQUEST_REF = re.compile(r"^refs/tags/belib-deploy-request-([0-9]+)-([0-9]+)$")
SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
PUBLIC_DEMO_SIDECAR_SCHEMA_VERSION = 1
PUBLIC_DEMO_PUBLICATION_INTENT = "public_general_demo_credentials"
PUBLIC_DEMO_SIDECAR_MAX_BYTES = 16 * 1024
PUBLIC_DEMO_VALIDATION_ORIGIN = "https://belib.hiroyuki9614.com"
PUBLIC_DEMO_VALIDATION_TIMEOUT = 15
PUBLIC_DEMO_VALIDATION_RESPONSE_MAX_BYTES = 64 * 1024
PUBLIC_DEMO_SHELF_PATH = "/api/v1/books?limit=100"
PUBLIC_DEMO_EXPECTED_SHELF_IDS = frozenset({4, 5})
PUBLIC_DEMO_SIDECAR_MARKER_VERSION = 1
PUBLIC_DEMO_SIDECAR_MARKER_KEYS = {
    "version",
    "kind",
    "uid",
    "mode",
    "nlink",
    "dev",
    "ino",
    "size",
    "ctime_ns",
    "mtime_ns",
}
PUBLIC_DEMO_ABSENT_SIDECAR_MARKER = {
    "version": PUBLIC_DEMO_SIDECAR_MARKER_VERSION,
    "kind": "absent",
}
PUBLIC_DEMO_SIDECAR_KEYS = {
    "schema_version",
    "publication_intent",
    "public_display_approved",
    "approval_ref",
    "email",
    "password",
}
_MISSING = object()


class DeployError(RuntimeError):
    """A failed, fail-closed frontend release operation."""


@dataclass(frozen=True)
class _PublicDemoCandidate:
    email: str
    password: str

    @property
    def pair(self) -> tuple[str, str]:
        return self.email, self.password


@dataclass(frozen=True)
class _PublicDemoIdentity:
    id: int
    email: str
    role: str
    shelf_ids: tuple[int, ...]


@dataclass(frozen=True)
class _PublicDemoSource:
    candidate: _PublicDemoCandidate | None
    marker: dict


class _DeferredPreSwitchGate(DeployError):
    """An API outage before any frontend action; leave request unconsumed."""


class _DeferredPreSwitchGitTransport(DeployError):
    """A positively identified Git fetch transport outage before frontend action."""


_GIT_NON_RETRYABLE_FAILURE_MARKERS = (
    "authentication failed",
    "authentication required",
    "could not read username",
    "repository not found",
    "repository access denied",
    "permission denied",
    "access denied",
    "not a git repository",
    "does not appear to be a git repository",
    "couldn't find remote ref",
    "could not find remote ref",
    "not our ref",
    "unadvertised object",
    "invalid object",
    "bad object",
    "corrupt",
    "corruption",
    "packfile",
    "index-pack failed",
    "invalid index-pack output",
    "unpack failed",
    "certificate verify failed",
    "server certificate verification failed",
    "host key verification failed",
    "protocol error",
)
_GIT_TRANSPORT_FAILURE_PATTERNS = (
    re.compile(r"\b(?:could not|couldn't) resolve (?:host|proxy)\b"),
    re.compile(r"\bfailed to connect\b"),
    re.compile(r"\b(?:connection|operation) (?:timed out|reset|refused|closed)\b"),
    re.compile(r"\bnetwork is unreachable\b"),
    re.compile(r"\bno route to host\b"),
    re.compile(r"\bunexpected disconnect while reading sideband packet\b"),
    re.compile(r"\bthe requested url returned error:\s*(?:429|5\d{2})\b"),
    re.compile(r"\bhttp\s+5\d{2}\b"),
)


def _is_retryable_git_transport_failure(stderr: object) -> bool:
    """Recognize only explicit network failures from a failed Git fetch."""
    if not isinstance(stderr, str):
        return False
    message = stderr.casefold()
    if any(marker in message for marker in _GIT_NON_RETRYABLE_FAILURE_MARKERS):
        return False
    return any(pattern.search(message) for pattern in _GIT_TRANSPORT_FAILURE_PATTERNS)


class _RejectRedirects(HTTPRedirectHandler):
    """Prevent public smoke requests from being redirected to another host."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _run(argv: list[str], *, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run quietly so Compose cannot echo protected host environment values."""
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            env=process_env,
        )
    except OSError:
        raise DeployError(f"Required command could not start: {Path(argv[0]).name}") from None
    if check and result.returncode != 0:
        raise DeployError(f"Command failed: {Path(argv[0]).name} (exit {result.returncode})")
    return result


def _run_json(argv: list[str], *, env: dict[str, str] | None = None) -> dict | list:
    result = _run(argv, env=env)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise DeployError("A Docker read-back was not valid JSON") from None


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _safe_nonempty_text(value: object) -> bool:
    if type(value) is not str or not value:
        return False
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _reject_public_demo_sidecar() -> None:
    raise DeployError("The public demo credentials sidecar is invalid")


def _validate_public_demo_parent(path: Path, owner_uid: int) -> None:
    parent = path.parent
    try:
        ancestors = (parent, *parent.parents)
        for directory in ancestors:
            result = directory.lstat()
            if not stat.S_ISDIR(result.st_mode):
                _reject_public_demo_sidecar()
            if directory == parent and (
                result.st_uid != owner_uid or stat.S_IMODE(result.st_mode) & 0o077
            ):
                _reject_public_demo_sidecar()
    except DeployError:
        raise
    except (OSError, ValueError):
        raise DeployError("The public demo credentials sidecar directory is unavailable") from None


def _public_demo_stat_marker(result: os.stat_result, owner_uid: int) -> dict:
    """Return only verified identity metadata for one opened sidecar inode."""
    try:
        values = {
            "uid": result.st_uid,
            "mode": stat.S_IMODE(result.st_mode),
            "nlink": result.st_nlink,
            "dev": result.st_dev,
            "ino": result.st_ino,
            "size": result.st_size,
            "ctime_ns": result.st_ctime_ns,
            "mtime_ns": result.st_mtime_ns,
        }
    except AttributeError:
        _reject_public_demo_sidecar()
    if (
        type(owner_uid) is not int
        or any(type(value) is not int for value in values.values())
        or values["uid"] != owner_uid
        or values["mode"] != 0o600
        or values["nlink"] != 1
        or values["size"] < 0
    ):
        _reject_public_demo_sidecar()
    return {
        "version": PUBLIC_DEMO_SIDECAR_MARKER_VERSION,
        "kind": "file",
        **values,
    }


def _valid_public_demo_marker(value: object) -> bool:
    if value == PUBLIC_DEMO_ABSENT_SIDECAR_MARKER:
        return True
    if not isinstance(value, dict) or set(value) != PUBLIC_DEMO_SIDECAR_MARKER_KEYS:
        return False
    if (
        type(value["version"]) is not int
        or value["version"] != PUBLIC_DEMO_SIDECAR_MARKER_VERSION
        or value["kind"] != "file"
    ):
        return False
    return all(type(value[key]) is int for key in PUBLIC_DEMO_SIDECAR_MARKER_KEYS - {"version", "kind"}) and (
        value["mode"] == 0o600
        and value["nlink"] == 1
        and value["size"] >= 0
    )


def _load_public_demo_source(config: dict) -> _PublicDemoSource:
    """Read one validated sidecar through one fd and retain its identity marker."""
    if "public_demo_credentials_file" not in config:
        return _PublicDemoSource(None, dict(PUBLIC_DEMO_ABSENT_SIDECAR_MARKER))

    configured_path = config["public_demo_credentials_file"]
    if (
        not isinstance(configured_path, str)
        or not configured_path
        or "\x00" in configured_path
    ):
        raise DeployError("The public demo credentials sidecar path is invalid")
    path = Path(configured_path)
    if not path.is_absolute():
        raise DeployError("The public demo credentials sidecar path must be absolute")
    owner_uid = config.get("agent_uid")
    if type(owner_uid) is not int or owner_uid < 0:
        raise DeployError("The public demo credentials sidecar owner is invalid")

    try:
        protected_env = Path(config["production_env_file"]).resolve(strict=False)
        if path.resolve(strict=False) == protected_env:
            _reject_public_demo_sidecar()
    except (KeyError, TypeError, OSError, RuntimeError, ValueError):
        raise DeployError("The public demo credentials sidecar path is invalid") from None

    _validate_public_demo_parent(path, owner_uid)
    fd: int | None = None
    try:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            _reject_public_demo_sidecar()
        flags = os.O_RDONLY | nofollow
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            return _PublicDemoSource(None, dict(PUBLIC_DEMO_ABSENT_SIDECAR_MARKER))
        except (OSError, ValueError):
            _reject_public_demo_sidecar()

        sidecar_stat = os.fstat(fd)
        if not stat.S_ISREG(sidecar_stat.st_mode):
            _reject_public_demo_sidecar()
        before_marker = _public_demo_stat_marker(sidecar_stat, owner_uid)
        if before_marker["size"] > PUBLIC_DEMO_SIDECAR_MAX_BYTES:
            _reject_public_demo_sidecar()

        content = bytearray()
        while len(content) <= PUBLIC_DEMO_SIDECAR_MAX_BYTES:
            chunk = os.read(fd, PUBLIC_DEMO_SIDECAR_MAX_BYTES + 1 - len(content))
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > PUBLIC_DEMO_SIDECAR_MAX_BYTES:
                _reject_public_demo_sidecar()
        after_stat = os.fstat(fd)
        if not stat.S_ISREG(after_stat.st_mode):
            _reject_public_demo_sidecar()
        after_marker = _public_demo_stat_marker(after_stat, owner_uid)
        if after_marker != before_marker or len(content) != after_marker["size"]:
            _reject_public_demo_sidecar()
        try:
            path_stat = os.stat(path, follow_symlinks=False)
            if not stat.S_ISREG(path_stat.st_mode):
                _reject_public_demo_sidecar()
            path_marker = _public_demo_stat_marker(path_stat, owner_uid)
        except (OSError, ValueError):
            _reject_public_demo_sidecar()
        if path_marker != after_marker:
            _reject_public_demo_sidecar()
        sidecar = json.loads(
            bytes(content).decode("utf-8"),
            object_pairs_hook=_unique_json_object,
        )
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError):
        _reject_public_demo_sidecar()
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
    if not isinstance(sidecar, dict) or set(sidecar) != PUBLIC_DEMO_SIDECAR_KEYS:
        _reject_public_demo_sidecar()
    if (
        type(sidecar["schema_version"]) is not int
        or sidecar["schema_version"] != PUBLIC_DEMO_SIDECAR_SCHEMA_VERSION
        or sidecar["publication_intent"] != PUBLIC_DEMO_PUBLICATION_INTENT
        or sidecar["public_display_approved"] is not True
        or not _safe_nonempty_text(sidecar["approval_ref"])
        or not _safe_nonempty_text(sidecar["email"])
        or not re.fullmatch(r"[^@\s]+@[^@\s]+", sidecar["email"])
        or not _safe_nonempty_text(sidecar["password"])
    ):
        _reject_public_demo_sidecar()

    return _PublicDemoSource(
        _PublicDemoCandidate(sidecar["email"], sidecar["password"]),
        after_marker,
    )


def _load_public_demo_candidate(config: dict) -> _PublicDemoCandidate | None:
    return _load_public_demo_source(config).candidate


def _load_public_demo_credentials(config: dict) -> tuple[str, str]:
    """Load only the explicitly approved sidecar pair, without validating authority."""
    candidate = _load_public_demo_source(config).candidate
    return candidate.pair if candidate is not None else ("", "")


def _set_optional_state_value(state: dict, key: str, value: object) -> None:
    if value is _MISSING:
        state.pop(key, None)
    else:
        state[key] = value


def _validate_public_demo_api_base_url(config: dict) -> None:
    """Bind public-demo credentials to the fixed protected HTTPS origin."""
    value = config.get("expected_api_base_url")
    try:
        parsed = urlsplit(value) if type(value) is str else None
        port = parsed.port if parsed is not None else None
    except ValueError:
        parsed = None
        port = None
    expected_hostname = urlsplit(PUBLIC_DEMO_VALIDATION_ORIGIN).hostname
    if (
        parsed is None
        or value != value.strip()
        or parsed.scheme != "https"
        or parsed.hostname != expected_hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or "?" in value
        or "#" in value
        or parsed.path not in ("", "/")
    ):
        raise DeployError(
            "The public demo API base URL must use the canonical protected HTTPS origin"
        )


def _frontend_compose_env(
    config: dict,
    source_sha: str,
    public_demo_credentials: tuple[str, str],
    image_ref: str,
) -> dict[str, str]:
    email, password = public_demo_credentials
    return {
        "COMPOSE_PROFILES": "",
        "VCS_REF": source_sha,
        "BELIB_FRONTEND_IMAGE": image_ref,
        "VITE_API_BASE_URL": config["expected_api_base_url"],
        "VITE_PUBLIC_DEMO_EMAIL": email,
        "VITE_PUBLIC_DEMO_PASSWORD": password,
    }


def _compose_prefix(config: dict, checkout: Path) -> list[str]:
    try:
        production_override = validate_override_path(config, config["agent_uid"])
        production_env = validate_private_owner_file(
            config["production_env_file"], config["agent_uid"], description="production env file"
        )
    except (ComposeIdentityError, KeyError, TypeError) as error:
        raise DeployError(str(error)) from None
    checkout_root = checkout.resolve(strict=True)
    base_file = checkout_root / "compose.yaml"
    if not base_file.is_file() or base_file.is_symlink():
        raise DeployError("The release checkout Compose file must be a regular file")
    command = [
        "docker",
        "compose",
        "--project-name",
        config["expected_project"],
        "--project-directory",
        str(checkout_root),
        "--file",
        str(base_file),
        "--file",
        str(production_override),
        "--env-file",
        str(production_env),
    ]
    return command


def _container(config: dict) -> dict:
    inspected = _run_json(["docker", "inspect", config["production_container"]])
    if not isinstance(inspected, list) or len(inspected) != 1:
        raise DeployError("The configured production frontend container is not unique")
    item = inspected[0]
    labels = item.get("Config", {}).get("Labels") or {}
    if (
        item.get("Name", "").lstrip("/") != config["production_container"]
        or labels.get("com.docker.compose.project") != config["expected_project"]
        or labels.get("com.docker.compose.service") != "frontend"
        or item.get("State", {}).get("Running") is not True
    ):
        raise DeployError("The live container no longer matches the approved Compose target")
    try:
        validate_live_container_compose_labels(labels, config)
    except ComposeIdentityError as error:
        raise DeployError(str(error)) from None
    networks = sorted((item.get("NetworkSettings", {}).get("Networks") or {}).keys())
    if networks != sorted(config["expected_networks"]):
        raise DeployError("The live frontend network identity changed")
    if not item.get("Image") or not item.get("Config", {}).get("Image"):
        raise DeployError("The live frontend image identity is incomplete")
    return item


def _image(config: dict, image_ref: str) -> dict:
    inspected = _run_json(["docker", "image", "inspect", image_ref])
    if not isinstance(inspected, list) or len(inspected) != 1:
        raise DeployError("The frontend image reference is not unique")
    return inspected[0]


def _retained_image_ref(image_id: str) -> str:
    digest = image_id.removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise DeployError("The current frontend image ID is not a full Docker SHA-256")
    return f"belib-frontend:rollback-{digest[:20]}"


def _retain_live_image(config: dict, item: dict) -> tuple[str, str]:
    image_id = item["Image"]
    image_ref = item["Config"]["Image"]
    retained_ref = _retained_image_ref(image_id)
    existing = _run(["docker", "image", "inspect", retained_ref], check=False)
    if existing.returncode == 0:
        data = json.loads(existing.stdout)
        if not isinstance(data, list) or data[0].get("Id") != image_id:
            raise DeployError("The immutable rollback tag already points to a different image")
    else:
        _run(["docker", "image", "tag", image_id, retained_ref])
        if _image(config, retained_ref).get("Id") != image_id:
            raise DeployError("The old frontend image could not be retained by immutable ID")
    return retained_ref, image_ref


def _checkout(config: dict, source_sha: str) -> Path:
    if not SOURCE_SHA.fullmatch(source_sha):
        raise DeployError("The release source is not a full 40-character SHA")
    root = Path(config["release_root"]).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    checkout = root / source_sha
    if checkout.exists():
        if not (checkout / ".git").exists():
            raise DeployError("A non-Git path occupies the release checkout location")
        status = _run(["git", "-C", str(checkout), "status", "--porcelain"])
        if status.stdout.strip():
            raise DeployError("The isolated release checkout contains local changes")
        _fetch_source(checkout, source_sha)
        _run(["git", "-C", str(checkout), "checkout", "--detach", "FETCH_HEAD"])
    else:
        checkout.mkdir(mode=0o700)
        _run(["git", "-C", str(checkout), "init", "--quiet"])
        _run(["git", "-C", str(checkout), "remote", "add", "origin", config["repo_url"]])
        _fetch_source(checkout, source_sha)
        _run(["git", "-C", str(checkout), "checkout", "--detach", "FETCH_HEAD"])
    actual = _run(["git", "-C", str(checkout), "rev-parse", "HEAD"]).stdout.strip().lower()
    if actual != source_sha:
        raise DeployError("The isolated checkout does not match the release SHA")
    return checkout


def _fetch_source(checkout: Path, source_sha: str) -> None:
    argv = ["git", "-C", str(checkout), "fetch", "--depth=1", "origin", source_sha]
    result = _run(argv, check=False)
    if result.returncode == 0:
        return
    if _is_retryable_git_transport_failure(result.stderr):
        raise _DeferredPreSwitchGitTransport(
            "Transient Git fetch transport failure; request retained for next timer run"
        ) from None
    raise DeployError(f"Command failed: {Path(argv[0]).name} (exit {result.returncode})")


def _http_get(url: str) -> tuple[int, dict[str, str], bytes]:
    request = Request(url, headers={"User-Agent": "belib-frontend-cd"})
    opener = build_opener(ProxyHandler({}), _RejectRedirects())
    try:
        with opener.open(request, timeout=15) as response:
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers.items()), error.read()
    except (URLError, TimeoutError):
        raise DeployError("The local production smoke request failed") from None


def _read_bounded_response(response, *, retain: bool = True) -> bytes:
    body = bytearray()
    while len(body) <= PUBLIC_DEMO_VALIDATION_RESPONSE_MAX_BYTES:
        chunk = response.read(min(8192, PUBLIC_DEMO_VALIDATION_RESPONSE_MAX_BYTES + 1 - len(body)))
        if not isinstance(chunk, (bytes, bytearray)):
            raise DeployError("The public demo authorization response was invalid")
        if not chunk:
            break
        body.extend(chunk)
        if len(body) > PUBLIC_DEMO_VALIDATION_RESPONSE_MAX_BYTES:
            raise DeployError("The public demo authorization response was too large")
    return bytes(body) if retain else b""


class _PublicDemoValidationSession:
    def __init__(self):
        self.cookies = CookieJar()
        self.opener = build_opener(
            ProxyHandler({}),
            HTTPCookieProcessor(self.cookies),
            _RejectRedirects(),
            HTTPSHandler(context=ssl.create_default_context()),
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        retain_statuses: set[int] | None = None,
    ) -> tuple[int, bytes]:
        if (
            not path.startswith("/")
            or "//" in path
            or "#" in path
            or ("?" in path and path != PUBLIC_DEMO_SHELF_PATH)
        ):
            raise DeployError("The public demo authorization path is invalid")
        request = Request(
            f"{PUBLIC_DEMO_VALIDATION_ORIGIN}{path}",
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "User-Agent": "belib-frontend-cd",
                "Origin": PUBLIC_DEMO_VALIDATION_ORIGIN,
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        try:
            with self.opener.open(request, timeout=PUBLIC_DEMO_VALIDATION_TIMEOUT) as response:
                retain = retain_statuses is None or response.status in retain_statuses
                return response.status, _read_bounded_response(response, retain=retain)
        except HTTPError as error:
            try:
                retain = retain_statuses is None or error.code in retain_statuses
                body = _read_bounded_response(error, retain=retain)
            except DeployError:
                raise
            return error.code, body
        except (OSError, URLError, TimeoutError):
            raise DeployError("The public demo authorization check failed") from None


def _public_demo_json(body: bytes) -> dict:
    if not isinstance(body, (bytes, bytearray)) or len(body) > PUBLIC_DEMO_VALIDATION_RESPONSE_MAX_BYTES:
        raise DeployError("The public demo authorization response was too large")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeError, TypeError, ValueError, json.JSONDecodeError):
        raise DeployError("The public demo authorization response was invalid") from None
    if not isinstance(value, dict):
        raise DeployError("The public demo authorization response was invalid")
    return value


def _public_demo_shelf_ids(body: bytes) -> tuple[int, ...]:
    shelf = _public_demo_json(body)
    total = shelf.get("total")
    page = shelf.get("page")
    limit = shelf.get("limit")
    books = shelf.get("books")
    if (
        type(total) is not int
        or total != len(PUBLIC_DEMO_EXPECTED_SHELF_IDS)
        or type(page) is not int
        or page != 1
        or type(limit) is not int
        or limit != 100
        or not isinstance(books, list)
        or len(books) > limit
    ):
        raise DeployError("The public demo shelf check failed")

    shelf_ids: list[int] = []
    for book in books:
        if not isinstance(book, dict) or type(book.get("id")) is not int or book["id"] < 1:
            raise DeployError("The public demo shelf check failed")
        shelf_ids.append(book["id"])
    if (
        len(shelf_ids) != len(PUBLIC_DEMO_EXPECTED_SHELF_IDS)
        or len(set(shelf_ids)) != len(shelf_ids)
        or set(shelf_ids) != PUBLIC_DEMO_EXPECTED_SHELF_IDS
    ):
        raise DeployError("The public demo shelf check failed")
    return tuple(sorted(shelf_ids))


def _validate_public_demo_candidate(candidate: _PublicDemoCandidate) -> _PublicDemoIdentity:
    """Prove the candidate identity, admin denial, and exact public shelf in one fresh session."""
    session = _PublicDemoValidationSession()
    sign_in_status, _ = session.request(
        "POST",
        "/api/auth/sign-in/email",
        retain_statuses=set(),
        body=json.dumps(
            {"email": candidate.email, "password": candidate.password},
            separators=(",", ":"),
        ).encode("utf-8"),
    )
    if sign_in_status != 200 or not list(session.cookies):
        raise DeployError("The public demo authorization check failed")

    me_status, me_body = session.request("GET", "/api/v1/me")
    if me_status != 200:
        raise DeployError("The public demo identity check failed")
    me = _public_demo_json(me_body)
    identity_id = me.get("id")
    identity_email = me.get("email")
    identity_role = me.get("role")
    if (
        type(identity_id) is not int
        or identity_id < 1
        or identity_email != candidate.email
        or identity_role != "user"
    ):
        raise DeployError("The public demo identity is not the approved general account")
    admin_status, admin_body = session.request(
        "GET", "/api/v1/admin/books", retain_statuses={403}
    )
    if admin_status != 403 or _public_demo_json(admin_body) != {
        "message": "Administrator role required",
        "code": "FORBIDDEN",
    }:
        raise DeployError("The public demo Admin API denial check failed")

    shelf_status, shelf_body = session.request("GET", PUBLIC_DEMO_SHELF_PATH)
    if shelf_status != 200:
        raise DeployError("The public demo shelf check failed")
    shelf_ids = _public_demo_shelf_ids(shelf_body)
    return _PublicDemoIdentity(identity_id, identity_email, identity_role, shelf_ids)


def smoke_test(base_url: str) -> None:
    status, _, login = _http_get(f"{base_url.rstrip('/')}/login")
    if status != 200 or not re.search(rb"<title>\s*BeLib\s*</title>", login, re.IGNORECASE):
        raise DeployError("The public login smoke failed")
    match = re.search(rb'href=["\']([^"\']*?/favicon\.svg\?v=[^"\']+)["\']', login)
    if not match:
        raise DeployError("The login page has no versioned BeLib favicon link")
    favicon_path = match.group(1).decode("ascii", errors="strict")
    status, headers, body = _http_get(f"{base_url.rstrip('/')}{favicon_path}")
    content_type = next(
        (value for key, value in headers.items() if key.lower() == "content-type"), ""
    ).split(";", 1)[0].strip().lower()
    if status != 200 or content_type != "image/svg+xml" or not body.lstrip().startswith(b"<svg"):
        raise DeployError("The versioned favicon smoke failed")
    status, _, _ = _http_get(f"{base_url.rstrip('/')}/health")
    if status != 200:
        raise DeployError("The public health smoke failed")
    for path in ("/api/v1/books", "/api/v1/admin/books"):
        status, _, _ = _http_get(f"{base_url.rstrip('/')}{path}")
        if status != 401:
            raise DeployError("An anonymous protected API smoke did not return HTTP 401")


def _verify_target_image(config: dict, source_sha: str) -> dict:
    expected_ref = f"belib-frontend:{source_sha}"
    image = _image(config, expected_ref)
    labels = image.get("Config", {}).get("Labels") or {}
    if (
        not re.fullmatch(r"sha256:[0-9a-f]{64}", image.get("Id", ""))
        or labels.get("org.opencontainers.image.revision", "").lower() != source_sha
    ):
        raise DeployError("The built frontend image revision label does not match the source SHA")
    return image


def _validate_frontend_compose(
    config: dict,
    checkout: Path,
    source_sha: str,
    *,
    public_demo_credentials: tuple[str, str] | None = None,
) -> None:
    """Reject candidate Compose changes that could expand host/runtime authority."""
    if public_demo_credentials is None:
        public_demo_credentials = _load_public_demo_credentials(config)
    compose_prefix = _compose_prefix(config, checkout)
    compose_env = _frontend_compose_env(
        config,
        source_sha,
        public_demo_credentials,
        f"belib-frontend:{source_sha}",
    )
    resolved = _run_json(compose_prefix + ["config", "--format", "json"], env=compose_env)
    all_profiles = _run_json(
        compose_prefix + ["--profile", "*", "config", "--format", "json"],
        env=compose_env,
    )
    if not isinstance(resolved, dict):
        raise DeployError("The resolved production Compose config has an invalid shape")
    if not isinstance(all_profiles, dict):
        raise DeployError("The resolved profiled production Compose config has an invalid shape")
    try:
        validate_private_owner_file(
            config["production_env_file"], config["agent_uid"], description="production env file"
        )
        validate_compose_services(all_profiles, require_migrate=True)
        validate_frontend_service(
            resolved,
            checkout=checkout,
            source_sha=source_sha,
            api_base_url=config["expected_api_base_url"],
            expected_networks=config["expected_networks"],
            expected_contract_sha256=config["expected_compose_contract_sha256"],
            public_demo_credentials=public_demo_credentials,
        )
    except (ComposeIdentityError, KeyError, TypeError) as error:
        if config.get("frontend_bootstrap_mode") == "legacy-override-update-required":
            raise DeployError(
                "Legacy pinned frontend override is bootstrap-only; the protected override must be updated to the exact-SHA frontend contract before any build or switch. "
                + str(error)
            ) from None
        raise DeployError(str(error)) from None


def _verify_running_image(config: dict, image_id: str, source_sha: str | None = None) -> dict:
    live = _container(config)
    if live["Image"] != image_id:
        raise DeployError("The live frontend image ID does not match the requested image")
    if source_sha and live.get("Config", {}).get("Image") != f"belib-frontend:{source_sha}":
        raise DeployError("The live frontend image tag does not match the source SHA")
    if source_sha:
        labels = live.get("Config", {}).get("Labels") or {}
        if labels.get("org.opencontainers.image.revision", "").lower() != source_sha:
            raise DeployError("The live frontend immutable revision label does not match the source SHA")
        files = [
            part.strip()
            for part in labels.get("com.docker.compose.project.config_files", "").split(",")
        ]
        try:
            live_base = Path(files[0]).resolve(strict=True) if len(files) == 2 else None
            expected_base = (Path(config["release_root"]) / source_sha / "compose.yaml").resolve(strict=True)
        except (KeyError, OSError, RuntimeError):
            raise DeployError("The live frontend Compose source checkout could not be verified") from None
        if live_base != expected_base:
            raise DeployError("The live frontend Compose source is not the exact release checkout")
    return live


def _up_frontend(
    config: dict,
    checkout: Path,
    source_sha: str,
    *,
    image_override: str | None = None,
    public_demo_credentials: tuple[str, str] | None = None,
) -> None:
    if public_demo_credentials is None:
        public_demo_credentials = _load_public_demo_credentials(config)
    prefix = _compose_prefix(config, checkout)
    env = _frontend_compose_env(
        config,
        source_sha,
        public_demo_credentials,
        image_override if image_override is not None else f"belib-frontend:{source_sha}",
    )
    _run(prefix + ["up", "--detach", "--no-deps", "--no-build", "frontend"], env=env)


def deploy_frontend(
    config: dict,
    source_sha: str,
    *,
    smoke=None,
    main_sha_reader=None,
    public_demo_source: _PublicDemoSource | None = None,
    active_public_demo_marker: dict | None = None,
) -> dict:
    if main_sha_reader is None:
        main_sha_reader = _main_sha
    if smoke is None:
        smoke = smoke_test
    if public_demo_source is None:
        public_demo_source = _load_public_demo_source(config)
    public_demo_candidate = public_demo_source.candidate
    public_demo_credentials = (
        public_demo_candidate.pair if public_demo_candidate is not None else ("", "")
    )
    _validate_public_demo_api_base_url(config)
    public_demo_identity = (
        _validate_public_demo_candidate(public_demo_candidate)
        if public_demo_candidate is not None
        else None
    )
    checkout = _checkout(config, source_sha)
    _validate_frontend_compose(
        config,
        checkout,
        source_sha,
        public_demo_credentials=public_demo_credentials,
    )

    current = _container(config)
    if current.get("Config", {}).get("Image") == f"belib-frontend:{source_sha}":
        if (
            not _valid_public_demo_marker(active_public_demo_marker)
            or active_public_demo_marker != public_demo_source.marker
        ):
            raise DeployError(
                "The active frontend public-demo sidecar provenance cannot be proven unchanged; "
                "a new main source SHA and authorized deployment request are required"
            )
        rollback_ref, _original_ref = _retain_live_image(config, current)
        image = _verify_target_image(config, source_sha)
        if current["Image"] == image.get("Id"):
            _verify_running_image(config, image["Id"], source_sha)
            smoke(config["base_url"])
            return {
                "source_sha": source_sha,
                "image_id": image["Id"],
                "image_ref": f"belib-frontend:{source_sha}",
                "previous_image_id": current["Image"],
                "previous_image_ref": rollback_ref,
                "public_demo_sidecar_marker": public_demo_source.marker,
                "no_op": True,
            }

    rollback_ref, original_ref = _retain_live_image(config, current)

    _run(
        _compose_prefix(config, checkout) + ["build", "frontend"],
        env=_frontend_compose_env(
            config,
            source_sha,
            public_demo_credentials,
            f"belib-frontend:{source_sha}",
        ),
    )
    image = _verify_target_image(config, source_sha)
    if public_demo_candidate is not None:
        if _validate_public_demo_candidate(public_demo_candidate) != public_demo_identity:
            raise DeployError("The public demo identity changed before the frontend switch")
    if main_sha_reader(config) != source_sha:
        raise DeployError("Main advanced before the frontend switch")
    switched = False
    try:
        switched = True
        _up_frontend(
            config,
            checkout,
            source_sha,
            public_demo_credentials=public_demo_credentials,
        )
        _verify_running_image(config, image["Id"], source_sha)
        smoke(config["base_url"])
        if main_sha_reader(config) != source_sha:
            raise DeployError("Main advanced during deployment")
    except Exception as error:
        if switched:
            try:
                _up_frontend(
                    config,
                    checkout,
                    source_sha,
                    image_override=rollback_ref,
                    public_demo_credentials=public_demo_credentials,
                )
                _verify_running_image(config, current["Image"])
                smoke(config["base_url"])
            except Exception:
                raise DeployError("Frontend smoke failed and the saved frontend rollback also failed") from None
            if isinstance(error, _DeferredPreSwitchGitTransport):
                raise DeployError("Frontend deployment failed after the frontend switch") from None
        if isinstance(error, DeployError):
            raise
        raise DeployError("Frontend deployment failed and the prior image was restored") from None
    return {
        "source_sha": source_sha,
        "image_id": image["Id"],
        "image_ref": f"belib-frontend:{source_sha}",
        "previous_image_id": current["Image"],
        "previous_image_ref": rollback_ref,
        "previous_original_ref": original_ref,
        "public_demo_sidecar_marker": public_demo_source.marker,
        "no_op": False,
    }


def _requests(config: dict) -> list[tuple[tuple[int, int], str, str]]:
    result = _run(["git", "ls-remote", "--refs", config["repo_url"], "refs/tags/belib-deploy-request-*"])
    requests: list[tuple[tuple[int, int], str, str]] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        match = REQUEST_REF.fullmatch(fields[1])
        if match and SOURCE_SHA.fullmatch(fields[0].lower()):
            requests.append(((int(match.group(1)), int(match.group(2))), fields[0].lower(), fields[1]))
    return sorted(requests, key=lambda item: item[0])


def _load_state(config: dict) -> dict:
    path = Path(config["state_file"])
    if not path.exists():
        return {"last_request": [0, 0], "current_sha": None, "previous": None, "current": None}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise DeployError("The deployment state file cannot be read") from None
    if not isinstance(value, dict):
        raise DeployError("The deployment state file has an invalid shape")
    return value


def _save_state(config: dict, state: dict) -> None:
    path = Path(config["state_file"])
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, sort_keys=True)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _previous_state_record(config: dict, state: dict, result: dict) -> dict | None:
    """Preserve the pre-switch image when replay sees the already-running candidate."""
    saved_current = state.get("current")
    image_id = result["previous_image_id"]
    image_ref = result["previous_image_ref"]
    source_sha = state.get("current_sha")
    if result.get("no_op"):
        saved_image_id = saved_current.get("image_id") if isinstance(saved_current, dict) else None
        if saved_image_id and saved_image_id != result["image_id"]:
            image_id = saved_image_id
            image_ref = _retained_image_ref(image_id)
        elif saved_image_id == result["image_id"]:
            saved_previous = state.get("previous")
            if saved_previous is None:
                return None
            if (
                not isinstance(saved_previous, dict)
                or not saved_previous.get("image_id")
                or not saved_previous.get("image_ref")
                or saved_previous["image_id"] == result["image_id"]
            ):
                raise DeployError("The previously recorded frontend rollback image is invalid")
            image_id = saved_previous["image_id"]
            image_ref = saved_previous["image_ref"]
            source_sha = saved_previous.get("source_sha")
        elif not saved_image_id and tuple(state.get("last_request", [0, 0])) == (0, 0):
            # The first request may have switched the bootstrap image before its
            # state save failed. Recover that durable owner-bootstrap pointer.
            bootstrap_image_id = config.get("bootstrap_image_id")
            if bootstrap_image_id and bootstrap_image_id != result["image_id"]:
                image_id = bootstrap_image_id
                image_ref = config.get("bootstrap_rollback_ref") or _retained_image_ref(image_id)
                source_sha = None
        if image_id == result["image_id"]:
            return None
        if image_id != result["previous_image_id"]:
            if _image(config, image_ref).get("Id") != image_id:
                raise DeployError("The previously recorded frontend image is no longer retained")
    return {"image_id": image_id, "image_ref": image_ref, "source_sha": source_sha}


def _synchronize_state_after_dispatch_failure(
    config: dict,
    state: dict,
    *,
    previous_request: tuple[int, int],
) -> None:
    """Record the actual frontend after a failed request, or fail closed as unknown."""
    prior_current = state.get("current")
    prior_source_sha = state.get("current_sha")
    prior_public_demo_marker = state.get("public_demo_sidecar_marker", _MISSING)
    try:
        live = _container(config)
        image_id = live.get("Image")
        image_ref = live.get("Config", {}).get("Image")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id or "") or not image_ref:
            raise DeployError("The failed deployment live image identity is incomplete")
    except Exception:
        state["current"] = None
        state["current_sha"] = None
        _set_optional_state_value(state, "public_demo_sidecar_marker", _MISSING)
        _set_optional_state_value(state, "previous_public_demo_sidecar_marker", _MISSING)
        state["last_result"] = "deployment failed; live image is unknown"
    else:
        labels = live.get("Config", {}).get("Labels") or {}
        revision_value = labels.get("org.opencontainers.image.revision", "")
        revision = revision_value.lower() if isinstance(revision_value, str) else ""
        state["current"] = {"image_id": image_id, "image_ref": image_ref}
        if SOURCE_SHA.fullmatch(revision):
            state["current_sha"] = revision
        elif isinstance(prior_current, dict) and prior_current.get("image_id") == image_id:
            state["current_sha"] = prior_source_sha
        else:
            state["current_sha"] = None

        if isinstance(prior_current, dict) and prior_current.get("image_id") == image_id:
            _set_optional_state_value(
                state,
                "public_demo_sidecar_marker",
                prior_public_demo_marker,
            )
        else:
            _set_optional_state_value(state, "public_demo_sidecar_marker", _MISSING)

        prior_image_id = prior_current.get("image_id") if isinstance(prior_current, dict) else None
        if prior_image_id and prior_image_id != image_id:
            prior_image_ref = _retained_image_ref(prior_image_id)
            try:
                retained = _image(config, prior_image_ref).get("Id") == prior_image_id
            except Exception:
                retained = False
            state["previous"] = (
                {"image_id": prior_image_id, "image_ref": prior_image_ref, "source_sha": prior_source_sha}
                if retained
                else None
            )
            if retained:
                _set_optional_state_value(
                    state,
                    "previous_public_demo_sidecar_marker",
                    prior_public_demo_marker,
                )
            else:
                _set_optional_state_value(state, "previous_public_demo_sidecar_marker", _MISSING)
        elif prior_image_id and prior_image_id == image_id:
            # The failed request did not change the live image; preserve the
            # already-recorded rollback image and its sidecar provenance marker.
            pass
        elif not prior_image_id and previous_request == (0, 0):
            bootstrap_image_id = config.get("bootstrap_image_id")
            bootstrap_ref = config.get("bootstrap_rollback_ref")
            if bootstrap_image_id and bootstrap_image_id != image_id:
                bootstrap_ref = bootstrap_ref or _retained_image_ref(bootstrap_image_id)
                try:
                    retained = _image(config, bootstrap_ref).get("Id") == bootstrap_image_id
                except Exception:
                    retained = False
                state["previous"] = (
                    {"image_id": bootstrap_image_id, "image_ref": bootstrap_ref, "source_sha": None}
                    if retained
                    else None
                )
            _set_optional_state_value(state, "previous_public_demo_sidecar_marker", _MISSING)
        else:
            _set_optional_state_value(state, "previous_public_demo_sidecar_marker", _MISSING)
        state["last_result"] = "failed"

    try:
        _save_state(config, state)
    except Exception:
        raise DeployError("Failed deployment state could not be synchronized") from None


def _main_sha(config: dict) -> str:
    argv = ["git", "ls-remote", "--refs", config["repo_url"], "refs/heads/main"]
    result = _run(argv, check=False)
    if result.returncode:
        if _is_retryable_git_transport_failure(result.stderr):
            raise _DeferredPreSwitchGitTransport(
                "Transient Git main ref transport failure; request retained for next timer run"
            ) from None
        raise DeployError(f"Command failed: git (exit {result.returncode})")
    fields = result.stdout.split()
    if len(fields) != 2 or not SOURCE_SHA.fullmatch(fields[0].lower()):
        raise DeployError("The current main SHA could not be read")
    return fields[0].lower()


def _validate_network(config: dict) -> None:
    api = GitHubAPI(repository=config["repository"])
    try:
        validate_release(api, config["request_sha"])
    except TransientGitHubAPIError:
        raise
    except GateError as error:
        raise DeployError(str(error)) from None


def _same_sha_public_demo_marker(state: dict, source_sha: str, marker: dict) -> dict | None:
    """Require unchanged sidecar provenance before allowing a same-SHA no-op."""
    if state.get("current_sha") != source_sha:
        return None
    current = state.get("current")
    saved_marker = state.get("public_demo_sidecar_marker", _MISSING)
    if (
        not isinstance(current, dict)
        or not current.get("image_id")
        or not _valid_public_demo_marker(saved_marker)
        or saved_marker != marker
    ):
        raise DeployError(
            "The active frontend public-demo sidecar provenance cannot be proven unchanged; "
            "a new main source SHA and authorized deployment request are required"
        )
    return marker


def _lock_path(config: dict) -> Path:
    path = Path(config["lock_file"])
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def dispatch_once(config: dict) -> int:
    if os.name != "posix":
        raise DeployError("The production dispatcher requires a POSIX host")
    if os.getuid() != config.get("agent_uid"):
        raise DeployError("The dispatcher must run as the configured production agent")

    with _lock_path(config).open("w", encoding="utf-8") as lock_stream:
        fcntl.flock(lock_stream, fcntl.LOCK_EX)
        state = _load_state(config)
        if state.get("last_result") in {
            "manual frontend rollback failed; live image is unknown",
            "deployment failed; live image is unknown",
        }:
            raise DeployError("The live frontend state is unknown; automatic deployment is stopped")
        requests = _requests(config)
        if not requests:
            return 0
        request_id, source_sha, ref = requests[-1]
        if tuple(state.get("last_request", [0, 0])) >= request_id:
            return 0

        current_main = _main_sha(config)
        if source_sha != current_main:
            state["last_request"] = list(request_id)
            state["last_result"] = "stale request skipped"
            _save_state(config, state)
            print(f"Skipped stale deployment request {ref}")
            return 0

        config["request_sha"] = source_sha
        try:
            public_demo_source = _load_public_demo_source(config)
            active_public_demo_marker = _same_sha_public_demo_marker(
                state, source_sha, public_demo_source.marker
            )
            try:
                _validate_network(config)
            except TransientGitHubAPIError:
                raise _DeferredPreSwitchGate(
                    "Transient GitHub release check failure; request retained for next timer run"
                ) from None
            if _main_sha(config) != source_sha:
                raise DeployError("Main advanced before the deployment began")
            result = deploy_frontend(
                config,
                source_sha,
                public_demo_source=public_demo_source,
                active_public_demo_marker=active_public_demo_marker,
            )
        except (_DeferredPreSwitchGate, _DeferredPreSwitchGitTransport) as error:
            # A visible service failure, but no state mutation or deployment.
            # A later timer tick may safely revalidate this pending request.
            raise DeployError(str(error)) from None
        except Exception as error:
            previous_request = tuple(state.get("last_request", [0, 0]))
            state["last_request"] = list(request_id)
            _synchronize_state_after_dispatch_failure(
                config,
                state,
                previous_request=previous_request,
            )
            if isinstance(error, DeployError):
                raise
            raise DeployError("The deployment request failed closed") from None

        previous = _previous_state_record(config, state, result)
        prior_public_demo_marker = state.get("public_demo_sidecar_marker", _MISSING)
        if result.get("no_op") and previous is not None:
            saved_current = state.get("current")
            if isinstance(saved_current, dict) and saved_current.get("image_id") == result["image_id"]:
                prior_public_demo_marker = state.get(
                    "previous_public_demo_sidecar_marker", _MISSING
                )
        state.update(
            {
                "last_request": list(request_id),
                "last_result": "success",
                "current_sha": source_sha,
                "current": {"image_id": result["image_id"], "image_ref": result["image_ref"]},
                "previous": previous,
            }
        )
        state["public_demo_sidecar_marker"] = result["public_demo_sidecar_marker"]
        if previous is None:
            _set_optional_state_value(state, "previous_public_demo_sidecar_marker", _MISSING)
        else:
            _set_optional_state_value(
                state,
                "previous_public_demo_sidecar_marker",
                prior_public_demo_marker,
            )
        _save_state(config, state)
        print(f"Deployed frontend main@{source_sha} image={result['image_id']} no_op={result['no_op']}")
        return 0


def rollback_frontend(config: dict) -> int:
    if os.name != "posix":
        raise DeployError("The production dispatcher requires a POSIX host")
    if os.getuid() != config.get("agent_uid"):
        raise DeployError("Rollback must run as the configured production agent")

    with _lock_path(config).open("w", encoding="utf-8") as lock_stream:
        fcntl.flock(lock_stream, fcntl.LOCK_EX)
        return _rollback_frontend_unlocked(config)


def _rollback_frontend_unlocked(config: dict) -> int:
    state = _load_state(config)
    current = state.get("current")
    previous = state.get("previous")
    source_sha = state.get("current_sha")
    if not current or not previous or not source_sha or not previous.get("image_ref"):
        raise DeployError("No saved frontend-only rollback image is recorded")
    # Manual rollback is no-build and must never read the optional publication
    # sidecar. The saved immutable image is restored with a blank demo pair.
    public_demo_credentials = ("", "")
    prior_public_demo_marker = state.get("public_demo_sidecar_marker", _MISSING)
    previous_public_demo_marker = state.get("previous_public_demo_sidecar_marker", _MISSING)
    checkout = _checkout(config, source_sha)
    live = _container(config)
    if live.get("Image") != current.get("image_id"):
        raise DeployError("The live frontend no longer matches the recorded deployment state")
    try:
        _up_frontend(
            config,
            checkout,
            source_sha,
            image_override=previous["image_ref"],
            public_demo_credentials=public_demo_credentials,
        )
        _verify_running_image(config, previous["image_id"])
        smoke_test(config["base_url"])
        prior_current = {**current, "source_sha": source_sha}
        state["current"], state["previous"] = previous, prior_current
        state["current_sha"] = previous.get("source_sha")
        _set_optional_state_value(
            state,
            "public_demo_sidecar_marker",
            previous_public_demo_marker,
        )
        _set_optional_state_value(
            state,
            "previous_public_demo_sidecar_marker",
            prior_public_demo_marker,
        )
        state["last_result"] = "manual frontend rollback"
        _save_state(config, state)
    except Exception as error:
        _synchronize_state_after_rollback_failure(
            config,
            state,
            prior_current=current,
            intended_current=previous,
            prior_source_sha=source_sha,
            prior_public_demo_marker=prior_public_demo_marker,
            intended_public_demo_marker=previous_public_demo_marker,
        )
        if isinstance(error, DeployError):
            raise
        raise DeployError("The manual frontend rollback failed closed") from None
    print(f"Restored frontend image={previous['image_id']}; no other service was targeted")
    return 0


def _synchronize_state_after_rollback_failure(
    config: dict,
    state: dict,
    *,
    prior_current: dict,
    intended_current: dict,
    prior_source_sha: str,
    prior_public_demo_marker: object = _MISSING,
    intended_public_demo_marker: object = _MISSING,
) -> None:
    """Record the observed runtime, or mark it unknown, before failing closed."""
    try:
        live = _container(config)
    except Exception:
        state["current"] = None
        state["current_sha"] = None
        state["last_result"] = "manual frontend rollback failed; live image is unknown"
    else:
        image_id = live.get("Image")
        recorded_prior_current = {
            **prior_current,
            "source_sha": prior_source_sha,
        }
        if image_id == intended_current.get("image_id"):
            state["current"] = intended_current
            state["previous"] = recorded_prior_current
            state["current_sha"] = intended_current.get("source_sha")
            _set_optional_state_value(
                state,
                "public_demo_sidecar_marker",
                intended_public_demo_marker,
            )
            _set_optional_state_value(
                state,
                "previous_public_demo_sidecar_marker",
                prior_public_demo_marker,
            )
        elif image_id == prior_current.get("image_id"):
            state["current"] = recorded_prior_current
            state["previous"] = intended_current
            state["current_sha"] = prior_source_sha
            _set_optional_state_value(
                state,
                "public_demo_sidecar_marker",
                prior_public_demo_marker,
            )
            _set_optional_state_value(
                state,
                "previous_public_demo_sidecar_marker",
                intended_public_demo_marker,
            )
        else:
            image_ref = live.get("Config", {}).get("Image")
            labels = live.get("Config", {}).get("Labels") or {}
            revision_value = labels.get("org.opencontainers.image.revision", "")
            revision = revision_value.lower() if isinstance(revision_value, str) else ""
            state["current"] = {"image_id": image_id, "image_ref": image_ref}
            state["previous"] = intended_current
            state["current_sha"] = revision if SOURCE_SHA.fullmatch(revision) else None
            _set_optional_state_value(state, "public_demo_sidecar_marker", _MISSING)
            _set_optional_state_value(state, "previous_public_demo_sidecar_marker", _MISSING)
        state["last_result"] = "manual frontend rollback verification failed"

    try:
        _save_state(config, state)
    except Exception:
        raise DeployError("Manual rollback failed and deployment state could not be synchronized") from None
    if state["current"] is None:
        raise DeployError("Manual rollback failed; live image read-back failed and state was marked unknown")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Protected local dispatcher configuration JSON")
    parser.add_argument("--rollback", action="store_true", help="Restore only the saved previous frontend image")
    parser.add_argument("--once", action="store_true", help="Poll once; used by the owner-side user timer")
    args = parser.parse_args()
    path = Path(args.config)
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise DeployError("The local dispatcher configuration cannot be read") from None
    required = (
        "repository",
        "repo_url",
        "production_container",
        "expected_project",
        "expected_networks",
        "production_env_file",
        "production_compose_override",
        "expected_compose_contract_sha256",
        "frontend_bootstrap_mode",
        "original_project_dir",
        "original_compose_file",
        "expected_api_base_url",
        "release_root",
        "state_file",
        "lock_file",
        "base_url",
        "agent_uid",
    )
    if any(key not in config for key in required):
        raise DeployError("The local dispatcher configuration is incomplete")
    if config["frontend_bootstrap_mode"] not in ("exact-sha", "legacy-override-update-required"):
        raise DeployError("The local frontend bootstrap mode is invalid")
    if args.rollback:
        return rollback_frontend(config)
    return dispatch_once(config)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DeployError, GateError, OSError, ValueError, KeyError) as error:
        print(f"BeLib frontend CD failed closed: {error}", file=sys.stderr)
        raise SystemExit(1)
