#!/usr/bin/env python3
"""Actual disposable PostgreSQL checks for the fixed retained database port."""
import importlib.util
import hashlib
import json
import os
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

pg = module('retained-postgres')
tx = module('retained-db-transaction')
prepared_copy = module('prepared-retained-copy')
SCHEMA = '''
CREATE ROLE belib_staging_app LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE belib_staging_migrate LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
ALTER SCHEMA public OWNER TO belib_staging_migrate;
SET ROLE belib_staging_migrate;
CREATE TABLE books(id integer PRIMARY KEY,title text);
CREATE TABLE reading_infos(id integer PRIMARY KEY,current_position varchar(255),user_id integer,book_id integer);
CREATE TABLE role_book_permissions(id integer PRIMARY KEY,role_id integer,book_id integer);
INSERT INTO books VALUES(1,'fixture epub'),(2,'fixture pdf');
INSERT INTO reading_infos VALUES(1,'epub:chapter3',10,1);
INSERT INTO role_book_permissions VALUES(1,2,1);
RESET ROLE;
GRANT USAGE ON SCHEMA public TO belib_staging_app;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO belib_staging_app;
'''
MIGRATION = '''SET ROLE belib_staging_migrate;
ALTER TABLE reading_infos ALTER COLUMN current_position TYPE text;
ALTER TABLE reading_infos ADD COLUMN progress_percentage integer;
ALTER TABLE reading_infos ADD COLUMN save_sequence bigint NOT NULL DEFAULT 0;
'''


class PreparedArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'backup.dump'
        self.path.write_bytes(b'synthetic prepared archive')
        self.path.chmod(0o600)
        self.record=prepared_copy.PreparedBackup.capture(self.path,
            sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),
            manifest_sha256='b'*64,restore_attestation_sha256='c'*64)

    def test_unchanged_artifact_is_verified(self):
        self.assertEqual(self.record.verify(),self.record.value['sha256'])

    def test_identical_bytes_in_replacement_inode_are_rejected(self):
        replacement=self.path.with_name('replacement')
        replacement.write_bytes(self.path.read_bytes())
        replacement.chmod(0o600)
        replacement.replace(self.path)
        with self.assertRaises(prepared_copy.PreparedCopyBlocked):
            self.record.verify()

    def test_tamper_and_symlink_are_rejected(self):
        self.path.write_bytes(b'changed archive')
        with self.assertRaises(prepared_copy.PreparedCopyBlocked):
            self.record.verify()
        self.path.unlink()
        self.path.symlink_to('/dev/null')
        with self.assertRaises(prepared_copy.PreparedCopyBlocked):
            self.record.verify()

    def test_duplicate_state_fields_are_rejected(self):
        with self.assertRaises(prepared_copy.PreparedCopyBlocked):
            prepared_copy._unique_pairs([('phase','verified'),('phase','migration_started')])


