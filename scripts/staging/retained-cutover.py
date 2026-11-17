#!/usr/bin/env python3
"""Fixed staging container cutover over an independently adopted exact plan.

All effects are start/stop on six pre-created immutable container IDs. There is
no Docker build/run, network/volume mutation, shell, arbitrary command, trust
provisioning, or implicit resource discovery. A separate admission layer owns
source/grant/plan adoption and preparation of the isolated candidate resources.
"""
import hashlib
import json
import os
import re
import subprocess
import time

PROJECT = 'belib-isolated-i5'
SERVICES = ('backend','frontend','nginx')


class CutoverBlocked(RuntimeError):
    pass


def configuration_digest(info):
    # No secret environment values are emitted: only this digest leaves memory.
    # Moby resolves these two HostConfig defaults when a stopped container starts.
    # They are not Compose inputs and must not turn a start/stop CAS into drift.
    host_config=dict(info.get('HostConfig') or {})
    host_config.pop('OomScoreAdj',None)
    host_config.pop('OomKillDisable',None)
    stable = {name:info.get(name) for name in ('Id','Image','Config','Mounts')}
    stable['HostConfig']=host_config
    stable['network_names'] = sorted(info.get('NetworkSettings',{}).get('Networks',{}))
    return hashlib.sha256(json.dumps(stable,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class RetainedCutover:
    def __init__(self,plan):
        schema = plan.get('schema') if isinstance(plan, dict) else None
        legacy = schema == 'belib-isolated-copy-cutover/v1'
        fields = {'schema','source_sha','operation_id','port','old','new'}
        if not legacy:
            fields |= {'prior_project','prior_application_sha','prior_generation_sha256',
                       'prior_http_port','prior_tls_loopback_port'}
        if (not isinstance(plan,dict) or set(plan)!=fields
                or schema not in {'belib-isolated-copy-cutover/v1','belib-isolated-copy-cutover/v2'}
                or not re.fullmatch('[0-9a-f]{40}',plan.get('source_sha',''))
                or not re.fullmatch('[0-9a-f]{32}',plan.get('operation_id',''))
                or type(plan.get('port')) is not int or not 1024<=plan['port']<=65535):
            raise CutoverBlocked('Cutover requires an exact adopted staging plan')
        if not legacy:
            prefix = PROJECT + '-upgrade-'
            if (not isinstance(plan['prior_project'], str)
                    or not re.fullmatch(re.escape(prefix)+r'[0-9a-f]{32}', plan['prior_project'])
                    or not re.fullmatch('[0-9a-f]{40}', plan['prior_application_sha'] or '')
                    or not re.fullmatch('[0-9a-f]{64}', plan['prior_generation_sha256'] or '')
                    or type(plan['prior_http_port']) is not int or not 1024<=plan['prior_http_port']<=65535
                    or type(plan['prior_tls_loopback_port']) is not int or not 1024<=plan['prior_tls_loopback_port']<=65535):
                raise CutoverBlocked('Prior-generation cutover identity is malformed')
        ids=[]
        for side in ('old','new'):
            if not isinstance(plan[side],dict) or set(plan[side])!=set(SERVICES):
                raise CutoverBlocked('Cutover requires exactly the three application services')
            for service,pin in plan[side].items():
                pin_fields={'id','configuration_sha256','network_ids'}
                if not legacy and side == 'old': pin_fields.add('image_id')
                if (not isinstance(pin,dict) or set(pin)!=pin_fields
                        or any(not isinstance(pin[k],str) or not re.fullmatch('[0-9a-f]{64}',pin[k]) for k in ('id','configuration_sha256'))
                        or ('image_id' in pin and not re.fullmatch('sha256:[0-9a-f]{64}',pin['image_id']))
                        or not isinstance(pin['network_ids'],dict) or not pin['network_ids']
                        or any(not isinstance(k,str) or not re.fullmatch('[A-Za-z0-9_.-]{1,128}',k)
                               or not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v) for k,v in pin['network_ids'].items())):
                    raise CutoverBlocked('Container pin is malformed')
                ids.append(pin['id'])
        if len(set(ids))!=6:
            raise CutoverBlocked('Old and candidate containers must be distinct')
        self.plan=json.loads(json.dumps(plan))
        self.prior_project = PROJECT if legacy else plan['prior_project']
        self.prior_application_sha = '81256268276d7ead95029849037f69ec6beec202' if legacy else plan['prior_application_sha']
        self.prefix=['/usr/bin/docker','--host',f'unix:///run/user/{os.getuid()}/docker.sock']
        self.environment={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','DOCKER_CONFIG':'/nonexistent'}

    def docker(self,*args):
        result=subprocess.run([*self.prefix,*args],env=self.environment,stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=90,check=False)
        if result.returncode:
            raise CutoverBlocked('Fixed staging container command failed')
        return result.stdout

    def observe(self,side):
        result={}
        for service,pin in self.plan[side].items():
            info=json.loads(self.docker('inspect',pin['id']))[0]
            project=self.prior_project if side=='old' else PROJECT+'-upgrade-'+self.plan['operation_id']
            labels=info.get('Config',{}).get('Labels') or {}
            if (info.get('Id')!=pin['id'] or configuration_digest(info)!=pin['configuration_sha256']
                    or (side=='old' and self.plan['schema']=='belib-isolated-copy-cutover/v2'
                        and info.get('Image')!=pin['image_id'])
                    or labels.get('com.docker.compose.project')!=project
                    or labels.get('com.docker.compose.service')!=service):
                raise CutoverBlocked('Staging container configuration changed; CAS refused')
            if service in ('backend','frontend'):
                image=json.loads(self.docker('image','inspect',info['Image']))[0]
                expected_revision=self.plan['source_sha'] if side=='new' else self.prior_application_sha
                if (image.get('Id')!=info['Image'] or
                        (image.get('Config',{}).get('Labels') or {}).get('org.opencontainers.image.revision')!=expected_revision):
                    raise CutoverBlocked('Running application image revision differs from the adopted source')
            networks=info.get('NetworkSettings',{}).get('Networks',{})
            if set(networks)!=set(pin['network_ids']):
                raise CutoverBlocked('Container network set changed')
            for name,network_id in pin['network_ids'].items():
                network=json.loads(self.docker('network','inspect',name))[0]
                if (network.get('Id')!=network_id or networks[name].get('NetworkID') not in ('',network_id)
                        or not name.startswith(project+'_')
                        or (network.get('Labels') or {}).get('com.docker.compose.project')!=project):
                    raise CutoverBlocked('Container network identity changed')
            ports=info.get('HostConfig',{}).get('PortBindings') or {}
            if service != 'nginx':
                expected={}
            elif side == 'old' and self.plan['schema'] == 'belib-isolated-copy-cutover/v2':
                expected={'8080/tcp':[{'HostIp':'127.0.0.1','HostPort':str(self.plan['prior_http_port'])}],
                          '8443/tcp':[{'HostIp':'127.0.0.1','HostPort':str(self.plan['prior_tls_loopback_port'])}]}
            else:
                expected={'8080/tcp':[{'HostIp':'127.0.0.1','HostPort':str(self.plan['port'])}]}
            if ports!=expected:
                raise CutoverBlocked('Cutover must reuse only the exact staging loopback endpoint')
            if info.get('State',{}).get('Paused') or info.get('State',{}).get('Restarting'):
                raise CutoverBlocked('Container is paused or restarting')
            result[service]=bool(info.get('State',{}).get('Running'))
        return result

    def stop_old_writers(self):
        self.observe('old')
        if any(self.observe('new').values()):
            raise CutoverBlocked('Candidate is already running before quiesce')
        for service in ('nginx','backend','frontend'):
            self.docker('stop','--time','20',self.plan['old'][service]['id'])
        if any(self.observe('old').values()):
            raise CutoverBlocked('Old application did not quiesce')

    def start_components(self,side):
        other='old' if side=='new' else 'new'
        if any(self.observe(other).values()) or self.observe(side)['nginx']:
            raise CutoverBlocked('Component start requires both public ingresses stopped')
        for service in ('backend','frontend'):
            self.docker('start',self.plan[side][service]['id'])
        self.verify_components(side)

    def verify_components(self,side):
        other='old' if side=='new' else 'new'
        states=self.observe(side)
        if any(self.observe(other).values()) or not states['backend'] or not states['frontend']:
            raise CutoverBlocked('Prepared application components differ from the adopted plan')
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            try:
                self.docker('exec','--user','node',self.plan[side]['backend']['id'],'node','-e',
                    "fetch('http://127.0.0.1:3000/health',{signal:AbortSignal.timeout(3000)}).then(r=>process.exit(r.status===200?0:1)).catch(()=>process.exit(1))")
                return
            except CutoverBlocked:
                time.sleep(.5)
        raise CutoverBlocked('Prepared backend health did not pass')

    def publish(self,side):
        # Only a durable commit/abort decision may call this method. The single
        # already-adopted loopback binding is never assigned a new guessed port.
        self.verify_components(side)
        self.docker('start',self.plan[side]['nginx']['id'])
        self.verify_active(side)

    def verify_active(self,side):
        other='old' if side=='new' else 'new'
        if any(self.observe(other).values()) or not all(self.observe(side).values()):
            raise CutoverBlocked('Application cutover state differs from the adopted plan')
        # The unchanged loopback endpoint is exclusively owned by this pinned
        # nginx container; source/image/configuration provenance comes from plan.
        url='http://127.0.0.1:'+str(self.plan['port'])+'/health'
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            result=subprocess.run(['/usr/bin/curl','-q','--noproxy','*','--fail','--silent','--max-time','3',
                '--output','/dev/null','--write-out','%{http_code}',url],env=self.environment,
                stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=5,check=False)
            if result.returncode==0 and result.stdout==b'200':
                return
            time.sleep(.5)
        raise CutoverBlocked('Pinned staging application health did not pass')

    def stop_new(self):
        # Never stop or adopt an unexpected replacement, even during rollback.
        self.observe('new')
        for service in ('nginx','backend','frontend'):
            self.docker('stop','--time','20',self.plan['new'][service]['id'])
        if any(self.observe('new').values()):
            raise CutoverBlocked('Candidate application did not stop')
