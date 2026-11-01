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
from urllib.request import ProxyHandler, Request, build_opener

from release_gate import GateError, GitHubAPI, validate_release


REQUEST_REF = re.compile(r"^refs/tags/belib-deploy-request-([0-9]+)-([0-9]+)$")
SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_SERVICES = {"db", "migrate", "backend", "frontend", "nginx"}
EXPECTED_FRONTEND_FIELDS = {
    "build",
    "command",
    "entrypoint",
    "expose",
    "image",
    "logging",
    "networks",
    "restart",
}


class DeployError(RuntimeError):
    """A failed, fail-closed frontend release operation."""


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


def _compose_prefix(config: dict, checkout: Path, override: Path | None = None) -> list[str]:
    command = [
        "docker",
        "compose",
        "--project-name",
        config["expected_project"],
        "--project-directory",
        str(checkout),
        "--file",
        str(checkout / "compose.yaml"),
        "--env-file",
        config["production_env_file"],
    ]
    if override:
        command.extend(["--file", str(override)])
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
        _run(["git", "-C", str(checkout), "fetch", "--depth=1", "origin", source_sha])
        _run(["git", "-C", str(checkout), "checkout", "--detach", "FETCH_HEAD"])
    else:
        checkout.mkdir(mode=0o700)
        _run(["git", "-C", str(checkout), "init", "--quiet"])
        _run(["git", "-C", str(checkout), "remote", "add", "origin", config["repo_url"]])
        _run(["git", "-C", str(checkout), "fetch", "--depth=1", "origin", source_sha])
        _run(["git", "-C", str(checkout), "checkout", "--detach", "FETCH_HEAD"])
    actual = _run(["git", "-C", str(checkout), "rev-parse", "HEAD"]).stdout.strip().lower()
    if actual != source_sha:
        raise DeployError("The isolated checkout does not match the release SHA")
    return checkout


def _http_get(url: str) -> tuple[int, dict[str, str], bytes]:
    request = Request(url, headers={"User-Agent": "belib-frontend-cd"})
    opener = build_opener(ProxyHandler({}))
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


