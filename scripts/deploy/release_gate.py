"""Read-only validation of a BeLib production release candidate."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
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
TRUSTED_REPOSITORY_OWNER_LOGIN = "hiroyuki9614"
SOLO_REVIEW_MARKER = "BELIB-SOLO-REVIEW v1"
SOLO_RELEASE_MARKER = "BELIB-SOLO-RELEASE v1"
SOLO_REVOKE_MARKER = "BELIB-SOLO-REVOKE v1"
SOLO_POLICY_COMMENT_MARKERS = (
    SOLO_REVIEW_MARKER,
    SOLO_RELEASE_MARKER,
    SOLO_REVOKE_MARKER,
)
SOLO_POLICY_COMMENT_MAX_BYTES = 4096
REVIEW_SESSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class GateError(RuntimeError):
    """Raised when a source revision does not satisfy the release gates."""


class TransientGitHubAPIError(GateError):
    """Raised when GitHub cannot be queried reliably yet."""


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
            if type(error.code) is int and (error.code == 429 or 500 <= error.code <= 599):
                raise TransientGitHubAPIError(
                    f"GitHub API request failed with HTTP {error.code}"
                ) from None
            raise GateError(f"GitHub API request failed with HTTP {error.code}") from None
        except (URLError, TimeoutError):
            raise TransientGitHubAPIError("GitHub API request failed") from None
        except json.JSONDecodeError:
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


def _is_full_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(
        char in "0123456789abcdef" for char in value.lower()
    )


def _is_positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _is_github_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value
    ):
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return True


def _verify_matching_github_identity(
    summary_identity: object, detail_identity: object, label: str
) -> None:
    if not isinstance(summary_identity, dict) or not isinstance(detail_identity, dict):
        raise GateError(f"The pull-request detail {label} identity is missing or invalid")
    summary_login = summary_identity.get("login")
    detail_login = detail_identity.get("login")
    if (
        not isinstance(summary_login, str)
        or not summary_login
        or not isinstance(detail_login, str)
        or not detail_login
    ):
        raise GateError(f"The pull-request detail {label} identity is missing or invalid")
    if summary_login != detail_login:
        raise GateError(f"The pull-request detail {label} identity does not match the summary")
    for identity_field in ("id", "node_id", "type"):
        summary_value = summary_identity.get(identity_field)
        detail_value = detail_identity.get(identity_field)
        if (
            summary_value is not None
            and detail_value is not None
            and summary_value != detail_value
        ):
            raise GateError(f"The pull-request detail {label} identity does not match the summary")


def _pull_base_identity(pull: dict) -> tuple[object, object]:
    base = pull.get("base")
    if not isinstance(base, dict):
        return None, None
    repository = base.get("repo")
    if not isinstance(repository, dict):
        return None, None
    return base.get("ref"), repository.get("full_name")


def _verify_merged_pr_detail(
    api: GitHubAPI, summary: dict, source_sha: str, pull_number: int
) -> dict:
    detail = api.get(f"/repos/{api.repository}/pulls/{pull_number}")
    if not isinstance(detail, dict):
        raise GateError("The pull-request detail is missing or invalid")
    if detail.get("number") != pull_number:
        raise GateError("The pull-request detail PR number does not match the summary")

    summary_merged_at = summary.get("merged_at")
    detail_merged_at = detail.get("merged_at")
    if (
        detail.get("state") != "closed"
        or not _is_github_timestamp(summary_merged_at)
        or not _is_github_timestamp(detail_merged_at)
        or detail_merged_at != summary_merged_at
    ):
        raise GateError("The merged PR detail has invalid merged status or timestamp")

    summary_base_ref, summary_base_repository = _pull_base_identity(summary)
    detail_base_ref, detail_base_repository = _pull_base_identity(detail)
    if (
        summary_base_ref != "main"
        or summary_base_repository != api.repository
        or detail_base_ref != "main"
        or detail_base_repository != api.repository
        or detail_base_ref != summary_base_ref
        or detail_base_repository != summary_base_repository
    ):
        raise GateError("The pull-request detail base ref or repository is inconsistent")

    summary_merge_sha = summary.get("merge_commit_sha")
    detail_merge_sha = detail.get("merge_commit_sha")
    if (
        not _is_full_sha(summary_merge_sha)
        or summary_merge_sha.lower() != source_sha
        or not _is_full_sha(detail_merge_sha)
        or detail_merge_sha.lower() != source_sha
        or detail_merge_sha.lower() != summary_merge_sha.lower()
    ):
        raise GateError("The pull-request detail merge commit SHA is inconsistent")

    summary_head = summary.get("head")
    detail_head = detail.get("head")
    if (
        not isinstance(summary_head, dict)
        or not isinstance(detail_head, dict)
        or not _is_full_sha(summary_head.get("sha"))
        or not _is_full_sha(detail_head.get("sha"))
        or not isinstance(summary_head.get("ref"), str)
        or not summary_head.get("ref")
        or not isinstance(detail_head.get("ref"), str)
        or not detail_head.get("ref")
        or detail_head.get("sha").lower() != summary_head.get("sha").lower()
        or detail_head.get("ref") != summary_head.get("ref")
    ):
        raise GateError("The pull-request detail head SHA or ref is inconsistent")

    _verify_matching_github_identity(summary.get("user"), detail.get("user"), "author")
    for pull_object in (summary, detail):
        association = pull_object.get("author_association")
        if association is not None and (
            not isinstance(association, str) or not association
        ):
            raise GateError("The pull-request detail author association is invalid")
    if (
        summary.get("author_association") is not None
        and detail.get("author_association") is not None
        and summary.get("author_association") != detail.get("author_association")
    ):
        raise GateError("The pull-request detail author association is inconsistent")

    summary_merged_by = summary.get("merged_by")
    detail_merged_by = detail.get("merged_by")
    if summary_merged_by is not None:
        _verify_matching_github_identity(summary_merged_by, detail_merged_by, "merger")
    elif detail_merged_by is not None:
        if not isinstance(detail_merged_by, dict) or not isinstance(
            detail_merged_by.get("login"), str
        ) or not detail_merged_by.get("login"):
            raise GateError("The pull-request detail merger identity is invalid")

    return detail


def _verify_merged_pr(api: GitHubAPI, source_sha: str) -> dict:
    associated = api.get(
        f"/repos/{api.repository}/commits/{source_sha}/pulls?per_page=100"
    )
    if not isinstance(associated, list) or any(
        not isinstance(pull, dict) for pull in associated
    ):
        raise GateError("The associated pull-request response is invalid")
    matches = [
        pull
        for pull in associated
        if _pull_base_identity(pull) == ("main", api.repository)
        and _is_github_timestamp(pull.get("merged_at"))
        and pull.get("state") in (None, "closed")
        and _is_full_sha(pull.get("merge_commit_sha"))
        and pull.get("merge_commit_sha").lower() == source_sha
    ]
    if len(matches) != 1:
        raise GateError("The source SHA is not the merge commit of exactly one main PR")
    pull_number = matches[0].get("number")
    if not _is_positive_int(pull_number):
        raise GateError("The merged PR identity is missing or invalid")
    return _verify_merged_pr_detail(api, matches[0], source_sha, pull_number)


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


def _pull_request_comments(api: GitHubAPI, pull_number: int) -> list[dict]:
    comments: list[dict] = []
    for page in range(1, 101):
        query = urlencode({"per_page": 100, "page": page})
        response = api.get(
            f"/repos/{api.repository}/issues/{pull_number}/comments?{query}"
        )
        if not isinstance(response, list):
            raise GateError("The pull-request comment history has an invalid response")
        comments.extend(response)
        if len(response) < 100:
            return comments
    raise GateError("The pull-request comment history exceeds the validation limit")


def _parse_github_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value
    ):
        raise GateError("Solo-owner evidence has an invalid comment timestamp")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise GateError("Solo-owner evidence has an invalid comment timestamp") from None


def _parse_structured_comment_body(body: object, marker: str) -> dict:
    if not isinstance(body, str) or len(body.encode("utf-8")) > SOLO_POLICY_COMMENT_MAX_BYTES:
        raise GateError("Solo-owner evidence comment is too large or malformed")
    try:
        expected_prefix, serialized = body.split("\n", 1)
    except ValueError:
        raise GateError("Solo-owner evidence comment has an invalid structure") from None
    # GitHub can preserve CRLF from owner-authored comment editors. Accept
    # either separator, but do not normalize the canonical JSON payload.
    if expected_prefix.endswith("\r"):
        expected_prefix = expected_prefix[:-1]
    if (
        expected_prefix != marker
        or "\n" in serialized
        or "\r" in serialized
        or not serialized
    ):
        raise GateError("Solo-owner evidence comment has an invalid structure")
    try:
        payload = json.loads(serialized)
    except json.JSONDecodeError:
        raise GateError("Solo-owner evidence comment has invalid JSON") from None
    if not isinstance(payload, dict):
        raise GateError("Solo-owner evidence comment must contain a JSON object")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if serialized != canonical:
        raise GateError("Solo-owner evidence comment is not in the canonical format")
    return payload


def _validate_policy_identity(
    payload: dict, *, api: GitHubAPI, pull_number: int, head_sha: str
) -> bool:
    repository = payload.get("repository")
    pr_number = payload.get("pr")
    candidate_sha = payload.get("head_sha")
    if repository != api.repository:
        raise GateError("Solo-owner evidence names a different repository")
    if type(pr_number) is not int or pr_number != pull_number:
        raise GateError("Solo-owner evidence names a different pull request")
    if not _is_full_sha(candidate_sha):
        raise GateError("Solo-owner evidence does not contain a full PR head SHA")
    return candidate_sha == head_sha


def _validate_policy_comment_metadata(comment: dict, owner_login: str) -> datetime:
    if not _is_positive_int(comment.get("id")):
        raise GateError("Solo-owner evidence comment has an invalid ID")
    user = comment.get("user")
    if (
        not isinstance(user, dict)
        or user.get("login") != owner_login
        or user.get("type") != "User"
        or comment.get("author_association") != "OWNER"
    ):
        raise GateError("Solo-owner evidence must be authored by the repository owner")
    created_at = comment.get("created_at")
    updated_at = comment.get("updated_at")
    if created_at != updated_at:
        raise GateError("Solo-owner evidence comment was edited")
    return _parse_github_timestamp(created_at)


def _repository_owner_login(api: GitHubAPI) -> str:
    if api.repository != REPOSITORY:
        raise GateError("Solo-owner policy is not configured for this repository")
    repository = api.get(f"/repos/{api.repository}")
    owner = repository.get("owner") if isinstance(repository, dict) else None
    if (
        not isinstance(repository, dict)
        or repository.get("full_name") != REPOSITORY
        or not isinstance(owner, dict)
        or owner.get("login") != TRUSTED_REPOSITORY_OWNER_LOGIN
        or owner.get("type") != "User"
    ):
        raise GateError("The trusted repository owner identity is missing or invalid")
    return TRUSTED_REPOSITORY_OWNER_LOGIN


def _validate_solo_review_payload(payload: dict, *, head_sha: str) -> None:
    required = {
        "evidence_sha256",
        "head_sha",
        "pr",
        "repository",
        "result",
        "review_session",
        "review_type",
        "unresolved_high_findings",
    }
    if set(payload) != required:
        raise GateError("Solo-owner AI review evidence has an invalid schema")
    if payload["head_sha"] != head_sha:
        raise GateError("Solo-owner AI review evidence is stale for the exact PR head")
    if payload["result"] != "PASS" or payload["review_type"] != "independent-ai":
        raise GateError("Solo-owner AI review evidence is not an independent PASS")
    if (
        type(payload["unresolved_high_findings"]) is not int
        or payload["unresolved_high_findings"] != 0
    ):
        raise GateError("Solo-owner AI review evidence has unresolved high findings")
    if not isinstance(payload["review_session"], str) or not REVIEW_SESSION_PATTERN.fullmatch(
        payload["review_session"]
    ):
        raise GateError("Solo-owner AI review evidence has an invalid reviewer session")
    if payload["review_session"].lower() in {"self-review", "manual-review", "unknown"}:
        raise GateError("Solo-owner AI review evidence does not identify an independent session")
    if not isinstance(payload["evidence_sha256"], str) or not SHA256_PATTERN.fullmatch(
        payload["evidence_sha256"]
    ):
        raise GateError("Solo-owner AI review evidence has an invalid evidence digest")


def _validate_solo_release_payload(payload: dict, *, head_sha: str) -> None:
    required = {"decision", "head_sha", "pr", "repository", "review_comment_id"}
    if set(payload) != required:
        raise GateError("Solo-owner release approval has an invalid schema")
    if payload["head_sha"] != head_sha:
        raise GateError("Solo-owner release approval is stale for the exact PR head")
    if payload["decision"] != "RELEASE" or not _is_positive_int(payload["review_comment_id"]):
        raise GateError("Solo-owner release approval is not an explicit RELEASE decision")


def _validate_solo_revoke_payload(payload: dict, *, head_sha: str) -> None:
    required = {"comment_id", "head_sha", "pr", "repository"}
    if set(payload) != required or payload["head_sha"] != head_sha:
        raise GateError("Solo-owner revocation has an invalid schema")
    if not _is_positive_int(payload["comment_id"]):
        raise GateError("Solo-owner revocation has an invalid comment ID")


def _verify_solo_owner_policy(
    api: GitHubAPI, pull: dict, head_sha: str, reviews: list[dict]
) -> None:
    owner_login = _repository_owner_login(api)
    merged_by = pull.get("merged_by")
    if (
        not isinstance(merged_by, dict)
        or merged_by.get("login") != owner_login
        or merged_by.get("type") != "User"
        or (
            "author_association" in merged_by
            and merged_by.get("author_association") != "OWNER"
        )
    ):
        raise GateError("Solo-owner policy requires the trusted owner to be the actual merger")

    author_login = pull.get("user", {}).get("login")
    if any(
        review.get("state") == "APPROVED"
        and review.get("user", {}).get("login") == author_login
        for review in reviews
    ):
        raise GateError(
            "No non-author approval applies: a self GitHub approval cannot replace "
            "independent review"
        )

    review_comments: list[tuple[dict, datetime, dict]] = []
    release_comments: list[tuple[dict, datetime, dict]] = []
    revoke_comments: list[tuple[dict, datetime, dict]] = []
    for comment in _pull_request_comments(api, int(pull["number"])):
        if not isinstance(comment, dict):
            raise GateError("The pull-request comment history has an invalid entry")
        body = comment.get("body")
        marker = next(
            (
                candidate
                for candidate in SOLO_POLICY_COMMENT_MARKERS
                if isinstance(body, str) and body.startswith(candidate)
            ),
            None,
        )
        if marker is None:
            continue
        payload = _parse_structured_comment_body(body, marker)
        if not _validate_policy_identity(
            payload, api=api, pull_number=int(pull["number"]), head_sha=head_sha
        ):
            continue
        created_at = _validate_policy_comment_metadata(comment, owner_login)
        if marker == SOLO_REVIEW_MARKER:
            _validate_solo_review_payload(payload, head_sha=head_sha)
            review_comments.append((comment, created_at, payload))
        elif marker == SOLO_RELEASE_MARKER:
            _validate_solo_release_payload(payload, head_sha=head_sha)
            release_comments.append((comment, created_at, payload))
        else:
            _validate_solo_revoke_payload(payload, head_sha=head_sha)
            revoke_comments.append((comment, created_at, payload))

    if revoke_comments:
        raise GateError("Solo-owner release evidence has been revoked")
    if len(review_comments) > 1 or len(release_comments) > 1:
        raise GateError("multiple current solo-owner review or release comments are contradictory")
    if len(review_comments) != 1 or len(release_comments) != 1:
        raise GateError(
            "No non-author approval applies to the exact merged PR head SHA; no exact PR head "
            "solo-owner evidence pair is present and exactly one "
            "solo-owner review/release evidence pair is required"
        )
    review_comment, review_created_at, _ = review_comments[0]
    release_comment, release_created_at, release_payload = release_comments[0]
    if review_comment["id"] == release_comment["id"]:
        raise GateError("Solo-owner review and release comments must be distinct")
    if release_payload["review_comment_id"] != review_comment["id"]:
        raise GateError("Solo-owner release approval must reference the exact review comment")
    if release_created_at <= review_created_at:
        raise GateError("Solo-owner release approval must follow the review comment")


def _verify_pr_review(api: GitHubAPI, pull: dict, release_workflow_id: int) -> None:
    head = pull.get("head", {})
    raw_head_sha = head.get("sha", "")
    if not _is_full_sha(raw_head_sha):
        raise GateError("The merged PR head SHA is missing or invalid")
    head_sha = raw_head_sha.lower()
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
        _verify_solo_owner_policy(api, pull, head_sha, reviews)

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
    if not _is_full_sha(source_sha):
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