@unittest.skipUnless(os.environ.get('BELIB_DISPOSABLE_POSTGRES_TEST') == '1', 'explicit disposable PostgreSQL opt-in required')
class RetainedPostgresTests(unittest.TestCase):
    def docker(self, *args):
        return subprocess.check_output(['/usr/bin/docker','--host',f'unix:///run/user/{os.getuid()}/docker.sock',*args],stderr=subprocess.DEVNULL)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='belib-real-pg-port-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.identity = {'operation_id': uuid.uuid4().hex, 'source_sha':'a'*40,'retained_state_sha256':'b'*64}
        self.source = self.docker('run','--detach','--pull=never','--name','belib-pg-port-test-'+uuid.uuid4().hex,
            '--network','none','--label','belib.disposable-test=retained-postgres',
            '--tmpfs','/var/lib/postgresql/data:rw,size=256m','--env','POSTGRES_HOST_AUTH_METHOD=trust',
            '--env','POSTGRES_USER='+pg.BOOTSTRAP,'--env','POSTGRES_DB='+pg.DATABASE,pg.IMAGE).decode().strip()
        self.addCleanup(lambda: self.docker('rm','-f',self.source))
        self.port = pg.RetainedPostgres(self.source,self.root,self.identity['operation_id'])
        self.addCleanup(self.port.cleanup_restore)
        for _ in range(50):
            try:
                self.port.docker('exec',self.source,'pg_isready','-h','127.0.0.1','-U',pg.BOOTSTRAP)
                break
            except pg.PostgresBlocked:
                time.sleep(.25)
        self.port.sql(SCHEMA)

    def prepare(self):
        self.port.quiesce_database()
        digest = self.port.backup()
        self.port.verify_restore(digest)
        return digest

    def test_exact_backup_restore_schema_counts_authorization_and_progress(self):
        digest = self.prepare()
        self.assertEqual(self.port.backup(),digest)
        self.port.sql(MIGRATION)
        self.port.verify_preserved(digest,upgraded=True)
        self.port.read_only_application()
        with self.assertRaises(pg.PostgresBlocked):
            self.port.sql("SET ROLE belib_staging_app; INSERT INTO books VALUES(3,'blocked');")
        self.port.release_writes()
        self.port.sql("SET ROLE belib_staging_app; INSERT INTO books VALUES(3,'acknowledged');")
        self.assertEqual(self.port.sql('SELECT count(*) FROM books'),'3')

    def test_tampered_dump_and_changed_database_fail_before_migration(self):
        digest = self.prepare()
        self.port.sql("INSERT INTO books VALUES(3,'changed')")
        with self.assertRaisesRegex(pg.PostgresBlocked,'does not match'):
            self.port.verify_restore(digest)
        with self.port.dump_path.open('ab') as stream:
            stream.write(b'tampered')
        with self.assertRaisesRegex(pg.PostgresBlocked,'digest'):
            self.port.restore_backup(digest)

    def test_new_port_instance_restores_exact_backup_after_migration(self):
        digest = self.prepare()
        self.port.sql(MIGRATION)
        self.port.cleanup_restore()
        fresh = pg.RetainedPostgres(self.source,self.root,self.identity['operation_id'])
        self.addCleanup(fresh.cleanup_restore)
        fresh.restore_backup(digest)
        self.assertEqual(fresh.sql("SELECT current_position FROM reading_infos"),'epub:chapter3')
        self.assertEqual(fresh.sql("SELECT count(*) FROM information_schema.columns WHERE table_name='reading_infos' AND column_name='save_sequence'"),'0')

    def operations(self, fail=False):
        port = self.port
        class Ops:
            digest = None
            def quiesce(self): port.quiesce_database()
            def backup(self):
                self.digest = port.backup()
                return self.digest
            def verify_restore(self,digest): port.verify_restore(digest)
            def migrate(self): port.sql(MIGRATION)
            def verify_upgraded(self):
                port.verify_preserved(self.digest,upgraded=True)
                if fail: raise RuntimeError('injected migration acceptance failure')
            def activate_read_only(self): port.read_only_application()
            def verify_active(self):
                port.verify_preserved(self.digest,upgraded=True)
            def release_writes(self): port.release_writes()
            def restore_backup(self,digest): port.restore_backup(digest)
            def verify_previous(self): port.verify_preserved(self.digest,upgraded=False)
            def resume_previous_read_only(self): port.read_only_application()
        return Ops()

    def test_engine_positive_uses_real_port_write_barrier_and_archive(self):
        receipt = tx.run_admitted_transaction(tx.Journal(self.root,self.identity),self.operations())
        self.assertEqual(receipt['phase'],'completed')
        self.port.sql("SET ROLE belib_staging_app; UPDATE reading_infos SET current_position='after-commit' WHERE id=1")
        self.assertEqual(self.port.sql('SELECT current_position FROM reading_infos'),'after-commit')

    def test_engine_failure_restores_previous_data_and_releases_old_writes(self):
        with self.assertRaises(tx.UpgradeError):
            tx.run_admitted_transaction(tx.Journal(self.root,self.identity),self.operations(fail=True))
        self.assertEqual(tx.Journal(self.root,self.identity).value['phase'],'aborted')
        self.assertEqual(self.port.sql('SELECT current_position FROM reading_infos'),'epub:chapter3')
        self.port.sql("SET ROLE belib_staging_app; UPDATE reading_infos SET current_position='after-rollback' WHERE id=1")

    def test_prepared_isolated_copy_keeps_original_database_and_roles_unchanged(self):
        digest = self.port.backup()  # read-only preparation, no source NOLOGIN
        prepared = prepared_copy.PreparedBackup.capture(self.port.dump_path,sha256=digest,
            manifest_sha256='b'*64,restore_attestation_sha256='c'*64)
        before = self.port.fingerprint()
        role_before = self.port.sql("SELECT rolcanlogin FROM pg_roles WHERE rolname='belib_staging_app'")
        migrated=[]
        def migrate(port,target):
            migrated.append(target)
            port.sql(MIGRATION,container=target)
        def verify(port,target):
            self.assertEqual(port.sql("SELECT current_position||':'||save_sequence FROM reading_infos",container=target),'epub:chapter3:0')
        kwargs=dict(prepared=prepared,source=self.port,migrate_copy=migrate,verify_copy=verify,state_path=self.root/'copy.json')
        result=prepared_copy.execute_prepared_copy(**kwargs)
        self.assertEqual(result['phase'],'verified')
        self.assertFalse(result['application_cutover'])
        self.assertEqual(prepared_copy.execute_prepared_copy(**kwargs),result)
        self.assertEqual(len(migrated),1)
        self.assertEqual(self.port.fingerprint(),before)
        self.assertEqual(self.port.sql("SELECT rolcanlogin FROM pg_roles WHERE rolname='belib_staging_app'"),role_before)

    def test_interrupted_unpublished_copy_resumes_from_same_archive_with_fresh_port(self):
        digest=self.port.backup()
        prepared=prepared_copy.PreparedBackup.capture(self.port.dump_path,sha256=digest,
            manifest_sha256='b'*64,restore_attestation_sha256='c'*64)
        before=self.port.fingerprint()
        class Interrupted(BaseException): pass
        def interrupt(port,target):
            port.sql(MIGRATION,container=target)
            raise Interrupted()
        with self.assertRaises(Interrupted):
            prepared_copy.execute_prepared_copy(prepared=prepared,source=self.port,migrate_copy=interrupt,
                verify_copy=lambda *_:None,state_path=self.root/'copy.json')
        fresh=pg.RetainedPostgres(self.source,self.root,self.identity['operation_id'])
        self.port.restore_id=None  # ownership is transferred to the restarted adapter
        self.addCleanup(fresh.cleanup_restore)
        original_cleanup=fresh.cleanup_restore
        def disappear_then_interrupt(**kwargs):
            original_cleanup(**kwargs)
            raise Interrupted()
        with patch.object(fresh,'cleanup_restore',side_effect=disappear_then_interrupt), self.assertRaises(Interrupted):
            prepared_copy.execute_prepared_copy(prepared=prepared,source=fresh,
                migrate_copy=lambda *_:self.fail('migration before discard reconciliation'),
                verify_copy=lambda *_:None,state_path=self.root/'copy.json')
        fresh=pg.RetainedPostgres(self.source,self.root,self.identity['operation_id'])
        self.addCleanup(fresh.cleanup_restore)
        result=prepared_copy.execute_prepared_copy(prepared=prepared,source=fresh,
            migrate_copy=lambda port,target:port.sql(MIGRATION,container=target),
            verify_copy=lambda port,target:self.assertEqual(port.sql('SELECT count(*) FROM books',container=target),'2'),
            state_path=self.root/'copy.json')
        self.assertEqual(result['phase'],'verified')
        self.assertEqual(result['attempt'],2)
        self.assertEqual(fresh.fingerprint(),before)

    def test_changed_original_is_recorded_truthfully_and_retry_is_terminal(self):
        digest=self.port.backup()
        prepared=prepared_copy.PreparedBackup.capture(self.port.dump_path,sha256=digest,
            manifest_sha256='b'*64,restore_attestation_sha256='c'*64)
        def unexpected_source_change(port,target):
            port.sql("INSERT INTO books VALUES(3,'concurrent source writer')")
            raise RuntimeError('injected unexpected original change')
        kwargs=dict(prepared=prepared,source=self.port,migrate_copy=unexpected_source_change,
                    verify_copy=lambda *_:None,state_path=self.root/'copy.json')
        with self.assertRaises(RuntimeError):
            prepared_copy.execute_prepared_copy(**kwargs)
        state=json.loads((self.root/'copy.json').read_text())
        self.assertEqual(state['phase'],'failed_original_changed')
        self.assertIs(state['original_database_mutated'],True)
        with self.assertRaises(prepared_copy.PreparedCopyBlocked):
            prepared_copy.execute_prepared_copy(**kwargs)


if __name__ == '__main__':
    unittest.main()
