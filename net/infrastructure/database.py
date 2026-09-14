"""SQLite tuning without opening connections during application startup."""
import math
import sys
from contextlib import closing
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import CommandError


def sqlite_timeout(value):
    """Reject unbounded waits, NaN and invalid configuration at settings load."""
    try:
        timeout = float(value)
    except (ValueError, TypeError):
        raise ImproperlyConfigured('NET_SQLITE_TIMEOUT must be finite seconds in (0, 60].') from None
    if not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ImproperlyConfigured('NET_SQLITE_TIMEOUT must be finite seconds in (0, 60].')
    return timeout


def initialize_sqlite_wal(connection, *, apply=False):
    """Explicit maintenance only; never downgrade WAL or weaken synchronous mode."""
    if connection.vendor != 'sqlite':
        return {'status': 'skipped', 'reason': 'not SQLite'}
    name = str(connection.settings_dict['NAME'])
    if any(arg in {'test', 'testserver'} or 'pytest' in arg for arg in sys.argv):
        return {'status': 'skipped', 'reason': 'test process'}
    # URI filenames are deliberately unsupported, including memory/read-only URIs.
    if not name or name == ':memory:' or name.startswith('file:'):
        return {'status': 'skipped', 'reason': 'memory database or SQLite URI'}
    if not Path(name).is_file():
        raise CommandError('SQLite maintenance requires an existing database file.')
    if apply and not getattr(settings, 'NET_SQLITE_WAL_ENABLED', False):
        raise CommandError('Set NET_SQLITE_WAL_ENABLED=true before applying WAL maintenance.')
    if connection.in_atomic_block or not connection.get_autocommit():
        raise CommandError('WAL maintenance must run outside a transaction.')
    with closing(connection.cursor()) as cursor:
        cursor.execute('PRAGMA journal_mode')
        mode = cursor.fetchone()[0].lower()
        if apply and mode != 'wal':
            cursor.execute('PRAGMA journal_mode=WAL')
            mode = cursor.fetchone()[0].lower()
            if mode != 'wal':
                raise CommandError(f'SQLite refused WAL; journal_mode remains {mode}.')
    return {'status': 'applied' if apply else 'preview', 'journal_mode': mode}
