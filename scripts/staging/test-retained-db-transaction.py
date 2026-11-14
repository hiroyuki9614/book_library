#!/usr/bin/env python3
"""Transaction regressions; opt-in actual PostgreSQL uses disposable tmpfs only."""

import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("retained_transaction", Path(__file__).with_name("retained-db-transaction.py"))
tx = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tx)
IDENTITY = {"operation_id": "1" * 32, "source_sha": "a" * 40, "retained_state_sha256": "b" * 64}


class Interrupted(BaseException):
    pass


class MemoryOperations:
    def __init__(self, fail=None, interrupt=None):
        self.events = []
        self.fail = fail
        self.interrupt = interrupt
        self.database = "old"
        self.application = "old"
        self.dump = None
        self.writes_enabled = True

    def event(self, name):
        self.events.append(name)
        if name == self.interrupt:
            raise Interrupted(name)
        if name == self.fail:
            raise RuntimeError(name)

    def quiesce(self):
        self.event("quiesce")
        self.application = "stopped"
        self.writes_enabled = False

    def backup(self):
        self.event("backup")
        self.dump = self.database
        return hashlib.sha256(self.dump.encode()).hexdigest()

    def verify_restore(self, digest):
        self.event("verify_restore")
        assert hashlib.sha256(self.dump.encode()).hexdigest() == digest
        assert self.dump == "old"

    def migrate(self):
        self.database = "new"
        self.event("migrate")

    def verify_upgraded(self):
        self.event("verify_upgraded")
        assert self.database == "new"

    def activate_read_only(self):
        assert not self.writes_enabled
        self.application = "new"
        self.event("activate")

    def verify_active(self):
        self.event("verify_active")
        assert self.application == self.database == "new"

    def restore_backup(self, digest):
        self.event("restore_backup")
        assert hashlib.sha256(self.dump.encode()).hexdigest() == digest
        self.database = self.dump

    def verify_previous(self):
        self.event("verify_previous")
        assert self.database == "old"

    def resume_previous_read_only(self):
        self.event("resume_previous")
        self.application = "old"
        self.writes_enabled = False

    def release_writes(self):
        self.writes_enabled = True
        self.event("release_writes")


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="belib-transaction-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)

    def journal(self, identity=IDENTITY):
        return tx.Journal(self.root, identity)

    def test_positive_and_exact_retry_do_not_repeat_effects(self):
        operations = MemoryOperations()
        result = tx.run_admitted_transaction(self.journal(), operations)
        self.assertEqual(result["phase"], "completed")
        before = list(operations.events)
        self.assertEqual(tx.run_admitted_transaction(self.journal(), operations), result)
        self.assertEqual(operations.events, before + ["verify_active"])
        self.assertEqual(operations.events[:5], ["quiesce", "backup", "verify_restore", "migrate", "verify_upgraded"])

    def test_failed_restore_never_migrates_or_activates(self):
        operations = MemoryOperations(fail="verify_restore")
        with self.assertRaises(tx.UpgradeError):
            tx.run_admitted_transaction(self.journal(), operations)
        self.assertNotIn("migrate", operations.events)
        self.assertEqual(operations.application, "old")
        self.assertEqual(self.journal().value["phase"], "aborted")

    def test_failures_after_mutation_restore_prior_database_and_application(self):
        for failure in ("migrate", "verify_upgraded", "activate", "verify_active"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(dir=self.root) as directory:
                journal = tx.Journal(Path(directory), {**IDENTITY, "operation_id": uuid.uuid4().hex})
                operations = MemoryOperations(fail=failure)
                with self.assertRaises(tx.UpgradeError):
                    tx.run_admitted_transaction(journal, operations)
                self.assertEqual((operations.database, operations.application), ("old", "old"))
                self.assertIn("restore_backup", operations.events)
                self.assertEqual(journal.value["phase"], "aborted")

    def test_interrupted_migration_or_activation_recovers_on_restart(self):
        for interruption in ("migrate", "activate", "verify_active"):
            with self.subTest(interruption=interruption), tempfile.TemporaryDirectory(dir=self.root) as directory:
                root = Path(directory)
                operations = MemoryOperations(interrupt=interruption)
                with self.assertRaises(Interrupted):
                    tx.run_admitted_transaction(tx.Journal(root, IDENTITY), operations)
                operations.interrupt = None
                result = tx.run_admitted_transaction(tx.Journal(root, IDENTITY), operations)
                self.assertEqual(result["phase"], "aborted")
                self.assertEqual((operations.database, operations.application), ("old", "old"))
                self.assertEqual(operations.events.count("migrate"), 1)

    def test_rollback_failure_stays_blocked_and_does_not_resume_app(self):
        operations = MemoryOperations(interrupt="migrate")
        with self.assertRaises(Interrupted):
            tx.run_admitted_transaction(self.journal(), operations)
        operations.interrupt = None
        operations.fail = "restore_backup"
        with self.assertRaisesRegex(tx.UpgradeError, "Recovery failed"):
            tx.run_admitted_transaction(self.journal(), operations)
        self.assertEqual(self.journal().value["phase"], "recovery_failed")
        self.assertNotIn("resume_previous", operations.events)
        self.assertEqual(operations.application, "stopped")

    def test_identity_tamper_and_symlink_rejected_before_any_effect(self):
        journal = self.journal()
        with self.assertRaises(tx.UpgradeError):
            self.journal({**IDENTITY, "source_sha": "c" * 40})
        journal.path.unlink()
        journal.path.symlink_to(self.root / "outside")
        with self.assertRaises(OSError):
            self.journal()

    def test_failed_write_publication_keeps_committed_database(self):
        operations = MemoryOperations(fail="release_writes")
        with self.assertRaises(RuntimeError):
            tx.run_admitted_transaction(self.journal(), operations)
        self.assertEqual(self.journal().value["phase"], "commit_decided")
        self.assertNotIn("restore_backup", operations.events)
        self.assertTrue(operations.writes_enabled)
        operations.fail = None
        self.assertEqual(tx.run_admitted_transaction(self.journal(), operations)["phase"], "completed")
        self.assertEqual(operations.events.count("migrate"), 1)

    def test_resume_failure_requiesces_before_reporting_recovery_failed(self):
        operations = MemoryOperations(interrupt="migrate")
        with self.assertRaises(Interrupted):
            tx.run_admitted_transaction(self.journal(), operations)
        operations.interrupt = None
        operations.fail = "resume_previous"
        with self.assertRaises(tx.UpgradeError):
            tx.run_admitted_transaction(self.journal(), operations)
        self.assertFalse(operations.writes_enabled)
        self.assertEqual(operations.application, "stopped")
        self.assertEqual(self.journal().value["phase"], "recovery_failed")

    def test_journal_phase_invariants_and_duplicate_keys_rejected(self):
        journal = self.journal()
        for phase in ("completed", "commit_decided", "migration_started", "rolled_back"):
            with self.subTest(phase=phase):
                journal.path.write_text(json.dumps({**journal.value, "phase": phase, "backup_sha256": None}))
                with self.assertRaises(tx.UpgradeError):
                    self.journal()
        journal.path.write_text('{"phase":"prepared","phase":"completed"}')
        with self.assertRaises(tx.UpgradeError):
            self.journal()
        with self.assertRaises(tx.UpgradeError):
            journal.write("completed")


PG_IMAGE = "postgres:16.14-bookworm@sha256:714b08dd70c05160db032e4321033ac4e5b6328935b2d03ed419c3a898aa8219"
BASE_SQL = """
CREATE ROLE fixture_reader NOLOGIN;
CREATE TABLE books(id integer PRIMARY KEY, allowed boolean NOT NULL);
CREATE TABLE reading_infos(book_id integer REFERENCES books(id), current_position text);
INSERT INTO books VALUES(1,true),(2,false);
INSERT INTO reading_infos VALUES(1,'epub:chapter-3');
ALTER TABLE books ENABLE ROW LEVEL SECURITY;
ALTER TABLE books FORCE ROW LEVEL SECURITY;
CREATE POLICY fixture_visibility ON books TO fixture_reader USING(allowed);
GRANT SELECT ON books TO fixture_reader;
"""
MIGRATION_SQL = """
ALTER TABLE reading_infos ADD COLUMN progress_percentage integer;
ALTER TABLE reading_infos ADD COLUMN save_sequence bigint NOT NULL DEFAULT 0;
UPDATE reading_infos SET progress_percentage=42 WHERE book_id=1;
"""


class PostgresOperations(MemoryOperations):
    """Test-only concrete pg_dump/pg_restore adapter; not a staging admission path."""
    def __init__(self, test, fail=None, interrupt=None):
        super().__init__(fail, interrupt)
        self.test = test
        self.dump = None

    def backup(self):
        self.event("backup")
        if self.test.backup_file.exists():
            self.dump = self.test.backup_file.read_bytes()
        else:
            self.dump = self.test.docker("exec", self.test.source, "pg_dump", "-U", "postgres", "-Fc", "postgres")
        with self.test.backup_file.open("wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(self.dump)
            stream.flush()
            os.fsync(stream.fileno())
        return hashlib.sha256(self.dump).hexdigest()

    def verify_restore(self, digest):
        self.event("verify_restore")
        assert hashlib.sha256(self.dump).hexdigest() == digest
        self.test.restore(self.test.restore_target, self.dump)
        self.test.assert_previous(self.test.restore_target)

    def migrate(self):
        self.test.sql(self.test.source, MIGRATION_SQL)
        self.event("migrate")

    def verify_upgraded(self):
        self.event("verify_upgraded")
        self.test.assertEqual(self.test.sql(self.test.source,
            "SELECT current_position||':'||progress_percentage||':'||save_sequence FROM reading_infos"), "epub:chapter-3:42:0")
        self.test.assert_authorization(self.test.source)
        self.test.assertEqual(self.test.sql(self.test.source, "SELECT count(*) FROM books"), "2")

    def verify_active(self):
        self.event("verify_active")
        assert self.application == "new"
        self.test.assertEqual(self.test.sql(self.test.source, "SELECT progress_percentage FROM reading_infos"), "42")

    def restore_backup(self, digest):
        self.event("restore_backup")
        dump = self.test.backup_file.read_bytes()
        assert hashlib.sha256(dump).hexdigest() == digest
        self.test.restore(self.test.source, dump)

    def verify_previous(self):
        self.event("verify_previous")
        self.test.assert_previous(self.test.source)


@unittest.skipUnless(os.environ.get("BELIB_DISPOSABLE_POSTGRES_TEST") == "1", "explicit disposable rootless PostgreSQL opt-in required")
class DisposablePostgresTests(unittest.TestCase):
    def docker(self, *args, data=None):
        return subprocess.run(["/usr/bin/docker", "--host", f"unix:///run/user/{os.getuid()}/docker.sock", *args],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=90).stdout

    def sql(self, container, sql):
        return self.docker("exec", "-i", container, "psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres", data=sql.encode()).decode().strip()

    def restore(self, container, dump):
        self.docker("exec", "-i", container, "pg_restore", "--clean", "--if-exists", "--exit-on-error", "--no-owner", "-U", "postgres", "-d", "postgres", data=dump)

    def setUp(self):
        info = json.loads(self.docker("info", "--format", "{{json .SecurityOptions}}"))
        self.assertTrue(any("rootless" in option for option in info))
        self.temp = tempfile.TemporaryDirectory(prefix="belib-disposable-transaction-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backup_file = self.root / "synthetic.dump"
        names = []
        self.addCleanup(lambda: [self.docker("rm", "-f", name) for name in names])
        for suffix in ("source", "restore"):
            name = "belib-upgrade-test-" + uuid.uuid4().hex + "-" + suffix
            self.docker("run", "--detach", "--pull=never", "--name", name,
                "--network", "none", "--label", "belib.disposable-test=retained-transaction",
                "--tmpfs", "/var/lib/postgresql/data:rw,size=256m", "--env", "POSTGRES_HOST_AUTH_METHOD=trust", PG_IMAGE)
            names.append(name)
            for attempt in range(40):
                try:
                    self.docker("exec", name, "pg_isready", "-U", "postgres")
                    break
                except subprocess.CalledProcessError:
                    time.sleep(0.25)
            else:
                self.fail("Disposable PostgreSQL did not start")
        self.source, self.restore_target = names
        self.sql(self.source, BASE_SQL)
        self.sql(self.restore_target, "CREATE ROLE fixture_reader NOLOGIN")

    def assert_authorization(self, container):
        self.assertEqual(self.sql(container, "SET ROLE fixture_reader; SELECT count(*) FROM books"), "1")

    def assert_previous(self, container):
        self.assertEqual(self.sql(container, "SELECT count(*) FROM books"), "2")
        self.assertEqual(self.sql(container, "SELECT current_position FROM reading_infos"), "epub:chapter-3")
        self.assertEqual(self.sql(container, "SELECT count(*) FROM information_schema.columns WHERE table_name='reading_infos' AND column_name='progress_percentage'"), "0")
        self.assert_authorization(container)

    def test_actual_dump_restore_migration_and_progress(self):
        ops = PostgresOperations(self)
        result = tx.run_admitted_transaction(tx.Journal(self.root, IDENTITY), ops)
        self.assertEqual(result["phase"], "completed")
        self.assertEqual(tx.run_admitted_transaction(tx.Journal(self.root, IDENTITY), ops), result)

    def test_actual_migration_failure_restores_without_reseed(self):
        ops = PostgresOperations(self, fail="verify_upgraded")
        with self.assertRaises(tx.UpgradeError):
            tx.run_admitted_transaction(tx.Journal(self.root, IDENTITY), ops)
        self.assert_previous(self.source)
        self.assertEqual(ops.application, "old")

    def test_actual_committed_migration_interruption_recovers(self):
        ops = PostgresOperations(self, interrupt="migrate")
        with self.assertRaises(Interrupted):
            tx.run_admitted_transaction(tx.Journal(self.root, IDENTITY), ops)
        # New adapter instance knows nothing about the in-memory dump or old process.
        fresh_ops = PostgresOperations(self)
        result = tx.run_admitted_transaction(tx.Journal(self.root, IDENTITY), fresh_ops)
        self.assertEqual(result["phase"], "aborted")
        self.assert_previous(self.source)

    def test_actual_acknowledged_write_survives_release_failure(self):
        test = self
        class ReleaseFailure(PostgresOperations):
            def release_writes(self):
                super().release_writes()
                test.sql(test.source, "INSERT INTO books VALUES(3,true)")
                raise RuntimeError("release acknowledgment lost")
        operations = ReleaseFailure(self)
        with self.assertRaisesRegex(RuntimeError, "acknowledgment lost"):
            tx.run_admitted_transaction(tx.Journal(self.root, IDENTITY), operations)
        self.assertEqual(self.sql(self.source, "SELECT count(*) FROM books"), "3")
        self.assertNotIn("restore_backup", operations.events)
        fresh_ops = PostgresOperations(self)
        fresh_ops.application = "new"
        result = tx.run_admitted_transaction(tx.Journal(self.root, IDENTITY), fresh_ops)
        self.assertEqual(result["phase"], "completed")
        self.assertEqual(self.sql(self.source, "SELECT count(*) FROM books"), "3")

    def test_actual_fixture_evidence_flows_into_transaction(self):
        fixture_path = Path(__file__).with_name("test-retained-upgrade.py")
        spec = importlib.util.spec_from_file_location("retained_evidence_test", fixture_path)
        fixture_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture_module)
        fixture = fixture_module.RetainedUpgradeTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        # Test-generated independent keys/identity pins remain fixture authority.
        # Backup bytes and restored query results below are from actual PostgreSQL.
        self.backup_file = fixture.backup_path
        self.backup_file.unlink()
        operations = PostgresOperations(self)
        operations.quiesce()
        fixture.backup_sha = operations.backup()
        operations.verify_restore(fixture.backup_sha)
        fixture.query_report = self.sql(self.restore_target,
            "SELECT count(*) FROM books; SELECT current_position FROM reading_infos").encode()
        fixture.payload["backup_sha256"] = fixture.backup_sha
        fixture.payload["integrity_query_sha256"] = hashlib.sha256(fixture.query_report).hexdigest()
        fixture.envelope = fixture.sign(fixture.payload)
        result = fixture.rehearsal(operations)
        self.assertEqual(result["transaction_rehearsal"]["phase"], "completed")
        self.assertFalse(result["deployable"])
        self.assertEqual(self.sql(self.source, "SELECT progress_percentage FROM reading_infos"), "42")


if __name__ == "__main__":
    unittest.main()
