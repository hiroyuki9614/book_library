#!/usr/bin/env python3
"""Bounded KAGOYA pull dispatcher for reviewed BeLib frontend releases."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

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


class DeployError(RuntimeError):
    """A failed, fail-closed frontend release operation."""


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


def _validate_frontend_compose(config: dict, checkout: Path, source_sha: str) -> None:
    """Reject candidate Compose changes that could expand host/runtime authority."""
    compose_prefix = _compose_prefix(config, checkout)
    compose_env = {
        "COMPOSE_PROFILES": "",
        "VCS_REF": source_sha,
        "BELIB_FRONTEND_IMAGE": f"belib-frontend:{source_sha}",
        # These are build-time public values; do not read demo credentials from the host env.
        "VITE_PUBLIC_DEMO_EMAIL": "",
        "VITE_PUBLIC_DEMO_PASSWORD": "",
        "VITE_API_BASE_URL": config["expected_api_base_url"],
    }
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
) -> None:
    prefix = _compose_prefix(config, checkout)
    env = {
        "COMPOSE_PROFILES": "",
        "VCS_REF": source_sha,
        "VITE_API_BASE_URL": config["expected_api_base_url"],
        "VITE_PUBLIC_DEMO_EMAIL": "",
        "VITE_PUBLIC_DEMO_PASSWORD": "",
    }
    if image_override is not None:
        env["BELIB_FRONTEND_IMAGE"] = image_override
    else:
        env["BELIB_FRONTEND_IMAGE"] = f"belib-frontend:{source_sha}"
    _run(prefix + ["up", "--detach", "--no-deps", "--no-build", "frontend"], env=env)


def deploy_frontend(config: dict, source_sha: str, *, smoke=None, main_sha_reader=None) -> dict:
    if main_sha_reader is None:
        main_sha_reader = _main_sha
    if smoke is None:
        smoke = smoke_test
    checkout = _checkout(config, source_sha)
    _validate_frontend_compose(config, checkout, source_sha)

    current = _container(config)
    rollback_ref, original_ref = _retain_live_image(config, current)
    if current.get("Config", {}).get("Image") == f"belib-frontend:{source_sha}":
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
                "no_op": True,
            }

    _run(
        _compose_prefix(config, checkout) + ["build", "frontend"],
        env={
            "COMPOSE_PROFILES": "",
            "VCS_REF": source_sha,
            "BELIB_FRONTEND_IMAGE": f"belib-frontend:{source_sha}",
            # The demo pair is not a CD prerequisite and has not been verified here.
            # Never expose values from the protected host env file in the public bundle.
            "VITE_API_BASE_URL": config["expected_api_base_url"],
            "VITE_PUBLIC_DEMO_EMAIL": "",
            "VITE_PUBLIC_DEMO_PASSWORD": "",
        },
    )
    image = _verify_target_image(config, source_sha)
    if main_sha_reader(config) != source_sha:
        raise DeployError("Main advanced before the frontend switch")
    switched = False
    try:
        switched = True
        _up_frontend(config, checkout, source_sha)
        _verify_running_image(config, image["Id"], source_sha)
        smoke(config["base_url"])
        if main_sha_reader(config) != source_sha:
            raise DeployError("Main advanced during deployment")
    except Exception as error:
        if switched:
            try:
                _up_frontend(config, checkout, source_sha, image_override=rollback_ref)
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
    try:
        live = _container(config)
        image_id = live.get("Image")
        image_ref = live.get("Config", {}).get("Image")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id or "") or not image_ref:
            raise DeployError("The failed deployment live image identity is incomplete")
    except Exception:
        state["current"] = None
        state["current_sha"] = None
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
        state["last_result"] = "failed"

    try:
        _save_state(config, state)
    except Exception:
        raise DeployError("Failed deployment state could not be synchronized") from None


def _main_sha(config: dict) -> str:
    result = _run(["git", "ls-remote", "--refs", config["repo_url"], "refs/heads/main"])
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
            try:
                _validate_network(config)
            except TransientGitHubAPIError:
                raise _DeferredPreSwitchGate(
                    "Transient GitHub release check failure; request retained for next timer run"
                ) from None
            if _main_sha(config) != source_sha:
                raise DeployError("Main advanced before the deployment began")
            result = deploy_frontend(config, source_sha)
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
        state.update(
            {
                "last_request": list(request_id),
                "last_result": "success",
                "current_sha": source_sha,
                "current": {"image_id": result["image_id"], "image_ref": result["image_ref"]},
                "previous": previous,
            }
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
        )
        _verify_running_image(config, previous["image_id"])
        smoke_test(config["base_url"])
        prior_current = {**current, "source_sha": source_sha}
        state["current"], state["previous"] = previous, prior_current
        state["current_sha"] = previous.get("source_sha")
        state["last_result"] = "manual frontend rollback"
        _save_state(config, state)
    except Exception as error:
        _synchronize_state_after_rollback_failure(
            config,
            state,
            prior_current=current,
            intended_current=previous,
            prior_source_sha=source_sha,
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
        elif image_id == prior_current.get("image_id"):
            state["current"] = recorded_prior_current
            state["previous"] = intended_current
            state["current_sha"] = prior_source_sha
        else:
            image_ref = live.get("Config", {}).get("Image")
            labels = live.get("Config", {}).get("Labels") or {}
            revision_value = labels.get("org.opencontainers.image.revision", "")
            revision = revision_value.lower() if isinstance(revision_value, str) else ""
            state["current"] = {"image_id": image_id, "image_ref": image_ref}
            state["previous"] = intended_current
            state["current_sha"] = revision if SOURCE_SHA.fullmatch(revision) else None
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
