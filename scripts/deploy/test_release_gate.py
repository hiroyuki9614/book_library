import unittest
from urllib.parse import parse_qs, urlsplit

from release_gate import GateError, REQUIRED_RELEASE_JOBS, validate_release


SOURCE_SHA = "a" * 40
PR_HEAD_SHA = "b" * 40
RELEASE_PATH = ".github/workflows/release-ci.yml"
MAIN_RUN_ID = 110
PR_RUN_ID = 210


class FakeAPI:
    repository = "hiroyuki9614/book_library"

    def __init__(self):
        self.main_run = {
            "id": MAIN_RUN_ID,
            "workflow_id": 1,
            "path": RELEASE_PATH,
            "event": "push",
            "head_branch": "main",
            "head_sha": SOURCE_SHA,
            "status": "completed",
            "conclusion": "success",
            "run_number": 11,
            "run_attempt": 1,
        }
        self.pr_run = {
            "id": PR_RUN_ID,
            "workflow_id": 1,
            "path": RELEASE_PATH,
            "event": "pull_request",
            "head_branch": "codex/cd-test",
            "head_sha": PR_HEAD_SHA,
            "status": "completed",
            "conclusion": "success",
            "run_number": 21,
            "run_attempt": 1,
        }
        self.pull = {
            "number": 70,
            "merged_at": "2026-11-02T00:00:00Z",
            "merge_commit_sha": SOURCE_SHA,
            "base": {"ref": "main", "repo": {"full_name": self.repository}},
            "head": {"ref": "codex/cd-test", "sha": PR_HEAD_SHA},
            "user": {"login": "author"},
        }
        self.reviews = [
            {
                "user": {"login": "reviewer"},
                "state": "APPROVED",
                "commit_id": PR_HEAD_SHA,
                "submitted_at": "2026-11-01T23:00:00Z",
                "author_association": "COLLABORATOR",
            }
        ]
        self.main_jobs = {name: "success" for name in REQUIRED_RELEASE_JOBS}
        self.pr_jobs = {name: "success" for name in REQUIRED_RELEASE_JOBS}

    def get(self, path):
        if path == f"/repos/{self.repository}/git/ref/heads/main":
            return {"object": {"sha": SOURCE_SHA}}
        if "/actions/runs?" in path and "event=push" in path:
            return {"workflow_runs": [self.main_run]}
        if path.endswith(f"/actions/runs/{MAIN_RUN_ID}/jobs?per_page=100"):
            return {"jobs": [{"name": name, "conclusion": result} for name, result in self.main_jobs.items()]}
        if f"/commits/{SOURCE_SHA}/pulls?" in path:
            return [self.pull]
        if f"/pulls/70/reviews?" in path:
            page = int(parse_qs(urlsplit(path).query).get("page", ["1"])[0])
            start = (page - 1) * 100
            return self.reviews[start : start + 100]
        if "/actions/runs?" in path and "event=pull_request" in path:
            return {"workflow_runs": [self.pr_run]}
        if path.endswith(f"/actions/runs/{PR_RUN_ID}/jobs?per_page=100"):
            return {"jobs": [{"name": name, "conclusion": result} for name, result in self.pr_jobs.items()]}
        raise AssertionError(f"Unexpected API request: {path}")


