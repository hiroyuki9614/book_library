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
seed=_load('seed')


class ExecutorBlocked(RuntimeError):
    pass


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
    if (not isinstance(config,dict) or set(config)!=fields or config.get('schema')!='belib-retained-operator/v1'
            or config.get('enabled') is not True or config.get('source_sha')!=grant['source_sha']
            or config.get('topology')!='isolated-copy'):
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
        trusted_root_contract_fingerprint=r['fingerprint'])
    attestation_sha=proof.validate_restore_attestation(attestation,backup_sha256=backup_sha,manifest_sha256=manifest_sha,
        query_report=report,trusted_issuer=v['issuer'],verifier_public_key_pem=v['public_key_pem'].encode(),
        trusted_verifier_fingerprint=v['fingerprint'])
    if (manifest_sha!=grant['retained_state_sha256'] or backup_sha!=grant['backup_sha256']
            or attestation_sha!=grant['restore_attestation_sha256']
            or grant['prior_application_sha']!=proof.OLD_APPLICATION_SHA):
        raise ExecutorBlocked('Prepared evidence differs from the one-use signed grant')
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


class IsolatedCopyOperations:
    def __init__(self,source,target,prepared,controller,config,values,journal):
        self.source,self.target,self.prepared,self.controller=source,target,prepared,controller
        self.config,self.values,self.journal=config,values,journal
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
            self.target.release_writes()
            self.controller.publish('new')

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
    source=pg.RetainedPostgres.for_retained(directory,controller.plan['operation_id'])
    source_pin=config['source_database']
    source_info=source.inspect(source.container)
    networks=source_info.get('NetworkSettings',{}).get('Networks',{})
    observed_networks={name:info.get('NetworkID') for name,info in networks.items()}
    if (not isinstance(source_pin,dict) or set(source_pin)!={'id','configuration_sha256','network_ids','volume_name'}
            or source_pin['id']!=source.container or source_pin['volume_name']!=pg.PROJECT+'_staging-postgres-data'
            or source_pin['configuration_sha256']!=cutover_module.configuration_digest(source_info)
            or source_pin['network_ids']!=observed_networks or set(observed_networks)!={pg.PROJECT+'_isolated'}
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
    env_fd,values=seed.validate_staging_admission(source_root,env_file,os.getuid())
    try:
        if values['BELIB_STAGE_VCS_REF']!=grant['source_sha'] or int(values['BELIB_STAGE_PORT'])!=controller.plan['port']:
            raise ExecutorBlocked('Staging environment source/endpoint differs from the adopted plan')
        journal=tx.Journal(directory,{'operation_id':controller.plan['operation_id'],'source_sha':grant['source_sha'],
                                     'retained_state_sha256':grant['retained_state_sha256']})
        operations=IsolatedCopyOperations(source,target,prepared,controller,config,values,journal)
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
