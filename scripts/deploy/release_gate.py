"""Read-only validation of a BeLib production release candidate."""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REPOSITORY = "hiroyuki9614/book_library"
RELEASE_WORKFLOW_PATH = ".github/workflows/release-ci.yml"
REQUIRED_RELEASE_JOBS = (
    "Backend tests, build, Prisma, and migrations",
    "Frontend tests, lint, and build",
    "Reader Playwright E2E",
    "Production images, Compose config, and isolated smoke",
)
REVIEWER_ASSOCIATIONS = {"COLLABORATOR", "OWNER"}


class GateError(RuntimeError):
    """Raised when a source revision does not satisfy the release gates."""


class GitHubAPI:
    def __init__(self, repository: str = REPOSITORY, token: str | None = None) -> None:
        self.repository = repository
        self.api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN")

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "belib-frontend-cd",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(f"{self.api_url}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise GateError(f"GitHub API request failed with HTTP {error.code}") from None
        except (URLError, TimeoutError, json.JSONDecodeError):
            raise GateError("GitHub API request failed") from None

    def get(self, path: str) -> dict:
        return self.request("GET", path)


def _jobs_for_run(api: GitHubAPI, run_id: int) -> dict[str, str]:
    response = api.get(
        f"/repos/{api.repository}/actions/runs/{run_id}/jobs?per_page=100"
    )
    return {job.get("name", ""): job.get("conclusion", "") for job in response.get("jobs", [])}


def _require_release_jobs(api: GitHubAPI, run: dict) -> None:
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise GateError("Release candidate gates have not succeeded for the exact SHA")
    jobs = _jobs_for_run(api, int(run["id"]))
    missing = [name for name in REQUIRED_RELEASE_JOBS if jobs.get(name) != "success"]
    if missing:
        raise GateError("One or more mandatory release jobs did not succeed")


def _matches_workflow_path(run: dict, expected_path: str) -> bool:
    """Match GitHub's path value with or without its documented @ref suffix."""
    actual_path = run.get("path")
    if actual_path == expected_path:
        return True
    suffix_prefix = f"{expected_path}@"
    return isinstance(actual_path, str) and actual_path.startswith(suffix_prefix) and bool(
        actual_path[len(suffix_prefix) :]
    )


def _canonical_release_workflow_id(api: GitHubAPI) -> int:
    workflow = api.get(
        f"/repos/{api.repository}/actions/workflows/{RELEASE_WORKFLOW_PATH.rsplit('/', 1)[-1]}"
    )
    workflow_id = workflow.get("id") if isinstance(workflow, dict) else None
    if (
        not isinstance(workflow, dict)
        or workflow.get("path") != RELEASE_WORKFLOW_PATH
        or type(workflow_id) is not int
        or workflow_id <= 0
    ):
        raise GateError("The canonical release workflow identity is missing or invalid")
    return workflow_id


def _is_release_workflow_run(run: dict, workflow_id: int) -> bool:
    return (
        isinstance(run, dict)
        and type(run.get("workflow_id")) is int
        and run["workflow_id"] == workflow_id
        and _matches_workflow_path(run, RELEASE_WORKFLOW_PATH)
    )


def _latest_run(
    runs: list[dict], *, event: str, branch: str, sha: str, workflow_id: int, path: str
) -> dict:
    matching = [
        run
        for run in runs
        if isinstance(run, dict)
        and run.get("event") == event
        and run.get("head_branch") == branch
        and run.get("head_sha", "").lower() == sha
        and run.get("workflow_id") == workflow_id
        and type(run.get("workflow_id")) is int
        and _matches_workflow_path(run, path)
    ]
    if not matching:
        raise GateError("No matching Release candidate gates run exists for the exact SHA")
    return max(
        matching,
        key=lambda run: (int(run.get("run_number", 0)), int(run.get("run_attempt", 0))),
    )


def _verify_main_run(
    api: GitHubAPI,
    source_sha: str,
    expected_run_id: int | None,
    release_workflow_id: int,
) -> dict:
    query = urlencode({"head_sha": source_sha, "event": "push", "per_page": 100})
    runs = api.get(f"/repos/{api.repository}/actions/runs?{query}").get("workflow_runs", [])
    run = _latest_run(
        runs,
        event="push",
        branch="main",
        sha=source_sha,
        workflow_id=release_workflow_id,
        path=RELEASE_WORKFLOW_PATH,
    )
    if expected_run_id is not None and int(run["id"]) != expected_run_id:
        raise GateError("The completed workflow run does not match the requested source SHA")
    _require_release_jobs(api, run)
    return run


def _verify_merged_pr(api: GitHubAPI, source_sha: str) -> dict:
    associated = api.get(
        f"/repos/{api.repository}/commits/{source_sha}/pulls?per_page=100"
    )
    matches = [
        pull
        for pull in associated
        if pull.get("base", {}).get("ref") == "main"
        and pull.get("base", {}).get("repo", {}).get("full_name") == api.repository
        and pull.get("merged_at")
        and pull.get("merge_commit_sha", "").lower() == source_sha
    ]
    if len(matches) != 1:
        raise GateError("The source SHA is not the merge commit of exactly one main PR")
    return matches[0]


def _pull_request_reviews(api: GitHubAPI, pull_number: int) -> list[dict]:
    reviews: list[dict] = []
    for page in range(1, 101):
        query = urlencode({"per_page": 100, "page": page})
        response = api.get(
            f"/repos/{api.repository}/pulls/{pull_number}/reviews?{query}"
        )
        if not isinstance(response, list):
            raise GateError("The pull-request review history has an invalid response")
        reviews.extend(response)
        if len(response) < 100:
            return reviews
    raise GateError("The pull-request review history exceeds the validation limit")


def _verify_pr_review(api: GitHubAPI, pull: dict, release_workflow_id: int) -> None:
    head = pull.get("head", {})
    head_sha = head.get("sha", "").lower()
    if len(head_sha) != 40:
        raise GateError("The merged PR head SHA is missing or invalid")
    reviews = _pull_request_reviews(api, int(pull["number"]))
    latest_by_user: dict[str, dict] = {}
    for review in reviews:
        reviewer = review.get("user", {}).get("login")
        if not reviewer or reviewer == pull.get("user", {}).get("login"):
            continue
        if review.get("author_association") not in REVIEWER_ASSOCIATIONS:
            continue
        if review.get("state") in {"PENDING", "COMMENTED"}:
            continue
        previous = latest_by_user.get(reviewer)
        if previous is None or (review.get("submitted_at") or "") >= (
            previous.get("submitted_at") or ""
        ):
            latest_by_user[reviewer] = review
    if any(
        review.get("state") == "CHANGES_REQUESTED"
        for review in latest_by_user.values()
    ):
        raise GateError("A non-author reviewer has requested changes that remain unresolved")

    approved = any(
        review.get("state") == "APPROVED"
        and review.get("commit_id", "").lower() == head_sha
        for review in latest_by_user.values()
    )
    if not approved:
        raise GateError("No non-author approval applies to the exact merged PR head SHA")

    query = urlencode({"head_sha": head_sha, "event": "pull_request", "per_page": 100})
    runs = api.get(f"/repos/{api.repository}/actions/runs?{query}").get("workflow_runs", [])
    pr_runs = [
        run
        for run in runs
        if run.get("head_branch") == head.get("ref")
        and run.get("head_sha", "").lower() == head_sha
    ]
    release_runs = [
        run for run in pr_runs if _is_release_workflow_run(run, release_workflow_id)
    ]
    if not release_runs:
        raise GateError("The merged PR head has no Release candidate gates run")
    release_run = max(
        release_runs,
        key=lambda run: (int(run.get("run_number", 0)), int(run.get("run_attempt", 0))),
    )
    _require_release_jobs(api, release_run)

    # Path-focused PR workflows remain supplementary, but any one that actually ran
    # on this exact head must also finish successfully before production promotion.
    latest_by_workflow: dict[int, dict] = {}
    for run in pr_runs:
        if not isinstance(run, dict):
            continue
        workflow_id = run.get("workflow_id")
        if type(workflow_id) is not int or workflow_id <= 0:
            continue
        if _is_release_workflow_run(run, release_workflow_id):
            continue
        previous = latest_by_workflow.get(workflow_id)
        if previous is None or (int(run.get("run_number", 0)), int(run.get("run_attempt", 0))) > (
            int(previous.get("run_number", 0)), int(previous.get("run_attempt", 0))
        ):
            latest_by_workflow[workflow_id] = run
    if any(
        run.get("status") != "completed" or run.get("conclusion") != "success"
        for run in latest_by_workflow.values()
    ):
        raise GateError("A path-focused PR workflow on the exact head SHA is not green")


def validate_release(
    api: GitHubAPI,
    source_sha: str,
    *,
    expected_main_run_id: int | None = None,
) -> dict:
    source_sha = source_sha.lower()
    if len(source_sha) != 40 or any(char not in "0123456789abcdef" for char in source_sha):
        raise GateError("A full 40-character source SHA is required")
    main_ref = api.get(f"/repos/{api.repository}/git/ref/heads/main")
    main_sha = main_ref.get("object", {}).get("sha", "").lower()
    if main_sha != source_sha:
        raise GateError("Stale or out-of-order source SHA; only current main may deploy")
    release_workflow_id = _canonical_release_workflow_id(api)
    main_run = _verify_main_run(api, source_sha, expected_main_run_id, release_workflow_id)
    pull = _verify_merged_pr(api, source_sha)
    _verify_pr_review(api, pull, release_workflow_id)
    return {
        "source_sha": source_sha,
        "main_run_id": int(main_run["id"]),
        "pr_number": int(pull["number"]),
        "pr_head_sha": pull["head"]["sha"].lower(),
    }
