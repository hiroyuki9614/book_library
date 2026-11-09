import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from public_demo_accounts import PublicDemoError, empty_build_args, load_accounts, verify_accounts


class PublicDemoAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.path = self.home / '.config/belib-production-cd/public-demo-accounts.json'
        self.path.parent.mkdir(parents=True, mode=0o700)
        self.config = {'agent_uid': os.getuid(), 'publish_demo_accounts': True}
        self.data = {'schema_version': 1, 'intentionally_public': True,
                     'admin': {'email': 'admin@example.test', 'password': 'synthetic-admin-pass', 'user_id': 11},
                     'user': {'email': 'user@example.test', 'password': 'synthetic-user-pass', 'user_id': 12}}
        self.save()
        self.home_patch = patch('public_demo_accounts.Path.home', return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)

    def save(self):
        self.path.write_text(json.dumps(self.data))
        self.path.chmod(0o600)

    def test_default_does_not_read_any_file_or_use_ambient_credentials(self):
        self.path.unlink()
        with patch.dict(os.environ, {'VITE_PUBLIC_DEMO_ADMIN_PASSWORD': 'private-do-not-publish'}):
            self.assertEqual(load_accounts({'agent_uid': os.getuid()}), (empty_build_args(), None))

    def test_explicit_pair_mapping_and_fingerprint(self):
        args, data = load_accounts(self.config)
        self.assertEqual(data, self.data)
        self.assertEqual(set(args), {
            'VITE_PUBLIC_DEMO_EMAIL',
            'VITE_PUBLIC_DEMO_PASSWORD',
            'PUBLIC_DEMO_ACCOUNTS_SHA256',
        })
        self.assertEqual(args['VITE_PUBLIC_DEMO_EMAIL'], self.data['user']['email'])
        self.assertEqual(args['VITE_PUBLIC_DEMO_PASSWORD'], 'synthetic-user-pass')
        self.assertEqual(args['PUBLIC_DEMO_ACCOUNTS_SHA256'], hashlib.sha256(self.path.read_bytes()).hexdigest())
        old = args['PUBLIC_DEMO_ACCOUNTS_SHA256']
        self.data['admin']['password'] = 'different-synthetic-pass'
        self.save()
        self.assertNotEqual(load_accounts(self.config)[0]['PUBLIC_DEMO_ACCOUNTS_SHA256'], old)

    def test_frontend_build_contract_excludes_admin_and_keeps_user_demo(self):
        root = Path(__file__).resolve().parents[2]
        dockerfile = (root / 'frontend' / 'Dockerfile').read_text(encoding='utf-8')
        compose = (root / 'compose.yaml').read_text(encoding='utf-8')
        declarations = set()
        for line in dockerfile.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0] in {'ARG', 'ENV'}:
                declarations.add((parts[0], parts[1].split('=', 1)[0]))
        self.assertFalse(any(name.startswith('VITE_PUBLIC_DEMO_ADMIN') for _, name in declarations))
        self.assertNotIn('VITE_PUBLIC_DEMO_ADMIN', compose)
        for name in ('VITE_PUBLIC_DEMO_EMAIL', 'VITE_PUBLIC_DEMO_PASSWORD'):
            self.assertIn(('ARG', name), declarations)
            self.assertIn(('ENV', name), declarations)
            self.assertIn(name, compose)
        build_args = empty_build_args()
        self.assertFalse(any(name.startswith('VITE_PUBLIC_DEMO_ADMIN') for name in build_args))
        self.assertTrue({'VITE_PUBLIC_DEMO_EMAIL', 'VITE_PUBLIC_DEMO_PASSWORD'} <= set(build_args))

    def test_partial_unapproved_or_mixed_identity_fails_closed(self):
        for mutate in (lambda d: d.update(intentionally_public=False),
                       lambda d: d['admin'].pop('password'),
                       lambda d: d['user'].update(user_id=11),
                       lambda d: d['user'].update(email='ADMIN@example.test'),
                       lambda d: d['user'].update(user_id=True),
                       lambda d: d['admin'].update(password='line\nbreak-secret')):
            baseline = json.loads(json.dumps(self.data))
            mutate(self.data)
            self.save()
            with self.assertRaises(PublicDemoError):
                load_accounts(self.config)
            self.data = baseline

    def test_rejects_symlink_public_permissions_and_wrong_owner(self):
        self.path.chmod(0o644)
        with self.assertRaises(PublicDemoError): load_accounts(self.config)
        self.path.chmod(0o600)
        with self.assertRaises(PublicDemoError): load_accounts({**self.config, 'agent_uid': os.getuid()+1})
        target = self.path.with_suffix('.saved')
        self.path.rename(target)
        self.path.symlink_to(target)
        with self.assertRaises(OSError): load_accounts(self.config)

    def test_verifier_uses_fixed_container_stdin_and_never_puts_password_in_argv(self):
        with patch('public_demo_accounts.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'verified\n', '')) as run:
            verify_accounts(self.config, self.data)
        args, kwargs = run.call_args
        self.assertEqual(args[0][:5], ['docker', 'exec', '-i', 'belib-production-backend-1', 'node'])
        self.assertNotIn('synthetic-admin-pass', ' '.join(args[0]))
        self.assertEqual(json.loads(kwargs['input']), self.data)
        self.assertEqual(kwargs['timeout'], 65)

    def test_auth_failure_and_timeout_never_fall_back(self):
        for effect in (subprocess.CompletedProcess([], 1, '', 'do-not-echo'), subprocess.TimeoutExpired([], 65)):
            with patch('public_demo_accounts.subprocess.run') as run:
                if isinstance(effect, Exception): run.side_effect = effect
                else: run.return_value = effect
                with self.assertRaises(PublicDemoError) as error: verify_accounts(self.config, self.data)
                self.assertNotIn('do-not-echo', str(error.exception))
                self.assertEqual(run.call_count, 1)


if __name__ == '__main__': unittest.main()
