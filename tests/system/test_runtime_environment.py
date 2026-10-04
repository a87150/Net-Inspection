import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.test import SimpleTestCase


class RuntimeEnvironmentTests(SimpleTestCase):
    def test_loads_database_password_from_os_credential_store(self):
        from net.infrastructure.environment import load_environment
        with TemporaryDirectory() as directory, patch.dict(os.environ, {
            'NET_ENV_FILE': str(Path(directory) / 'absent.env'), 'DB_USER': 'net_app',
            'DB_PASSWORD_KEYRING_SERVICE': 'test-net'}, clear=True):
            with patch('keyring.get_password', return_value='test-secret'):
                load_environment()
                self.assertEqual(os.environ['DB_PASSWORD'], 'test-secret')

    def test_file_loads_without_overwriting_explicit_process_environment(self):
        from net.infrastructure.environment import load_environment
        with TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('DB_ENGINE=mysql\nDB_PORT=3306\n', encoding='utf-8')
            with patch.dict(os.environ, {'NET_ENV_FILE': str(path), 'DB_PORT': '3307'}, clear=True):
                load_environment()
                self.assertEqual(os.environ['DB_ENGINE'], 'mysql')
                self.assertEqual(os.environ['DB_PORT'], '3307')

    def test_demo_forces_the_test_database_over_external_settings(self):
        # The shared .env must never point the demo at the production database.
        from deploy.demo import configure_environment
        with patch.dict(os.environ, {'DB_ENGINE': 'postgresql', 'DB_NAME': 'net',
                                   'DJANGO_SECRET_KEY': 'existing'}, clear=True):
            configure_environment(Path('runtime'))
            # 引擎跟随配置（MariaDB 或 PostgreSQL 都行），库名一律换成测试库。
            self.assertEqual(os.environ['DB_ENGINE'], 'postgresql')
            self.assertEqual(os.environ['DB_NAME'], 'net-test')
            self.assertEqual(os.environ['DJANGO_SQLITE_PATH'], '')
            self.assertEqual(Path(os.environ['DJANGO_STATIC_ROOT']),
                             Path('runtime') / 'staticfiles')

    def test_demo_refuses_to_run_on_sqlite(self):
        # A file-backed SQLite demo is neither isolated nor shared with production.
        from deploy.demo import configure_environment
        with patch.dict(os.environ, {'DB_ENGINE': 'sqlite'}, clear=True):
            with self.assertRaises(ValueError):
                configure_environment(Path('runtime'))

    def test_demo_honours_a_configured_database_name(self):
        from deploy.demo import configure_environment
        with patch.dict(os.environ, {'DB_ENGINE': 'mysql', 'DB_TEST_NAME': 'net-demo'},
                        clear=True):
            configure_environment(Path('runtime'))
            self.assertEqual(os.environ['DB_NAME'], 'net-demo')
