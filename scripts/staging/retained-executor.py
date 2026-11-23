#!/usr/bin/env python3
"""One admitted isolated-copy retained upgrade; no trust enrollment or resource creation.

Called only by the immutable launcher after root-sealed admission. Prepared
backup/evidence, independently adopted public pins, pre-created candidate
containers/networks/volume and exact image digests come from root-owned config.
The original database/container/volume is never modified by this executor.
"""
import hashlib
import importlib.util
import json
import os
import re
import secrets
import stat
import subprocess
from pathlib import Path


def _load(name):
    spec=importlib.util.spec_from_file_location('executor_'+name.replace('-','_'),Path(__file__).with_name(name+'.py'))
    value=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


pg=_load('retained-postgres')
copy=_load('prepared-retained-copy')
proof=_load('retained-upgrade')
tx=_load('retained-db-transaction')
cutover_module=_load('retained-cutover')
STAGING_ENV_FILE=Path('/home/hiroyuki/.config/belib-staging/i5.env')
seed=_load('seed')


class ExecutorBlocked(RuntimeError):
    pass


def _replace_env_source(raw, expected_source, candidate_source):
    if (not isinstance(raw, bytes) or not re.fullmatch('[0-9a-f]{40}', expected_source or '')
            or not re.fullmatch('[0-9a-f]{40}', candidate_source or '')):
        raise ExecutorBlocked('Staging env source transition is malformed')
    lines=raw.split(b'\n')
    matches=[index for index,line in enumerate(lines) if line.startswith(b'BELIB_STAGE_VCS_REF=')]
    if len(matches)!=1 or lines[matches[0]]!=b'BELIB_STAGE_VCS_REF='+expected_source.encode('ascii'):
        raise ExecutorBlocked('Live staging env source differs from its admitted transition')
    lines[matches[0]]=b'BELIB_STAGE_VCS_REF='+candidate_source.encode('ascii')
    return b'\n'.join(lines)


def read_evidence(path,maximum=2*1024*1024):
    if path.resolve(strict=True)!=path:
        raise ExecutorBlocked('Evidence path contains a symlink')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1
                or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>maximum):
            raise ExecutorBlocked('Evidence must be a bounded private regular file')
        raw=stream.read(maximum+1)
        if len(raw)>maximum:
            raise ExecutorBlocked('Evidence exceeds its size bound')
        return raw


