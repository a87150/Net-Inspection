"""Deployment checks run only against temporary files and an isolated database."""
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from cryptography.fernet import Fernet
from django.conf import settings
from django.test import TestCase
from deploy import setup
from deploy.service import role_lock
from tests.databases import connect, credentials, isolated_database, scalars, subprocess_environment


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


class DeploymentSetupTests(TestCase):
    # TestCase 而非 SimpleTestCase：prepare 用例要直连隔离库做断言。
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
        with TemporaryDirectory() as directory, isolated_database() as name:
            root = Path(directory)
            # setup.load_config 读 .env 文件并清掉环境变量里的 DB_*，所以引擎、库名和
            # 凭证都得写进文件本身，光给子进程设环境变量没用。
            engine = {**credentials(), 'DB_NAME': name}
            env_path = configuration(root, **engine)
            env = subprocess_environment(name, env_path)
            script = '''from pathlib import Path
from deploy import setup
setup.ROOT = Path(__import__('sys').argv[1])
try:
    setup.prepare(setup.ROOT / '.env', interactive=False)
except ValueError as exc:
    if 'No active administrator' not in str(exc): raise
'''
            command = [sys.executable, '-X', 'utf8', '-c', script, str(root)]
            result = subprocess.run(command, cwd=settings.BASE_DIR, env=env, capture_output=True, timeout=600)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            database = connect(name)
            try:
                for table in ('net_people', 'net_network_device', 'net_taskrun'):
                    self.assertEqual(scalars(database, 'SELECT count(*) FROM ' + table), [0])
                with database.cursor() as cursor:
                    # MariaDB 的 tinyint(1) 接受 1，PostgreSQL 的 boolean 只接受 TRUE。
                    flag = 'TRUE' if database.vendor == 'postgresql' else '1'
                    cursor.execute('INSERT INTO auth_user (password,is_superuser,username,'
                                    'first_name,last_name,email,is_staff,is_active,date_joined)'
                                    ' VALUES (%s,' + flag + ',%s,%s,%s,%s,' + flag + ',' + flag + ',now())',
                                    ['!fixture', 'fixture', '', '', ''])
            finally:
                database.close()
            original = env_path.read_bytes()
            result = subprocess.run(command, cwd=settings.BASE_DIR, env=env, capture_output=True, timeout=600)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            self.assertEqual(env_path.read_bytes(), original)
            self.assertTrue((root / 'staticfiles/app/css/style.css').is_file())
            database = connect(name)
            try:
                self.assertEqual(scalars(database, 'SELECT username FROM auth_user'), ['fixture'])
            finally:
                database.close()

    def test_invalid_environment_is_rejected_without_echoing_its_content(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text("DB_PASSWORD='private-fixture-value", encoding='utf-8')
            with self.assertRaises(ValueError) as error:
                setup.load_config(path)
            self.assertIn('syntax', str(error.exception))
            self.assertNotIn('private-fixture-value', str(error.exception))
