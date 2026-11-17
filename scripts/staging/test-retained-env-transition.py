#!/usr/bin/env python3
"""Unit checks for the commit-only VCS ref switch used by generation v2."""
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SPEC=importlib.util.spec_from_file_location("retained_executor_env",Path(__file__).with_name("retained-executor.py"))
executor=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(executor)


class EnvironmentCommitTests(unittest.TestCase):
    def fixture(self, root):
        directory=root/"config";directory.mkdir(mode=0o700)
        path=directory/"i5.env"
        prior=("BELIB_STAGE_DB_BOOTSTRAP_PASSWORD="+"1"*64+"\n"
               "BELIB_STAGE_DB_APP_PASSWORD="+"2"*64+"\n"
               "BELIB_STAGE_DB_MIGRATE_PASSWORD="+"3"*64+"\n"
               "BELIB_STAGE_AUTH_SECRET="+"4"*64+"\n"
               "BELIB_STAGE_VCS_REF="+"a"*40+"\n"
               "BELIB_STAGE_PORT=18083\n").encode()
        candidate=prior.replace(b"BELIB_STAGE_VCS_REF="+b"a"*40,b"BELIB_STAGE_VCS_REF="+b"b"*40)
        path.write_bytes(prior);path.chmod(0o600)
        transition={"prior_sha256":hashlib.sha256(prior).hexdigest(),
            "candidate_sha256":hashlib.sha256(candidate).hexdigest(),
            "prior_source_sha":"a"*40,"candidate_source_sha":"b"*40}
        source=SimpleNamespace(directory=root,operation_id="c"*32)
        prepared=SimpleNamespace(value={"sha256":"d"*64})
        controller=SimpleNamespace(plan={"operation_id":"c"*32})
        journal=SimpleNamespace(value={"phase":"commit_decided"})
        operation=executor.IsolatedCopyOperations(source,None,prepared,controller,
            {"environment_transition":transition},{},journal,env_file=path,
            prior_env_raw=prior,candidate_env_raw=candidate)
        return path,prior,candidate,operation

    def test_commit_switches_only_source_atomically_and_repeats_exactly(self):
        with tempfile.TemporaryDirectory() as temporary:
            path,prior,candidate,operation=self.fixture(Path(temporary))
            with patch.object(executor,"STAGING_ENV_FILE",path):
                operation._commit_candidate_env()
                self.assertEqual(path.read_bytes(),candidate)
                self.assertEqual(path.stat().st_mode & 0o777,0o600)
                operation._commit_candidate_env()
                self.assertEqual(path.read_bytes(),candidate)

    def test_commit_refuses_env_drift_without_overwriting_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            path,prior,candidate,operation=self.fixture(Path(temporary))
            drift=prior.replace(b"BELIB_STAGE_PORT=18083",b"BELIB_STAGE_PORT=18084")
            path.write_bytes(drift)
            with patch.object(executor,"STAGING_ENV_FILE",path), self.assertRaisesRegex(
                    executor.ExecutorBlocked,"drifted"):
                operation._commit_candidate_env()
            self.assertEqual(path.read_bytes(),drift)

    def test_commit_reconciles_exact_mode0400_pending_after_commit_decision(self):
        with tempfile.TemporaryDirectory() as temporary:
            path,prior,candidate,operation=self.fixture(Path(temporary))
            pending=path.parent/('.i5.env.'+operation.controller.plan['operation_id']+'.pending')
            pending.write_bytes(candidate);pending.chmod(0o400)
            with patch.object(executor,"STAGING_ENV_FILE",path):
                operation._commit_candidate_env()
            self.assertEqual(path.read_bytes(),candidate)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertFalse(pending.exists())

    def test_unique_partial_temporary_does_not_wedge_same_operation_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            path,prior,candidate,operation=self.fixture(Path(temporary))
            orphan=path.parent/('.i5.env.'+operation.controller.plan['operation_id']+'.deadbeef.pending')
            orphan.write_bytes(candidate[:12]);orphan.chmod(0o600)
            with patch.object(executor,"STAGING_ENV_FILE",path):
                operation._commit_candidate_env()
            self.assertEqual(path.read_bytes(),candidate)
            self.assertEqual(orphan.read_bytes(),candidate[:12])

    def test_source_transition_preserves_missing_final_newline_exactly(self):
        prior=(b"BELIB_STAGE_DB_BOOTSTRAP_PASSWORD="+b"1"*64+b"\n"
               b"BELIB_STAGE_DB_APP_PASSWORD="+b"2"*64+b"\n"
               b"BELIB_STAGE_DB_MIGRATE_PASSWORD="+b"3"*64+b"\n"
               b"BELIB_STAGE_AUTH_SECRET="+b"4"*64+b"\n"
               b"BELIB_STAGE_VCS_REF="+b"a"*40+b"\n"
               b"BELIB_STAGE_PORT=18083")
        candidate=executor._replace_env_source(prior,"a"*40,"b"*40)
        restored=executor._replace_env_source(candidate,"b"*40,"a"*40)
        self.assertFalse(prior.endswith(b"\n"))
        self.assertEqual(restored,prior)
        self.assertEqual(hashlib.sha256(restored).hexdigest(),hashlib.sha256(prior).hexdigest())


if __name__=="__main__":
    unittest.main()
