#!/usr/bin/env python3
"""Publish a lightweight, immutable Git tag after release gates have passed."""

from __future__ import annotations

import json
import os
import re
import sys
from urllib.parse import quote

from release_gate import GateError, GitHubAPI, validate_release


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")

    if repository != "hiroyuki9614/book_library":
        raise GateError("This handoff is limited to hiroyuki9614/book_library")
    if not re.fullmatch(r"[0-9]+", run_id) or not re.fullmatch(r"[0-9]+", run_attempt):
        raise GateError("GitHub Actions run identity is missing")
    if not event_path:
        raise GateError("GitHub event payload is unavailable")
    with open(event_path, encoding="utf-8") as event_file:
        event = json.load(event_file)

    expected_main_run_id: int | None = None
    if event_name == "workflow_run":
        workflow_run = event.get("workflow_run", {})
        if (
            workflow_run.get("name") != "Release candidate gates"
            or workflow_run.get("event") != "push"
            or workflow_run.get("head_branch") != "main"
            or workflow_run.get("conclusion") != "success"
            or workflow_run.get("head_repository", {}).get("full_name") != repository
        ):
            raise GateError("Only successful main push gates can create a deployment request")
        source_sha = workflow_run.get("head_sha", "")
        expected_main_run_id = int(workflow_run["id"])
    elif event_name == "workflow_dispatch":
        if os.environ.get("GITHUB_REF") != "refs/heads/main":
            raise GateError("Manual replay must run from the main branch")
        source_sha = os.environ.get("INPUT_SOURCE_SHA", "")
    else:
        raise GateError("Unsupported GitHub event")

    api = GitHubAPI(repository=repository)
    proof = validate_release(
        api,
        source_sha,
        expected_main_run_id=expected_main_run_id,
    )
    latest_main = api.get(f"/repos/{repository}/git/ref/heads/main").get("object", {}).get("sha", "")
    if latest_main.lower() != proof["source_sha"]:
        raise GateError("Main advanced while the release request was being validated")
    request_ref = f"refs/tags/belib-deploy-request-{run_id}-{run_attempt}"
    ref_path = f"/repos/{repository}/git/ref/{quote(request_ref.removeprefix('refs/'), safe='/')}"
    try:
        api.request("POST", f"/repos/{repository}/git/refs", {"ref": request_ref, "sha": proof["source_sha"]})
        result = "created"
    except GateError as error:
        if "HTTP 422" not in str(error):
            raise
        existing = api.get(ref_path)
        if existing.get("object", {}).get("sha", "").lower() != proof["source_sha"]:
            raise GateError("The existing deployment request tag points to a different SHA") from None
        result = "already exists for the same SHA"

    print(
        f"Deployment request {result}: PR #{proof['pr_number']} "
        f"main@{proof['source_sha']} tag={request_ref}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GateError, KeyError, ValueError, OSError) as error:
        print(f"CD handoff rejected: {error}", file=sys.stderr)
        raise SystemExit(1)
