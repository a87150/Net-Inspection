r"""Launch the persistent, loopback-only demo without touching db.sqlite3.

Windows: .\.venv\Scripts\python.exe -m deploy.demo
Linux:   ./.venv/bin/python -m deploy.demo
Starts the task Worker by default. Use --no-worker for an offline display-only demo.
This is not a production DB.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys


BASE_DIR = Path(__file__).resolve().parent.parent


def prepare(runtime):
    from net import require_runtime
    require_runtime()
    runtime = runtime.resolve()
    marker = runtime / '.network-inspection-demo'
    if runtime.exists() and any(runtime.iterdir()) and not marker.is_file():
        raise ValueError('Refusing an unowned nonempty demo runtime directory.')
    runtime.mkdir(parents=True, exist_ok=True)
    marker.write_text('Network Inspection isolated demo v1\n', encoding='utf-8')
    for name in ('logs', 'incoming', 'incoming/processed', 'incoming/failed'):
        (runtime / name).mkdir(parents=True, exist_ok=True)
    # Deliberately override inherited production/old-database settings.
    os.environ.update({
        'DJANGO_SETTINGS_MODULE': 'net.settings', 'DJANGO_DEBUG': 'false',
        'DJANGO_ALLOWED_HOSTS': '127.0.0.1,localhost',
        'DJANGO_SECRET_KEY': 'DEMO-ONLY-NOT-FOR-PRODUCTION-LOCAL-ISOLATED-DATABASE',
        'DB_ENGINE': 'sqlite', 'DJANGO_SQLITE_PATH': str(runtime / 'demo.sqlite3'),
        'DJANGO_STATIC_ROOT': str(runtime / 'staticfiles'),
    })
    import django
    django.setup()
    from django.core.management import call_command
    call_command('migrate', interactive=False, verbosity=0)
    if not (runtime / '.seeded').exists():
        call_command('seed_demo_data')
        (runtime / '.seeded').write_text('Seed complete; subsequent starts preserve edits.\n', encoding='utf-8')
    call_command('collectstatic', interactive=False, verbosity=0)
    print(f'Demo database: {runtime / "demo.sqlite3"}', flush=True)
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
