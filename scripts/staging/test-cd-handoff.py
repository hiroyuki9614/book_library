#!/usr/bin/env python3
"""Offline regression tests for the read-only staging CD admission handoff."""

from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts/staging/cd-handoff.py"
SPEC = importlib.util.spec_from_file_location("cd_handoff", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
handoff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(handoff)

SOURCE_SHA = "a" * 40
BASE_SHA = "b" * 40
REPOSITORY = handoff.REPOSITORY
PULL_NUMBER = 103
RUN_ID = "7001"
RUN_ATTEMPT = "1"


def repository_response() -> dict:
    return {
        "full_name": REPOSITORY,
        "owner": {"login": handoff.REPOSITORY_OWNER, "type": "User"},
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


def ci_run() -> dict:
    return {
        "id": 501,
        "name": handoff.STAGING_CI_NAME,
        "workflow_id": 77,
        "path": f"{handoff.STAGING_CI_PATH}@refs/pull/{PULL_NUMBER}/merge",
        "event": "pull_request",
        "head_branch": "candidate-staging",
        "head_sha": "c" * 40,
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
        "run_number": 14,
        "run_attempt": 2,
        "status": "completed",
        "conclusion": "success",
    }


class FakeAPI:
    def __init__(self):
        self.repo = repository_response()
        self.pull = pull_response()
        self.reviews = review_response()
        self.workflow = {"id": 77, "path": handoff.STAGING_CI_PATH}
        self.runs = [ci_run()]
        self.jobs = [
            {"name": name, "conclusion": "success"}
            for name in handoff.REQUIRED_CI_JOBS
        ]

    def get(self, path):
        if path == f"/repos/{REPOSITORY}":
            return self.repo
        if path == f"/repos/{REPOSITORY}/pulls/{PULL_NUMBER}":
            return self.pull
        if path.startswith(f"/repos/{REPOSITORY}/pulls/{PULL_NUMBER}/reviews?"):
            return self.reviews
        if path == f"/repos/{REPOSITORY}/actions/workflows/staging-ci.yml":
            return self.workflow
        if path.startswith(f"/repos/{REPOSITORY}/actions/workflows/77/runs?"):
            return {"workflow_runs": self.runs}
        if path == f"/repos/{REPOSITORY}/actions/runs/501/jobs?per_page=100":
            return {"jobs": self.jobs}
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
        }
        arguments.update(overrides)
        return handoff.validate_handoff(self.api, **arguments)

    def test_admits_exact_head_and_records_supervisor_receipt_without_deployment(self):
        receipt = self.validate()
        self.assertEqual(receipt["source"]["sha"], SOURCE_SHA)
        self.assertEqual(receipt["source"]["pull_request"], PULL_NUMBER)
        self.assertEqual(receipt["evidence"]["staging_ci"]["run_id"], 501)
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
        self.assertFalse(receipt["deployment"]["started"])
        self.assertFalse(receipt["deployment"]["asserted"])
        self.assertEqual(receipt["status"], "admission-approved-awaiting-existing-supervisor")

    def test_rejects_non_owner_wrong_repository_or_non_immutable_sha(self):
        with self.assertRaises(handoff.HandoffError):
            self.validate(requester="another-user")
        with self.assertRaises(handoff.HandoffError):
            self.validate(repository="someone-else/book_library")
        with self.assertRaises(handoff.HandoffError):
            self.validate(source_sha=SOURCE_SHA[:12])
        with self.assertRaises(handoff.HandoffError):
            self.validate(pull_number=0)

    def test_manual_dispatch_requires_main_owner_and_explicit_approval(self):
        event = {
            "sender": {"login": handoff.REPOSITORY_OWNER},
            "repository": {"full_name": REPOSITORY},
        }
        handoff.validate_dispatch_context(
            event_name="workflow_dispatch",
            ref="refs/heads/main",
            actor=handoff.REPOSITORY_OWNER,
            explicit_approval="true",
            event=event,
        )
        for values in (
            {"ref": "refs/heads/candidate"},
            {"actor": "another-user"},
            {"explicit_approval": "false"},
            {"event": {"sender": {"login": "another-user"}, "repository": {"full_name": REPOSITORY}}},
            {"event": {"sender": {"login": handoff.REPOSITORY_OWNER}, "repository": {"full_name": "fork/book_library"}}},
        ):
            arguments = {
                "event_name": "workflow_dispatch",
                "ref": "refs/heads/main",
                "actor": handoff.REPOSITORY_OWNER,
                "explicit_approval": "true",
                "event": event,
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

    def test_requires_canonical_latest_full_green_staging_ci_run(self):
        self.api.workflow["path"] = ".github/workflows/other.yml"
        with self.assertRaises(handoff.HandoffError):
            self.validate()

        self.api = FakeAPI()
        failed = copy.deepcopy(ci_run())
        failed.update(run_number=15, conclusion="failure")
        self.api.runs.append(failed)
        with self.assertRaises(handoff.HandoffError):
            self.validate()

        self.api = FakeAPI()
        self.api.jobs[0]["conclusion"] = "failure"
        with self.assertRaises(handoff.HandoffError):
            self.validate()

        self.api = FakeAPI()
        self.api.runs[0]["pull_requests"][0]["head"]["sha"] = "d" * 40
        with self.assertRaises(handoff.HandoffError):
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

    def test_manual_workflow_requires_explicit_owner_approval_and_no_deployer(self):
        workflow = (ROOT / ".github/workflows/staging-cd.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("approve_staging:", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("INPUT_APPROVE_STAGING", workflow)
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
        self.assertIn("Deployment started: false", workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
