r"""Launch the loopback Web and Worker using the shared root .env configuration.

Windows: .\.venv\Scripts\python.exe -m deploy.demo
Linux:   ./.venv/bin/python -m deploy.demo
Starts the task Worker by default. Use --no-worker for an offline display-only demo.
Without an explicit backend, use the isolated SQLite demo. MySQL/MariaDB is never seeded.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys


BASE_DIR = Path(__file__).resolve().parent.parent


def configure_environment(runtime):
    defaults = {
        'DJANGO_SETTINGS_MODULE': 'net.settings', 'DJANGO_DEBUG': 'false',
        'DJANGO_ALLOWED_HOSTS': '127.0.0.1,localhost',
        'DJANGO_SECRET_KEY': 'DEMO-ONLY-NOT-FOR-PRODUCTION-LOCAL-ISOLATED-DATABASE',
        'DB_ENGINE': 'sqlite', 'DJANGO_STATIC_ROOT': str(runtime / 'staticfiles'),
    }
    for name, value in defaults.items():
        os.environ.setdefault(name, value)
    if os.environ['DB_ENGINE'].lower() == 'sqlite':
        os.environ['DJANGO_SQLITE_PATH'] = str(runtime / 'demo.sqlite3')


def prepare(runtime):
    from net.infrastructure.environment import load_environment
    load_environment()
    from net import require_runtime
    require_runtime()
    runtime = runtime.resolve()
    marker = runtime / '.network-inspection-demo'
    if runtime.exists() and any(runtime.iterdir()) and not marker.is_file():
        raise ValueError('Refusing an unowned nonempty demo runtime directory.')
    runtime.mkdir(parents=True, exist_ok=True)
    marker.write_text('Network Inspection isolated demo v1\n', encoding='utf-8')
    for name in ('logs', 'pc-staging'):
        (runtime / name).mkdir(parents=True, exist_ok=True)
    for variable, filename in (
        ('PC_LOG_SOURCE_ENCRYPTION_KEY', '.pc-log-source.key'),
        ('DEVICE_BACKUP_ENCRYPTION_KEY', '.device-backup.key'),
    ):
        if not os.environ.get(variable):
            from cryptography.fernet import Fernet
            key_path = runtime / filename
            try:
                with key_path.open('x', encoding='ascii') as key_file:
                    key_file.write(Fernet.generate_key().decode('ascii'))
            except FileExistsError:
                pass
            os.environ[variable] = key_path.read_text(encoding='ascii').strip()
    # Deliberately override inherited production/old-database settings.
    configure_environment(runtime)
    import django
    django.setup()
    from django.core.management import call_command
    call_command('migrate', interactive=False, verbosity=0)
    if os.environ['DB_ENGINE'].lower() == 'sqlite' and not (runtime / '.seeded').exists():
        call_command('seed_demo_data')
        (runtime / '.seeded').write_text('Seed complete; subsequent starts preserve edits.\n', encoding='utf-8')
    call_command('collectstatic', interactive=False, verbosity=0)
    from django.conf import settings
    database = settings.DATABASES['default']
    print(f'Database: {database["ENGINE"]} / {database["NAME"]}', flush=True)
    print(f'DEBUG=False; static root: {runtime / "staticfiles"}', flush=True)


def build_argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir', type=Path, default=BASE_DIR / 'demo-runtime')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument(
        '--no-worker', dest='with_worker', action='store_false', default=True,
        help='只启动 Web，不执行巡检、分析或人员目录任务',
    )
    return parser


def run_services(application, *, port, with_worker=True, web_server=None, process_factory=None):
    if web_server is None:
        from waitress import serve as web_server
    process_factory = process_factory or subprocess.Popen
    worker = None
    if with_worker:
        worker = process_factory(
            [
                sys.executable, str(BASE_DIR / 'manage.py'), 'run_task_worker',
                '--threads', '4', '--poll-seconds', '5', '--lease-seconds', '60',
            ],
            cwd=BASE_DIR,
            env=os.environ.copy(),
        )
        print(f'Worker process started: PID {getattr(worker, "pid", "unknown")}', flush=True)
    else:
        print('Worker disabled; queued tasks will not execute.', flush=True)
    try:
        web_server(application, host='127.0.0.1', port=port, threads=4)
    finally:
        if worker is not None and worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)


def main():
    parser = build_argument_parser()
    options = parser.parse_args()
    if not 1 <= options.port <= 65535:
        parser.error('port must be between 1 and 65535')
    try:
        prepare(options.runtime_dir)
    except ValueError as exc:
        parser.error(str(exc))
    if not options.prepare_only:
        from net.wsgi import application
        print(f'Open http://127.0.0.1:{options.port}/ (Ctrl+C to stop)', flush=True)
        run_services(application, port=options.port, with_worker=options.with_worker)


if __name__ == '__main__':
    main()