def _write_override(image_ref: str, directory: Path) -> Path:
    path = directory / "frontend-image-override.yaml"
    path.write_text(
        "services:\n  frontend:\n    image: " + json.dumps(image_ref) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _verify_target_image(config: dict, source_sha: str) -> dict:
    expected_ref = f"belib-frontend:{source_sha}"
    image = _image(config, expected_ref)
    labels = image.get("Config", {}).get("Labels") or {}
    if labels.get("org.opencontainers.image.revision", "").lower() != source_sha:
        raise DeployError("The built frontend image revision label does not match the source SHA")
    return image


def _validate_frontend_compose(config: dict, checkout: Path, source_sha: str) -> None:
    """Reject candidate Compose changes that could expand host/runtime authority."""
    resolved = _run_json(
        _compose_prefix(config, checkout) + ["config", "--format", "json"],
        env={
            "VCS_REF": source_sha,
            "BELIB_FRONTEND_IMAGE": f"belib-frontend:{source_sha}",
            # These are build-time public values; do not read demo credentials from the host env.
            "VITE_PUBLIC_DEMO_EMAIL": "",
            "VITE_PUBLIC_DEMO_PASSWORD": "",
        },
    )
    if not isinstance(resolved, dict):
        raise DeployError("The resolved production Compose config has an invalid shape")
    services = resolved.get("services")
    if not isinstance(services, dict) or set(services) != EXPECTED_SERVICES:
        raise DeployError("The candidate Compose service set differs from the approved BeLib project")

    frontend = services.get("frontend")
    if not isinstance(frontend, dict) or set(frontend) != EXPECTED_FRONTEND_FIELDS:
        raise DeployError("The frontend Compose service adds unsupported runtime capabilities")
    if frontend.get("image") != f"belib-frontend:{source_sha}":
        raise DeployError("The frontend Compose image is not tagged with the exact source SHA")
    if frontend.get("command") is not None or frontend.get("entrypoint") is not None:
        raise DeployError("The frontend Compose service may not override the reviewed image command")
    if frontend.get("expose") != ["8080"] or frontend.get("restart") != "unless-stopped":
        raise DeployError("The frontend Compose runtime contract changed")
    if frontend.get("logging") != {
        "driver": "json-file",
        "options": {"max-file": "3", "max-size": "10m"},
    }:
        raise DeployError("The frontend Compose logging contract changed")

    build = frontend.get("build")
    if not isinstance(build, dict) or set(build) != {"args", "context", "dockerfile", "target"}:
        raise DeployError("The frontend build adds unsupported build capabilities")
    if (
        Path(str(build.get("context", ""))).resolve() != (checkout / "frontend").resolve()
        or build.get("dockerfile") != "Dockerfile"
        or build.get("target") != "runtime"
    ):
        raise DeployError("The frontend build must use only the reviewed frontend runtime context")
    args = build.get("args")
    if not isinstance(args, dict) or set(args) != {
        "VCS_REF",
        "VITE_API_BASE_URL",
        "VITE_PUBLIC_DEMO_EMAIL",
        "VITE_PUBLIC_DEMO_PASSWORD",
    }:
        raise DeployError("The frontend build argument set differs from the approved contract")
    if (
        args.get("VCS_REF") != source_sha
        or not isinstance(args.get("VITE_API_BASE_URL"), str)
        or not args.get("VITE_API_BASE_URL")
        or args.get("VITE_PUBLIC_DEMO_EMAIL") not in ("", None)
        or args.get("VITE_PUBLIC_DEMO_PASSWORD") not in ("", None)
    ):
        raise DeployError("The frontend build arguments contain an invalid revision or public value")

    network_refs = frontend.get("networks")
    declared_networks = resolved.get("networks")
    if not isinstance(network_refs, dict) or not isinstance(declared_networks, dict):
        raise DeployError("The resolved frontend network identity is missing")
    if any(value not in (None, {}) for value in network_refs.values()):
        raise DeployError("The frontend Compose network options may not change")
    actual_networks = []
    for name in network_refs:
        network = declared_networks.get(name)
        if not isinstance(network, dict) or not isinstance(network.get("name"), str):
            raise DeployError("A frontend Compose network has no resolved identity")
        actual_networks.append(network["name"])
    if sorted(actual_networks) != sorted(config["expected_networks"]):
        raise DeployError("The candidate frontend networks differ from the approved production target")


def _verify_running_image(config: dict, image_id: str, source_sha: str | None = None) -> dict:
    live = _container(config)
    if live["Image"] != image_id:
        raise DeployError("The live frontend image ID does not match the requested image")
    if source_sha and live.get("Config", {}).get("Image") != f"belib-frontend:{source_sha}":
        raise DeployError("The live frontend image tag does not match the source SHA")
    return live


def _up_frontend(
    config: dict,
    checkout: Path,
    source_sha: str,
    *,
    image_override: str | None = None,
) -> None:
    prefix = _compose_prefix(config, checkout)
    env = {"VCS_REF": source_sha}
    if image_override is not None:
        env["BELIB_FRONTEND_IMAGE"] = image_override
        with tempfile.TemporaryDirectory(prefix="belib-frontend-compose-") as temporary:
            override = _write_override(image_override, Path(temporary))
            _run(
                prefix
                + ["--file", str(override), "up", "--detach", "--no-deps", "--no-build", "frontend"],
                env=env,
            )
    else:
        env["BELIB_FRONTEND_IMAGE"] = f"belib-frontend:{source_sha}"
        _run(prefix + ["up", "--detach", "--no-deps", "--no-build", "frontend"], env=env)


def deploy_frontend(config: dict, source_sha: str, *, smoke=smoke_test, main_sha_reader=None) -> dict:
    if main_sha_reader is None:
        main_sha_reader = _main_sha
    checkout = _checkout(config, source_sha)
    _validate_frontend_compose(config, checkout, source_sha)

    current = _container(config)
    rollback_ref, original_ref = _retain_live_image(config, current)
    if current.get("Config", {}).get("Image") == f"belib-frontend:{source_sha}":
        image = _verify_target_image(config, source_sha)
        if current["Image"] == image.get("Id"):
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
            "VCS_REF": source_sha,
            "BELIB_FRONTEND_IMAGE": f"belib-frontend:{source_sha}",
            # The demo pair is not a CD prerequisite and has not been verified here.
            # Never expose values from the protected host env file in the public bundle.
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
            _validate_network(config)
            if _main_sha(config) != source_sha:
                raise DeployError("Main advanced before the deployment began")
            result = deploy_frontend(config, source_sha)
        except Exception as error:
            state["last_request"] = list(request_id)
            state["last_result"] = "failed"
            _save_state(config, state)
            if isinstance(error, DeployError):
                raise
            raise DeployError("The deployment request failed closed") from None

        previous = {
            "image_id": result["previous_image_id"],
            "image_ref": result["previous_image_ref"],
            "source_sha": state.get("current_sha"),
        }
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
    if os.getuid() != config.get("agent_uid"):
        raise DeployError("Rollback must run as the configured production agent")
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
    _up_frontend(
        config,
        checkout,
        source_sha,
        image_override=previous["image_ref"],
    )
    _verify_running_image(config, previous["image_id"])
    smoke_test(config["base_url"])
    state["current"], state["previous"] = previous, current
    state["current_sha"] = previous.get("source_sha")
    state["last_result"] = "manual frontend rollback"
    _save_state(config, state)
    print(f"Restored frontend image={previous['image_id']}; no other service was targeted")
    return 0


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
        "release_root",
        "state_file",
        "lock_file",
        "base_url",
        "agent_uid",
    )
    if any(key not in config for key in required):
        raise DeployError("The local dispatcher configuration is incomplete")
    if args.rollback:
        return rollback_frontend(config)
    return dispatch_once(config)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DeployError, GateError, OSError, ValueError, KeyError) as error:
        print(f"BeLib frontend CD failed closed: {error}", file=sys.stderr)
        raise SystemExit(1)
