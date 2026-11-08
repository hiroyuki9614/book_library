#!/usr/bin/env python3
"""Offline regression tests for the read-only staging CD admission handoff."""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts/staging/cd-handoff.py"
SPEC = importlib.util.spec_from_file_location("cd_handoff", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
handoff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(handoff)

SOURCE_SHA = "a" * 40
BASE_SHA = "b" * 40
RUN_HEAD_SHA = "c" * 40
MAIN_SHA = "d" * 40
STAGING_BLOB_SHA = "1" * 40
RELEASE_BLOB_SHA = "2" * 40
CD_BLOB_SHA = "3" * 40
REPOSITORY = handoff.REPOSITORY
PULL_NUMBER = 103
RUN_ID = "7001"
RUN_ATTEMPT = "1"


def repository_response() -> dict:
    return {
        "full_name": REPOSITORY,
        "owner": {"login": handoff.REPOSITORY_OWNER, "type": "User"},
        "default_branch": "main",
    }


def pull_response() -> dict:
    return {
        "number": PULL_NUMBER,
        "state": "open",
        "draft": True,
        "base": {
            "ref": "main",
            "sha": BASE_SHA,
            "repo": {"full_name": REPOSITORY},
        },
        "head": {
            "ref": "candidate-staging",
            "sha": SOURCE_SHA,
            "repo": {"full_name": REPOSITORY},
        },
        "user": {"login": "candidate-author"},
    }


def review_response() -> list[dict]:
    return [
        {
            "id": 8801,
            "state": "APPROVED",
            "submitted_at": "2026-11-08T10:00:00Z",
            "commit_id": SOURCE_SHA,
            "author_association": "COLLABORATOR",
            "user": {"login": "independent-reviewer"},
        }
    ]


def workflow_run(*, path: str, name: str, workflow_id: int, run_id: int, run_number: int) -> dict:
    return {
        "id": run_id,
        "name": name,
        "workflow_id": workflow_id,
        "path": f"{path}@refs/pull/{PULL_NUMBER}/merge",
        "event": "pull_request",
        "head_branch": "candidate-staging",
        "head_sha": RUN_HEAD_SHA,
        "head_repository": {"full_name": REPOSITORY},
        "pull_requests": [
            {
                "number": PULL_NUMBER,
                "head": {
                    "sha": SOURCE_SHA,
                    "ref": "candidate-staging",
                    "repo": {"full_name": REPOSITORY},
                },
                "base": {
                    "sha": BASE_SHA,
                    "ref": "main",
                    "repo": {"full_name": REPOSITORY},
                },
            }
        ],
        "run_number": run_number,
        "run_attempt": 2,
        "status": "completed",
        "conclusion": "success",
        "completed_at": "2026-11-09T01:00:00Z",
    }


def workflow_jobs(run_id: int, names: tuple[str, ...]) -> list[dict]:
    return [
        {
            "id": run_id * 10 + index,
            "run_id": run_id,
            "run_attempt": 2,
            "head_sha": RUN_HEAD_SHA,
            "name": name,
            "status": "completed",
            "conclusion": "success",
            "completed_at": "2026-11-09T01:00:00Z",
        }
        for index, name in enumerate(names, start=1)
    ]


class FakeAPI:
    def __init__(self):
        self.repo = repository_response()
        self.pull = pull_response()
        self.reviews = review_response()
        self.workflows = {
            "staging-ci.yml": {
                "id": 77,
                "path": handoff.STAGING_CI_PATH,
                "name": handoff.STAGING_CI_NAME,
            },
            "release-ci.yml": {
                "id": 88,
                "path": handoff.RELEASE_CI_PATH,
                "name": handoff.RELEASE_CI_NAME,
            },
        }
        self.runs = {
            77: [
                workflow_run(
                    path=handoff.STAGING_CI_PATH,
                    name=handoff.STAGING_CI_NAME,
                    workflow_id=77,
                    run_id=501,
                    run_number=14,
                )
            ],
            88: [
                workflow_run(
                    path=handoff.RELEASE_CI_PATH,
                    name=handoff.RELEASE_CI_NAME,
                    workflow_id=88,
                    run_id=601,
                    run_number=20,
                )
            ],
        }
        self.jobs = {
            (501, 2): workflow_jobs(501, handoff.REQUIRED_STAGING_CI_JOBS),
            (601, 2): workflow_jobs(601, handoff.REQUIRED_RELEASE_CI_JOBS),
        }
        self.workflow_blobs = {
            (handoff.CD_WORKFLOW_PATH, MAIN_SHA): CD_BLOB_SHA,
            (handoff.STAGING_CI_PATH, MAIN_SHA): STAGING_BLOB_SHA,
            (handoff.STAGING_CI_PATH, SOURCE_SHA): STAGING_BLOB_SHA,
            (handoff.RELEASE_CI_PATH, MAIN_SHA): RELEASE_BLOB_SHA,
            (handoff.RELEASE_CI_PATH, SOURCE_SHA): RELEASE_BLOB_SHA,
        }
        self.main_ref_sha = MAIN_SHA
        self.main_ref_reads = 0
        self.move_main_on_final_read = False
        self.calls: list[str] = []

    def get(self, path):
        self.calls.append(path)
        parsed = urlsplit(path)
        clean_path = parsed.path
        query = parse_qs(parsed.query)
        if clean_path == f"/repos/{REPOSITORY}":
            return self.repo
        if clean_path == f"/repos/{REPOSITORY}/git/ref/heads/main":
            self.main_ref_reads += 1
            sha = self.main_ref_sha
            if self.move_main_on_final_read and self.main_ref_reads > 1:
                sha = "e" * 40
            return {"ref": "refs/heads/main", "object": {"sha": sha}}
        if clean_path.startswith(f"/repos/{REPOSITORY}/contents/"):
            workflow_path = clean_path.removeprefix(f"/repos/{REPOSITORY}/contents/")
            ref = query.get("ref", [""])[0]
            blob_sha = self.workflow_blobs.get((workflow_path, ref))
            return {"path": workflow_path, "sha": blob_sha}
        if clean_path == f"/repos/{REPOSITORY}/pulls/{PULL_NUMBER}":
            return self.pull
        if clean_path == f"/repos/{REPOSITORY}/pulls/{PULL_NUMBER}/reviews":
            return self.reviews
        workflow_match = re.fullmatch(
            rf"/repos/{re.escape(REPOSITORY)}/actions/workflows/(staging-ci|release-ci)\.yml",
            clean_path,
        )
        if workflow_match:
            filename = f"{workflow_match.group(1)}.yml"
            return self.workflows[filename]
        runs_match = re.fullmatch(
            rf"/repos/{re.escape(REPOSITORY)}/actions/workflows/(77|88)/runs", clean_path
        )
        if runs_match:
            return {"workflow_runs": self.runs[int(runs_match.group(1))]}
        jobs_match = re.fullmatch(
            rf"/repos/{re.escape(REPOSITORY)}/actions/runs/(\d+)/attempts/(\d+)/jobs",
            clean_path,
        )
        if jobs_match:
            key = (int(jobs_match.group(1)), int(jobs_match.group(2)))
            return {"jobs": self.jobs.get(key, [])}
        raise AssertionError(f"Unexpected API path: {path}")

    def pages(self, path):
        return self.get(f"{path}?per_page=100&page=1")


class CDHandoffTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()

    def validate(self, **overrides):
        arguments = {
            "repository": REPOSITORY,
            "source_sha": SOURCE_SHA,
            "pull_number": PULL_NUMBER,
            "requester": handoff.REPOSITORY_OWNER,
            "run_id": RUN_ID,
            "run_attempt": RUN_ATTEMPT,
            "workflow_ref": f"{REPOSITORY}/{handoff.CD_WORKFLOW_PATH}@refs/heads/main",
            "workflow_sha": MAIN_SHA,
            "github_sha": MAIN_SHA,
        }
        arguments.update(overrides)
        return handoff.validate_handoff(self.api, **arguments)

    def test_records_exact_candidate_evidence_pending_supervisor_without_deployment(self):
        receipt = self.validate()
        self.assertEqual(receipt["source"]["sha"], SOURCE_SHA)
        self.assertEqual(receipt["source"]["pull_request"], PULL_NUMBER)
        self.assertEqual(receipt["evidence"]["staging_ci"]["run_id"], 501)
        self.assertEqual(receipt["evidence"]["staging_ci"]["source_sha"], SOURCE_SHA)
        self.assertEqual(receipt["evidence"]["staging_ci"]["run_head_sha"], RUN_HEAD_SHA)
        self.assertEqual(receipt["evidence"]["staging_ci"]["run_attempt"], 2)
        self.assertEqual(receipt["evidence"]["staging_ci"]["trust_class"], "candidate-source-ci-only")
        self.assertEqual(receipt["evidence"]["release_ci"]["run_id"], 601)
        self.assertEqual(
            receipt["evidence"]["release_ci"]["required_jobs"],
            list(handoff.REQUIRED_RELEASE_CI_JOBS),
        )
        self.assertEqual(
            receipt["evidence"]["workflow_provenance"]["authority_sha"], MAIN_SHA
        )
        self.assertTrue(
            all(
                item["matches_authoritative_main"]
                for item in receipt["evidence"]["workflow_provenance"]["candidate_workflows"].values()
            )
        )
        self.assertEqual(
            receipt["evidence"]["independent_review"]["commit_sha"], SOURCE_SHA
        )
        self.assertEqual(
            receipt["target"],
            {
                "host": "ubuntu-worker",
                "principal": "hiroyuki",
                "compose_project": "belib-isolated-i5",
                "transport": "existing staging supervisor",
            },
        )
        self.assertEqual(receipt["lock"]["github_concurrency_group"], handoff.LOCK_GROUP)
        self.assertTrue(receipt["supervisor_verification"]["required"])
        self.assertEqual(
            receipt["supervisor_verification"]["status"],
            "pending-independent-provenance-verification",
        )
        self.assertFalse(receipt["supervisor_verification"]["deployment_authorized"])
        self.assertFalse(receipt["deployment"]["started"])
        self.assertFalse(receipt["deployment"]["asserted"])
        self.assertFalse(receipt["deployment"]["authorized"])
        self.assertEqual(
            receipt["status"],
            "candidate-ci-passed-awaiting-existing-supervisor-verification",
        )
        self.assertIn(
            f"/actions/runs/501/attempts/2/jobs?",
            "\n".join(self.api.calls),
        )
        self.assertIn(
            f"/actions/runs/601/attempts/2/jobs?",
            "\n".join(self.api.calls),
        )

    def test_rejects_non_owner_wrong_repository_or_non_immutable_sha(self):
        with self.assertRaises(handoff.HandoffError):
            self.validate(requester="another-user")
        with self.assertRaises(handoff.HandoffError):
            self.validate(repository="someone-else/book_library")
        with self.assertRaises(handoff.HandoffError):
            self.validate(source_sha=SOURCE_SHA[:12])
        with self.assertRaises(handoff.HandoffError):
            self.validate(pull_number=0)

    def test_manual_dispatch_requires_main_owner_and_supervisor_review_request(self):
        event = {
            "sender": {"login": handoff.REPOSITORY_OWNER},
            "repository": {"full_name": REPOSITORY},
        }
        handoff.validate_dispatch_context(
            event_name="workflow_dispatch",
            ref="refs/heads/main",
            actor=handoff.REPOSITORY_OWNER,
            supervisor_review_request="true",
            event=event,
            workflow_ref=f"{REPOSITORY}/{handoff.CD_WORKFLOW_PATH}@refs/heads/main",
            workflow_sha=MAIN_SHA,
            github_sha=MAIN_SHA,
        )
        for values in (
            {"ref": "refs/heads/candidate"},
            {"workflow_ref": f"{REPOSITORY}/{handoff.CD_WORKFLOW_PATH}@refs/heads/candidate"},
            {"workflow_sha": "e" * 40},
            {"github_sha": "e" * 40},
            {"actor": "another-user"},
            {"supervisor_review_request": "false"},
            {"event": {"sender": {"login": "another-user"}, "repository": {"full_name": REPOSITORY}}},
            {"event": {"sender": {"login": handoff.REPOSITORY_OWNER}, "repository": {"full_name": "fork/book_library"}}},
        ):
            arguments = {
                "event_name": "workflow_dispatch",
                "ref": "refs/heads/main",
                "actor": handoff.REPOSITORY_OWNER,
                "supervisor_review_request": "true",
                "event": event,
                "workflow_ref": f"{REPOSITORY}/{handoff.CD_WORKFLOW_PATH}@refs/heads/main",
                "workflow_sha": MAIN_SHA,
                "github_sha": MAIN_SHA,
            }
            arguments.update(values)
            with self.assertRaises(handoff.HandoffError):
                handoff.validate_dispatch_context(**arguments)

    def test_rejects_wrong_repository_owner_identity(self):
        self.api.repo["owner"]["login"] = "another-user"
        with self.assertRaises(handoff.HandoffError):
            self.validate()

    def test_rejects_closed_wrong_base_fork_or_unmatched_pr_head(self):
        for mutate in (
            lambda pull: pull.update(state="closed"),
            lambda pull: pull["base"].update(ref="production"),
            lambda pull: pull["head"]["repo"].update(full_name="fork/book_library"),
            lambda pull: pull["head"].update(sha="d" * 40),
        ):
            self.api = FakeAPI()
            mutate(self.api.pull)
            with self.assertRaises(handoff.HandoffError):
                self.validate()

    def test_requires_independent_current_exact_head_review(self):
        self.api.reviews = []
        with self.assertRaises(handoff.HandoffError):
            self.validate()

        self.api = FakeAPI()
        self.api.reviews = [
            {
                **review_response()[0],
                "user": {"login": "candidate-author"},
            }
        ]
        with self.assertRaises(handoff.HandoffError):
            self.validate()

        self.api = FakeAPI()
        self.api.reviews[0]["commit_id"] = "d" * 40
        with self.assertRaises(handoff.HandoffError):
            self.validate()

        self.api = FakeAPI()
        self.api.reviews[0]["author_association"] = "CONTRIBUTOR"
        with self.assertRaises(handoff.HandoffError):
            self.validate()

    def test_latest_unresolved_changes_requested_review_blocks_admission(self):
        self.api.reviews.append(
            {
                "id": 8802,
                "state": "CHANGES_REQUESTED",
                "submitted_at": "2026-11-08T11:00:00Z",
                "commit_id": SOURCE_SHA,
                "author_association": "COLLABORATOR",
                "user": {"login": "independent-reviewer"},
            }
        )
        with self.assertRaises(handoff.HandoffError):
            self.validate()

    def test_rejects_candidate_controlled_workflow_definitions(self):
        self.api.workflow_blobs[(handoff.STAGING_CI_PATH, SOURCE_SHA)] = "f" * 40
        with self.assertRaisesRegex(handoff.HandoffError, "differs from authoritative main"):
            self.validate()

        self.api = FakeAPI()
        self.api.workflow_blobs[(handoff.STAGING_CI_PATH, MAIN_SHA)] = None
        with self.assertRaises(handoff.HandoffError):
            self.validate()

        self.api = FakeAPI()
        self.api.workflow_blobs[(handoff.RELEASE_CI_PATH, SOURCE_SHA)] = "f" * 40
        with self.assertRaisesRegex(handoff.HandoffError, "release-ci.yml"):
            self.validate()

    def test_run_name_override_does_not_change_pinned_workflow_identity(self):
        self.api.runs[77][0]["name"] = f"Candidate source CI only (PR #{PULL_NUMBER})"
        self.api.runs[88][0]["name"] = f"Release candidate gates (PR #{PULL_NUMBER})"
        receipt = self.validate()
        self.assertEqual(receipt["evidence"]["staging_ci"]["run_id"], 501)
        self.assertEqual(receipt["evidence"]["release_ci"]["run_id"], 601)

    def test_rejects_cd_workflow_not_pinned_to_authoritative_main(self):
        with self.assertRaises(handoff.HandoffError):
            self.validate(workflow_sha="e" * 40, github_sha="e" * 40)

        self.api = FakeAPI()
        self.api.move_main_on_final_read = True
        with self.assertRaisesRegex(handoff.HandoffError, "main moved"):
            self.validate()

    def test_requires_latest_terminal_green_staging_and_release_runs(self):
        failed_staging = copy.deepcopy(self.api.runs[77][0])
        failed_staging.update(id=502, run_number=15, conclusion="failure")
        self.api.runs[77].append(failed_staging)
        with self.assertRaisesRegex(handoff.HandoffError, "staging-ci.yml run"):
            self.validate()

        self.api = FakeAPI()
        self.api.runs[88][0]["status"] = "in_progress"
        with self.assertRaisesRegex(handoff.HandoffError, "release-ci.yml run"):
            self.validate()

        self.api = FakeAPI()
        self.api.runs[77][0]["completed_at"] = None
        with self.assertRaises(handoff.HandoffError):
            self.validate()

    def test_rejects_missing_failing_wrong_attempt_or_wrong_head_jobs(self):
        self.api.jobs[(601, 2)].pop()
        with self.assertRaisesRegex(handoff.HandoffError, "Required workflow job"):
            self.validate()

        self.api = FakeAPI()
        self.api.jobs[(601, 2)][0]["conclusion"] = "failure"
        with self.assertRaisesRegex(handoff.HandoffError, "not terminal and successful"):
            self.validate()

        self.api = FakeAPI()
        self.api.jobs[(501, 2)][0]["run_attempt"] = 1
        with self.assertRaisesRegex(handoff.HandoffError, "wrong run, attempt"):
            self.validate()

        self.api = FakeAPI()
        self.api.jobs[(601, 2)][0]["head_sha"] = "e" * 40
        with self.assertRaisesRegex(handoff.HandoffError, "wrong run, attempt, or head SHA"):
            self.validate()

        self.api = FakeAPI()
        self.api.runs[88][0]["head_sha"] = "e" * 40
        with self.assertRaisesRegex(handoff.HandoffError, "wrong run, attempt, or head SHA"):
            self.validate()

        self.api = FakeAPI()
        self.api.runs[77][0]["run_attempt"] = 3
        with self.assertRaisesRegex(handoff.HandoffError, "no job evidence"):
            self.validate()

    def test_requires_exact_pr_head_for_each_workflow(self):
        self.api.runs[77][0]["pull_requests"][0]["head"]["sha"] = "e" * 40
        with self.assertRaisesRegex(handoff.HandoffError, "staging-ci.yml run proves"):
            self.validate()

        self.api = FakeAPI()
        self.api.runs[88][0]["pull_requests"][0]["head"]["sha"] = "e" * 40
        with self.assertRaisesRegex(handoff.HandoffError, "release-ci.yml run proves"):
            self.validate()

    def test_receipt_is_created_once_with_restrictive_mode(self):
        receipt = self.validate()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "handoff.json"
            handoff.write_receipt(path, receipt)
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(stored, receipt)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(handoff.HandoffError):
                handoff.write_receipt(path, receipt)

    def test_candidate_source_ci_cannot_self_attest_or_publish_handoff(self):
        workflow = (ROOT / ".github/workflows/staging-ci.yml").read_text(encoding="utf-8")
        self.assertIn("Candidate source CI only", workflow)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("workflow_dispatch:", workflow)
        self.assertNotIn("actions/upload-artifact@", workflow)
        self.assertNotIn("staging-cd.yml", workflow)
        for name in handoff.REQUIRED_STAGING_CI_JOBS:
            self.assertIn(f"name: {name}", workflow)

        release_workflow = (ROOT / ".github/workflows/release-ci.yml").read_text(encoding="utf-8")
        for name in handoff.REQUIRED_RELEASE_CI_JOBS:
            self.assertIn(f"name: {name}", release_workflow)
        self.assertEqual(
            release_workflow.count("ref: ${{ github.event.pull_request.head.sha || github.sha }}"),
            4,
        )

    def test_manual_workflow_requires_trusted_main_context_and_no_deployer(self):
        workflow = (ROOT / ".github/workflows/staging-cd.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn(
            f"github.workflow_ref == '{REPOSITORY}/{handoff.CD_WORKFLOW_PATH}@refs/heads/main'",
            workflow,
        )
        self.assertIn("github.workflow_sha == github.sha", workflow)
        self.assertIn("ref: ${{ github.workflow_sha }}", workflow)
        self.assertIn("request_supervisor_review:", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("INPUT_REQUEST_SUPERVISOR_REVIEW", workflow)
        self.assertIn("GITHUB_ACTOR", SCRIPT_PATH.read_text(encoding="utf-8"))
        self.assertIn("pull-requests: read", workflow)
        self.assertIn("actions: read", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn(handoff.LOCK_GROUP, workflow)
        self.assertNotIn("self-hosted", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertNotIn("ssh", workflow.lower())
        self.assertNotIn("sudo", workflow.lower())
        self.assertNotIn("docker compose", workflow.lower())
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertIn("Deployment authorized: false", workflow)
        self.assertIn("Independent existing-supervisor provenance verification: pending.", workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
