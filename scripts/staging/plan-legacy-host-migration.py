#!/usr/bin/env python3
"""Build a review-only disposition plan for BeLib's stale legacy Serve route.

This module is intentionally pure: it does not read files, inspect the host,
contact services, or apply the proposed Serve configuration.
"""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any

SCHEMA = "belib-legacy-host-migration-observation/v1"
PLAN_SCHEMA = "belib-legacy-host-migration-plan/v1"
MAX_INPUT_BYTES = 16_384
OWNER_HOST = "ubuntu-worker"
OWNER_PRINCIPAL = "hiroyuki"
OWNER_UID = 1000
OWNER_TAILNET_DOMAIN = "ubuntu-worker.tail63cd2d.ts.net"
LEGACY_HOST = "fedora-worker.tail63cd2d.ts.net:443"
LEGACY_PATH = "/bri-referee-pilot-comp.png"
LEGACY_ASSET = "/home/hiroyuki/tailscale-share/bri-referee-pilot-comp.png"
FORWARD = "127.0.0.1:18443"
TOP_LEVEL_KEYS = {
    "schema", "source_sha", "actual_host", "actual_principal", "actual_uid",
    "same_physical_device_confirmed", "serve_config", "cert_domains",
    "legacy_asset_observation",
}


class ObservationError(ValueError):
    """The supplied bounded observation is missing or contradicts known facts."""


def _no_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ObservationError("duplicate JSON object key")
        result[key] = value
    return result


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_object(value: Any, name: str, keys: set[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ObservationError(f"{name} must be an object")
    if keys is not None and set(value) != keys:
        raise ObservationError(f"{name} has missing or unknown fields")
    return value


def _validate_config(value: Any) -> dict[str, Any]:
    config = _require_object(value, "serve_config", {"TCP", "Web"})
    tcp = _require_object(config["TCP"], "serve_config.TCP", {"443"})
    _require_object(tcp["443"], "serve_config.TCP.443", {"HTTPS"})
    if tcp["443"]["HTTPS"] is not True:
        raise ObservationError("the observed HTTPS listener is not the known baseline")

    web = _require_object(config["Web"], "serve_config.Web", {LEGACY_HOST})
    server = _require_object(web[LEGACY_HOST], "legacy Web server", {"Handlers"})
    handlers = _require_object(server["Handlers"], "legacy handlers", {LEGACY_PATH})
    handler = _require_object(handlers[LEGACY_PATH], "legacy handler", {"Path"})
    if handler["Path"] != LEGACY_ASSET:
        raise ObservationError("the observed legacy handler differs from the known route")
    return config


def _validate_observation(observation: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    observation = _require_object(observation, "observation", TOP_LEVEL_KEYS)
    if observation["schema"] != SCHEMA:
        raise ObservationError("unsupported observation schema")
    source_sha = observation["source_sha"]
    if not isinstance(source_sha, str) or len(source_sha) != 40 or any(c not in "0123456789abcdef" for c in source_sha):
        raise ObservationError("source_sha must be a pinned 40-character lowercase Git SHA")
    if observation["actual_host"] != OWNER_HOST:
        raise ObservationError("actual_host does not identify the current Ubuntu owner host")
    if observation["actual_principal"] != OWNER_PRINCIPAL or type(observation["actual_uid"]) is not int or observation["actual_uid"] != OWNER_UID:
        raise ObservationError("actual principal and uid do not match the observed nonprivileged owner")
    if observation["same_physical_device_confirmed"] is not True:
        raise ObservationError("same physical device confirmation is required")

    domains = observation["cert_domains"]
    if (not isinstance(domains, list) or domains != [OWNER_TAILNET_DOMAIN]
            or any(not isinstance(domain, str) for domain in domains)):
        raise ObservationError("cert_domains must exactly identify the observed Ubuntu Tailnet host")

    asset = _require_object(
        observation["legacy_asset_observation"], "legacy_asset_observation", {"path", "state"},
    )
    if asset["path"] != LEGACY_ASSET or asset["state"] != "verified_absent":
        raise ObservationError("the exact legacy asset path must be verified absent")

    config = _validate_config(observation["serve_config"])
    return observation, config


def build_plan(observation: Any) -> dict[str, Any]:
    """Return a deterministic, explicitly unapproved migration proposal."""
    observed, current = _validate_observation(observation)
    current_digest = _sha256(_canonical(current))
    proposed = {
        "TCP": {"443": {"TCPForward": FORWARD}},
        "Web": {},
    }
    context = {
        "source_sha": observed["source_sha"],
        "actual_host": observed["actual_host"],
        "actual_principal": observed["actual_principal"],
        "actual_uid": observed["actual_uid"],
        "same_physical_device_confirmed": observed["same_physical_device_confirmed"],
        "cert_domains": observed["cert_domains"],
        "legacy_asset_observation": observed["legacy_asset_observation"],
        "current_config_sha256": current_digest,
    }
    return {
        "schema": PLAN_SCHEMA,
        "status": "APPROVAL_REQUIRED",
        "deployment_authorized": False,
        "source_sha": observed["source_sha"],
        "context_sha256": _sha256(_canonical(context)),
        "current_config_sha256": current_digest,
        "proposed_config": proposed,
        "config_diff": [
            {
                "op": "replace",
                "path": "/TCP/443",
                "from": current["TCP"]["443"],
                "to": proposed["TCP"]["443"],
            },
            {
                "op": "remove",
                "path": f"/Web/{LEGACY_HOST}",
                "from": current["Web"][LEGACY_HOST],
            },
        ],
        "rollback_exact_config": current,
        "owner_decision": "Retirement of the stale Fedora-named BRI Web handler requires explicit owner approval.",
        "readiness_note": (
            "Proposal only. The existing Serve helper and nginx configuration require separate review and adaptation; "
            "this plan is not executable, signed, approved, or evidence of code/runtime acceptance."
        ),
    }


def plan_from_bytes(raw: bytes) -> dict[str, Any]:
    if not isinstance(raw, bytes) or len(raw) > MAX_INPUT_BYTES:
        raise ObservationError("observation exceeds the fixed input bound")
    try:
        observation = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_no_duplicate_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ObservationError("malformed bounded UTF-8 JSON observation") from exc
    return build_plan(observation)


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        plan = plan_from_bytes(raw)
    except ObservationError as exc:
        print(f"plan-legacy-host-migration: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(_canonical(plan) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
