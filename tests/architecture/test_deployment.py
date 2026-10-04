"""Exercise production static serving and the isolated demo startup contract."""
from io import StringIO
from tempfile import TemporaryDirectory
from pathlib import Path
import subprocess
import sys

from deploy import demo

from django.conf import settings
from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings
from tests.databases import connect, isolated_database, scalars, subprocess_environment

class ProductionStaticTests(SimpleTestCase):
    def test_collected_css_and_js_are_served_with_debug_false(self):
        with TemporaryDirectory() as root, override_settings(DEBUG=False, STATIC_ROOT=root):
            call_command('collectstatic', interactive=False, verbosity=0, stdout=StringIO())
            client = Client()
            for path, media in (
                ('app/css/style.css', 'text/css'),
                ('app/css/foundation.css', 'text/css'),
                ('app/css/operations.css', 'text/css'),
                ('app/js/common/table_workspace.js', 'javascript'),
            ):
                with self.subTest(path=path):
                    response = client.get('/static/' + path)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(media, response['Content-Type'])
                    body = b''.join(response.streaming_content) if response.streaming else response.content
                    self.assertTrue(body)
                    self.assertEqual(body, (settings.BASE_DIR / 'static' / path).read_bytes())


class DemoLauncherTests(TestCase):
    # 用 TestCase 而非 SimpleTestCase：prepare 用例要直连隔离库做断言。
    def test_service_runner_starts_and_stops_a_separate_worker_process(self):
        events = []

        class WorkerProcess:
            def poll(self):
                return None

            def terminate(self):
                events.append('worker-stopped')

            def wait(self, timeout=None):
                events.append(('worker-waited', timeout))
                return 0

        def process_factory(command, **kwargs):
            events.append(('worker-started', command, kwargs))
            return WorkerProcess()

        def web_server(_application, **kwargs):
            events.append(('web-served', kwargs))

        runner = getattr(demo, 'run_services', None)
        self.assertIsNotNone(runner)
        runner(
            object(), port=8123, web_server=web_server,
            process_factory=process_factory,
        )

        self.assertEqual(events[0][0], 'worker-started')
        self.assertIn('run_task_worker', events[0][1])
        self.assertEqual(events[1], (
            'web-served', {'host': '127.0.0.1', 'port': 8123, 'threads': 4},
        ))
        self.assertEqual(events[2:], ['worker-stopped', ('worker-waited', 10)])

    def test_demo_cli_runs_worker_by_default_and_can_disable_it(self):
        parser_factory = getattr(demo, 'build_argument_parser', None)
        self.assertIsNotNone(parser_factory)

        self.assertTrue(parser_factory().parse_args([]).with_worker)
        self.assertFalse(parser_factory().parse_args(['--no-worker']).with_worker)

    def test_prepare_creates_isolated_persistent_database_and_keeps_user_changes(self):
        with TemporaryDirectory() as directory, isolated_database() as name:
            root = Path(directory) / 'isolated-demo'
            cmd = [sys.executable, '-m', 'deploy.demo', '--runtime-dir', str(root), '--prepare-only']
            env = subprocess_environment(name, root / 'absent.env',
                                       DJANGO_STATIC_ROOT=str(root / 'staticfiles'))
            for attempt in range(2):
                result = subprocess.run(cmd, cwd=settings.BASE_DIR, env=env, capture_output=True, timeout=600)
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
                database = connect(name)
                try:
                    self.assertEqual(scalars(database, 'SELECT count(*) FROM net_peoplesyncsource'), [2])
                    if attempt == 0:
                        with database.cursor() as cursor:
                            cursor.execute("UPDATE net_people SET name='Retained edit' WHERE employee_id='DEMO-P001'")
                    else:
                        self.assertEqual(scalars(database, "SELECT name FROM net_people WHERE employee_id='DEMO-P001'"),
                                         ['Retained edit'])
                finally:
                    database.close()

    def test_prepare_refuses_nonempty_unowned_directory(self):
        with TemporaryDirectory() as directory:
            marker = Path(directory) / 'user.txt'
            marker.write_text('user content')
            result = subprocess.run([sys.executable, '-m', 'deploy.demo', '--runtime-dir', directory, '--prepare-only'],
                                    cwd=settings.BASE_DIR, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'unowned', result.stderr)
            self.assertEqual(marker.read_text(), 'user content')
