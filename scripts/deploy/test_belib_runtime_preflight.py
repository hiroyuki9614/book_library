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
            "storage_driver": "local",
            "error_class": "none",
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
                "docker_state": "running",
                "task_state": "running",
                "runc_state": "running",
                "task_container_id": f"{index:012x}",
                "runc_container_id": f"{index:012x}",
                "health": "not_configured" if name in {"frontend", "nginx"} else "healthy",
                "restart": "unless-stopped",
                "expected_image_id": f"sha256:{index:064x}",
                "observed_image_id": f"sha256:{index:064x}",
                "networks": [NETWORK],
                "volumes": [VOLUME] if name == "db" else (["belib-production_book-files"] if name == "backend" else []),
            }
            for index, name in enumerate(("db", "backend", "frontend", "nginx"), 1)
        },
        "volumes": {
            "postgres": {
                "name": VOLUME,
                "exists": True,
                "expected_identity": "a" * 64,
                "observed_identity": "a" * 64,
            },
            "book_files": {
                "name": "belib-production_book-files",
                "exists": True,
                "expected_identity": "b" * 64,
                "observed_identity": "b" * 64,
            },
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
        evidence["services"]["frontend"]["docker_state"] = "exited"
        evidence["services"]["frontend"]["task_state"] = "exited"
        evidence["services"]["frontend"]["runc_state"] = "absent"
        evidence["services"]["frontend"]["task_state"] = "absent"
        evidence["services"]["frontend"]["task_container_id"] = None
        evidence["services"]["frontend"]["runc_container_id"] = None
        report = assess(evidence, HEAD)
        self.assertEqual(report["decision"], "NORMAL_CONTAINER_STOPPED")
        self.assertEqual(report["authorization"], "NONE")
        self.assertFalse(report["mutation_attempted"])

    def test_exited_with_stopped_or_absent_task_runc_is_normal_stop(self):
        evidence = healthy_snapshot()
        service = evidence["services"]["db"]
        service.update(docker_state="exited", task_state="stopped", runc_state="absent",
                       task_container_id=None, runc_container_id=None)
        self.assertEqual(assess(evidence, HEAD)["decision"], "NORMAL_CONTAINER_STOPPED")

    def test_stopped_task_runc_with_matching_ids_is_normal_stop(self):
        evidence = healthy_snapshot()
        service = evidence["services"]["db"]
        service.update(docker_state="exited", task_state="stopped", runc_state="stopped")
        self.assertEqual(assess(evidence, HEAD)["decision"], "NORMAL_CONTAINER_STOPPED")

    def test_stopped_task_runc_with_wrong_id_is_shared_conflict(self):
        evidence = healthy_snapshot()
        service = evidence["services"]["db"]
        service.update(docker_state="exited", task_state="stopped", runc_state="stopped",
                       task_container_id="f" * 12)
        self.assert_shared_failure(evidence, "SERVICE_DB_RUNTIME_ID_MISMATCH")

    def test_exited_docker_with_live_task_runc_is_shared_conflict(self):
        evidence = healthy_snapshot()
        service = evidence["services"]["db"]
        service.update(docker_state="exited", task_state="running", runc_state="running")
        self.assert_shared_failure(evidence, "OCI_STATE_MISMATCH")

    def test_runtime_container_id_mismatch_blocks(self):
        evidence = healthy_snapshot()
        evidence["services"]["db"]["task_container_id"] = "f" * 12
        self.assert_shared_failure(evidence, "SERVICE_DB_RUNTIME_ID_MISMATCH")

    def test_unknown_storage_driver_is_invalid_evidence(self):
        evidence = healthy_snapshot()
        evidence["runtime"]["storage_driver"] = "bogus"
        report = assess(evidence, HEAD)
        self.assertEqual(report["decision"], "PREFLIGHT_EVIDENCE_INVALID")
        self.assertIn("STORAGE_DRIVER_INVALID", report["reason_codes"])

    def test_r2_storage_does_not_attach_book_files_to_backend(self):
        evidence = healthy_snapshot()
        evidence["runtime"]["storage_driver"] = "r2"
        evidence["services"]["backend"]["volumes"] = []
        self.assertEqual(assess(evidence, HEAD)["decision"], "READY_FOR_OWNER_REVIEW")

    def test_r2_storage_rejects_book_files_backend_mount(self):
        evidence = healthy_snapshot()
        evidence["runtime"]["storage_driver"] = "r2"
        self.assert_shared_failure(evidence, "SERVICE_BACKEND_VOLUME_MISMATCH")

    def test_database_unhealthy_blocks(self):
        evidence = healthy_snapshot()
        evidence["services"]["db"]["health"] = "unhealthy"
        report = assess(evidence, HEAD)
        self.assertEqual(report["decision"], "SERVICE_UNHEALTHY")
        self.assertIn("SERVICE_DB_NOT_HEALTHY", report["reason_codes"])
        self.assertEqual(report["authorization"], "NONE")
        self.assertFalse(report["mutation_attempted"])

    def test_db_and_backend_without_healthchecks_remain_unhealthy(self):
        for name in ("db", "backend"):
            with self.subTest(service=name):
                evidence = healthy_snapshot()
                evidence["services"][name]["health"] = "not_configured"
                report = assess(evidence, HEAD)
                self.assertEqual(report["decision"], "SERVICE_UNHEALTHY")
                self.assertIn(f"SERVICE_{name.upper()}_NOT_HEALTHY", report["reason_codes"])
                self.assertEqual(report["authorization"], "NONE")
                self.assertFalse(report["mutation_attempted"])
                self.assertFalse(report["safe_to_repair"])

    def test_frontend_and_nginx_unhealthy_or_starting_block(self):
        for name in ("frontend", "nginx"):
            for health in ("unhealthy", "starting"):
                with self.subTest(service=name, health=health):
                    evidence = healthy_snapshot()
                    evidence["services"][name]["health"] = health
                    report = assess(evidence, HEAD)
                    self.assertEqual(report["decision"], "SERVICE_UNHEALTHY")
                    self.assertIn(f"SERVICE_{name.upper()}_NOT_HEALTHY", report["reason_codes"])
                    self.assertEqual(report["authorization"], "NONE")
                    self.assertFalse(report["mutation_attempted"])
                    self.assertFalse(report["safe_to_repair"])

    def test_frontend_and_nginx_explicitly_healthy_are_accepted(self):
        evidence = healthy_snapshot()
        evidence["services"]["frontend"]["health"] = "healthy"
        evidence["services"]["nginx"]["health"] = "healthy"
        report = assess(evidence, HEAD)
        self.assertEqual(report["decision"], "READY_FOR_OWNER_REVIEW")
        self.assertEqual(report["authorization"], "NONE")
        self.assertFalse(report["mutation_attempted"])
        self.assertFalse(report["safe_to_repair"])

    def test_duplicate_container_ids_block(self):
        evidence = healthy_snapshot()
        evidence["services"]["nginx"]["container_id"] = evidence["services"]["db"]["container_id"]
        self.assert_shared_failure(evidence, "DUPLICATE_CONTAINER_ID")

    def test_task_runc_mismatch_blocks(self):
        evidence = healthy_snapshot()
        evidence["services"]["db"]["runc_state"] = "stopped"
        self.assert_shared_failure(evidence, "OCI_STATE_MISMATCH")

    def test_wrong_but_valid_image_digest_blocks(self):
        evidence = healthy_snapshot()
        evidence["services"]["backend"]["observed_image_id"] = "sha256:" + "f" * 64
        self.assert_shared_failure(evidence, "SERVICE_BACKEND_IMAGE_ID_MISMATCH")

    def test_wrong_but_valid_database_volume_identity_blocks(self):
        evidence = healthy_snapshot()
        evidence["volumes"]["postgres"]["observed_identity"] = "c" * 64
        self.assert_shared_failure(evidence, "VOLUME_POSTGRES_IDENTITY_MISMATCH")

    def test_wrong_book_files_volume_identity_blocks(self):
        evidence = healthy_snapshot()
        evidence["volumes"]["book_files"]["observed_identity"] = "d" * 64
        self.assert_shared_failure(evidence, "VOLUME_BOOK_FILES_IDENTITY_MISMATCH")

    def test_missing_book_files_volume_blocks(self):
        evidence = healthy_snapshot()
        evidence["volumes"]["book_files"]["exists"] = False
        self.assert_shared_failure(evidence, "VOLUME_BOOK_FILES_MISSING")

    def test_duplicate_runtime_id_event_blocks_with_unique_service_ids(self):
        evidence = healthy_snapshot()
        evidence["runtime"]["error_class"] = "duplicate_container_id"
        self.assertTrue(len({s["container_id"] for s in evidence["services"].values()}) == 4)
        self.assert_shared_failure(evidence, "RUNTIME_ERROR_REPORTED")

    def test_other_project_impact_blocks(self):
        evidence = healthy_snapshot()
        evidence["runtime"]["other_project_impact"] = "affected"
        self.assert_shared_failure(evidence, "OTHER_PROJECT_IMPACT")

    def test_wrong_database_volume_identity_blocks(self):
        evidence = healthy_snapshot()
        evidence["services"]["db"]["volumes"] = ["other-project_data"]
        self.assert_shared_failure(evidence, "SERVICE_DB_VOLUME_MISMATCH")

    def test_namespace_error_blocks_with_realistic_frontend_health(self):
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

    def test_cli_rejects_path_container_and_shell_options(self):
        for option, value in (("--evidence", "/tmp/snapshot.json"), ("--path", "/tmp/snapshot.json"),
                              ("--container", "anything"), ("--shell", "id")):
            with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()):
                code = main(["--expected-head", HEAD, option, value], stdin=io.BytesIO(b"{}"))
            self.assertEqual(code, 30)
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["decision"], "PREFLIGHT_EVIDENCE_INVALID")
            self.assertEqual(report["authorization"], "NONE")
            self.assertNotIn("/tmp/snapshot.json", stdout.getvalue())
            self.assertNotIn("anything", stdout.getvalue())

    def test_cli_reads_bounded_json_from_stdin(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = main(["--expected-head", HEAD], stdin=io.BytesIO(json.dumps(healthy_snapshot()).encode()))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["decision"], "READY_FOR_OWNER_REVIEW")

    def test_cli_stdin_over_limit_fails_closed(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = main(["--expected-head", HEAD], stdin=io.BytesIO(b" " * (64 * 1024 + 1)))
        self.assertEqual(code, 30)
        self.assertEqual(json.loads(stdout.getvalue())["decision"], "PREFLIGHT_EVIDENCE_INVALID")

    def test_cli_invalid_stdin_is_not_reflected(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = main(["--expected-head", HEAD], stdin=io.BytesIO(b'{"token":"dont-echo"}'))
        self.assertEqual(code, 30)
        self.assertNotIn("dont-echo", stdout.getvalue())

    def test_non_object_snapshot_is_invalid_evidence(self):
        report = assess([], HEAD)
        self.assertEqual(report["decision"], "PREFLIGHT_EVIDENCE_INVALID")
        self.assertIn("SNAPSHOT_NOT_OBJECT", report["reason_codes"])

    def test_untrusted_runtime_enum_lists_and_objects_fail_closed(self):
        for bad in ([], {}):
            evidence = healthy_snapshot()
            evidence["runtime"]["error_class"] = bad
            report = assess(evidence, HEAD)
            self.assertEqual(report["decision"], "PREFLIGHT_EVIDENCE_INVALID")
            self.assertNotIn(json.dumps(bad), json.dumps(report))

    def test_malformed_state_and_health_values_fail_closed(self):
        for field in ("docker_state", "health"):
            evidence = healthy_snapshot()
            evidence["services"]["db"][field] = ["running"]
            self.assertEqual(assess(evidence, HEAD)["decision"], "PREFLIGHT_EVIDENCE_INVALID")

    def test_invalid_head_type_fails_closed(self):
        report = assess(healthy_snapshot(), [])
        self.assertEqual(report["decision"], "PREFLIGHT_EVIDENCE_INVALID")
        self.assertIn("EXPECTED_HEAD_INVALID", report["reason_codes"])

    def test_snapshot_never_authenticates_claimed_source(self):
        evidence = healthy_snapshot()
        report = assess(evidence, HEAD)
        self.assertIn("not evidence authentication", report["note"])
        self.assertEqual(report["authorization"], "NONE")


if __name__ == "__main__":
    unittest.main()
