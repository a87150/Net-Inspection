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

    def test_demo_preserves_explicit_mariadb_settings(self):
        from deploy.demo import configure_environment
        with patch.dict(os.environ, {'DB_ENGINE': 'mysql', 'DB_NAME': 'example',
                                   'DJANGO_SECRET_KEY': 'existing'}, clear=True):
            configure_environment(Path('runtime'))
            self.assertEqual(os.environ['DB_ENGINE'], 'mysql')
            self.assertEqual(os.environ['DB_NAME'], 'example')
            self.assertEqual(os.environ['DJANGO_SECRET_KEY'], 'existing')
            self.assertNotIn('DJANGO_SQLITE_PATH', os.environ)
