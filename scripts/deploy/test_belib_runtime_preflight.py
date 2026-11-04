"""Offline tests for the read-only BeLib runtime snapshot validator."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

try:
    from .belib_runtime_preflight import assess, main
except ImportError:
    from belib_runtime_preflight import assess, main

HEAD = "1f41e1feafd0ae7aa333f4c2b676c563f00fc764"
PROJECT = "belib-production"
VOLUME = "belib-production_postgres-data"
NETWORK = "belib-production_default"


def healthy_snapshot():
    return {
        "schema_version": 2,
        "source": {
            "repository": "hiroyuki9614/book_library",
            "head": HEAD,
            "work_prompt_document_id": "63ef369e-efce-4546-b643-8d27302dc801",
        },
        "runtime": {
            "rootless": True,
            "docker_task_runc_consistent": True,
            "shared_inventory_complete": True,
            "namespace_error": False,
            "other_project_impact": "none",
            "old_db_process_access": "confirmed_absent",
        },
        "services": {
            name: {
                "project": PROJECT,
                "service": name,
                "container_id": f"{index:012x}",
                "state": "running",
                "health": "healthy",
                "restart": "unless-stopped",
                "image_id": f"sha256:{index:064x}",
                "networks": [NETWORK],
                "volumes": [VOLUME] if name == "db" else [],
            }
            for index, name in enumerate(("db", "backend", "frontend", "nginx"), 1)
        },
        "postgres_volume": {
            "name": VOLUME,
            "exists": True,
            "identity": "a" * 64,
        },
    }


class ReadOnlyPreflightTests(unittest.TestCase):
    def assert_shared_failure(self, snapshot, reason):
        report = assess(snapshot, HEAD)
        self.assertEqual(report["decision"], "SHARED_RUNTIME_RECOVERY_REQUIRED")
        self.assertIn(reason, report["reason_codes"])
        self.assertEqual(report["authorization"], "NONE")
        self.assertFalse(report["mutation_attempted"])
        self.assertFalse(report["safe_to_repair"])

    def test_healthy_snapshot_is_only_ready_for_owner_review(self):
        report = assess(healthy_snapshot(), HEAD)
        self.assertEqual(report["decision"], "READY_FOR_OWNER_REVIEW")
        self.assertEqual(report["authorization"], "NONE")
        self.assertFalse(report["mutation_attempted"])
        self.assertFalse(report["safe_to_repair"])

    def test_ordinary_stopped_container_blocks(self):
        evidence = healthy_snapshot()
        evidence["services"]["frontend"]["state"] = "exited"
        report = assess(evidence, HEAD)
        self.assertEqual(report["decision"], "NORMAL_CONTAINER_STOPPED")
        self.assertEqual(report["authorization"], "NONE")
        self.assertFalse(report["mutation_attempted"])

    def test_database_unhealthy_blocks(self):
        evidence = healthy_snapshot()
        evidence["services"]["db"]["health"] = "unhealthy"
        report = assess(evidence, HEAD)
        self.assertEqual(report["decision"], "SERVICE_UNHEALTHY")
        self.assertIn("SERVICE_DB_NOT_HEALTHY", report["reason_codes"])
        self.assertEqual(report["authorization"], "NONE")
        self.assertFalse(report["mutation_attempted"])

    def test_duplicate_container_ids_block(self):
        evidence = healthy_snapshot()
        evidence["services"]["nginx"]["container_id"] = evidence["services"]["db"]["container_id"]
        self.assert_shared_failure(evidence, "DUPLICATE_CONTAINER_ID")

    def test_task_runc_mismatch_blocks(self):
        evidence = healthy_snapshot()
        evidence["runtime"]["docker_task_runc_consistent"] = False
        self.assert_shared_failure(evidence, "OCI_STATE_MISMATCH")

    def test_other_project_impact_blocks(self):
        evidence = healthy_snapshot()
        evidence["runtime"]["other_project_impact"] = "affected"
        self.assert_shared_failure(evidence, "OTHER_PROJECT_IMPACT")

    def test_wrong_database_volume_identity_blocks(self):
        evidence = healthy_snapshot()
        evidence["services"]["db"]["volumes"] = ["other-project_data"]
        self.assert_shared_failure(evidence, "SERVICE_DB_VOLUME_MISMATCH")

    def test_namespace_error_blocks(self):
        evidence = healthy_snapshot()
        evidence["runtime"]["namespace_error"] = True
        self.assert_shared_failure(evidence, "NAMESPACE_ERROR")

    def test_unknown_old_database_process_state_blocks(self):
        evidence = healthy_snapshot()
        evidence["runtime"]["old_db_process_access"] = "unknown"
        self.assert_shared_failure(evidence, "OLD_DB_PROCESS_ACCESS_UNKNOWN")

    def test_service_without_health_status_is_unhealthy(self):
        evidence = healthy_snapshot()
        evidence["services"]["backend"]["health"] = "not_configured"
        report = assess(evidence, HEAD)
        self.assertEqual(report["decision"], "SERVICE_UNHEALTHY")
        self.assertIn("SERVICE_BACKEND_NOT_HEALTHY", report["reason_codes"])

    def test_unexpected_fields_and_secret_are_not_reflected(self):
        evidence = healthy_snapshot()
        evidence["operator_supplied_command"] = "docker rm secret-should-not-appear"
        evidence["token"] = "secret-should-not-appear"
        report = assess(evidence, HEAD)
        self.assertEqual(report["decision"], "PREFLIGHT_EVIDENCE_INVALID")
        encoded = json.dumps(report)
        self.assertNotIn("secret-should-not-appear", encoded)
        self.assertIn("SNAPSHOT_FIELDS_INVALID", report["reason_codes"])

    def test_cli_invalid_file_fails_closed_without_echoing_input(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = main(["--evidence", "/nonexistent/snapshot.json", "--expected-head", HEAD])
        self.assertEqual(code, 30)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["decision"], "PREFLIGHT_EVIDENCE_INVALID")
        self.assertEqual(report["authorization"], "NONE")

    def test_cli_rejects_unknown_command_or_path_options(self):
        for option, value in (("--container", "anything"), ("--shell", "id")):
            with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()):
                code = main(["--evidence", "/tmp/snapshot.json", "--expected-head", HEAD, option, value])
            self.assertEqual(code, 30)
            self.assertEqual(
                json.loads(stdout.getvalue())["decision"], "PREFLIGHT_EVIDENCE_INVALID"
            )

    def test_cli_does_not_treat_shell_metacharacters_in_path_as_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            literal = Path(directory) / "$(touch SHOULD_NOT_EXIST).json"
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                code = main(["--evidence", str(literal), "--expected-head", HEAD])
            self.assertEqual(code, 30)
            self.assertFalse((Path(directory) / "SHOULD_NOT_EXIST").exists())
            self.assertEqual(json.loads(stdout.getvalue())["authorization"], "NONE")

    def test_non_object_snapshot_is_invalid_evidence(self):
        report = assess([], HEAD)
        self.assertEqual(report["decision"], "PREFLIGHT_EVIDENCE_INVALID")
        self.assertIn("SNAPSHOT_NOT_OBJECT", report["reason_codes"])

    def test_snapshot_never_authenticates_claimed_source(self):
        evidence = healthy_snapshot()
        report = assess(evidence, HEAD)
        self.assertIn("not evidence authentication", report["note"])
        self.assertEqual(report["authorization"], "NONE")


if __name__ == "__main__":
    unittest.main()
