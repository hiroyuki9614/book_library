#!/usr/bin/env python3
"""Validate staging admission evidence and write a receipt for the existing supervisor."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REPOSITORY = "hiroyuki9614/book_library"
REPOSITORY_OWNER = "hiroyuki9614"
STAGING_CI_PATH = ".github/workflows/staging-ci.yml"
STAGING_CI_NAME = "BeLib staging PR CI"
REQUIRED_CI_JOBS = (
    "Staging Backend",
    "Staging Frontend",
    "Reader Playwright",
    "Staging Compose Isolation",
)
STAGING_HOST = "ubuntu-worker"
STAGING_PRINCIPAL = "hiroyuki"
COMPOSE_PROJECT = "belib-isolated-i5"
LOCK_GROUP = "belib-staging-cd-belib-isolated-i5"
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")


class HandoffError(RuntimeError):
    """Raised when staging admission evidence is missing or inconsistent."""


class GitHubAPI:
    def __init__(self, token: str, api_url: str = "https://api.github.com") -> None:
        if api_url.rstrip("/") != "https://api.github.com":
            raise HandoffError("Only the configured GitHub API host is allowed")
        if not token:
            raise HandoffError("Read-only GitHub token is unavailable")
        self.token = token
        self.api_url = api_url.rstrip("/")

    def get(self, path: str) -> object:
        request = Request(
            f"{self.api_url}{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "belib-staging-cd-handoff",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise HandoffError(f"GitHub API request failed with HTTP {error.code}") from None
        except (URLError, TimeoutError, json.JSONDecodeError):
            raise HandoffError("GitHub API request failed") from None

    def pages(self, path: str) -> list[dict]:
        result: list[dict] = []
        for page in range(1, 21):
            separator = "&" if "?" in path else "?"
            response = self.get(f"{path}{separator}{urlencode({'per_page': 100, 'page': page})}")
            if not isinstance(response, list) or any(not isinstance(item, dict) for item in response):
                raise HandoffError("GitHub API returned an invalid list")
            result.extend(response)
            if len(response) < 100:
                return result
        raise HandoffError("GitHub API pagination exceeded the supported limit")


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and bool(SHA_PATTERN.fullmatch(value))


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _full_name(value: object) -> str:
    return value.get("full_name", "") if isinstance(value, dict) else ""


def _identity_login(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    login = value.get("login")
    return login if isinstance(login, str) else ""


def _workflow_path_matches(run: dict, path: str) -> bool:
    actual = run.get("path")
    if actual == path:
        return True
    prefix = f"{path}@"
    return isinstance(actual, str) and actual.startswith(prefix) and bool(actual[len(prefix) :])


def _review_pages(api: GitHubAPI, pull_number: int) -> list[dict]:
    return api.pages(f"/repos/{REPOSITORY}/pulls/{pull_number}/reviews")


def _current_review_state(reviews: list[dict]) -> dict[str, dict]:
    latest: dict[str, tuple[datetime, int, dict]] = {}
    for review in reviews:
        state = review.get("state")
        if state not in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            continue
        user = review.get("user")
        login = _identity_login(user)
        submitted_at = review.get("submitted_at")
        review_id = review.get("id")
        commit_id = review.get("commit_id")
        if (
            not login
            or not isinstance(submitted_at, str)
            or not submitted_at
            or not _positive_int(review_id)
            or not _is_sha(commit_id)
        ):
            raise HandoffError("Pull request review evidence is incomplete")
        try:
            if not submitted_at.endswith("Z"):
                raise ValueError
            submitted_time = datetime.fromisoformat(submitted_at[:-1] + "+00:00")
        except ValueError:
            raise HandoffError("Pull request review timestamp is invalid") from None
        key = login.casefold()
        current = latest.get(key)
        if current is None or (submitted_time, review_id) > current[:2]:
            latest[key] = (submitted_time, review_id, review)
    return {login: item[2] for login, item in latest.items()}


def _independent_review(api: GitHubAPI, pull_number: int, author_login: str, source_sha: str) -> dict:
    reviews = _current_review_state(_review_pages(api, pull_number))
    if any(review.get("state") == "CHANGES_REQUESTED" for review in reviews.values()):
        raise HandoffError("An unresolved changes-requested review blocks staging")

    eligible = []
    for review in reviews.values():
        login = _identity_login(review.get("user"))
        association = review.get("author_association")
        if (
            review.get("state") == "APPROVED"
            and login.casefold() != author_login.casefold()
            and association in {"COLLABORATOR", "OWNER"}
            and review.get("commit_id") == source_sha
        ):
            eligible.append(review)
    if not eligible:
        raise HandoffError("No independent collaborator or owner approved this exact PR head")
    review = max(eligible, key=lambda item: (item["submitted_at"], item["id"]))
    return {
        "login": _identity_login(review["user"]),
        "review_id": review["id"],
        "author_association": review["author_association"],
        "commit_sha": review["commit_id"],
        "submitted_at": review["submitted_at"],
    }


def _pull_request(api: GitHubAPI, pull_number: int, source_sha: str) -> dict:
    pull = api.get(f"/repos/{REPOSITORY}/pulls/{pull_number}")
    if not isinstance(pull, dict):
        raise HandoffError("Pull request evidence is invalid")
    base = pull.get("base")
    head = pull.get("head")
    author = pull.get("user")
    if (
        pull.get("number") != pull_number
        or pull.get("state") != "open"
        or not isinstance(base, dict)
        or base.get("ref") != "main"
        or _full_name(base.get("repo")) != REPOSITORY
        or not isinstance(head, dict)
        or head.get("sha") != source_sha
        or _full_name(head.get("repo")) != REPOSITORY
        or not isinstance(head.get("ref"), str)
        or not head.get("ref")
        or not _identity_login(author)
    ):
        raise HandoffError("Open same-repository PR head or main base does not match")
    return pull


def _run_matches_pull_request(run: dict, pull_number: int, pull: dict, source_sha: str) -> bool:
    pull_requests = run.get("pull_requests")
    if not isinstance(pull_requests, list):
        return False
    pr_head = pull.get("head", {})
    for run_pull in pull_requests:
        if not isinstance(run_pull, dict) or run_pull.get("number") != pull_number:
            continue
        head = run_pull.get("head")
        base = run_pull.get("base")
        if (
            isinstance(head, dict)
            and isinstance(base, dict)
            and head.get("sha") == source_sha
            and head.get("ref") == pr_head.get("ref")
            and _full_name(head.get("repo")) == REPOSITORY
            and base.get("ref") == "main"
            and _full_name(base.get("repo")) == REPOSITORY
        ):
            return True
    return False


def _latest_ci_run(api: GitHubAPI, pull_number: int, pull: dict, source_sha: str) -> dict:
    workflow = api.get(
        f"/repos/{REPOSITORY}/actions/workflows/{STAGING_CI_PATH.rsplit('/', 1)[-1]}"
    )
    if (
        not isinstance(workflow, dict)
        or workflow.get("path") != STAGING_CI_PATH
        or not _positive_int(workflow.get("id"))
    ):
        raise HandoffError("Canonical staging CI workflow identity is missing")
    workflow_id = workflow["id"]
    query = urlencode({"event": "pull_request", "per_page": 100})
    response = api.get(
        f"/repos/{REPOSITORY}/actions/workflows/{workflow_id}/runs?{query}"
    )
    if not isinstance(response, dict) or not isinstance(response.get("workflow_runs"), list):
        raise HandoffError("Staging CI run evidence is invalid")

    matching = [
        run
        for run in response["workflow_runs"]
        if isinstance(run, dict)
        and run.get("event") == "pull_request"
        and run.get("workflow_id") == workflow_id
        and _workflow_path_matches(run, STAGING_CI_PATH)
        and run.get("name") == STAGING_CI_NAME
        and run.get("head_branch") == pull["head"]["ref"]
        and _full_name(run.get("head_repository")) == REPOSITORY
        and _run_matches_pull_request(run, pull_number, pull, source_sha)
        and _positive_int(run.get("id"))
        and _positive_int(run.get("run_number"))
        and _positive_int(run.get("run_attempt"))
    ]
    if not matching:
        raise HandoffError("No staging CI run proves the exact PR head")
    run = max(
        matching,
        key=lambda item: (item["run_number"], item["run_attempt"], item["id"]),
    )
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise HandoffError("Latest staging CI run for this exact PR head is not green")

    jobs_response = api.get(
        f"/repos/{REPOSITORY}/actions/runs/{run['id']}/jobs?per_page=100"
    )
    if not isinstance(jobs_response, dict) or not isinstance(jobs_response.get("jobs"), list):
        raise HandoffError("Staging CI job evidence is invalid")
    jobs = {
        job.get("name"): job.get("conclusion")
        for job in jobs_response["jobs"]
        if isinstance(job, dict) and isinstance(job.get("name"), str)
    }
    if any(jobs.get(name) != "success" for name in REQUIRED_CI_JOBS):
        raise HandoffError("One or more required exact-head staging CI jobs are not green")
    return run


def validate_handoff(
    api: GitHubAPI,
    *,
    repository: str,
    source_sha: str,
    pull_number: int,
    requester: str,
    run_id: str,
    run_attempt: str,
) -> dict:
    if repository != REPOSITORY:
        raise HandoffError("Staging handoff is limited to the BeLib repository")
    if not _is_sha(source_sha):
        raise HandoffError("Source revision must be a full lowercase Git SHA")
    if type(pull_number) is not int or pull_number <= 0:
        raise HandoffError("Pull request number must be positive")
    if not isinstance(requester, str) or requester.casefold() != REPOSITORY_OWNER.casefold():
        raise HandoffError("Only the repository owner can approve staging")
    if not re.fullmatch(r"[0-9]+", run_id) or int(run_id) <= 0:
        raise HandoffError("GitHub Actions run identity is missing")
    if not re.fullmatch(r"[0-9]+", run_attempt) or int(run_attempt) <= 0:
        raise HandoffError("GitHub Actions run attempt is missing")

    repo = api.get(f"/repos/{REPOSITORY}")
    if (
        not isinstance(repo, dict)
        or repo.get("full_name") != REPOSITORY
        or _identity_login(repo.get("owner")).casefold() != REPOSITORY_OWNER.casefold()
        or (isinstance(repo.get("owner"), dict) and repo["owner"].get("type") != "User")
    ):
        raise HandoffError("Repository owner identity does not match the configured owner")

    pull = _pull_request(api, pull_number, source_sha)
    ci_run = _latest_ci_run(api, pull_number, pull, source_sha)
    review = _independent_review(
        api, pull_number, _identity_login(pull.get("user")), source_sha
    )
    latest_pull = _pull_request(api, pull_number, source_sha)
    if latest_pull["head"]["ref"] != pull["head"]["ref"]:
        raise HandoffError("Pull request head moved during staging admission")
    receipt_seed = (
        f"{REPOSITORY}:{pull_number}:{source_sha}:{run_id}:{run_attempt}"
    ).encode("utf-8")
    receipt_id = "belib-staging-" + hashlib.sha256(receipt_seed).hexdigest()
    return {
        "schema": "belib-staging-handoff/v1",
        "receipt_id": receipt_id,
        "status": "admission-approved-awaiting-existing-supervisor",
        "source": {
            "repository": REPOSITORY,
            "pull_request": pull_number,
            "sha": source_sha,
        },
        "evidence": {
            "staging_ci": {
                "workflow_path": STAGING_CI_PATH,
                "run_id": ci_run["id"],
                "run_attempt": ci_run["run_attempt"],
                "run_url": f"https://github.com/{REPOSITORY}/actions/runs/{ci_run['id']}",
                "required_jobs": list(REQUIRED_CI_JOBS),
            },
            "independent_review": review,
            "owner_staging_approval": {
                "login": REPOSITORY_OWNER,
                "actions_run_id": int(run_id),
                "actions_run_attempt": int(run_attempt),
            },
        },
        "lock": {
            "github_concurrency_group": LOCK_GROUP,
            "receipt_id": receipt_id,
        },
        "target": {
            "host": STAGING_HOST,
            "principal": STAGING_PRINCIPAL,
            "compose_project": COMPOSE_PROJECT,
            "transport": "existing staging supervisor",
        },
        "deployment": {
            "started": False,
            "asserted": False,
        },
    }


def write_receipt(path: Path, receipt: dict) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        raise HandoffError("Could not create a new staging handoff receipt") from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(receipt, output, sort_keys=True, separators=(",", ":"))
        output.write("\n")


def validate_dispatch_context(
    *, event_name: str, ref: str, actor: str, explicit_approval: str, event: object
) -> None:
    if event_name != "workflow_dispatch" or ref != "refs/heads/main":
        raise HandoffError("Handoff must be manually dispatched from trusted main")
    if not isinstance(actor, str) or actor.casefold() != REPOSITORY_OWNER.casefold():
        raise HandoffError("Only the repository owner can approve staging")
    if not isinstance(explicit_approval, str) or explicit_approval.lower() != "true":
        raise HandoffError("Explicit owner staging approval is required")
    if not isinstance(event, dict):
        raise HandoffError("GitHub event payload is invalid")
    sender = event.get("sender")
    repository = event.get("repository")
    if (
        _identity_login(sender).casefold() != actor.casefold()
        or _full_name(repository) != REPOSITORY
    ):
        raise HandoffError("Workflow dispatch identity or repository does not match")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt-path", type=Path, required=True)
    args = parser.parse_args()
    try:
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        event_name = os.environ.get("GITHUB_EVENT_NAME", "")
        ref = os.environ.get("GITHUB_REF", "")
        actor = os.environ.get("GITHUB_ACTOR", "")
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
        source_sha = os.environ.get("INPUT_SOURCE_SHA", "")
        pull_number_text = os.environ.get("INPUT_PULL_REQUEST_NUMBER", "")
        explicit_approval = os.environ.get("INPUT_APPROVE_STAGING", "").lower()

        event_path = os.environ.get("GITHUB_EVENT_PATH", "")
        if not event_path:
            raise HandoffError("GitHub event payload is unavailable")
        try:
            event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise HandoffError("GitHub event payload is invalid") from None
        validate_dispatch_context(
            event_name=event_name,
            ref=ref,
            actor=actor,
            explicit_approval=explicit_approval,
            event=event,
        )
        if not pull_number_text.isdigit():
            raise HandoffError("Pull request number is missing")

        api = GitHubAPI(os.environ.get("GITHUB_TOKEN", ""))
        receipt = validate_handoff(
            api,
            repository=repository,
            source_sha=source_sha,
            pull_number=int(pull_number_text),
            requester=actor,
            run_id=run_id,
            run_attempt=run_attempt,
        )
        write_receipt(args.receipt_path, receipt)
    except (HandoffError, OSError, ValueError) as error:
        print(f"Staging CD handoff rejected: {error}", file=sys.stderr)
        return 1

    print(
        "STAGING_HANDOFF_ADMITTED "
        f"PR=#{receipt['source']['pull_request']} "
        f"source_sha={receipt['source']['sha']} "
        f"receipt_id={receipt['receipt_id']} "
        "deployment=not_started"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
