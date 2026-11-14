#!/usr/bin/env python3
"""Prepared-archive isolated-copy executor; contains no host admission or cutover.

The installed caller verifies its independent evidence/one-use grant and holds the
project lock. This unit preserves the original DB: only the newly restored copy
is migrated. A prepared archive is never regenerated while consuming a grant.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import stat
from pathlib import Path


class PreparedCopyBlocked(RuntimeError):
    pass


def _module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pg = _module('retained-postgres')


def _unique_pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:
            raise PreparedCopyBlocked('Prepared-copy state has duplicate fields')
        result[key]=value
    return result


class PreparedBackup:
    FIELDS = {'path','sha256','st_dev','st_ino','size','mtime_ns','ctime_ns',
              'manifest_sha256','restore_attestation_sha256'}

    def __init__(self, value):
        if not isinstance(value,dict) or set(value) != self.FIELDS:
            raise PreparedCopyBlocked('Prepared archive identity fields are invalid')
        if (not isinstance(value['path'],str) or not Path(value['path']).is_absolute()
                or any(not isinstance(value[name],str) or not re.fullmatch('[0-9a-f]{64}',value[name])
                       for name in ('sha256','manifest_sha256','restore_attestation_sha256'))
                or any(type(value[name]) is not int or value[name]<0 for name in
                       ('st_dev','st_ino','size','mtime_ns','ctime_ns'))):
            raise PreparedCopyBlocked('Prepared archive identity is malformed')
        self.value = dict(value)

    @classmethod
    def capture(cls,path,*,sha256,manifest_sha256,restore_attestation_sha256):
        info = Path(path).stat(follow_symlinks=False)
        result = cls({'path':str(path),'sha256':sha256,'st_dev':info.st_dev,'st_ino':info.st_ino,
                      'size':info.st_size,'mtime_ns':info.st_mtime_ns,'ctime_ns':info.st_ctime_ns,
                      'manifest_sha256':manifest_sha256,'restore_attestation_sha256':restore_attestation_sha256})
        result.verify()
        return result

    def verify(self):
        path = Path(self.value['path'])
        if path.resolve(strict=True) != path:
            raise PreparedCopyBlocked('Prepared archive path contains a symlink')
        fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_nlink != 1
                    or stat.S_IMODE(before.st_mode) != 0o600 or not 0<before.st_size<=pg.MAX_DUMP):
                raise PreparedCopyBlocked('Prepared archive is not a safe operator artifact')
            wanted = tuple(self.value[k] for k in ('st_dev','st_ino','size','mtime_ns','ctime_ns'))
            identity = lambda i:(i.st_dev,i.st_ino,i.st_size,i.st_mtime_ns,i.st_ctime_ns)
            if identity(before) != wanted:
                raise PreparedCopyBlocked('Prepared archive identity changed')
            digest = hashlib.file_digest(stream,'sha256').hexdigest()
            if digest != self.value['sha256'] or identity(os.fstat(stream.fileno())) != wanted:
                raise PreparedCopyBlocked('Prepared archive bytes changed')
        return self.value['sha256']


def execute_prepared_copy(*, prepared, source, migrate_copy, verify_copy, state_path):
    """Restore/compare/migrate a disposable candidate without mutating the source.

    The migration and acceptance ports belong to the immutable installed caller;
    no command from a grant or evidence document is executed. This candidate
    deliberately does not report app cutover, deployment or live acceptance.
    Retrying a completed exact identity only re-verifies the still-running copy.
    Interrupted unpublished copies are discarded only by exact identity and
    rebuilt from the same archive, at most three attempts. No original DB restore.
    """
    if not isinstance(prepared,PreparedBackup):
        raise PreparedCopyBlocked('Verified prepared archive record required')
    if source.dump_path != Path(prepared.value['path']):
        raise PreparedCopyBlocked('Prepared archive differs from the fixed source port')
    if state_path.parent != source.directory or state_path.suffix != '.json':
        raise PreparedCopyBlocked('Copy journal must remain in the private operation directory')
    digest = prepared.verify()
    original = source.fingerprint()
    identity = {'schema':'belib-prepared-retained-copy/v1','prepared_backup':prepared.value,
                'source_container_id':source.container,'operation_id':source.operation_id,
                'original_fingerprint_sha256':hashlib.sha256(json.dumps(original,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
    attempt = 1
    if state_path.exists():
        fd = os.open(state_path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1
                    or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>16384):
                raise PreparedCopyBlocked('Prepared-copy state is unsafe')
            raw=stream.read(16385)
            if len(raw)>16384:
                raise PreparedCopyBlocked('Prepared-copy state is oversized')
            state = json.loads(raw,object_pairs_hook=_unique_pairs)
        if not isinstance(state,dict):
            raise PreparedCopyBlocked('Prepared-copy state is not an object')
        if any(state.get(k)!=v for k,v in identity.items()):
            raise PreparedCopyBlocked('Prepared-copy state belongs to another artifact/source')
        if set(state) != set(identity) | {'phase','copy_container_id','deployment_authorized','application_cutover','original_database_mutated','attempt'}:
            raise PreparedCopyBlocked('Prepared-copy state fields are invalid')
        if (state.get('deployment_authorized') is not False or state.get('application_cutover') is not False
                or state.get('original_database_mutated') not in (False,None)
                or type(state.get('attempt')) is not int or not 1<=state['attempt']<=3):
            raise PreparedCopyBlocked('Prepared-copy state cannot authorize published recovery')
        copy_id=state.get('copy_container_id')
        if copy_id is not None and (not isinstance(copy_id,str) or not re.fullmatch('[0-9a-f]{64}',copy_id)):
            raise PreparedCopyBlocked('Prepared-copy identity is invalid')
        if state.get('phase')=='verified':
            if state.get('original_database_mutated') is not False:
                raise PreparedCopyBlocked('Verified copy lacks unchanged-source confirmation')
            source.restore_id=copy_id
            source.verify_disposable_identity()
            verify_copy(source,source.restore_id)
            return state
        if state.get('phase') not in {'restore_started','migration_started','failed_original_preserved','discard_started'} or state['attempt']>=3:
            raise PreparedCopyBlocked('Prepared-copy retry bound or phase is invalid')
        # Nothing was published. Reconcile only this disposable identity, retain
        # the source/backup and rebuild from the exact prepared archive.
        if copy_id:
            source.restore_id=copy_id
            # Durable intent precedes rm; an acknowledged deletion followed by a
            # crash is safely reconciled by exact-ID absence on the next resume.
            pg._atomic(state_path,json.dumps({**state,'phase':'discard_started'},sort_keys=True,separators=(',',':')).encode())
            source.cleanup_restore(missing_ok=True)
        attempt=state['attempt']+1
    def record(phase, original_mutated=None):
        state={**identity,'phase':phase,'attempt':attempt,'copy_container_id':source.restore_id,'deployment_authorized':False,
               'application_cutover':False,'original_database_mutated':original_mutated}
        pg._atomic(state_path,json.dumps(state,sort_keys=True,separators=(',',':')).encode())
        return state
    record('restore_started')
    try:
        # verify_restore restores only a separate --network none tmpfs DB, then
        # proves all original table data/schema still equals the prepared archive.
        source.verify_restore(digest)
        prepared.verify()
        record('migration_started')
        migrate_copy(source,source.restore_id)
        verify_copy(source,source.restore_id)
        prepared.verify()
        if source.fingerprint()!=original:
            raise PreparedCopyBlocked('Original database changed during candidate verification')
        return record('verified',False)
    except Exception:
        try:
            changed=source.fingerprint()!=original
        except Exception:
            record('failed_original_unknown',None)
        else:
            record('failed_original_changed' if changed else 'failed_original_preserved',changed)
        raise
