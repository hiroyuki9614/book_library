"""Explicit owner opt-in for newly provisioned, deliberately public demo accounts."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path


class PublicDemoError(RuntimeError):
    pass


PUBLIC_DEMO_VITE_PREFIX = 'VITE_PUBLIC_DEMO_'
PUBLIC_DEMO_USER_BUILD_KEYS = frozenset({
    'VITE_PUBLIC_DEMO_EMAIL',
    'VITE_PUBLIC_DEMO_PASSWORD',
})
LEGACY_ADMIN_BUILD_KEYS = frozenset({
    'VITE_PUBLIC_DEMO_ADMIN_EMAIL',
    'VITE_PUBLIC_DEMO_ADMIN_PASSWORD',
})


def drop_legacy_admin_build_args(values: dict) -> dict:
    """Drop only the two retired admin keys and reject other public-demo names."""
    if not isinstance(values, dict):
        raise PublicDemoError('Public demo build argument names are invalid')
    allowed = PUBLIC_DEMO_USER_BUILD_KEYS | LEGACY_ADMIN_BUILD_KEYS
    if any(
        isinstance(key, str)
        and key.startswith(PUBLIC_DEMO_VITE_PREFIX)
        and key not in allowed
        for key in values
    ):
        raise PublicDemoError('Unsupported public demo build argument name')
    return {key: value for key, value in values.items() if key not in LEGACY_ADMIN_BUILD_KEYS}


def empty_build_args() -> dict[str, str]:
    return {key: '' for key in (
        'VITE_PUBLIC_DEMO_EMAIL', 'VITE_PUBLIC_DEMO_PASSWORD',
        'PUBLIC_DEMO_USER_BUILD_SHA256',
    )}


def _public_user_build_fingerprint(user: dict[str, str]) -> str:
    """Fingerprint only the intentionally public pair compiled into the frontend."""
    public_inputs = json.dumps(
        {'email': user['email'], 'password': user['password']},
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
    ).encode('utf-8')
    return hashlib.sha256(public_inputs).hexdigest()


def load_accounts(config: dict) -> tuple[dict[str, str], dict | None]:
    """No ambient env, production secrets, operator passwords or arbitrary paths."""
    if config.get('publish_demo_accounts') is not True:
        return empty_build_args(), None
    path = Path.home() / '.config/belib-production-cd/public-demo-accounts.json'
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != config['agent_uid'] or parent.st_mode & 0o077:
        raise PublicDemoError('Public demo directory must be a private owner directory')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != config['agent_uid']
                or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > 4096):
            raise PublicDemoError('Public demo file must be a private owner regular file')
        raw = os.read(fd, 4097)
        if len(raw) > 4096:
            raise PublicDemoError('Public demo file exceeds its size limit')
    finally:
        os.close(fd)
    try:
        data = json.loads(raw)
        if set(data) != {'schema_version', 'intentionally_public', 'admin', 'user'}:
            raise ValueError()
        if data['schema_version'] != 1 or data['intentionally_public'] is not True:
            raise ValueError()
        for role in ('admin', 'user'):
            pair = data[role]
            if set(pair) != {'email', 'password', 'user_id'}:
                raise ValueError()
            if (not isinstance(pair['email'], str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', pair['email'])
                    or not isinstance(pair['password'], str) or not 12 <= len(pair['password']) <= 128
                    or any(c in pair['password'] for c in '\r\n\x00')
                    or type(pair['user_id']) is not int or pair['user_id'] < 1):
                raise ValueError()
        if data['admin']['email'].casefold() == data['user']['email'].casefold() or data['admin']['user_id'] == data['user']['user_id']:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise PublicDemoError('Public demo file has an invalid schema') from None
    return {
        'VITE_PUBLIC_DEMO_EMAIL': data['user']['email'],
        'VITE_PUBLIC_DEMO_PASSWORD': data['user']['password'],
        'PUBLIC_DEMO_USER_BUILD_SHA256': _public_user_build_fingerprint(data['user']),
    }, data


_VERIFY_JS = r'''
import { prisma } from '/app/dist/src/lib/prisma.js';
setTimeout(()=>process.exit(2),60000).unref();
let input=''; for await (const chunk of process.stdin) input+=chunk;
const accounts=JSON.parse(input);
const base='http://127.0.0.1:'+process.env.APP_PORT;
const headers={'Content-Type':'application/json',Origin:process.env.FRONTEND_URL};
const request=(path,options={})=>fetch(base+path,{...options,redirect:'manual',signal:AbortSignal.timeout(10000)});
try {
for(const role of ['admin','user']) {
 const p=accounts[role]; let cookie;
 const live=await prisma.user.findUnique({where:{id:p.user_id},select:{email:true,deletedAt:true,role:{select:{name:true}}}});
 if(!live || live.deletedAt!==null || live.email!==p.email || live.role.name!==role) throw new Error('active identity');
 try {
  const login=await request('/api/auth/sign-in/email',{method:'POST',headers,body:JSON.stringify({email:p.email,password:p.password})});
  if(login.status!==200) throw new Error('login');
  cookie=login.headers.getSetCookie().map(x=>x.split(';')[0]).join('; ');
  if(!cookie) throw new Error('cookie');
  const response=await request('/api/v1/me',{headers:{Cookie:cookie}});
  const me=await response.json();
  if(response.status!==200 || me.id!==p.user_id || me.email!==p.email || me.role!==role) throw new Error('identity');
  const admin=await request('/api/v1/admin/books',{headers:{Cookie:cookie}});
  await admin.body?.cancel();
  if(admin.status!==(role==='admin'?200:403)) throw new Error('permission');
 } finally {
  if(cookie) {
   const logout=await request('/api/auth/sign-out',{method:'POST',headers:{...headers,Cookie:cookie},body:'{}'});
   if(logout.status!==200) throw new Error('logout');
  }
 }
}
console.log('verified');
} finally { await prisma.$disconnect(); }
'''


def verify_accounts(config: dict, data: dict | None) -> None:
    if data is None:
        return
    # The caller first validates the unchanged protected Compose contract.
    # No network destination is accepted from the credentials file.
    try:
        result = subprocess.run(
            ['docker', 'exec', '-i', 'belib-production-backend-1', 'node', '--input-type=module', '-e', _VERIFY_JS],
            input=json.dumps(data), text=True, capture_output=True, timeout=65, check=False,
        )
        if result.returncode or result.stdout.strip() != 'verified':
            raise PublicDemoError('Public demo role/login verification failed')
    except (OSError, subprocess.TimeoutExpired):
        raise PublicDemoError('Public demo role/login verification unavailable') from None