class ReleaseGateTests(unittest.TestCase):
    def test_accepts_exact_main_sha_with_green_pr_and_merge_gates(self):
        proof = validate_release(FakeAPI(), SOURCE_SHA, expected_main_run_id=MAIN_RUN_ID)
        self.assertEqual(
            proof,
            {
                "source_sha": SOURCE_SHA,
                "main_run_id": MAIN_RUN_ID,
                "pr_number": 70,
                "pr_head_sha": PR_HEAD_SHA,
            },
        )

    def test_accepts_documented_workflow_path_ref_suffix_for_main_and_pr_runs(self):
        api = FakeAPI()
        api.main_run["path"] = f"{RELEASE_PATH}@refs/heads/main"
        api.pr_run["path"] = f"{RELEASE_PATH}@refs/pull/70/merge"

        proof = validate_release(api, SOURCE_SHA, expected_main_run_id=MAIN_RUN_ID)

        self.assertEqual(proof["source_sha"], SOURCE_SHA)

    def test_rejects_workflow_path_lookalike_with_ref_suffix(self):
        api = FakeAPI()
        api.main_run["path"] = f"{RELEASE_PATH}-untrusted.yml@refs/heads/main"

        with self.assertRaisesRegex(GateError, "No matching Release candidate gates run"):
            validate_release(api, SOURCE_SHA)

    def test_rejects_stale_sha_before_checking_any_run(self):
        api = FakeAPI()
        original_get = api.get

        def stale_main(path):
            result = original_get(path)
            if path.endswith("/git/ref/heads/main"):
                return {"object": {"sha": "c" * 40}}
            return result

        api.get = stale_main
        with self.assertRaisesRegex(GateError, "Stale or out-of-order"):
            validate_release(api, SOURCE_SHA)

    def test_rejects_missing_mandatory_main_job(self):
        api = FakeAPI()
        api.main_jobs[REQUIRED_RELEASE_JOBS[-1]] = "failure"
        with self.assertRaisesRegex(GateError, "mandatory release jobs"):
            validate_release(api, SOURCE_SHA)

    def test_rejects_review_approval_on_an_older_head(self):
        api = FakeAPI()
        api.reviews[0]["commit_id"] = "c" * 40
        with self.assertRaisesRegex(GateError, "exact merged PR head SHA"):
            validate_release(api, SOURCE_SHA)

    def test_rejects_exact_head_approval_from_public_noncollaborator(self):
        api = FakeAPI()
        api.reviews[0]["author_association"] = "NONE"
        with self.assertRaisesRegex(GateError, "No non-author approval applies"):
            validate_release(api, SOURCE_SHA, expected_main_run_id=MAIN_RUN_ID)

    def test_rejects_author_approval_even_on_exact_head(self):
        api = FakeAPI()
        api.pull["user"]["login"] = "reviewer"
        with self.assertRaisesRegex(GateError, "No non-author approval applies"):
            validate_release(api, SOURCE_SHA, expected_main_run_id=MAIN_RUN_ID)

    def test_ignores_changes_request_from_public_noncollaborator(self):
        api = FakeAPI()
        api.reviews.append(
            {
                "user": {"login": "outside-reviewer"},
                "state": "CHANGES_REQUESTED",
                "commit_id": PR_HEAD_SHA,
                "submitted_at": "2026-11-02T00:30:00Z",
                "author_association": "NONE",
            }
        )
        proof = validate_release(api, SOURCE_SHA, expected_main_run_id=MAIN_RUN_ID)
        self.assertEqual(proof["pr_head_sha"], PR_HEAD_SHA)

    def test_rejects_latest_changes_requested_review(self):
        api = FakeAPI()
        api.reviews.append(
            {
                "user": {"login": "reviewer"},
                "state": "CHANGES_REQUESTED",
                "commit_id": PR_HEAD_SHA,
                "submitted_at": "2026-11-02T00:30:00Z",
                "author_association": "COLLABORATOR",
            }
        )
        with self.assertRaisesRegex(GateError, "remain unresolved"):
            validate_release(api, SOURCE_SHA)

    def test_rejects_changes_requested_by_another_reviewer(self):
        api = FakeAPI()
        api.reviews.append(
            {
                "user": {"login": "second-reviewer"},
                "state": "CHANGES_REQUESTED",
                "commit_id": PR_HEAD_SHA,
                "submitted_at": "2026-11-02T00:30:00Z",
                "author_association": "COLLABORATOR",
            }
        )
        with self.assertRaisesRegex(GateError, "remain unresolved"):
            validate_release(api, SOURCE_SHA)

    def test_rejects_outstanding_changes_request_on_an_older_head(self):
        api = FakeAPI()
        api.reviews.append(
            {
                "user": {"login": "second-reviewer"},
                "state": "CHANGES_REQUESTED",
                "commit_id": "c" * 40,
                "submitted_at": "2026-11-02T00:30:00Z",
                "author_association": "COLLABORATOR",
            }
        )
        with self.assertRaisesRegex(GateError, "remain unresolved"):
            validate_release(api, SOURCE_SHA)

    def test_comment_does_not_clear_a_changes_request(self):
        api = FakeAPI()
        api.reviews.extend(
            [
                {
                    "user": {"login": "reviewer"},
                    "state": "CHANGES_REQUESTED",
                    "commit_id": PR_HEAD_SHA,
                    "submitted_at": "2026-11-02T00:30:00Z",
                    "author_association": "COLLABORATOR",
                },
                {
                    "user": {"login": "reviewer"},
                    "state": "COMMENTED",
                    "commit_id": PR_HEAD_SHA,
                    "submitted_at": "2026-11-02T00:40:00Z",
                    "author_association": "COLLABORATOR",
                },
            ]
        )
        with self.assertRaisesRegex(GateError, "remain unresolved"):
            validate_release(api, SOURCE_SHA)

    def test_rejects_changes_requested_review_on_a_later_history_page(self):
        api = FakeAPI()
        api.reviews = [
            {
                "user": {"login": "reviewer"},
                "state": "APPROVED",
                "commit_id": PR_HEAD_SHA,
                "submitted_at": "2026-11-01T23:00:00Z",
                "author_association": "COLLABORATOR",
            }
        ]
        api.reviews.extend(
            {
                "user": {"login": f"commenter-{index}"},
                "state": "COMMENTED",
                "commit_id": PR_HEAD_SHA,
                "submitted_at": f"2026-11-02T00:{index:02d}:00Z",
                "author_association": "COLLABORATOR",
            }
            for index in range(99)
        )
        api.reviews.append(
            {
                "user": {"login": "reviewer"},
                "state": "CHANGES_REQUESTED",
                "commit_id": PR_HEAD_SHA,
                "submitted_at": "2026-11-02T02:00:00Z",
                "author_association": "COLLABORATOR",
            }
        )
        with self.assertRaisesRegex(GateError, "remain unresolved"):
            validate_release(api, SOURCE_SHA)

    def test_rejects_unexpected_completed_pr_workflow_failure(self):
        api = FakeAPI()
        api.pr_run["conclusion"] = "failure"
        with self.assertRaisesRegex(GateError, "Release candidate gates"):
            validate_release(api, SOURCE_SHA)


if __name__ == "__main__":
    unittest.main()
