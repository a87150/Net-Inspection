from unittest.mock import patch

from django.core.checks import Warning as CheckWarning
from django.db import connections
from django.test import SimpleTestCase

from net.infrastructure import tzcheck


class _Cursor:
    def __init__(self, result):
        self.result = result

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args, **kwargs):
        self.result

    def fetchone(self):
        return (self.result,)


class MySqlTimeZoneTableCheckTests(SimpleTestCase):
    databases = {'default'}

    def _check(self, vendor, result, fail=False):
        connection = connections['default']
        cursor = _Cursor(result)

        class Broken:
            def execute(self, *args, **kwargs):
                if fail:
                    raise RuntimeError('no database')

            def fetchone(self):
                return (None,)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch.object(connection, 'cursor',
                          return_value=Broken() if fail else cursor):
            with patch.object(connection, 'vendor', vendor):
                return tzcheck.check_mysql_timezone_tables(None)

    def test_missing_tables_raise_a_warning_with_the_import_command(self):
        issues = self._check('mysql', None)

        self.assertEqual(len(issues), 1)
        self.assertIsInstance(issues[0], CheckWarning)
        self.assertEqual(issues[0].id, 'net.W001')
        self.assertIn('mariadb-tzinfo-to-sql', str(issues[0].hint))

    def test_loaded_tables_are_silent(self):
        self.assertEqual(self._check('mysql', '2026-01-15 12:00:00'), [])

    def test_other_engines_are_not_checked(self):
        self.assertEqual(self._check('postgresql', None), [])

    def test_an_unreachable_database_is_not_this_checks_business(self):
        self.assertEqual(self._check('mysql', None, fail=True), [])