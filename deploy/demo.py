r"""Launch the loopback Web and Worker using the shared root .env configuration.

Windows: .\.venv\Scripts\python.exe -m deploy.demo
Linux:   ./.venv/bin/python -m deploy.demo
Starts the task Worker by default. Use --no-worker for an offline display-only demo.
Always runs against the isolated SQLite demo database: the shared root .env is deliberately
overridden so a demo can never point at a production MySQL/MariaDB database.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys


BASE_DIR = Path(__file__).resolve().parent.parent


def demo_engine():
    """Which server the demo runs on: whatever .env says."""
    return os.getenv('DB_ENGINE', 'mysql').strip().lower()


def demo_sqlite_path(runtime):
    """Where an SQLite demo keeps its database; the directory must already exist."""
    path = Path(runtime) / 'demo.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def demo_database():
    """The demo never touches the production database, on any backend."""
    return os.getenv('DB_TEST_NAME') or os.getenv('DB_DEMO_NAME') or 'net-test'


def configure_environment(runtime):
    """Force the isolated demo environment; the shared .env must never win."""
    os.environ.update({
        'DJANGO_SETTINGS_MODULE': 'net.settings', 'DJANGO_DEBUG': 'false',
        'DJANGO_ALLOWED_HOSTS': '127.0.0.1,localhost',
        'DJANGO_SECRET_KEY': 'DEMO-ONLY-NOT-FOR-PRODUCTION-LOCAL-ISOLATED-DATABASE',
        # DB_NAME points demo at the test database; host/user/password still
        # come from .env, which load_environment() read before this ran.
        # DB_ENGINE is inherited so a demo can run on MariaDB or PostgreSQL, but
        # SQLite is refused: the demo has to be a real server, not a local file.
        'DB_ENGINE': demo_engine(), 'DB_NAME': demo_database(),
        # 别再写死空串：DB_ENGINE 可能是 sqlite，空路径会变成 sqlite3.connect('')，
        # 直接报 unable to open database file。默认落到隔离的 runtime 目录里；
        # configure_environment 跑在 prepare() 建目录之前，所以父目录得先建出来。
        'DJANGO_SQLITE_PATH': os.getenv('DJANGO_SQLITE_PATH') or demo_sqlite_path(runtime),
        'DJANGO_STATIC_ROOT': str(runtime / 'staticfiles'),
    })


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
    from django.db import connections
    from django.db.utils import OperationalError
    connection = connections['default']
    try:
        connection.ensure_connection()
    except OperationalError as exc:
        # migrate cannot create the database itself. MariaDB says 1049 and
        # PostgreSQL SQLSTATE 3D000; both mean the database is not there yet.
        text = str(exc)
        if '1049' not in text and '3D000' not in text:
            raise
        name = connection.settings_dict['NAME']
        connection.close()
        connection.settings_dict['NAME'] = ''
        with connection.cursor() as cursor:
            if connection.vendor == 'postgresql':
                # PostgreSQL 没有库级字符集，只能定排序规则。C collation 让中文名/DN
                # 的比较顺序跟 MariaDB 的 utf8mb4_bin 一样按字节序，两边结果才对得上。
                cursor.execute(
                    'CREATE DATABASE "%s" TEMPLATE template0 ENCODING \'UTF8\' '
                    "LC_COLLATE 'C' LC_CTYPE 'C'" % name.replace('"', '""'),
                )
            else:
                cursor.execute(
                    'CREATE DATABASE `%s` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin'
                    % name.replace('`', '``'),
                )
        connection.settings_dict['NAME'] = name
        connection.close()
        print('Created database: ' + name, flush=True)
    call_command('migrate', interactive=False, verbosity=0)
    if not (runtime / '.seeded').exists():
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
