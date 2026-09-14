"""Deployment checks run only against temporary files and an isolated SQLite database."""
from contextlib import closing
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from cryptography.fernet import Fernet
from django.test import SimpleTestCase
from deploy import setup
from deploy.service import role_lock


def configuration(root, **overrides):
    values = {
        'DJANGO_SECRET_KEY': 'fixture-stable-secret-' * 4, 'DJANGO_DEBUG': 'false',
        'DJANGO_ALLOWED_HOSTS': 'localhost,127.0.0.1', 'DB_ENGINE': 'sqlite',
        'DJANGO_SQLITE_PATH': str(root / 'isolated.sqlite3'),
        'DJANGO_STATIC_ROOT': str(root / 'staticfiles'), 'WEB_LISTEN': '127.0.0.1:18080',
        **{key: Fernet.generate_key().decode() for key in setup.KEYS},
    }
    values.update(overrides)
    path = root / '.env'
    lines = []
    for key, value in values.items():
        text = str(value).replace(chr(92), '/')
        lines.append(f"{key}='{text}'")
    path.write_text('\n'.join(lines), encoding='utf-8')
    return path


class DeploymentSetupTests(SimpleTestCase):
    def test_existing_configuration_is_never_regenerated(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_bytes(b'keep-existing-keys-and-database')
            setup.configure(path, interactive=False)
            self.assertEqual(path.read_bytes(), b'keep-existing-keys-and-database')

    def test_new_configuration_quotes_password_and_uses_unique_stable_keys(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, clear=True), patch.object(setup, 'ROOT', Path(directory)):
            path = Path(directory) / '.env'
            password = "literal'\\${NEVER_EXPAND}password"
            with patch('builtins.input', return_value=''), patch('getpass.getpass', return_value=password):
                setup.configure(path)
            setup.load_config(path)
            self.assertEqual(os.environ['DB_PASSWORD'], password)
            self.assertEqual(os.environ['DB_ENGINE'], 'mysql')
            self.assertEqual(len({os.environ[key] for key in setup.KEYS}), 3)
            original = path.read_bytes()
            setup.configure(path)
            self.assertEqual(path.read_bytes(), original)

    def test_invalid_newline_does_not_leave_partial_environment(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            with patch('builtins.input', return_value=''), patch('getpass.getpass', return_value='invalid\npassword'):
                with self.assertRaises(ValueError):
                    setup.configure(path)
            self.assertFalse(path.exists())

    def test_missing_environment_noninteractive_has_no_side_effect(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            with self.assertRaises(ValueError):
                setup.configure(path, interactive=False)
            self.assertFalse(path.exists())

    def test_selected_file_overrides_stale_shell_database_and_keys(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, clear=True):
            root = Path(directory)
            with patch.object(setup, 'ROOT', root):
                path = configuration(root)
                os.environ.update(DB_ENGINE='mysql', DB_PASSWORD='wrong-database-secret', NET_ENV_FILE='absent')
                setup.load_config(path)
                self.assertEqual(os.environ['DB_ENGINE'], 'sqlite')
                self.assertNotIn('DB_PASSWORD', os.environ)
                self.assertEqual(os.environ['NET_ENV_FILE'], str(path))

    def test_refuses_implicit_database_debug_placeholder_and_missing_keys(self):
        for overrides in ({'DB_ENGINE': ''}, {'DJANGO_DEBUG': 'true'}, {'DJANGO_SECRET_KEY': 'replace-with-secret'}, {setup.KEYS[0]: ''}, {'DJANGO_SQLITE_PATH': ''}):
            with self.subTest(overrides=overrides), TemporaryDirectory() as directory, patch.dict(os.environ, clear=True):
                root = Path(directory)
                path = configuration(root, **overrides)
                with patch.object(setup, 'ROOT', root), self.assertRaises(ValueError):
                    setup.load_config(path)

    def test_busy_port_is_rejected_without_starting_a_server(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, clear=True), socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); sock.listen()
            root = Path(directory)
            path = configuration(root, WEB_LISTEN=f'127.0.0.1:{sock.getsockname()[1]}')
            with patch.object(setup, 'ROOT', root), self.assertRaisesRegex(ValueError, 'occupied'):
                setup.preflight(path)

    def test_role_lock_blocks_duplicates_and_releases_after_failure(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'worker.lock'
            with role_lock(path):
                with self.assertRaises(RuntimeError):
                    with role_lock(path):
                        self.fail('Duplicate process was allowed')
            with role_lock(path):
                pass

    def test_prepare_migrates_only_fixture_database_and_preserves_existing_records(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            env_path = configuration(root)
            env = {**os.environ, 'NET_ENV_FILE': str(env_path), 'DB_ENGINE': 'sqlite'}
            script = '''from pathlib import Path
from deploy import setup
setup.ROOT = Path(__import__('sys').argv[1])
try:
    setup.prepare(setup.ROOT / '.env', interactive=False)
except ValueError as exc:
    if 'No active administrator' not in str(exc): raise
'''
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(root)], cwd=setup.ROOT, env=env, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            with closing(sqlite3.connect(root / 'isolated.sqlite3')) as db, db:
                for table in ('net_people', 'net_network_device', 'net_taskrun'):
                    self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)
                db.execute("INSERT INTO auth_user (password,is_superuser,username,first_name,last_name,email,is_staff,is_active,date_joined) VALUES ('fixture-password-hash',1,'fixture-admin','','','',1,1,'2026-01-01')")
            original = env_path.read_bytes()
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(root)], cwd=setup.ROOT, env=env, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            self.assertEqual(env_path.read_bytes(), original)
            self.assertTrue((root / 'staticfiles/app/css/style.css').is_file())
            with closing(sqlite3.connect(root / 'isolated.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT username FROM auth_user').fetchall(), [('fixture-admin',)])


    def test_invalid_environment_is_rejected_without_echoing_its_content(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text("DB_PASSWORD='private-fixture-value", encoding='utf-8')
            with self.assertRaises(ValueError) as error:
                setup.load_config(path)
            self.assertIn('syntax', str(error.exception))
            self.assertNotIn('private-fixture-value', str(error.exception))