def admitted_evidence(config,grant):
    fields={'schema','enabled','source_sha','topology','prepared_directory','authorities',
            'cutover_plan','candidate_database','source_database','migration_image'}
    generation=grant.get('schema')=='belib-staging-bootstrap-approval/v2'
    if generation:
        fields|={'prior_project','prior_generation_sha256','environment_transition'}
    if (not isinstance(config,dict) or set(config)!=fields
            or config.get('schema')!=('belib-retained-operator/v2' if generation else 'belib-retained-operator/v1')
            or config.get('enabled') is not True or config.get('source_sha')!=grant['source_sha']
            or config.get('topology')!='isolated-copy'
            or (generation and (config.get('prior_generation_sha256')!=grant.get('prior_generation_sha256')
                or config.get('prior_project')!=config['cutover_plan'].get('prior_project')))):
        raise ExecutorBlocked('Independent retained topology/configuration is not adopted for this source')
    authorities=config['authorities']
    if not isinstance(authorities,dict) or set(authorities)!={'restore','network','roots'}:
        raise ExecutorBlocked('Independent retained evidence authorities are not adopted')
    for pin in authorities.values():
        if (not isinstance(pin,dict) or set(pin)!={'issuer','public_key_pem','fingerprint'}
                or not isinstance(pin['public_key_pem'],str)):
            raise ExecutorBlocked('Independent evidence authority is malformed')
    directory=Path(config['prepared_directory'])
    pg._private_directory(directory)
    parsed=lambda name:proof.strict_json(read_evidence(directory/name))
    manifest=parsed('manifest.json')
    attestation=parsed('restore-attestation.json')
    network=parsed('network-inventory.json')
    roots=parsed('backup-roots.json')
    report=read_evidence(directory/'query-report.json',1024*1024)
    n,r,v=(authorities[name] for name in ('network','roots','restore'))
    manifest_sha=proof.validate_manifest(manifest,grant['source_sha'],trusted_network_inventory=network,
        trusted_network_inventory_issuer=n['issuer'],network_inventory_public_key_pem=n['public_key_pem'].encode(),
        trusted_network_inventory_fingerprint=n['fingerprint'])
    backup_sha=proof.validate_backup_artifact(directory/'backup.dump',uid=os.getuid(),trusted_root_contract=roots,
        trusted_root_contract_issuer=r['issuer'],root_contract_public_key_pem=r['public_key_pem'].encode(),
        trusted_root_contract_fingerprint=r['fingerprint'],
        expected_prior_project=manifest.get('prior_project') if generation else None,
        expected_prior_generation_sha256=manifest.get('prior_generation_sha256') if generation else None)
    attestation_sha=proof.validate_restore_attestation(attestation,backup_sha256=backup_sha,manifest_sha256=manifest_sha,
        query_report=report,trusted_issuer=v['issuer'],verifier_public_key_pem=v['public_key_pem'].encode(),
        trusted_verifier_fingerprint=v['fingerprint'])
    if (manifest_sha!=grant['retained_state_sha256'] or backup_sha!=grant['backup_sha256']
            or attestation_sha!=grant['restore_attestation_sha256']
            or (not generation and grant['prior_application_sha']!=proof.OLD_APPLICATION_SHA)
            or manifest.get('prior_application_sha')!=grant['prior_application_sha']
            or (generation and manifest.get('prior_generation_sha256')!=grant.get('prior_generation_sha256'))):
        raise ExecutorBlocked('Prepared evidence differs from the one-use signed grant')
    if generation:
        transition=config.get('environment_transition')
        if (not isinstance(transition,dict)
                or set(transition)!={'prior_sha256','candidate_sha256','prior_source_sha','candidate_source_sha'}
                or any(not re.fullmatch('[0-9a-f]{64}',transition.get(name,'')) for name in ('prior_sha256','candidate_sha256'))
                or transition.get('prior_source_sha')!=grant['prior_application_sha']
                or transition.get('candidate_source_sha')!=grant['source_sha']):
            raise ExecutorBlocked('Adopted environment transition is malformed or unbound')
    return directory,manifest,copy.PreparedBackup.capture(directory/'backup.dump',sha256=backup_sha,
        manifest_sha256=manifest_sha,restore_attestation_sha256=attestation_sha)


def candidate_database(config,source,controller):
    pin=config['candidate_database']
    if (not isinstance(pin,dict) or set(pin)!={'id','configuration_sha256','volume_name','network_id'}
            or any(not isinstance(pin[k],str) or not re.fullmatch('[0-9a-f]{64}',pin[k])
                   for k in ('id','configuration_sha256','network_id')) or pin['id']==source.container):
        raise ExecutorBlocked('Separate candidate database identity is invalid')
    target=pg.RetainedPostgres(pin['id'],source.directory,source.operation_id)
    info=target.inspect(target.container)
    project=pg.PROJECT+'-upgrade-'+source.operation_id
    labels=info.get('Config',{}).get('Labels') or {}
    mounts=[m for m in info.get('Mounts',[]) if m.get('Destination')=='/var/lib/postgresql/data']
    networks=info.get('NetworkSettings',{}).get('Networks',{})
    if (cutover_module.configuration_digest(info)!=pin['configuration_sha256']
            or info.get('Config',{}).get('Image')!=pg.IMAGE
            or labels.get('com.docker.compose.project')!=project or labels.get('com.docker.compose.service')!='db'
            or pin['volume_name']!=project+'_staging-postgres-data'
            or len(mounts)!=1 or mounts[0].get('Type')!='volume' or mounts[0].get('Name')!=pin['volume_name']
            or len(networks)!=1 or next(iter(networks.values())).get('NetworkID')!=pin['network_id']
            or 'db' not in (next(iter(networks.values())).get('Aliases') or [])):
        raise ExecutorBlocked('Candidate DB must use only the separate adopted staging volume/network')
    network=json.loads(target.docker('network','inspect',pin['network_id']))[0]
    if network.get('Internal') is not True or (network.get('Labels') or {}).get('com.docker.compose.project')!=project:
        raise ExecutorBlocked('Candidate DB network is not the isolated staging network')
    for service in ('backend','frontend'):
        if set(controller.plan['new'][service]['network_ids'].values())!={pin['network_id']}:
            raise ExecutorBlocked('Candidate application is not isolated with its candidate database')
    old_ids={x.get('NetworkID') for x in source.inspect(source.container).get('NetworkSettings',{}).get('Networks',{}).values()}
    if pin['network_id'] in old_ids:
        raise ExecutorBlocked('Candidate database may not share the old database network')
    return target


