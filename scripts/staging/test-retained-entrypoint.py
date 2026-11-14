#!/usr/bin/env python3
"""Opt-in real rootless Docker/PG integration of bootstrap -> real launcher -> executor.

Only root ownership/execution and physical legacy identity are substituted for
synthetic resources. All signature validation, exact Git source, env admission,
prepared evidence, PG dump/restore/migration, role barrier, container CAS, HTTP
health, journals and receipt writes use the actual implementation.
"""
import base64
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
import unittest
import uuid
from contextlib import ExitStack,redirect_stdout,redirect_stderr
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
STAGE=ROOT/'scripts/staging'
OLD_IMAGE='sha256:df3725ef307ebd11eb353e17aecb340c8b909c1451f9032d28fb5cb285a1218c'
NGINX='nginx:1.27-alpine@sha256:62223d644fa234c3a1cc785ee14242ec47a77364226f1c811d2f669f96dc2ac8'
RUNTIME=('operator-bootstrap.py','launch-guard.py','retained-executor.py','retained-cutover.py','retained-upgrade.py',
         'retained-postgres.py','prepared-retained-copy.py','retained-db-transaction.py','seed.py','source-tree-guard.py','preflight.sh')


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


@unittest.skipUnless(os.environ.get('BELIB_DISPOSABLE_ENTRYPOINT_TEST')=='1','explicit isolated Docker entrypoint opt-in required')
class RetainedEntrypointTests(unittest.TestCase):
    def docker(self,*args,input=None):
        try:
            return subprocess.run(['/usr/bin/docker','--host',f'unix:///run/user/{os.getuid()}/docker.sock',*args],
                input=input,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=120).stdout
        except subprocess.CalledProcessError as error:
            raise RuntimeError('Disposable fixture command failed: '+error.stderr.decode()) from error

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='belib-entry-test-',dir=ROOT.parent);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.containers=[];self.networks=[];self.volumes=[];self.images=[]
        self.addCleanup(self.cleanup_resources)
        self.operation=uuid.uuid4().hex;self.project='belib-entry-test-'+self.operation[:10]
        self.newproject=self.project+'-upgrade-'+self.operation
        self.sources=self.root/'sources';self.sources.mkdir();source=self.sources/'initial';source.mkdir()
        archive=subprocess.check_output(['/usr/bin/git','-C',str(ROOT),'archive','HEAD'])
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:bundle.extractall(source,filter='data')
        for name in RUNTIME:shutil.copyfile(STAGE/name,source/'scripts/staging'/name)
        def git(*args):return subprocess.check_output(['/usr/bin/git','-C',str(source),*args],stderr=subprocess.DEVNULL).decode().strip()
        git('init','-q');git('add','--force','.');git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixed fixture source')
        self.sha=git('rev-parse','HEAD');self.source=self.sources/self.sha;source.rename(self.source)
        provenance=subprocess.run(['/usr/bin/python3','-I','-B',str(self.source/'scripts/staging/source-tree-guard.py'),'--repo-root',str(self.source),'--source-sha',self.sha],capture_output=True,text=True)
        self.assertEqual(provenance.returncode,0,provenance.stderr+provenance.stdout)
        self.bootstrap=load(self.source/'scripts/staging/operator-bootstrap.py','fixture_bootstrap')
        self.bootstrap.TARGET={**self.bootstrap.TARGET,'project':self.project}
        self.launch=load(self.source/'scripts/staging/launch-guard.py','fixture_launch')
        self.executor=load(self.source/'scripts/staging/retained-executor.py','fixture_executor')
        data=load(STAGE/'test-retained-postgres.py','fixture_data');self.pg=data.pg
        self.oldnet=self.make_network(self.project);self.newnet=self.make_network(self.newproject)
        self.oldingress=self.make_network(self.project,'ingress');self.newingress=self.make_network(self.newproject,'ingress')
        self.old_db=self.make_db(self.project,self.oldnet)
        self.new_db=self.make_db(self.newproject,self.newnet)
        self.books=self.project+'_staging-book-files';self.docker('volume','create','--label','belib.disposable-test=entrypoint',self.books);self.volumes.append(self.books)
        self.prepared=self.root/'prepared';self.prepared.mkdir(mode=0o700)
        self.source_port=self.pg.RetainedPostgres(self.old_db,self.prepared,self.operation)
        self.source_port.sql(data.SCHEMA)
        self.digest=self.source_port.backup();self.original=self.source_port.fingerprint()
        target=self.pg.RetainedPostgres(self.new_db,self.prepared,self.operation)
        target.sql("CREATE ROLE belib_staging_app LOGIN NOINHERIT; CREATE ROLE belib_staging_migrate LOGIN NOINHERIT;")
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));self.port=sock.getsockname()[1]
        build=self.root/'build';build.mkdir()
        old_tags=json.loads(self.docker('image','inspect',OLD_IMAGE))[0].get('RepoTags') or []
        if not old_tags:raise RuntimeError('Fixture base image has no local tag; do not pull or retag retained images')
        (build/'Dockerfile').write_text('FROM '+old_tags[0]+'\nLABEL org.opencontainers.image.revision='+self.sha+'\n')
        self.newimage=self.docker('build','--network','none','--quiet',str(build)).decode().strip();self.images.append(self.newimage)
        (build/'migration.sql').write_text(data.MIGRATION);(build/'migration.sql').chmod(0o644)
        (build/'npx').write_text('#!/bin/sh\n[ "$*" = "prisma migrate deploy" ] || exit 91\nexec psql -X -v ON_ERROR_STOP=1 "$DATABASE_URL" -f /migration.sql\n')
        (build/'npx').chmod(0o755)
        (build/'Dockerfile').write_text('FROM '+self.pg.IMAGE+'\nRUN groupadd -g 1000 node && useradd -u 1000 -g 1000 node\nCOPY npx /usr/local/bin/npx\nCOPY migration.sql /migration.sql\n')
        self.migrationimage=self.docker('build','--network','none','--quiet',str(build)).decode().strip();self.images.append(self.migrationimage)
        self.oldapps=self.make_apps('old',self.project,self.oldnet,OLD_IMAGE)
        self.newapps=self.make_apps('new',self.newproject,self.newnet,self.newimage)
        for identity in self.oldapps.values():self.docker('start',identity)
        self.wait_http()
        self.initial_info={}
        plan={'schema':'belib-isolated-copy-cutover/v1','source_sha':self.sha,'operation_id':self.operation,'port':self.port}
        for side,apps,network in [('old',self.oldapps,self.oldnet),('new',self.newapps,self.newnet)]:
            plan[side]={}
            network_info=json.loads(self.docker('network','inspect',network))[0]
            for service,identity in apps.items():
                info=json.loads(self.docker('inspect',identity))[0]
                self.initial_info[identity]=info
                plan[side][service]={'id':identity,'configuration_sha256':self.executor.cutover_module.configuration_digest(info),
                                     'network_ids':{name:json.loads(self.docker('network','inspect',name))[0]['Id'] for name in info['NetworkSettings']['Networks']}}
        self.fixture_module=load(STAGE/'test-retained-upgrade.py','fixture_evidence')
        self.fixture_module.upgrade.EXPECTED_PROJECT=self.project
        self.fixture_module.upgrade.OLD_DB_CONTAINER_ID=self.old_db
        self.bootstrap.RETAINED_DB_ID=self.old_db
        self.fixture_module.upgrade.RETAINED_VOLUMES=(self.project+'_staging-book-files',self.project+'_staging-postgres-data')
        self.fixture=self.fixture_module.RetainedUpgradeTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        f=self.fixture;f.source_sha=self.sha;f.manifest['source_sha']=self.sha;f.network_ids=sorted([self.oldnet,self.oldingress])
        f.manifest['network_ids']=f.network_ids
        inv=f.network_inventory['payload'];inv['network_ids']=f.network_ids;f.network_inventory=f.sign_with(f.network_private_key,inv)
        f.manifest_sha=f.manifest_digest(f.manifest);f.backup_path=self.prepared/'backup.dump';f.backup_sha=self.digest
        f.query_report=json.dumps(self.original,sort_keys=True).encode();f.payload.update(backup_sha256=self.digest,manifest_sha256=f.manifest_sha,integrity_query_sha256=hashlib.sha256(f.query_report).hexdigest());f.envelope=f.sign(f.payload)
        for name,value in [('manifest.json',f.manifest),('restore-attestation.json',f.envelope),('network-inventory.json',f.network_inventory),('backup-roots.json',f.root_contract)]:
            path=self.prepared/name;path.write_bytes(f.upgrade.canonical_json(value) if hasattr(f,'upgrade') else self.fixture_module.upgrade.canonical_json(value));path.chmod(0o600)
        (self.prepared/'query-report.json').write_bytes(f.query_report);(self.prepared/'query-report.json').chmod(0o600)
        info=json.loads(self.docker('inspect',self.new_db))[0]
        self.config={'schema':'belib-retained-operator/v1','enabled':True,'source_sha':self.sha,'topology':'isolated-copy',
            'prepared_directory':str(self.prepared),'cutover_plan':plan,'migration_image':self.migrationimage,
            'candidate_database':{'id':self.new_db,'configuration_sha256':self.executor.cutover_module.configuration_digest(info),
                'volume_name':self.newproject+'_staging-postgres-data','network_id':self.newnet},
            'authorities':{name:{'issuer':issuer,'public_key_pem':pem.decode(),'fingerprint':fingerprint}
                for name,issuer,pem,fingerprint in [('restore',f.issuer,f.public_pem,f.fingerprint),('network',f.network_issuer,f.network_public_pem,f.network_fingerprint),('roots',f.root_issuer,f.root_public_pem,f.root_fingerprint)]}}
        old_info=json.loads(self.docker('inspect',self.old_db))[0]
        self.config['source_database']={'id':self.old_db,'configuration_sha256':self.executor.cutover_module.configuration_digest(old_info),'volume_name':self.project+'_staging-postgres-data','network_ids':{name:item['NetworkID'] for name,item in old_info['NetworkSettings']['Networks'].items()}}
        self.executor.pg.PROJECT=self.project;self.executor.pg.RETAINED_ID=self.old_db
        self.executor.proof.EXPECTED_PROJECT=self.project
        self.executor.proof.OLD_DB_CONTAINER_ID=self.old_db;self.executor.proof.RETAINED_VOLUMES=self.fixture_module.upgrade.RETAINED_VOLUMES
        self.executor.cutover_module.PROJECT=self.project
        self.home=self.root/'home';(self.home/'.config/belib-staging').mkdir(parents=True)
        self.env=self.home/'.config/belib-staging/i5.env'
        self.env.write_text(''.join(k+'='+v+'\n' for k,v in {'BELIB_STAGE_DB_BOOTSTRAP_PASSWORD':'1'*64,'BELIB_STAGE_DB_APP_PASSWORD':'2'*64,'BELIB_STAGE_DB_MIGRATE_PASSWORD':'3'*64,'BELIB_STAGE_AUTH_SECRET':'4'*64,'BELIB_STAGE_VCS_REF':self.sha,'BELIB_STAGE_PORT':str(self.port)}.items()));self.env.chmod(0o600)
        self.state=self.root/'operator-state';self.state.mkdir(mode=0o700)
        manifest={p.relative_to(self.source).as_posix():{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'mode':stat.S_IMODE(p.stat().st_mode)} for p in self.source.rglob('*') if p.is_file() and '.git' not in p.relative_to(self.source).parts}
        self.main_config=self.root/'operator.json';self.main_config.write_bytes(self.bootstrap.canonical({'schema':'belib-staging-operator/v1','enabled':True,'source_sha':self.sha,'manifest':manifest}))
        self.retained_config=self.root/'retained.json';self.retained_config.write_bytes(self.bootstrap.canonical(self.config))
        self.key=self.root/'approver.pem';self.public=self.root/'approver.pub'
        subprocess.run(['/usr/bin/openssl','genpkey','-algorithm','ED25519','-out',str(self.key)],check=True)
        subprocess.run(['/usr/bin/openssl','pkey','-in',str(self.key),'-pubout','-out',str(self.public)],check=True)

    def cleanup_resources(self):
        for identity in reversed(self.containers):self.docker('rm','-f',identity)
        for network in reversed(self.networks):self.docker('network','rm',network)
        for volume in reversed(self.volumes):self.docker('volume','rm',volume)
        for image in reversed(self.images):self.docker('image','rm',image)

    def make_network(self,project,kind='isolated'):
        name=project+'_'+kind;args=['network','create','--label','com.docker.compose.project='+project]
        if kind=='isolated':args+=['--internal']
        value=self.docker(*args,name).decode().strip();self.networks.append(value);return value

    def make_db(self,project,network):
        volume=project+'_staging-postgres-data';self.docker('volume','create','--label','belib.disposable-test=entrypoint',volume);self.volumes.append(volume)
        identity=self.docker('run','-d','--pull=never','--network',network,'--network-alias','db','--label','com.docker.compose.project='+project,
            '--label','com.docker.compose.service=db','--mount','type=volume,src='+volume+',dst=/var/lib/postgresql/data',
            '--env','POSTGRES_HOST_AUTH_METHOD=trust','--env','POSTGRES_USER='+self.pg.BOOTSTRAP,'--env','POSTGRES_DB='+self.pg.DATABASE,self.pg.IMAGE).decode().strip();self.containers.append(identity)
        for _ in range(60):
            try:self.docker('exec',identity,'pg_isready','-h','127.0.0.1','-U',self.pg.BOOTSTRAP);return identity
            except RuntimeError:time.sleep(.25)
        self.fail('fixture DB startup timeout')

    def make_apps(self,side,project,network,image):
        apps={}
        for service in ('backend','frontend','nginx'):
            args=['create','--pull=never','--network',network,'--network-alias',service,'--label','com.docker.compose.project='+project,'--label','com.docker.compose.service='+service]
            if service=='nginx':
                path=self.root/(side+'-nginx.conf');path.write_text('events {}\nhttp { server { listen 8080; location /health { return 200 "fixture '+side+'"; } } }\n')
                args+=['--publish','127.0.0.1:'+str(self.port)+':8080','--mount','type=bind,src='+str(path)+',dst=/etc/nginx/nginx.conf,readonly',NGINX]
            else:
                if service=='backend':args+=['--mount','type=volume,src='+self.books+',dst=/home/node']
                args+=[image,'node','-e',"process.on('SIGTERM',()=>process.exit(0));require('http').createServer((q,r)=>{r.writeHead(200);r.end('fixture health')}).listen(3000,'0.0.0.0')"]
            identity=self.docker(*args).decode().strip();self.containers.append(identity);apps[service]=identity
            if service=='nginx':self.docker('network','connect',self.oldingress if side=='old' else self.newingress,identity)
        return apps

    def wait_http(self):
        for _ in range(40):
            result=subprocess.run(['/usr/bin/curl','-q','--noproxy','*','--fail','--silent','http://127.0.0.1:'+str(self.port)+'/health'],stdout=subprocess.DEVNULL)
            if result.returncode==0:return
            time.sleep(.25)
        self.fail('fixture ingress did not start')

    def invoke(self,*,forged=False,interrupt=False):
        b,l=self.bootstrap,self.launch
        issued=int(time.time())
        grant={**b.TARGET,'schema':'belib-staging-bootstrap-approval/v1','source_sha':self.sha,'operation':'upgrade-retained',
            'nonce':uuid.uuid4().hex+uuid.uuid4().hex,'evidence_sha256':'a'*64,'issued_at':issued,'expires_at':issued+120,
            'prior_application_sha':self.fixture_module.upgrade.OLD_APPLICATION_SHA,'retained_state_sha256':self.fixture.manifest_sha,
            'backup_sha256':self.digest,'restore_attestation_sha256':self.fixture.verify()}
        raw=b.canonical(grant);payload=self.root/'payload';payload.write_bytes(raw);signature=self.root/'signature'
        subprocess.run(['/usr/bin/openssl','pkeyutl','-sign','-inkey',str(self.key),'-rawin','-in',str(payload),'-out',str(signature)],check=True)
        envelope={'payload':base64.b64encode(raw).decode(),'signature':base64.b64encode(b'x'*64 if forged else signature.read_bytes()).decode()}
        grant_path=self.root/'grant.json';grant_path.write_bytes(b.canonical(envelope))
        real_lstat=Path.lstat;real_fstat=os.fstat
        def root_owned(path):
            original=real_lstat(path)
            if path==self.source or path in self.source.parents:
                return SimpleNamespace(st_mode=original.st_mode&~0o022,st_uid=0,st_nlink=original.st_nlink,st_size=original.st_size)
            return original
        def spawn(root,checked,config):
            arguments=b.launcher_arguments(checked)
            arguments[arguments.index('--env-file')+1]=str(self.env)  # physical host-only path mapping
            admission={'schema':'belib-staging-operator-admission/v1','grant':checked,'source_root':str(root),'pid':os.getpid(),
                'argv_sha256':hashlib.sha256(b.canonical(arguments)).hexdigest(),'retained_config':config}
            fd=os.memfd_create('fixture-root-admission',os.MFD_ALLOW_SEALING);os.write(fd,b.canonical(admission));fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
            def root_descriptor(number):
                info=real_fstat(number)
                if number==fd and info.st_nlink==0 and 'fixture-root-admission' in os.readlink('/proc/self/fd/'+str(number)):
                    return SimpleNamespace(st_mode=info.st_mode,st_uid=0,st_nlink=info.st_nlink,st_size=info.st_size)
                return info
            with patch.dict(os.environ,{'PATH':'/usr/bin:/bin','HOME':str(self.home),'BELIB_BOOTSTRAP_FD':str(fd)},clear=True),patch.object(os,'fstat',side_effect=root_descriptor),patch.object(sys,'argv',['launch-guard.py',*arguments]):
                try:return l.main(arguments)
                except KeyboardInterrupt:return 124
        output=io.StringIO();errors=io.StringIO();child={'active':False}
        actual_project_lock=l.project_lock
        actual_run=subprocess.run
        def diagnostic_run(command,**kwargs):
            migration='npx' in command and 'prisma' in command
            if migration:kwargs['stderr']=subprocess.PIPE
            result=actual_run(command,**kwargs)
            if migration and result.returncode:print('FIXTURE MIGRATION STDERR: '+result.stderr.decode(),file=sys.stderr)
            return result
        actual_execute=self.executor.execute_admitted
        def diagnostic_execute(*args):
            try:return actual_execute(*args)
            except Exception:
                traceback.print_exc()
                def differences(left,right,path=''):
                    if isinstance(left,dict) and isinstance(right,dict):
                        return sum((differences(left.get(k),right.get(k),path+'.'+k) for k in set(left)|set(right)),[])
                    return [] if left==right else [path]
                for identity,prior in self.initial_info.items():
                    after=json.loads(self.docker('inspect',identity))[0]
                    changes=differences({k:prior.get(k) for k in ('Config','HostConfig','Mounts')},{k:after.get(k) for k in ('Config','HostConfig','Mounts')})
                    if changes:
                        print('FIXTURE CONFIG DIFF: '+str(changes),file=sys.stderr)
                        print('FIXTURE OOM: '+repr([(key,prior['HostConfig'].get(key),after['HostConfig'].get(key)) for key in ('OomScoreAdj','OomKillDisable')]),file=sys.stderr)
                raise
        lock_path=Path('/tmp')/('belib-retained-entry-test-'+self.operation+'.lock')
        with ExitStack() as stack:
            for target,name,value in [(b,'CONFIG',self.main_config),(b,'RETAINED_CONFIG',self.retained_config),(b,'STATE',self.state),(b,'SOURCES',self.sources),(b,'INSTALL',Path(b.__file__).resolve()),(b,'PUBLIC_KEY',self.public.read_bytes()),(b,'PUBLIC_FINGERPRINT',hashlib.sha256(self.public.read_bytes()).hexdigest()),(b,'trusted_path',lambda *a,**k:None),(b,'spawn_admitted',spawn),(l,'OPERATOR_SOURCES',self.sources),(l,'EXPECTED_HOME',self.home),(l,'project_lock',lambda:actual_project_lock(lock_path)),(l,'load_retained_executor',lambda _:self.executor),(Path,'lstat',root_owned),(sys,'executable','/usr/bin/python3')]:stack.enter_context(patch.object(target,name,value))
            # Root identity is needed only while entering bootstrap; the real
            # launcher runs as the actual UID1000 via this root-boundary shim.
            real_uid=os.getuid;real_euid=os.geteuid
            stack.enter_context(patch.object(b.os,'getuid',side_effect=lambda:1000 if child['active'] else 0))
            stack.enter_context(patch.object(b.os,'geteuid',side_effect=lambda:1000 if child['active'] else 0))
            # Set root identity for bootstrap, then switch before descriptor check.
            def boundary_spawn(*args):
                child['active']=True
                return spawn(*args)
            stack.enter_context(patch.object(b,'spawn_admitted',boundary_spawn))
            stack.enter_context(patch.object(self.executor,'execute_admitted',diagnostic_execute))
            stack.enter_context(patch.object(subprocess,'run',diagnostic_run))
            if interrupt:
                original=self.executor.IsolatedCopyOperations.migrate
                def fail_after_migration(ops):original(ops);raise KeyboardInterrupt()
                stack.enter_context(patch.object(self.executor.IsolatedCopyOperations,'migrate',fail_after_migration))
            stack.enter_context(patch.object(sys,'argv',['operator-bootstrap.py','--grant',str(grant_path)]))
            stack.enter_context(redirect_stdout(output));stack.enter_context(redirect_stderr(errors))
            l.ROOT_ADMISSION=None
            status=b.main()
        lock_path.unlink(missing_ok=True)
        return status,output.getvalue(),errors.getvalue(),grant

    def test_real_entry_positive_wrong_signature_and_completed_reconciliation(self):
        before={identity:json.loads(self.docker('inspect',identity))[0]['State']['Running'] for identity in self.containers}
        status,_,_,_=self.invoke(forged=True);self.assertEqual(status,1)
        self.assertEqual(before,{identity:json.loads(self.docker('inspect',identity))[0]['State']['Running'] for identity in self.containers})
        status,output,errors,grant=self.invoke();self.assertEqual(status,0,errors+'\n'+output)
        self.assertEqual(self.source_port.fingerprint(),self.original)
        target=self.pg.RetainedPostgres(self.new_db,self.prepared,self.operation)
        target.sql("SET ROLE belib_staging_app; UPDATE reading_infos SET current_position='acknowledged-after-cutover' WHERE id=1")
        status,output,errors,_=self.invoke();self.assertEqual(status,0,errors+'\n'+output)
        self.assertEqual(target.sql('SELECT current_position FROM reading_infos'),'acknowledged-after-cutover')
        receipt=json.loads((self.state/(grant['nonce']+'.result.json')).read_text());self.assertEqual(receipt['exit_code'],0)

    def test_real_entry_interruption_restores_old_binding_without_old_db_mutation(self):
        status,_,errors,_=self.invoke(interrupt=True);self.assertEqual(status,124,errors)
        status,output,errors,_=self.invoke();self.assertEqual(status,1,errors+'\n'+output)
        self.assertEqual(self.source_port.fingerprint(),self.original)
        self.assertTrue(all(json.loads(self.docker('inspect',identity))[0]['State']['Running'] for identity in self.oldapps.values()))
        self.assertFalse(any(json.loads(self.docker('inspect',identity))[0]['State']['Running'] for identity in self.newapps.values()))
        journal=json.loads((self.prepared/(self.operation+'.json')).read_text());self.assertEqual(journal['phase'],'aborted')


if __name__=='__main__':unittest.main()
