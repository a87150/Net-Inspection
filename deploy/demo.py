r"""Launch the persistent, loopback-only demo without touching db.sqlite3.

Windows: .\.venv\Scripts\python.exe -m deploy.demo
Linux:   ./.venv/bin/python -m deploy.demo
Never starts a Worker or contacts providers/devices. This is not a production DB.
"""
import argparse
import os
from pathlib import Path


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
    print('Offline seeded records only. No Worker started; do not run a real Worker against this demo.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir', type=Path, default=BASE_DIR / 'demo-runtime')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--prepare-only', action='store_true')
    options = parser.parse_args()
    if not 1 <= options.port <= 65535:
        parser.error('port must be between 1 and 65535')
    try:
        prepare(options.runtime_dir)
    except ValueError as exc:
        parser.error(str(exc))
    if not options.prepare_only:
        from waitress import serve
        from net.wsgi import application
        print(f'Open http://127.0.0.1:{options.port}/ (Ctrl+C to stop)', flush=True)
        serve(application, host='127.0.0.1', port=options.port, threads=4)


if __name__ == '__main__':
    main()
