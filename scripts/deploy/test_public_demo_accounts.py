import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from public_demo_accounts import (
    PublicDemoError,
    drop_legacy_admin_build_args,
    empty_build_args,
    load_accounts,
    verify_accounts,
)


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
            'PUBLIC_DEMO_USER_BUILD_SHA256',
        })
        self.assertEqual(args['VITE_PUBLIC_DEMO_EMAIL'], self.data['user']['email'])
        self.assertEqual(args['VITE_PUBLIC_DEMO_PASSWORD'], 'synthetic-user-pass')
        original = args['PUBLIC_DEMO_USER_BUILD_SHA256']
        self.assertRegex(original, r'^[0-9a-f]{64}$')
        self.data['admin']['password'] = 'different-synthetic-pass'
        self.data['admin']['email'] = 'changed-admin@example.test'
        self.save()
        self.assertEqual(load_accounts(self.config)[0]['PUBLIC_DEMO_USER_BUILD_SHA256'], original)
        self.data['user']['password'] = 'changed-public-user-pass'
        self.save()
        self.assertNotEqual(load_accounts(self.config)[0]['PUBLIC_DEMO_USER_BUILD_SHA256'], original)

    def test_drops_only_exact_legacy_admin_build_keys_and_rejects_other_names(self):
        args = {
            'VITE_PUBLIC_DEMO_EMAIL': 'user@example.test',
            'VITE_PUBLIC_DEMO_PASSWORD': 'synthetic-user-pass',
            'VITE_PUBLIC_DEMO_ADMIN_EMAIL': 'admin@example.test',
            'VITE_PUBLIC_DEMO_ADMIN_PASSWORD': 'synthetic-admin-pass',
        }
        self.assertEqual(
            drop_legacy_admin_build_args(args),
            {
                'VITE_PUBLIC_DEMO_EMAIL': 'user@example.test',
                'VITE_PUBLIC_DEMO_PASSWORD': 'synthetic-user-pass',
            },
        )
        for unexpected_name in (
            'VITE_PUBLIC_DEMO_ADMIN_PASSWORD_COPY',
            'VITE_PUBLIC_DEMO_ADMN_PASSWORD',
        ):
            with self.subTest(name=unexpected_name):
                with self.assertRaisesRegex(PublicDemoError, '^Unsupported public demo build argument name$'):
                    drop_legacy_admin_build_args({unexpected_name: 'synthetic-only-marker'})

    def test_frontend_build_contract_excludes_admin_and_keeps_user_demo(self):
        root = Path(__file__).resolve().parents[2]
        dockerfile = (root / 'frontend' / 'Dockerfile').read_text(encoding='utf-8')
        compose = (root / 'compose.yaml').read_text(encoding='utf-8')
        env_example = (root / 'frontend' / '.env.example').read_text(encoding='utf-8')
        production_sources = '\n'.join(
            path.read_text(encoding='utf-8')
            for path in (root / 'frontend' / 'src').rglob('*')
            if path.is_file()
            and path.suffix in {'.js', '.jsx', '.ts', '.tsx'}
            and '.test.' not in path.name
            and '.spec.' not in path.name
        )
        declarations = set()
        for line in dockerfile.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0] in {'ARG', 'ENV'}:
                declarations.add((parts[0], parts[1].split('=', 1)[0]))
        self.assertFalse(any(name.startswith('VITE_PUBLIC_DEMO_ADMIN') for _, name in declarations))
        self.assertNotIn('VITE_PUBLIC_DEMO_ADMIN', compose)
        self.assertNotIn('VITE_PUBLIC_DEMO_ADMIN', env_example)
        self.assertNotIn('VITE_PUBLIC_DEMO_ADMIN', production_sources)
        self.assertNotIn('PUBLIC_DEMO_ACCOUNTS_SHA256', dockerfile + compose)
        self.assertIn(('ARG', 'PUBLIC_DEMO_USER_BUILD_SHA256'), declarations)
        self.assertIn('PUBLIC_DEMO_USER_BUILD_SHA256', compose)
        self.assertIn('com.belib.public-demo-user-build-sha256', dockerfile)
        for name in ('VITE_PUBLIC_DEMO_EMAIL', 'VITE_PUBLIC_DEMO_PASSWORD'):
            self.assertIn(('ARG', name), declarations)
            self.assertIn(('ENV', name), declarations)
            self.assertIn(name, compose)
        build_args = empty_build_args()
        self.assertFalse(any(name.startswith('VITE_PUBLIC_DEMO_ADMIN') for name in build_args))
        self.assertTrue({'VITE_PUBLIC_DEMO_EMAIL', 'VITE_PUBLIC_DEMO_PASSWORD'} <= set(build_args))

    def test_vite_bundle_excludes_admin_environment_markers(self):
        root = Path(__file__).resolve().parents[2]
        frontend = root / 'frontend'
        vite = frontend / 'node_modules' / '.bin' / 'vite'
        node = shutil.which('node')
        if not vite.is_file() or node is None:
            self.skipTest('Frontend Vite dependencies are not installed')
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            env_dir = temporary_root / 'env'
            env_dir.mkdir()
            out_dir = temporary_root / 'dist'
            runner = temporary_root / 'build.mjs'
            runner.write_text(
                'import { build } from ' + json.dumps((frontend / 'node_modules' / 'vite' / 'dist' / 'node' / 'index.js').as_uri()) + ';\n'
                'await build({\n'
                '  root: ' + json.dumps(str(frontend)) + ',\n'
                '  configFile: ' + json.dumps(str(frontend / 'vite.config.ts')) + ',\n'
                '  envDir: ' + json.dumps(str(env_dir)) + ',\n'
                '  build: { outDir: ' + json.dumps(str(out_dir)) + ', emptyOutDir: true },\n'
                '});\n',
                encoding='utf-8',
            )
            admin_email = 'build-only-admin-email-marker@example.invalid'
            admin_password = 'build-only-admin-password-marker'
            public_email = 'build-only-user@example.invalid'
            public_password = 'build-only-public-user-marker'
            result = subprocess.run(
                [
                    node, str(runner),
                ],
                cwd=frontend,
                env={
                    'PATH': str(Path(node).parent),
                    'VITE_PUBLIC_DEMO_EMAIL': public_email,
                    'VITE_PUBLIC_DEMO_PASSWORD': public_password,
                    'VITE_PUBLIC_DEMO_ADMIN_EMAIL': admin_email,
                    'VITE_PUBLIC_DEMO_ADMIN_PASSWORD': admin_password,
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, 'Vite bundle smoke build failed')
            bundle = ''.join(path.read_text(encoding='utf-8', errors='replace') for path in out_dir.rglob('*.js'))
        self.assertTrue(public_email in bundle and public_password in bundle, 'The general demo pair was omitted')
        self.assertTrue(admin_email not in bundle and admin_password not in bundle, 'An admin marker reached the frontend bundle')
        self.assertTrue('VITE_PUBLIC_DEMO_ADMIN' not in bundle, 'An admin Vite key reached the frontend bundle')

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
