"""Shared production deployment preparation. Never seeds or resets a database."""
import argparse
import base64
import getpass
import os
from pathlib import Path
import secrets
import socket
import sys

ROOT = Path(__file__).resolve().parent.parent
KEYS = ('DOMAIN_OPERATION_ENCRYPTION_KEY', 'PC_LOG_SOURCE_ENCRYPTION_KEY', 'DEVICE_BACKUP_ENCRYPTION_KEY')


def configure(path, *, interactive=True):
    if path.exists():
        print(f'Using existing configuration: {path}')
        return
    if not interactive:
        raise ValueError('Configuration is missing. Run interactively once or provide --env-file.')
    def ask(label, default):
        return input(f'{label} [{default}]: ').strip() or default
    hosts = ask('Allowed website host/IP (comma separated, no http:// or port)', f'{socket.gethostname()},127.0.0.1,localhost')
    values = {
        'DJANGO_SECRET_KEY': secrets.token_urlsafe(64), 'DJANGO_DEBUG': 'false',
        'DJANGO_ALLOWED_HOSTS': hosts, 'DB_ENGINE': 'mysql',
        'DB_HOST': ask('Existing MySQL/MariaDB host', '127.0.0.1'),
        'DB_PORT': ask('Database port', '3306'), 'DB_NAME': ask('Existing database name', 'net_inspection'),
        'DB_USER': ask('Database user', 'net_inspection'),
        'DB_PASSWORD': getpass.getpass('Database password (hidden): '),
        'NET_PAGE_CACHE_ENABLED': 'false', 'NET_TRUST_PROXY_HEADERS': 'false',
        'WEB_LISTEN': ask('Web listen address', '0.0.0.0:8000'), 'WEB_THREADS': '4',
        'WORKER_THREADS': '4', 'WORKER_POLL_SECONDS': '5', 'WORKER_LEASE_SECONDS': '60',
        'DJANGO_STATIC_ROOT': str(ROOT / 'runtime' / 'deployment' / 'staticfiles'),
        **{key: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode() for key in KEYS},
    }
    if not values['DB_PASSWORD']:
        raise ValueError('Database password cannot be empty. No configuration was written.')
    lines = []
    for key, value in values.items():
        value = str(value).replace('\\', '\\\\').replace("'", "\\'")
        if '\n' in value or '\r' in value or '\x00' in value:
            raise ValueError(f'Unsupported newline in {key}')
        lines.append(f"{key}='{value}'\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create preserves an existing environment even if two installers race.
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.writelines(lines)
    print(f'Created protected configuration: {path}; back it up with the database.')


def load_config(path):
    if not path.is_file():
        raise ValueError(f'Configuration not found: {path}')
    from dotenv import dotenv_values
    from dotenv.parser import parse_stream
    with path.open(encoding='utf-8-sig') as stream:
        for binding in parse_stream(stream):
            if binding.error:
                raise ValueError(f'Invalid environment syntax near line {binding.original.line}; values were not printed.')
    values = dotenv_values(path, interpolate=False, encoding='utf-8-sig')
    if not values.get('DB_ENGINE'):
        raise ValueError('Set DB_ENGINE explicitly; refusing an implicit SQLite fallback.')
    if values['DB_ENGINE'].lower() not in {'mysql', 'sqlite'}:
        raise ValueError('DB_ENGINE must be mysql or sqlite.')
    # Service/maintenance commands use the selected file, not the invoking shell database.
    for name in list(os.environ):
        if name.startswith(('DB_', 'DJANGO_', 'NET_', 'DOMAIN_OPERATION_ENCRYPTION_KEY',
                            'PC_LOG_SOURCE_ENCRYPTION_KEY', 'DEVICE_BACKUP_', 'WEB_', 'WORKER_')):
            os.environ.pop(name, None)
    os.environ.update({key: value for key, value in values.items() if value is not None})
    os.environ['NET_ENV_FILE'] = str(path)
    os.environ['DJANGO_SETTINGS_MODULE'] = 'net.settings'
    from net.infrastructure.environment import load_environment
    load_environment()
    if os.environ.get('DJANGO_DEBUG', '').lower() not in {'false', '0', 'no'}:
        raise ValueError('Production deployment requires DJANGO_DEBUG=false in the environment file.')
    secret = os.environ.get('DJANGO_SECRET_KEY', '')
    if len(secret) < 32 or any(word in secret.lower() for word in ('change-me', 'change_me', 'replace-with', 'django-insecure', 'demo-only')):
        raise ValueError('Configure a stable, non-placeholder DJANGO_SECRET_KEY (32+ characters).')
    from cryptography.fernet import Fernet
    for name in KEYS:
        try:
            Fernet(os.environ.get(name, '').encode('ascii'))
        except (ValueError, UnicodeError):
            raise ValueError(f'{name} is missing/invalid. Restore the existing key; do not regenerate keys for existing data.') from None
    hosts = os.environ.get('DJANGO_ALLOWED_HOSTS', '').split(',')
    if not any(host.strip() for host in hosts) or any(host.strip() == '*' or '://' in host or '/' in host for host in hosts):
        raise ValueError('DJANGO_ALLOWED_HOSTS must contain explicit hostnames/IPs, without schemes, paths or wildcard *.')
    if values['DB_ENGINE'].lower() == 'mysql' and not all(os.environ.get(key) for key in ('DB_HOST', 'DB_NAME', 'DB_USER', 'DB_PASSWORD')):
        raise ValueError('Configure DB_HOST, DB_NAME, DB_USER and DB_PASSWORD (or a service-accessible keyring).')
    if values['DB_ENGINE'].lower() == 'sqlite':
        sqlite_path = os.environ.get('DJANGO_SQLITE_PATH', '')
        if not sqlite_path:
            raise ValueError('SQLite deployment requires an explicit DJANGO_SQLITE_PATH; refusing to select another database implicitly.')
        sqlite_path = Path(sqlite_path)
        os.environ['DJANGO_SQLITE_PATH'] = str(sqlite_path if sqlite_path.is_absolute() else ROOT / sqlite_path)
    listen_address()
    for key, default, minimum, maximum in (
        ('WEB_THREADS', 4, 1, 64), ('WORKER_THREADS', 4, 1, 64),
        ('WORKER_POLL_SECONDS', 5, 1, 3600), ('WORKER_LEASE_SECONDS', 60, 10, 86400),
    ):
        number = int(os.environ.get(key, default))
        if not minimum <= number <= maximum:
            raise ValueError(f'{key} must be between {minimum} and {maximum}.')
    runtime = ROOT / 'runtime' / 'deployment'
    (runtime / 'logs').mkdir(parents=True, exist_ok=True)
    return runtime


def listen_address():
    value = os.environ.get('WEB_LISTEN', '0.0.0.0:8000')
    host, separator, port = value.rpartition(':')
    if not separator or not host or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError('WEB_LISTEN must be host:port, for example 0.0.0.0:8000.')
    return host.strip('[]'), int(port)


def preflight(path):
    load_config(path)
    host, port = listen_address()
    family = socket.AF_INET6 if ':' in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            raise ValueError(f'Web port {port} is occupied/unavailable. Stop the existing deployment first.') from None
    print(f'Configuration and listen port validated; environment: {path}')


def prepare(path, *, interactive=True):
    load_config(path)
    import django
    django.setup()
    from django.conf import settings
    from django.db import connection
    from django.core.management import call_command
    database = settings.DATABASES['default']
    print(f'Database: {database["ENGINE"]} / {database["HOST"] if "HOST" in database else ""} / {database["NAME"]}')
    try:
        connection.ensure_connection()
    except Exception:
        raise ValueError('Database connection failed. Verify the database exists, credentials, network and service-account access; no reset was attempted.') from None
    call_command('check')
    call_command('migrate', interactive=False)
    call_command('collectstatic', interactive=False, verbosity=0)
    from django.contrib.auth import get_user_model
    admins = get_user_model().objects.filter(is_superuser=True, is_active=True).only('password')
    if not any(admin.has_usable_password() for admin in admins.iterator()):
        if not interactive:
            raise ValueError('No active administrator. Rerun interactively to create one; services have not been started.')
        print('Create the initial website administrator (not the database user):')
        call_command('createsuperuser', interactive=True)
    print('Database, static files and administrator ready. No demonstration data was loaded.')


def probe(path):
    load_config(path)
    from urllib.request import Request, urlopen
    import time
    host, port = listen_address()
    host = '127.0.0.1' if host == '0.0.0.0' else '::1' if host == '::' else host
    address = f'[{host}]' if ':' in host else host
    allowed = next(v.strip().lstrip('.') for v in os.environ['DJANGO_ALLOWED_HOSTS'].split(',') if v.strip())
    for attempt in range(30):
        try:
            # A login page and a static asset do not enqueue or execute tasks.
            for route in ('/login/', '/static/app/css/style.css'):
                with urlopen(Request(f'http://{address}:{port}{route}', headers={'Host': allowed}), timeout=2) as response:
                    if response.status != 200:
                        raise OSError('Unexpected response')
            print(f'Web and static HTTP checks passed on port {port}.')
            return
        except (OSError, ValueError):
            if attempt == 29:
                raise ValueError('Web health check failed; inspect runtime/deployment/logs and the configured service account.') from None
            time.sleep(1)


def processes():
    """Linux read-only check: do not alter unrelated processes."""
    if sys.platform != 'linux':
        return
    for directory in Path('/proc').iterdir():
        if not directory.name.isdecimal() or int(directory.name) == os.getpid():
            continue
        try:
            command = (directory / 'cmdline').read_bytes().decode(errors='replace').split('\0')
            cwd = (directory / 'cwd').resolve()
        except (OSError, PermissionError):
            continue
        if not any(part in {'deploy.demo', 'deploy.service', 'run_task_worker', 'waitress', 'runserver'} for part in command):
            continue
        if cwd == ROOT or any(str(ROOT) in part for part in command):
            raise ValueError(f'Existing project process PID {directory.name}; stop its original launcher before deploying.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['configure', 'preflight', 'prepare', 'probe', 'processes'])
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--non-interactive', action='store_true')
    args = parser.parse_args()
    os.chdir(ROOT)
    try:
        if args.action == 'processes':
            processes()
        elif args.action in {'configure', 'prepare'}:
            globals()[args.action](args.env_file.resolve(), interactive=not args.non_interactive)
        else:
            globals()[args.action](args.env_file.resolve())
    except (ValueError, OSError) as exc:
        print(f'Deployment stopped: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
