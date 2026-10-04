"""Throwaway databases on the configured engine, for tests that must isolate data."""
import os
from contextlib import contextmanager
from uuid import uuid4

from django.conf import settings
from django.db import ConnectionHandler


def credentials() -> dict:
    """The engine and login the running process already uses."""
    base = settings.DATABASES['default']
    return {
        'DB_ENGINE': 'postgresql' if 'postgresql' in base['ENGINE'] else 'mysql',
        'DB_USER': base.get('USER', ''), 'DB_PASSWORD': base.get('PASSWORD', ''),
        'DB_HOST': base.get('HOST', ''), 'DB_PORT': str(base.get('PORT', '')),
    }


def connect(name):
    """An independent connection to one database; leaves the default connection alone."""
    base = settings.DATABASES['default']
    return ConnectionHandler({'default': {**base, 'NAME': name}})['default']


def subprocess_environment(name, env_file, **extra):
    """Environment for a child process that must use the isolated database.

    A NET_ENV_FILE that does not exist means .env is never loaded, so the credentials
    have to be passed explicitly; otherwise the child falls back to settings.py's
    placeholder account and cannot connect.
    """
    environment = {**os.environ, **credentials(), 'NET_ENV_FILE': str(env_file),
                  'DB_TEST_NAME': name, **extra}
    return environment


def _create(name):
    """Create the database through a keeper connection; the target does not exist yet."""
    base = settings.DATABASES['default']
    keeper = 'postgres' if 'postgresql' in base['ENGINE'] else 'mysql'
    connection = ConnectionHandler({'default': {**base, 'NAME': keeper}})['default']
    try:
        with connection.cursor() as cursor:
            if connection.vendor == 'postgresql':
                cursor.execute('CREATE DATABASE ' + chr(34) + name + chr(34))
            else:
                cursor.execute('CREATE DATABASE ' + chr(96) + name + chr(96)
                               + ' CHARACTER SET utf8mb4 COLLATE utf8mb4_bin')
    finally:
        connection.close()


@contextmanager
def isolated_database():
    """Hand out a throwaway database name, then drop it.

    SQLite cannot apply net.0044_access_records: its CheckConstraint spans joined
    columns, so Django has to remake the table and that fails there. Tests that need
    a migrated, isolated database therefore use the engine the project ships.
    """
    name = 'net-archtest-' + uuid4().hex[:8]
    _create(name)
    try:
        yield name
    finally:
        base = settings.DATABASES['default']
        # DROP DATABASE needs another database attached: neither MariaDB nor
        # PostgreSQL accepts an empty name. Never connect to the isolated database
        # here; if the child never created it, that error would mask the real failure.
        keeper = 'postgres' if 'postgresql' in base['ENGINE'] else 'mysql'
        connection = ConnectionHandler({'default': {**base, 'NAME': keeper}})['default']
        try:
            with connection.cursor() as cursor:
                if connection.vendor == 'postgresql':
                    cursor.execute('DROP DATABASE IF EXISTS ' + chr(34) + name + chr(34) + ' WITH (FORCE)')
                else:
                    cursor.execute('DROP DATABASE IF EXISTS ' + chr(96) + name + chr(96))
        except Exception:
            pass
        finally:
            connection.close()


def scalars(connection, sql):
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return [row[0] for row in cursor.fetchall()]