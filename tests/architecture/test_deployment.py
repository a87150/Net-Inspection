"""Exercise production static serving and the isolated demo startup contract."""
from io import StringIO
from contextlib import closing
from tempfile import TemporaryDirectory
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from django.conf import settings
from django.core.management import call_command
from django.test import Client, SimpleTestCase, override_settings


class ProductionStaticTests(SimpleTestCase):
    def test_collected_css_and_js_are_served_with_debug_false(self):
        with TemporaryDirectory() as root, override_settings(DEBUG=False, STATIC_ROOT=root):
            call_command('collectstatic', interactive=False, verbosity=0, stdout=StringIO())
            client = Client()
            for path, media in (
                ('app/css/style.css', 'text/css'),
                ('app/js/common/table_workspace.js', 'javascript'),
            ):
                with self.subTest(path=path):
                    response = client.get('/static/' + path)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(media, response['Content-Type'])
                    body = b''.join(response.streaming_content) if response.streaming else response.content
                    self.assertGreater(len(body), 100)
                    self.assertEqual(body, (settings.BASE_DIR / 'static' / path).read_bytes())


class DemoLauncherTests(SimpleTestCase):
    def test_prepare_creates_isolated_persistent_database_and_keeps_user_changes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'isolated-demo'
            original_path = settings.BASE_DIR / 'db.sqlite3'
            original = hashlib.sha256(original_path.read_bytes()).hexdigest() if original_path.exists() else None
            cmd = [sys.executable, '-m', 'deploy.demo', '--runtime-dir', str(root), '--prepare-only']
            env = {**os.environ, 'DB_ENGINE': 'mysql', 'DJANGO_SQLITE_PATH': str(settings.BASE_DIR / 'db.sqlite3')}
            for attempt in range(2):
                result = subprocess.run(cmd, cwd=settings.BASE_DIR, env=env, capture_output=True, timeout=45)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
                with closing(sqlite3.connect(root / 'demo.sqlite3')) as database, database:
                    self.assertEqual(database.execute('SELECT count(*) FROM net_peoplesyncsource').fetchone()[0], 2)
                    if attempt == 0:
                        database.execute("UPDATE net_people SET name='Retained edit' WHERE employee_id='DEMO-P001'")
                    else:
                        self.assertEqual(database.execute("SELECT name FROM net_people WHERE employee_id='DEMO-P001'").fetchone()[0], 'Retained edit')
                self.assertTrue((root / 'staticfiles/app/css/style.css').is_file())
            current = hashlib.sha256(original_path.read_bytes()).hexdigest() if original_path.exists() else None
            self.assertEqual(current, original)

    def test_prepare_refuses_nonempty_unowned_directory(self):
        with TemporaryDirectory() as directory:
            marker = Path(directory) / 'user.txt'
            marker.write_text('user content')
            result = subprocess.run([sys.executable, '-m', 'deploy.demo', '--runtime-dir', directory, '--prepare-only'],
                                    cwd=settings.BASE_DIR, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'unowned', result.stderr)
            self.assertEqual(marker.read_text(), 'user content')