def _existing_transaction_phase(directory,identity):
    path=directory/(identity['operation_id']+'.json')
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:
        return 'prepared'
    except OSError as error:
        raise ExecutorBlocked('Existing transaction journal is unavailable') from error
    try:
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1
                or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>4096):
            raise ExecutorBlocked('Existing transaction journal metadata is unsafe')
        raw=os.read(fd,4097)
    finally:
        os.close(fd)
    try: value=json.loads(raw)
    except (ValueError,UnicodeError) as error: raise ExecutorBlocked('Existing transaction journal is malformed') from error
    generation='prior_generation_sha256' in identity
    schema='belib-retained-db-transaction/v2' if generation else 'belib-retained-db-transaction/v1'
    expected={"schema",*identity,"phase","backup_sha256"}
    if (not isinstance(value,dict) or set(value)!=expected or value.get('schema')!=schema
            or any(value.get(key)!=item for key,item in identity.items())
            or value.get('phase') not in tx.PHASES):
        raise ExecutorBlocked('Existing transaction journal belongs to a different grant or predecessor')
    try: tx._validate_phase_backup(value)
    except Exception as error: raise ExecutorBlocked('Existing transaction journal phase is invalid') from error
    return value['phase']


class IsolatedCopyOperations:
    def __init__(self,source,target,prepared,controller,config,values,journal,*,env_file=None,
                 prior_env_raw=None,candidate_env_raw=None):
        self.source,self.target,self.prepared,self.controller=source,target,prepared,controller
        self.config,self.values,self.journal=config,values,journal
        self.env_file=Path(env_file) if env_file is not None else None
        self.prior_env_raw=prior_env_raw
        self.candidate_env_raw=candidate_env_raw
        self.digest=prepared.value['sha256']
        self.original_path=source.directory/'original-fingerprint.json'
        self.original=json.loads(read_evidence(self.original_path)) if self.original_path.exists() else None
        self.previous=journal.value['phase'] in {'rollback_started','rolled_back','resume_previous_started','abort_decided','aborted'}

    def quiesce(self):
        if any(self.controller.observe('new').values()):
            self.controller.stop_new()
        self.controller.stop_old_writers()
        # Original DB is read only to this executor, including its roles/ACLs.
        if self.source.sql("SELECT count(*) FROM pg_stat_activity WHERE datname='belib_staging' AND pid<>pg_backend_pid() AND backend_type='client backend'")!='0':
            raise ExecutorBlocked('Original database still has clients after application quiesce')
        if self.original is None:
            self.original=self.source.fingerprint()
            pg._atomic(self.original_path,json.dumps(self.original,sort_keys=True,separators=(',',':')).encode())

    def backup(self):
        return self.prepared.verify()  # Never create a new dump under this grant.

    def verify_restore(self,digest):
        self.source.verify_restore(digest)
        self.prepared.verify()
        self.target._restore(self.target.container,digest)
        self.prepared.verify()
        self.target.baseline=self.source.baseline
        if self.target.fingerprint()!=self.source.baseline:
            raise ExecutorBlocked('Candidate copy differs from the approved archive')

    def migrate(self):
        image=self.config['migration_image']
        if not isinstance(image,str) or not re.fullmatch('sha256:[0-9a-f]{64}',image):
            raise ExecutorBlocked('Migration image is not an immutable adopted digest')
        info=json.loads(self.source.docker('image','inspect',image))[0]
        if info.get('Id')!=image:
            raise ExecutorBlocked('Migration image differs from the independently adopted source/image mapping')
        environment={**self.source.environment,'DATABASE_URL':'postgresql://belib_staging_migrate:'+self.values['BELIB_STAGE_DB_MIGRATE_PASSWORD']+'@db:5432/belib_staging'}
        command=[*self.source.prefix,'run','--rm','--pull=never','--network',self.config['candidate_database']['network_id'],
            '--user','node','--cap-drop','ALL','--security-opt','no-new-privileges','--read-only','--tmpfs','/tmp:rw,size=134217728',
            '--env','DATABASE_URL','--env','NPM_CONFIG_CACHE=/tmp/npm-cache',image,'npx','prisma','migrate','deploy']
        result=subprocess.run(command,env=environment,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,timeout=600,check=False)
        if result.returncode:
            raise ExecutorBlocked('Fixed candidate Prisma migration failed')

    def verify_upgraded(self):
        self.target.verify_preserved(self.digest,upgraded=True)
        self.verify_previous()

    def activate_read_only(self):
        self.previous=False
        self.target.read_only_application()
        self.controller.start_components('new')

    def verify_active(self):
        # Reconciliation after publication must not compare live row values to an
        # old dump: acknowledged user writes are valid and must never roll back.
        self.controller.verify_components('new')
        if self.target.sql("SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='reading_infos' AND ((column_name='save_sequence' AND data_type='bigint') OR (column_name='current_position' AND data_type='text') OR (column_name='progress_percentage' AND data_type='integer'))")!='3':
            raise ExecutorBlocked('Candidate reading-progress schema is unavailable')

    def release_writes(self):
        if self.previous:
            self.controller.publish('old')
        else:
            if self.candidate_env_raw is not None:
                self._commit_candidate_env()
            self.target.release_writes()
            self.controller.publish('new')

    def _commit_candidate_env(self):
        seed=_load('seed')
        transition=self.config.get('environment_transition')
        if (self.env_file != STAGING_ENV_FILE
                or not isinstance(transition,dict)
                or hashlib.sha256(self.prior_env_raw).hexdigest()!=transition.get('prior_sha256')
                or hashlib.sha256(self.candidate_env_raw).hexdigest()!=transition.get('candidate_sha256')):
            raise ExecutorBlocked('Sealed staging environment transition is inconsistent')
        fd=seed.open_staging_env_file(self.env_file,os.getuid())
        try:
            current_raw,current_values=seed.read_staging_env_file(fd)
            info=os.fstat(fd)
        finally:
            os.close(fd)
        current_sha=hashlib.sha256(current_raw).hexdigest()
        if current_sha==transition['candidate_sha256']:
            return
        if (current_sha!=transition['prior_sha256']
                or current_values.get('BELIB_STAGE_VCS_REF')!=transition['prior_source_sha']):
            raise ExecutorBlocked('Live staging env drifted before its commit CAS')
        directory=self.env_file.parent
        d_info=directory.lstat()
        if not stat.S_ISDIR(d_info.st_mode) or stat.S_ISLNK(d_info.st_mode) or d_info.st_uid!=os.getuid() or stat.S_IMODE(d_info.st_mode)!=0o700:
            raise ExecutorBlocked('Staging env parent directory is unsafe')
        legacy_pending=directory/('.i5.env.'+self.controller.plan['operation_id']+'.pending')
        try:
            p_info=legacy_pending.lstat()
            if (not stat.S_ISREG(p_info.st_mode) or p_info.st_uid!=os.getuid() or p_info.st_nlink!=1
                    or stat.S_IMODE(p_info.st_mode) not in {0o400,0o600}):
                raise ExecutorBlocked('Staging env pending file is unsafe')
            legacy_pending.unlink()
        except FileNotFoundError:
            pass
        pending=directory/('.i5.env.'+self.controller.plan['operation_id']+'.'+secrets.token_hex(12)+'.pending')
        fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            os.fchmod(fd,stat.S_IMODE(info.st_mode))
            with os.fdopen(fd,'wb') as stream:
                stream.write(self.candidate_env_raw);stream.flush();os.fsync(stream.fileno())
            current_info=self.env_file.lstat()
            check_fd=seed.open_staging_env_file(self.env_file,os.getuid())
            try:
                check_raw,_=seed.read_staging_env_file(check_fd)
                check_info=os.fstat(check_fd)
            finally:
                os.close(check_fd)
            if ((current_info.st_dev,current_info.st_ino,current_info.st_mtime_ns,current_info.st_ctime_ns)
                    !=(info.st_dev,info.st_ino,info.st_mtime_ns,info.st_ctime_ns)
                    or check_raw!=self.prior_env_raw
                    or (check_info.st_dev,check_info.st_ino)!=(info.st_dev,info.st_ino)):
                raise ExecutorBlocked('Live staging env changed during the commit CAS')
            os.replace(pending,self.env_file)
            dfd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try: os.fsync(dfd)
            finally: os.close(dfd)
        finally:
            pending.unlink(missing_ok=True)
        check_fd=seed.open_staging_env_file(self.env_file,os.getuid())
        try: committed_raw,_=seed.read_staging_env_file(check_fd)
        finally: os.close(check_fd)
        if hashlib.sha256(committed_raw).hexdigest()!=transition['candidate_sha256']:
            raise ExecutorBlocked('Candidate staging env commit failed its exact readback')

    def restore_backup(self,digest):
        # Option (a): rollback changes only the application binding. It NEVER
        # restores an archive over the retained original database/volume.
        self.prepared.verify()
        self.controller.stop_new()
        self.previous=True
        self.verify_previous()

    def verify_previous(self):
        if self.original is None or self.source.fingerprint()!=self.original:
            raise ExecutorBlocked('Original database changed; automatic cutover/recovery refused')

    def resume_previous_read_only(self):
        self.previous=True
        self.controller.start_components('old')  # ingress remains stopped


