"""Offline safety tests. No Docker/socket/network/production access."""

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


def complete_evidence():
    return {
        "schema_version": 1,
        "source": {"repository": "hiroyuki9614/book_library", "head": HEAD,
                   "work_prompt_document_id": "63ef369e-efce-4546-b643-8d27302dc801",
                   "github_identity_readback": True},
        "operator": {"principal": "agent", "uid": 1002, "host": "v133-18-43-217-vir"},
        "runtime": {"rootless": True, "docker_service_enabled": True,
                    "linger_enabled": True, "runc_container_state_consistent": True,
                    "postgres_old_process_confirmed_absent": True,
                    "shared_docker_impact_inventory_complete": True,
                    "memory_pressure_root_cause_addressed": True},
        "services": {name: {"restart": "unless-stopped", "image_id_retained": True}
                     for name in ("db", "backend", "frontend", "nginx")},
        "postgres_volume": {"name": "belib-production_postgres-data", "exists": True,
                            "identity_pinned": True, "data_integrity_verified": True},
        "backup": {"postgres_volume": "belib-production_postgres-data",
                   "checksum_verified": True, "restore_drill_verified": True},
        "protected_storage": {"r2_objects_unchanged_verified": True,
                              "private_access_verified": True},
        "coordination": {"other_projects_considered": True,
                         "owner_approved_cross_project_effects": True,
                         "existing_cd_lock_respected": True, "rollback_rehearsed": True},
    }


class ReadOnlyPreflightTests(unittest.TestCase):
    def check_block(self, mutate, code):
        e = complete_evidence()
        mutate(e)
        result = assess(e, HEAD)
        self.assertEqual(result["decision"], "BLOCKED")
        self.assertIn(code, result["reason_codes"])
        self.assertEqual(result["authorization"], "NONE")
        self.assertFalse(result["mutation_attempted"])
        self.assertFalse(result["safe_to_repair"])

    def test_complete_is_only_readiness_for_review(self):
        r = assess(complete_evidence(), HEAD)
        self.assertEqual(r["decision"], "READY_FOR_OWNER_REVIEW")
        self.assertEqual(r["authorization"], "NONE")
        self.assertFalse(r["safe_to_repair"])

    def test_current_oci_mismatch_blocks(self):
        self.check_block(lambda e: e["runtime"].update(runc_container_state_consistent=False),
                         "RUNC_STATE_AMBIGUOUS")

    def test_postgres_process_ambiguity_blocks(self):
        self.check_block(lambda e: e["runtime"].update(postgres_old_process_confirmed_absent=False),
                         "OLD_DB_PROCESS_UNVERIFIED")

    def test_cross_project_approval_missing_blocks(self):
        self.check_block(lambda e: e["coordination"].update(owner_approved_cross_project_effects=False),
                         "CROSS_PROJECT_APPROVAL_MISSING")

    def test_backup_wrong_volume_blocks(self):
        self.check_block(lambda e: e["backup"].update(postgres_volume="other"),
                         "BACKUP_VOLUME_MISMATCH")

    def test_backup_restore_missing_blocks(self):
        self.check_block(lambda e: e["backup"].update(restore_drill_verified=False),
                         "BACKUP_RESTORE_UNVERIFIED")

    def test_source_stale_blocks(self):
        self.check_block(lambda e: e["source"].update(head="0"*40),
                         "SOURCE_HEAD_MISMATCH")

    def test_omitted_services_blocks(self):
        self.check_block(lambda e: e["services"].pop("nginx"), "SERVICE_INVENTORY_MISMATCH")

    def test_oom_unresolved_blocks(self):
        self.check_block(lambda e: e["runtime"].update(memory_pressure_root_cause_addressed=False),
                         "OOM_RECURRENCE_UNRESOLVED")

    def test_r2_privacy_unverified_blocks(self):
        self.check_block(lambda e: e["protected_storage"].update(private_access_verified=False),
                         "R2_PRIVACY_UNVERIFIED")

    def test_wrong_owner_blocks(self):
        self.check_block(lambda e: e["operator"].update(uid=0), "WRONG_RUNTIME_OWNER")

    def test_input_secrets_never_reflected(self):
        e = complete_evidence()
        e["secret_token"] = "must-not-appear-123"
        self.assertNotIn("must-not-appear-123", json.dumps(assess(e, HEAD)))

    def test_cli_missing_file_fails_closed(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            code = main(["--evidence", "/nonexistent/file.json", "--expected-head", HEAD])
        self.assertEqual(code, 30)
        self.assertEqual(json.loads(stdout.getvalue())["decision"], "PREFLIGHT_ERROR")

    def test_cli_valid_but_incomplete_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "safe.json"
            path.write_text('{"schema_version":1,"token":"hidden"}', encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                code = main(["--evidence", str(path), "--expected-head", HEAD])
            self.assertEqual(code, 20)
            self.assertNotIn("hidden", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
