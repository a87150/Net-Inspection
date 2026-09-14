import threading
from django.db import OperationalError, connection
from django.test import TransactionTestCase
from net.models import Computer
from net.devices.pc.logs import _isolated_retry, _import_database_guard
from net.inspections.executor import _database_guard


class PCImportLockRetryTests(TransactionTestCase):
    def test_sqlite_busy_rolls_back_before_retry(self):
        if connection.vendor != 'sqlite':
            self.skipTest('SQLite-specific retry')
        attempts = []

        def persist():
            row = Computer.objects.create(computer_name='retry-transaction')
            attempts.append(True)
            if len(attempts) == 1:
                raise OperationalError('database is locked')
            return row

        row = _isolated_retry(persist)
        self.assertEqual(Computer.objects.filter(computer_name='retry-transaction').count(), 1)
        self.assertEqual(Computer.objects.get().pk, row.pk)

    def test_import_waits_for_worker_write_guard(self):
        if connection.vendor != 'sqlite':
            self.skipTest('SQLite-specific guard')
        started, entered = threading.Event(), threading.Event()

        def import_write():
            started.set()
            with _import_database_guard():
                entered.set()

        with _database_guard():
            thread = threading.Thread(target=import_write)
            thread.start()
            self.assertTrue(started.wait(1))
            blocked = not entered.wait(0.1)
        thread.join(2)
        self.assertTrue(blocked, 'Import writes must wait for the Worker write transaction')
        self.assertTrue(entered.is_set())

    def test_unrelated_database_error_is_not_retried(self):
        attempts = []

        def persist():
            attempts.append(True)
            raise OperationalError('no such column: missing')

        with self.assertRaises(OperationalError):
            _isolated_retry(persist)
        self.assertEqual(len(attempts), 1)