def execute_admitted(admission,source_root,env_file):
    grant=admission['grant']
    if grant.get('operation')!='upgrade-retained' or admission.get('source_root')!=str(source_root):
        raise ExecutorBlocked('Exact root-admitted retained operation is required')
    config=admission.get('retained_config')
    directory,manifest,prepared=admitted_evidence(config,grant)
    controller=cutover_module.RetainedCutover(config['cutover_plan'])
    if controller.plan['source_sha']!=grant['source_sha']:
        raise ExecutorBlocked('Adopted cutover source differs from the grant')
    source_pin=config['source_database']
    generation=grant.get('schema')=='belib-staging-bootstrap-approval/v2'
    if generation:
        source=pg.RetainedPostgres.for_generation(directory,controller.plan['operation_id'],
            {**source_pin,'project':controller.plan['prior_project'],'image_id':manifest['db_image_id']})
    else:
        source=pg.RetainedPostgres.for_retained(directory,controller.plan['operation_id'])
    source_info=source.inspect(source.container)
    networks=source_info.get('NetworkSettings',{}).get('Networks',{})
    observed_networks={name:info.get('NetworkID') for name,info in networks.items()}
    expected_source_fields={'id','configuration_sha256','network_ids','volume_name'}
    if generation:
        expected_source_fields.add('image_id')
    expected_project=controller.plan['prior_project'] if generation else pg.PROJECT
    if (not isinstance(source_pin,dict) or set(source_pin)!=expected_source_fields
            or source_pin['id']!=source.container or source_pin['volume_name']!=expected_project+'_staging-postgres-data'
            or source_pin['configuration_sha256']!=cutover_module.configuration_digest(source_info)
            or source_pin['network_ids']!=observed_networks or set(observed_networks)!={expected_project+'_isolated'}
            or (generation and source_pin.get('image_id')!=manifest.get('db_image_id'))
            or source_info.get('HostConfig',{}).get('PortBindings')):
        raise ExecutorBlocked('Original database configuration/network differs from the independently adopted plan')
    target=candidate_database(config,source,controller)
    controller.observe('old');controller.observe('new')
    old_networks=set(observed_networks.values())
    for pin in controller.plan['old'].values():
        old_networks.update(pin['network_ids'].values())
    if sorted(old_networks)!=manifest['network_ids']:
        raise ExecutorBlocked('Independent original network inventory differs from live resources')
    if manifest['db_container_id']!=source.container:
        raise ExecutorBlocked('Independent manifest differs from the original DB identity')
    descriptor_path=directory/'prepared-artifact.json'
    if descriptor_path.exists():
        previous=copy.PreparedBackup(json.loads(read_evidence(descriptor_path)))
        previous.verify()
        if previous.value!=prepared.value:
            raise ExecutorBlocked('Prepared archive identity differs from the resumed operation')
    else:
        pg._atomic(descriptor_path,json.dumps(prepared.value,sort_keys=True,separators=(',',':')).encode())
    prior_env_raw=None
    candidate_env_raw=None
    journal_identity={'operation_id':controller.plan['operation_id'],'source_sha':grant['source_sha'],
                      'retained_state_sha256':grant['retained_state_sha256']}
    if generation:
        transition=config['environment_transition']
        journal_identity.update(prior_generation_sha256=grant['prior_generation_sha256'],
            env_prior_sha256=transition['prior_sha256'],env_candidate_sha256=transition['candidate_sha256'])
    existing_phase=_existing_transaction_phase(directory,journal_identity)
    if grant.get('schema') == 'belib-staging-bootstrap-approval/v2':
        live_fd=seed.open_staging_env_file(env_file,os.getuid())
        try:
            env_raw,values=seed.read_staging_env_file(live_fd)
        finally:
            os.close(live_fd)
        seed.verify_reviewed_source(source_root,grant['source_sha'])
        if values.get('BELIB_STAGE_PORT')!=str(controller.plan['prior_http_port']):
            raise ExecutorBlocked('Live staging env differs from the exact active generation')
        current_env_sha=hashlib.sha256(env_raw).hexdigest()
        transition=config['environment_transition']
        if (values.get('BELIB_STAGE_VCS_REF')==controller.plan['prior_application_sha']
                and current_env_sha==transition['prior_sha256']):
            prior_env_raw=env_raw
            candidate_env_raw=_replace_env_source(env_raw,controller.plan['prior_application_sha'],grant['source_sha'])
        elif (values.get('BELIB_STAGE_VCS_REF')==grant['source_sha']
                and current_env_sha==transition['candidate_sha256']
                and existing_phase in {'commit_decided','completed'}):
            candidate_env_raw=env_raw
            prior_env_raw=_replace_env_source(env_raw,grant['source_sha'],controller.plan['prior_application_sha'])
        else:
            raise ExecutorBlocked('Live staging env differs from the admitted pre-commit or committed state')
        env_fd=seed.create_sealed_env_snapshot(candidate_env_raw)
        values=dict(values);values['BELIB_STAGE_VCS_REF']=grant['source_sha']
    else:
        env_fd,values=seed.validate_staging_admission(source_root,env_file,os.getuid())
    try:
        if values['BELIB_STAGE_VCS_REF']!=grant['source_sha'] or int(values['BELIB_STAGE_PORT'])!=controller.plan['port']:
            raise ExecutorBlocked('Staging environment source/endpoint differs from the adopted plan')
        if prior_env_raw is not None:
            transition=config['environment_transition']
            if (hashlib.sha256(prior_env_raw).hexdigest()!=transition['prior_sha256']
                    or hashlib.sha256(candidate_env_raw).hexdigest()!=transition['candidate_sha256']):
                raise ExecutorBlocked('Live and candidate env hashes differ from the adopted transition')
            journal_identity.update(prior_generation_sha256=grant['prior_generation_sha256'],
                env_prior_sha256=transition['prior_sha256'],env_candidate_sha256=transition['candidate_sha256'])
        journal=tx.Journal(directory,journal_identity)
        operations=IsolatedCopyOperations(source,target,prepared,controller,config,values,journal,
            env_file=env_file,prior_env_raw=prior_env_raw,candidate_env_raw=candidate_env_raw)
        result=tx.run_admitted_transaction(journal,operations)
        if result['phase']!='completed':
            raise ExecutorBlocked('Previous application restored; upgrade was not published')
        return {'schema':'belib-retained-upgrade-result/v1','source_sha':grant['source_sha'],'phase':result['phase'],
                'original_database_id':source.container,'candidate_database_id':target.container,
                'backup_sha256':prepared.value['sha256'],'operation_id':controller.plan['operation_id'],
                'application_cutover':True,'original_database_mutated':False}
    finally:
        os.close(env_fd)
        source.cleanup_restore()
