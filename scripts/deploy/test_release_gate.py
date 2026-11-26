import base64
import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import release_gate
from release_gate import (
    GateError,
    GitHubAPI,
    REQUIRED_RELEASE_JOBS,
    TransientGitHubAPIError,
    validate_release,
)


SOURCE_SHA = "a" * 40
PR_HEAD_SHA = "b" * 40
RELEASE_PATH = ".github/workflows/release-ci.yml"
MAIN_RUN_ID = 110
PR_RUN_ID = 210
CANONICAL_WORKFLOW_ID = 1
SPOOF_WORKFLOW_ID = 2
OWNER_LOGIN = "hiroyuki9614"
SOLO_REVIEW_MARKER = "BELIB-SOLO-REVIEW v1"
SOLO_RELEASE_MARKER = "BELIB-SOLO-RELEASE v1"
SOLO_REVOKE_MARKER = "BELIB-SOLO-REVOKE v1"


def structured_comment(
    marker,
    payload,
    comment_id,
    *,
    created_at="2026-11-02T00:00:00Z",
    updated_at=None,
    user=None,
    author_association="OWNER",
):
    return {
        "id": comment_id,
        "body": f"{marker}\n{json.dumps(payload, sort_keys=True, separators=(',', ':'))}",
        "created_at": created_at,
        "updated_at": updated_at if updated_at is not None else created_at,
        "user": user or {"login": OWNER_LOGIN, "type": "User"},
        "author_association": author_association,
    }


def solo_comments(*, head_sha=PR_HEAD_SHA, review_comment_id=501, release_comment_id=502):
    review_payload = {
        "evidence_sha256": "c" * 64,
        "head_sha": head_sha,
        "pr": 70,
        "repository": "hiroyuki9614/book_library",
        "result": "PASS",
        "review_session": "independent-session-20261103",
        "review_type": "independent-ai",
        "unresolved_high_findings": 0,
    }
    release_payload = {
        "decision": "RELEASE",
        "head_sha": head_sha,
        "pr": 70,
        "repository": "hiroyuki9614/book_library",
        "review_comment_id": review_comment_id,
    }
    return [
        structured_comment(
            SOLO_REVIEW_MARKER,
            review_payload,
            review_comment_id,
            created_at="2026-11-02T00:00:00Z",
        ),
        structured_comment(
            SOLO_RELEASE_MARKER,
            release_payload,
            release_comment_id,
            created_at="2026-11-02T00:01:00Z",
        ),
    ]


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
        self.canonical_workflow = {
            "id": CANONICAL_WORKFLOW_ID,
            "path": RELEASE_PATH,
        }
        self.repository_info = {
            "full_name": self.repository,
            "owner": {"login": OWNER_LOGIN, "type": "User"},
        }
        self.main_runs = [self.main_run]
        self.pr_runs = [self.pr_run]
        self.pull_summary = {
            "number": 70,
            "state": "closed",
            "merged_at": "2026-11-02T00:00:00Z",
            "merge_commit_sha": SOURCE_SHA,
            "base": {"ref": "main", "repo": {"full_name": self.repository}},
            "head": {"ref": "codex/cd-test", "sha": PR_HEAD_SHA},
            "user": {"login": "author", "type": "User"},
            "author_association": "CONTRIBUTOR",
        }
        self.pull = {
            "number": 70,
            "state": "closed",
            "merged_at": "2026-11-02T00:00:00Z",
            "merge_commit_sha": SOURCE_SHA,
            "base": {"ref": "main", "repo": {"full_name": self.repository}},
            "head": {"ref": "codex/cd-test", "sha": PR_HEAD_SHA},
            "user": {"login": "author", "type": "User"},
            "author_association": "CONTRIBUTOR",
            "merged_by": {"login": OWNER_LOGIN, "type": "User"},
        }
        self.detail_requests = []
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
        self.run_jobs = {MAIN_RUN_ID: self.main_jobs, PR_RUN_ID: self.pr_jobs}
        self.comments = []

    def get(self, path):
        if path == f"/repos/{self.repository}":
            return self.repository_info
        if path == f"/repos/{self.repository}/actions/workflows/release-ci.yml":
            return self.canonical_workflow
        if path == f"/repos/{self.repository}/git/ref/heads/main":
            return {"object": {"sha": SOURCE_SHA}}
        if "/actions/runs?" in path and "event=push" in path:
            return {"workflow_runs": self.main_runs}
        if "/actions/runs/" in path and path.endswith("/jobs?per_page=100"):
            run_id = int(path.split("/actions/runs/", 1)[1].split("/", 1)[0])
            jobs = self.run_jobs.get(run_id, {})
            return {"jobs": [{"name": name, "conclusion": result} for name, result in jobs.items()]}
        if f"/commits/{SOURCE_SHA}/pulls?" in path:
            return [self.pull_summary]
        if path == f"/repos/{self.repository}/pulls/70":
            self.detail_requests.append(path)
            return self.pull
        if f"/pulls/70/reviews?" in path:
            page = int(parse_qs(urlsplit(path).query).get("page", ["1"])[0])
            start = (page - 1) * 100
            return self.reviews[start : start + 100]
        if f"/issues/70/comments?" in path:
            page = int(parse_qs(urlsplit(path).query).get("page", ["1"])[0])
            start = (page - 1) * 100
            return self.comments[start : start + 100]
        if "/actions/runs?" in path and "event=pull_request" in path:
            return {"workflow_runs": self.pr_runs}
        raise AssertionError(f"Unexpected API request: {path}")


class OwnerConsentReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # These are local synthetic keys, never a configured trust anchor. Setup
        # errors fail the suite; do not skip or replace real signing with a mock.
        cls.openssl = shutil.which("openssl")
        if cls.openssl is None:
            raise RuntimeError("Receipt tests require installed openssl")
        temporary = tempfile.TemporaryDirectory(prefix="belib-receipt-tests-")
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        for name, algorithm in (("signer", "ED25519"), ("other", "ED25519"), ("ec", "EC")):
            options = ["-pkeyopt", "ec_paramgen_curve:P-256"] if algorithm == "EC" else []
            cls.run_openssl(
                "genpkey", "-algorithm", algorithm, *options, "-out", str(cls.root / f"{name}.pem")
            )
            cls.run_openssl(
                "pkey", "-in", str(cls.root / f"{name}.pem"),
                "-pubout", "-out", str(cls.root / f"{name}.pub.pem"),
            )
        cls.public_key = (cls.root / "signer.pub.pem").read_bytes()

    @classmethod
    def run_openssl(cls, *arguments):
        subprocess.run(
            [cls.openssl, *arguments], check=True, timeout=10, shell=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def setUp(self):
        self.payload = {
            "schema_version": "belib-owner-consent-v1",
            "repository": FakeAPI.repository,
            "pr": 70,
            "head_sha": PR_HEAD_SHA,
            "owner_login": OWNER_LOGIN,
            "decision": "MERGE",
            "scope": "normal-ui",
            "issued_at": "2026-11-02T00:00:00Z",
            "expires_at": "2026-11-02T00:10:00Z",
            "receipt_id": "b07a511e-55b4-40fb-9ddd-79e75cc3c92a",
            "independent_review_sha256": "c" * 64,
            "i5_preview_sha256": "d" * 64,
        }
        self.now = datetime(2026, 11, 2, 0, 5, tzinfo=timezone.utc)

    def serialize(self, payload):
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def sign(self, payload_bytes):
        (self.root / "payload.json").write_bytes(payload_bytes)
        self.run_openssl(
            "pkeyutl", "-sign", "-rawin", "-inkey", str(self.root / "signer.pem"),
            "-in", str(self.root / "payload.json"), "-out", str(self.root / "signature.bin"),
        )
        return (self.root / "signature.bin").read_bytes()

    def verify(self, payload_bytes, signature, **overrides):
        arguments = {
            "trusted_public_key_pem": self.public_key,
            "repository": FakeAPI.repository,
            "pr": 70,
            "head_sha": PR_HEAD_SHA,
            "owner_login": OWNER_LOGIN,
            "expected_review_sha256": self.payload["independent_review_sha256"],
            "expected_i5_preview_sha256": self.payload["i5_preview_sha256"],
            "now": self.now,
        }
        arguments.update(overrides)
        return release_gate._verify_owner_consent_receipt(payload_bytes, signature, **arguments)

    def test_verifies_real_detached_signature_over_exact_canonical_bytes(self):
        serialized = self.serialize(self.payload)
        signature = self.sign(serialized)
        self.assertEqual(len(signature), 64)
        self.assertEqual(self.verify(serialized, signature), self.payload)
        self.assertEqual(
            self.verify(serialized, signature, now=self.now - timedelta(minutes=5)), self.payload
        )

    def test_requires_independent_expected_evidence_digests(self):
        serialized = self.serialize(self.payload)
        signature = self.sign(serialized)
        arguments = {
            "trusted_public_key_pem": self.public_key,
            "repository": FakeAPI.repository,
            "pr": 70,
            "head_sha": PR_HEAD_SHA,
            "owner_login": OWNER_LOGIN,
            "now": self.now,
        }
        with self.assertRaises(TypeError):
            release_gate._verify_owner_consent_receipt(serialized, signature, **arguments)

    def test_expected_evidence_digests_must_match_in_order_and_be_canonical(self):
        serialized = self.serialize(self.payload)
        signature = self.sign(serialized)
        invalid = (
            {"expected_review_sha256": "C" * 64},
            {"expected_review_sha256": "c" * 63},
            {"expected_review_sha256": "g" * 64},
            {"expected_review_sha256": None},
            {"expected_i5_preview_sha256": "D" * 64},
            {"expected_i5_preview_sha256": "d" * 63},
            {"expected_i5_preview_sha256": "g" * 64},
            {"expected_i5_preview_sha256": None},
            {"expected_review_sha256": "d" * 64},
            {"expected_i5_preview_sha256": "c" * 64},
            # A correctly signed receipt cannot supply its own expected hashes.
            {
                "expected_review_sha256": "e" * 64,
                "expected_i5_preview_sha256": "f" * 64,
            },
        )
        for expected in invalid:
            with self.subTest(expected=expected), self.assertRaises(GateError):
                self.verify(serialized, signature, **expected)

    def test_expected_digest_str_subclasses_cannot_override_mismatch_comparison(self):
        class AdversarialDigest(str):
            def __eq__(self, other):
                return True

            def __ne__(self, other):
                return False

        serialized = self.serialize(self.payload)
        signature = self.sign(serialized)
        for field, mismatch in (
            ("expected_review_sha256", "e" * 64),
            ("expected_i5_preview_sha256", "f" * 64),
        ):
            with self.subTest(field=field), self.assertRaises(GateError):
                self.verify(serialized, signature, **{field: AdversarialDigest(mismatch)})

    def test_signed_self_asserted_digests_do_not_replace_caller_expected_values(self):
        spoofed = {
            **self.payload,
            "independent_review_sha256": "e" * 64,
            "i5_preview_sha256": "f" * 64,
        }
        serialized = self.serialize(spoofed)
        signature = self.sign(serialized)
        with self.assertRaisesRegex(GateError, "do not match the expected values"):
            self.verify(serialized, signature)

    def test_accepts_real_signature_at_exact_ten_minute_lifetime_boundary(self):
        payload = {
            **self.payload,
            "issued_at": "2026-11-02T00:00:00Z",
            "expires_at": "2026-11-02T00:10:00Z",
        }
        serialized = self.serialize(payload)
        signature = self.sign(serialized)
        self.assertEqual(
            self.verify(serialized, signature, now=datetime(2026, 11, 2, 0, 9, 59, tzinfo=timezone.utc)),
            payload,
        )

    def test_missing_malformed_or_non_ed25519_key_fails(self):
        serialized = self.serialize(self.payload)
        signature = self.sign(serialized)
        keys = (
            None, b"", "not-bytes", b"not a public key",
            (self.root / "signer.pem").read_bytes(),
            (self.root / "ec.pub.pem").read_bytes(),
            self.public_key + b"trailing data", b"x" * 257,
            b"-----BEGIN PUBLIC KEY-----\n=bad=\n-----END PUBLIC KEY-----\n",
        )
        for index, key in enumerate(keys):
            with self.subTest(case=index), self.assertRaises(GateError):
                self.verify(serialized, signature, trusted_public_key_pem=key)

    def test_rejects_different_ed25519_key_and_forged_or_absent_signature(self):
        serialized = self.serialize(self.payload)
        signature = self.sign(serialized)
        with self.assertRaisesRegex(GateError, "signature verification failed"):
            self.verify(
                serialized, signature,
                trusted_public_key_pem=(self.root / "other.pub.pem").read_bytes(),
            )
        for forged in (None, "signature", b"", signature[:-1], signature + b"x", b"\0" * 64):
            with self.subTest(signature_type=type(forged).__name__), self.assertRaises(GateError):
                self.verify(serialized, forged)

    def test_tampering_with_either_evidence_digest_or_receipt_id_breaks_signature(self):
        signature = self.sign(self.serialize(self.payload))
        for field, value in (
            ("independent_review_sha256", "e" * 64), ("i5_preview_sha256", "e" * 64),
            ("receipt_id", "7054fe6c-cd78-42cb-a952-ed71da2fe739"),
        ):
            expected = {}
            if field == "independent_review_sha256":
                expected["expected_review_sha256"] = value
            elif field == "i5_preview_sha256":
                expected["expected_i5_preview_sha256"] = value
            with self.subTest(field=field), self.assertRaisesRegex(GateError, "signature verification failed"):
                self.verify(self.serialize({**self.payload, field: value}), signature, **expected)

    def test_rejects_even_signed_missing_extra_or_wrongly_typed_fields(self):
        for field in self.payload:
            changed = {key: value for key, value in self.payload.items() if key != field}
            serialized = self.serialize(changed)
            with self.subTest(missing=field), self.assertRaises(GateError):
                self.verify(serialized, self.sign(serialized))
        for changed in (
            {**self.payload, "extra": "untrusted"},
            *({**self.payload, "pr": value} for value in (True, 70.0, "70", 0, -1)),
            *({**self.payload, field: None} for field in self.payload if field != "pr"),
        ):
            serialized = self.serialize(changed)
            with self.subTest(fields=list(changed)), self.assertRaises(GateError):
                self.verify(serialized, self.sign(serialized))

    def test_rejects_duplicate_trailing_and_noncanonical_json_even_when_signed(self):
        serialized = self.serialize(self.payload)
        invalid = (
            serialized[:-1] + b',"pr":70}',
            serialized + b"{}", serialized + b"\n", b" " + serialized,
            json.dumps(self.payload).encode("utf-8"),
            serialized.replace(b'"pr":70', b'"pr":NaN'),
            serialized.replace(b'"normal-ui"', b'"\\ud800"'),
            b"\xef\xbb\xbf" + serialized, b"\xff", b"[]", b"x" * 4097,
        )
        for index, candidate in enumerate(invalid):
            with self.subTest(case=index), self.assertRaises(GateError):
                self.verify(candidate, self.sign(candidate))
        for candidate in (None, "{}", b""):
            with self.subTest(missing=candidate), self.assertRaises(GateError):
                self.verify(candidate, self.sign(serialized))

    def test_rejects_signed_wrong_context_version_decision_scope_id_and_digests(self):
        cases = (
            ("repository", "other/book_library"), ("repository", "invalid repository"),
            ("pr", 71), ("head_sha", "e" * 40), ("head_sha", "B" * 40),
            ("head_sha", "b" * 39), ("schema_version", "belib-owner-consent-v2"),
            ("owner_login", "other-owner"), ("owner_login", ""),
            ("owner_login", "owner name"), ("decision", "RELEASE"),
            ("decision", "mergeして"), ("scope", "production"),
            ("receipt_id", ""), ("receipt_id", "not-unique"),
            ("receipt_id", self.payload["receipt_id"].upper()),
            ("receipt_id", "b07a511e-55b4-10fb-9ddd-79e75cc3c92a"),
            *((field, value) for field in ("independent_review_sha256", "i5_preview_sha256")
              for value in ("C" * 64, "c" * 63, "g" * 64, "")),
        )
        for field, value in cases:
            serialized = self.serialize({**self.payload, field: value})
            with self.subTest(field=field, value=value), self.assertRaises(GateError):
                self.verify(serialized, self.sign(serialized))
        serialized = self.serialize(self.payload)
        signature = self.sign(serialized)
        for field, value in (
            ("repository", "other/repository"), ("pr", 71), ("pr", True),
            ("head_sha", "e" * 40), ("owner_login", "other-owner"),
        ):
            with self.subTest(expected=field), self.assertRaises(GateError):
                self.verify(serialized, signature, **{field: value})
        # Invalid owner syntax must fail even if caller and signed payload agree.
        for login in ("", "owner name", "-owner", "owner-", "owner--name", "x" * 40):
            serialized = self.serialize({**self.payload, "owner_login": login})
            with self.subTest(invalid_owner=login), self.assertRaises(GateError):
                self.verify(serialized, self.sign(serialized), owner_login=login)

    def test_rejects_invalid_time_windows_and_non_strict_utc_timestamps(self):
        invalid_times = (
            "2026-11-02T00:00:00+00:00", "2026-11-02T00:00:00.1Z",
            "2026-11-02t00:00:00z", "2026-11-02T00:00:60Z",
            "2026-02-30T00:00:00Z", "2026-11-02T00:00:00Z\n", "",
        )
        for field in ("issued_at", "expires_at"):
            for value in invalid_times:
                serialized = self.serialize({**self.payload, field: value})
                with self.subTest(field=field, time=value), self.assertRaises(GateError):
                    self.verify(serialized, self.sign(serialized))
        for issued_at, expires_at in (
            ("2026-11-02T00:06:00Z", "2026-11-02T00:10:00Z"),
            ("2026-11-02T00:00:00Z", "2026-11-02T00:04:00Z"),
            ("2026-11-02T00:00:00Z", "2026-11-02T00:05:00Z"),
            ("2026-11-02T00:00:00Z", "2026-11-02T00:00:00Z"),
            ("2026-11-02T00:10:00Z", "2026-11-02T00:00:00Z"),
            ("2026-11-02T00:00:00Z", "2026-11-02T00:10:01Z"),
            ("2026-11-02T00:00:00Z", "2026-11-03T00:00:00Z"),
        ):
            serialized = self.serialize({**self.payload, "issued_at": issued_at, "expires_at": expires_at})
            with self.subTest(issued_at=issued_at, expires_at=expires_at), self.assertRaises(GateError):
                self.verify(serialized, self.sign(serialized))
        serialized = self.serialize(self.payload)
        signature = self.sign(serialized)
        for now in (self.now.replace(tzinfo=None), "2026-11-02T00:05:00Z",
                    self.now.astimezone(timezone(timedelta(hours=9)))):
            with self.subTest(now=now), self.assertRaises(GateError):
                self.verify(serialized, signature, now=now)

    def test_missing_or_failed_openssl_fails_closed_without_echoing_material(self):
        serialized = self.serialize(self.payload)
        signature = self.sign(serialized)
        with patch.object(release_gate.shutil, "which", return_value=None):
            with self.assertRaises(GateError):
                self.verify(serialized, signature)
        for error in (OSError("sensitive material"), subprocess.TimeoutExpired("openssl", 10)):
            with patch.object(release_gate.subprocess, "run", side_effect=error):
                with self.assertRaises(GateError) as raised:
                    self.verify(serialized, signature)
                self.assertNotIn("sensitive material", str(raised.exception))

    def test_signed_receipt_in_owner_comments_cannot_satisfy_current_release_gate(self):
        serialized = self.serialize(self.payload)
        signature = self.sign(serialized)
        # A real synthetic signature verifies only with the explicitly supplied
        # test key. No current release path calls this primitive or trusts it.
        self.assertEqual(self.verify(serialized, signature), self.payload)
        envelope = {
            "payload": self.payload,
            "signature": base64.b64encode(signature).decode("ascii"),
            "public_key": self.public_key.decode("ascii"),
        }
        # This helper is deliberately NOT connected to either current release
        # path. Guard against accidental activation, even if the receipt later
        # becomes time-valid or the error is raised for an unrelated reason.
        for marker, rejection in (
            ("BELIB-OWNER-CONSENT v1", "solo-owner evidence pair"),
            (SOLO_RELEASE_MARKER, "different repository"),
        ):
            api = FakeAPI()
            api.reviews = []
            api.comments = [
                structured_comment(marker, envelope, 503),
                {"body": "ChatGPT owner-token approval: mergeして"},
            ]
            with self.subTest(marker=marker):
                with patch.object(
                    release_gate, "_verify_owner_consent_receipt",
                    side_effect=AssertionError("Inactive consent verifier was called by release gate"),
                ):
                    with self.assertRaisesRegex(GateError, rejection):
                        validate_release(api, SOURCE_SHA, expected_main_run_id=MAIN_RUN_ID)

        # Existing documented solo-owner evidence still works even when an
        # unrelated (signed-looking) new-format comment is present.
        api = FakeAPI()
        api.reviews = []
        api.comments = solo_comments() + [
            structured_comment("BELIB-OWNER-CONSENT v1", envelope, 503)
        ]
        with patch.object(
            release_gate, "_verify_owner_consent_receipt",
            side_effect=AssertionError("Inactive consent verifier was called by release gate"),
        ):
            proof = validate_release(api, SOURCE_SHA, expected_main_run_id=MAIN_RUN_ID)
        self.assertEqual(proof["pr_head_sha"], PR_HEAD_SHA)


class ReleaseGateTests(unittest.TestCase):
    def test_github_api_classifies_only_supported_transient_failures(self):
        failures = [
            HTTPError("https://api.github.com", 429, "synthetic", {}, None),
            HTTPError("https://api.github.com", 503, "synthetic", {}, None),
            URLError("synthetic connection failure"),
        ]
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with patch.object(release_gate, "urlopen", side_effect=failure):
                    with self.assertRaises(TransientGitHubAPIError):
                        GitHubAPI(token="synthetic").get("/synthetic")

    def test_github_api_keeps_permanent_http_failure_terminal(self):
        failure = HTTPError("https://api.github.com", 403, "synthetic", {}, None)
        with patch.object(release_gate, "urlopen", side_effect=failure):
            with self.assertRaises(GateError) as raised:
                GitHubAPI(token="synthetic").get("/synthetic")
        self.assertNotIsInstance(raised.exception, TransientGitHubAPIError)

    def test_accepts_exact_main_sha_with_green_pr_and_merge_gates(self):
        api = FakeAPI()
        proof = validate_release(api, SOURCE_SHA, expected_main_run_id=MAIN_RUN_ID)
        self.assertEqual(
            proof,
            {
                "source_sha": SOURCE_SHA,
                "main_run_id": MAIN_RUN_ID,
                "pr_number": 70,
                "pr_head_sha": PR_HEAD_SHA,
            },
        )
        self.assertNotIn("merged_by", api.pull_summary)
        self.assertEqual(api.detail_requests, [f"/repos/{api.repository}/pulls/70"])

    def test_rejects_non_positive_or_non_integer_summary_pr_number_before_detail_fetch(self):
        for number in (0, -1, True, "70", "70/other"):
            with self.subTest(number=number):
                api = FakeAPI()
                api.pull_summary["number"] = number
                with self.assertRaisesRegex(GateError, "merged PR identity"):
                    validate_release(api, SOURCE_SHA)
                self.assertEqual(api.detail_requests, [])

    def test_rejects_missing_pull_request_detail(self):
        api = FakeAPI()
        api.pull = None

        with self.assertRaisesRegex(GateError, "pull-request detail"):
            validate_release(api, SOURCE_SHA)

    def test_rejects_inconsistent_pull_request_detail(self):
        cases = (
            ("number", 71, "pull-request detail PR number"),
            ("merge_commit_sha", "c" * 40, "merge commit"),
            ("head", {"ref": "codex/cd-test", "sha": "c" * 40}, "head SHA"),
            ("base", {"ref": "release", "repo": {"full_name": FakeAPI.repository}}, "base ref"),
            ("user", {"login": "different-author", "type": "User"}, "author identity"),
        )
        for field, value, message in cases:
            with self.subTest(field=field):
                api = FakeAPI()
                api.pull[field] = value
                with self.assertRaisesRegex(GateError, message):
                    validate_release(api, SOURCE_SHA)

    def test_rejects_unmerged_or_invalidly_timestamped_pull_request_detail(self):
        for field, value in (
            ("state", "open"),
            ("merged_at", None),
            ("merged_at", "not-a-github-timestamp"),
        ):
            with self.subTest(field=field, value=value):
                api = FakeAPI()
                api.pull[field] = value
                with self.assertRaisesRegex(GateError, "merged PR detail"):
                    validate_release(api, SOURCE_SHA)

    def test_rejects_detail_merger_without_inventing_owner_identity(self):
        api = FakeAPI()
        api.reviews = []
        api.comments = solo_comments()
        del api.pull["merged_by"]

        with self.assertRaisesRegex(GateError, "actual merger"):
            validate_release(api, SOURCE_SHA)

    def test_accepts_documented_workflow_path_ref_suffix_for_main_and_pr_runs(self):
        api = FakeAPI()
        api.main_run["path"] = f"{RELEASE_PATH}@refs/heads/main"
        api.pr_run["path"] = f"{RELEASE_PATH}@refs/pull/70/merge"

        proof = validate_release(api, SOURCE_SHA, expected_main_run_id=MAIN_RUN_ID)

        self.assertEqual(proof["source_sha"], SOURCE_SHA)

    def test_rejects_spoof_main_run_with_matching_jobs_and_workflow_path_prefix(self):
        api = FakeAPI()
        api.main_run["workflow_id"] = SPOOF_WORKFLOW_ID
        api.main_run["path"] = f"{RELEASE_PATH}@spoof.yml@refs/heads/main"

        with self.assertRaisesRegex(GateError, "No matching Release candidate gates run"):
            validate_release(api, SOURCE_SHA)

    def test_rejects_spoof_pr_run_with_matching_jobs_and_workflow_path_prefix(self):
        api = FakeAPI()
        api.pr_run["workflow_id"] = SPOOF_WORKFLOW_ID
        api.pr_run["path"] = f"{RELEASE_PATH}@spoof.yml@refs/pull/70/merge"

        with self.assertRaisesRegex(GateError, "no Release candidate gates run"):
            validate_release(api, SOURCE_SHA)

    def test_spoof_release_path_remains_gated_as_an_unrelated_pr_workflow(self):
        api = FakeAPI()
        spoof_run = {
            **api.pr_run,
            "id": PR_RUN_ID + 1,
            "workflow_id": SPOOF_WORKFLOW_ID,
            "path": f"{RELEASE_PATH}@spoof.yml@refs/pull/70/merge",
            "status": "completed",
            "conclusion": "failure",
            "run_number": api.pr_run["run_number"] + 1,
        }
        api.pr_runs.append(spoof_run)

        with self.assertRaisesRegex(GateError, "path-focused PR workflow"):
            validate_release(api, SOURCE_SHA)

    def test_rejects_missing_or_malformed_canonical_workflow_id(self):
        for invalid_workflow in (
            {"path": RELEASE_PATH},
            {"id": 0, "path": RELEASE_PATH},
            {"id": "1", "path": RELEASE_PATH},
            {"id": True, "path": RELEASE_PATH},
            {"id": CANONICAL_WORKFLOW_ID, "path": f"{RELEASE_PATH}@spoof.yml"},
        ):
            with self.subTest(workflow=invalid_workflow):
                api = FakeAPI()
                api.canonical_workflow = invalid_workflow
                with self.assertRaisesRegex(GateError, "canonical release workflow identity"):
                    validate_release(api, SOURCE_SHA)

    def test_latest_main_run_selection_ignores_spoof_and_checks_expected_run_id(self):
        api = FakeAPI()
        spoof_run = {
            **api.main_run,
            "id": 111,
            "workflow_id": SPOOF_WORKFLOW_ID,
            "path": f"{RELEASE_PATH}@spoof.yml@refs/heads/main",
            "run_number": api.main_run["run_number"] + 1,
        }
        api.main_runs.append(spoof_run)

        proof = validate_release(api, SOURCE_SHA, expected_main_run_id=MAIN_RUN_ID)

        self.assertEqual(proof["main_run_id"], MAIN_RUN_ID)
        with self.assertRaisesRegex(GateError, "does not match the requested source SHA"):
            validate_release(api, SOURCE_SHA, expected_main_run_id=spoof_run["id"])

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
        api.pull_summary["user"]["login"] = "reviewer"
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

    def test_unverified_chat_merge_claim_and_owner_token_are_not_release_evidence(self):
        # Issue #100: an agent can call GitHub with an owner-attributed token.
        # A human-sounding comment and owner merged_by identity do not prove
        # that an authenticated chat user authorized this exact PR head.
        api = FakeAPI()
        api.reviews = []
        api.comments = [{
            "id": 503,
            "body": f"i5 preview OK; mergeして PR #70 HEAD {PR_HEAD_SHA}",
            "user": {"login": OWNER_LOGIN, "type": "User"},
            "author_association": "OWNER",
            "created_at": "2026-11-02T00:02:00Z",
            "updated_at": "2026-11-02T00:02:00Z",
        }]
        with self.assertRaisesRegex(GateError, "solo-owner evidence pair"):
            validate_release(api, SOURCE_SHA, expected_main_run_id=MAIN_RUN_ID)

    def test_rejects_unauthenticated_chat_approval_claim_shapes(self):
        cases = (
            ("forged_owner_comment", "comment", OWNER_LOGIN, "User", 70, PR_HEAD_SHA),
            ("bot_comment", "comment", "release-agent[bot]", "Bot", 70, PR_HEAD_SHA),
            ("pr_body_claim", "body", OWNER_LOGIN, "User", 70, PR_HEAD_SHA),
            ("different_pr", "comment", OWNER_LOGIN, "User", 71, PR_HEAD_SHA),
            ("different_head", "comment", OWNER_LOGIN, "User", 70, "c" * 40),
        )
        for name, location, login, user_type, claimed_pr, claimed_head in cases:
            with self.subTest(claim=name):
                api = FakeAPI()
                api.reviews = []
                # Owner merged_by and green CI remain present in every case;
                # agent-writable claims cannot authenticate human chat consent.
                claim = (
                    f"Authenticated chat approval from {OWNER_LOGIN}: mergeして "
                    f"{api.repository} PR #{claimed_pr} HEAD {claimed_head}"
                )
                if location == "body":
                    api.pull_summary["body"] = api.pull["body"] = claim
                else:
                    api.comments = [{
                        "id": 503,
                        "body": claim,
                        "user": {"login": login, "type": user_type},
                        "author_association": "OWNER" if login == OWNER_LOGIN else "NONE",
                        "created_at": "2026-11-02T00:02:00Z",
                        "updated_at": "2026-11-02T00:02:00Z",
                    }]

                with self.assertRaisesRegex(GateError, "solo-owner evidence pair"):
                    validate_release(api, SOURCE_SHA, expected_main_run_id=MAIN_RUN_ID)

    def test_accepts_solo_owner_policy_with_exact_immutable_comments(self):
        api = FakeAPI()
        api.reviews = []
        api.comments = solo_comments()

        proof = validate_release(api, SOURCE_SHA, expected_main_run_id=MAIN_RUN_ID)

        self.assertEqual(proof["pr_head_sha"], PR_HEAD_SHA)

    def test_accepts_solo_owner_policy_with_github_crlf_comment_separator(self):
        api = FakeAPI()
        api.reviews = []
        api.comments = solo_comments()
        for comment in api.comments:
            comment["body"] = comment["body"].replace("\n", "\r\n", 1)

        proof = validate_release(api, SOURCE_SHA, expected_main_run_id=MAIN_RUN_ID)

        self.assertEqual(proof["pr_head_sha"], PR_HEAD_SHA)

    def test_accepts_solo_owner_policy_with_one_trailing_crlf_after_json(self):
        api = FakeAPI()
        api.reviews = []
        api.comments = solo_comments()
        # Exact GitHub owner-review body shape from PR #95.
        api.comments[0]["body"] = api.comments[0]["body"].replace(
            "\n", "\r\n", 1
        ) + "\r\n"
        api.comments[1]["body"] = api.comments[1]["body"].replace(
            "\n", "\r\n", 1
        )

        proof = validate_release(api, SOURCE_SHA, expected_main_run_id=MAIN_RUN_ID)
        self.assertEqual(proof["pr_head_sha"], PR_HEAD_SHA)

    def test_accepts_solo_owner_policy_with_one_trailing_lf_after_json(self):
        api = FakeAPI()
        api.reviews = []
        api.comments = solo_comments()
        api.comments[0]["body"] += "\n"

        proof = validate_release(api, SOURCE_SHA, expected_main_run_id=MAIN_RUN_ID)
        self.assertEqual(proof["pr_head_sha"], PR_HEAD_SHA)

    def test_rejects_solo_owner_policy_with_multiple_trailing_newlines(self):
        for suffix in ("\r\n\r\n", "\n\n", "\r\n\n"):
            with self.subTest(suffix=repr(suffix)):
                api = FakeAPI()
                api.reviews = []
                api.comments = solo_comments()
                api.comments[0]["body"] += suffix

                with self.assertRaisesRegex(GateError, "invalid structure"):
                    validate_release(api, SOURCE_SHA)

    def test_rejects_solo_owner_policy_with_embedded_json_linebreaks(self):
        for newline in ("\n", "\r\n"):
            with self.subTest(newline=repr(newline)):
                api = FakeAPI()
                api.reviews = []
                api.comments = solo_comments()
                api.comments[0]["body"] = api.comments[0]["body"].replace(
                    ',"head_sha"', ',' + newline + '"head_sha"', 1
                )

                with self.assertRaisesRegex(GateError, "invalid structure"):
                    validate_release(api, SOURCE_SHA)

    def test_rejects_solo_owner_policy_with_carriage_return_inside_json(self):
        api = FakeAPI()
        api.reviews = []
        api.comments = solo_comments()
        api.comments[0]["body"] += "\r"

        with self.assertRaisesRegex(GateError, "invalid structure"):
            validate_release(api, SOURCE_SHA)

    def test_rejects_nonstandard_json_constants_in_solo_comments(self):
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                body = f'{SOLO_REVIEW_MARKER}\n{{"example":{constant}}}'
                with self.assertRaisesRegex(GateError, "invalid JSON"):
                    release_gate._parse_structured_comment_body(
                        body, SOLO_REVIEW_MARKER
                    )

    def test_rejects_solo_policy_when_review_comment_was_edited(self):
        api = FakeAPI()
        api.reviews = []
        api.comments = solo_comments()
        api.comments[0]["updated_at"] = "2026-11-02T00:02:00Z"

        with self.assertRaisesRegex(GateError, "edited"):
            validate_release(api, SOURCE_SHA)

    def test_rejects_solo_policy_when_release_references_the_wrong_review(self):
        api = FakeAPI()
        api.reviews = []
        api.comments = solo_comments(review_comment_id=501, release_comment_id=502)
        release_payload = json.loads(api.comments[1]["body"].split("\n", 1)[1])
        release_payload["review_comment_id"] = 999
        api.comments[1]["body"] = (
            f"{SOLO_RELEASE_MARKER}\n"
            f"{json.dumps(release_payload, sort_keys=True, separators=(',', ':'))}"
        )

        with self.assertRaisesRegex(GateError, "reference"):
            validate_release(api, SOURCE_SHA)

    def test_rejects_solo_policy_from_public_lookalike_comment(self):
        api = FakeAPI()
        api.reviews = []
        api.comments = solo_comments()
        api.comments[0]["user"] = {"login": "public-user", "type": "User"}
        api.comments[0]["author_association"] = "NONE"

        with self.assertRaisesRegex(GateError, "repository owner"):
            validate_release(api, SOURCE_SHA)

    def test_rejects_solo_policy_when_owner_is_not_the_actual_merger(self):
        api = FakeAPI()
        api.reviews = []
        api.comments = solo_comments()
        api.pull["merged_by"] = {"login": "different-user", "type": "User"}

        with self.assertRaisesRegex(GateError, "actual merger"):
            validate_release(api, SOURCE_SHA)

    def test_rejects_self_github_approval_in_solo_mode(self):
        api = FakeAPI()
        api.pull["user"] = {"login": OWNER_LOGIN}
        api.pull_summary["user"] = {"login": OWNER_LOGIN, "type": "User"}
        api.reviews = [
            {
                "user": {"login": OWNER_LOGIN},
                "state": "APPROVED",
                "commit_id": PR_HEAD_SHA,
                "submitted_at": "2026-11-01T23:00:00Z",
                "author_association": "OWNER",
            }
        ]
        api.comments = solo_comments()

        with self.assertRaisesRegex(GateError, "self GitHub approval"):
            validate_release(api, SOURCE_SHA)

    def test_rejects_revoked_solo_policy(self):
        api = FakeAPI()
        api.reviews = []
        api.comments = solo_comments()
        api.comments.append(
            structured_comment(
                SOLO_REVOKE_MARKER,
                {
                    "comment_id": 501,
                    "head_sha": PR_HEAD_SHA,
                    "pr": 70,
                    "repository": "hiroyuki9614/book_library",
                },
                503,
                created_at="2026-11-02T00:02:00Z",
            )
        )

        with self.assertRaisesRegex(GateError, "revoked"):
            validate_release(api, SOURCE_SHA)

    def test_rejects_contradictory_multiple_current_review_comments(self):
        api = FakeAPI()
        api.reviews = []
        api.comments = solo_comments()
        duplicate = solo_comments(review_comment_id=504, release_comment_id=505)[0]
        api.comments.insert(1, duplicate)

        with self.assertRaisesRegex(GateError, "multiple"):
            validate_release(api, SOURCE_SHA)

    def test_rejects_stale_solo_policy_for_an_older_pr_head(self):
        api = FakeAPI()
        api.reviews = []
        api.comments = solo_comments(head_sha="c" * 40)

        with self.assertRaisesRegex(GateError, "exact PR head"):
            validate_release(api, SOURCE_SHA)


if __name__ == "__main__":
    unittest.main()
