import threading
from unittest.mock import patch

from django.db import OperationalError, connection
from django.test import TransactionTestCase

from net.models import Domain_Controller_Config, TaskRun
from net.domain.sync_tasks import enqueue_domain_sync
from net.inspections.queue import claim_next_task, renew_lease
from net.inspections.worker import TaskWorker


class SQLiteWorkerBusyTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'sqlite':
            self.skipTest('SQLite-specific lock recovery')

    def test_heartbeat_retries_transient_write_lock_and_renews(self):
        Domain_Controller_Config.objects.create(host='dc.test', base_dn='DC=test', bind_username='reader')
        enqueue_domain_sync()
        task = claim_next_task('busy-test', 60)
        before = task.lease_expires_at
        busy = True

        def locked_once(execute, sql, params, many, context):
            nonlocal busy
            if busy and sql.lstrip().upper().startswith('UPDATE'):
                busy = False
                raise OperationalError('database is locked')
            return execute(sql, params, many, context)

        with connection.execute_wrapper(locked_once):
            self.assertTrue(renew_lease(task.pk, 'busy-test', 60))
        task.refresh_from_db()
        self.assertGreater(task.lease_expires_at, before)
        self.assertEqual(task.status, TaskRun.Status.RUNNING)

    def test_constant_lock_is_bounded(self):
        Domain_Controller_Config.objects.create(host='dc.test', base_dn='DC=test', bind_username='reader')
        task = enqueue_domain_sync()
        claim_next_task('busy-test', 60)
        with connection.execute_wrapper(lambda *args: (_ for _ in ()).throw(OperationalError('database is locked'))):
            with self.assertRaises(OperationalError):
                renew_lease(task.pk, 'busy-test', 60)

    def test_service_continues_after_lock_and_observes_stop(self):
        worker = TaskWorker(poll_seconds=0.01)
        stop = threading.Event()
        calls = []

        def poll(event):
            calls.append(True)
            if len(calls) == 1:
                raise OperationalError('database is locked')
            event.set()
            return False

        with patch.object(worker, 'run_once', side_effect=poll):
            worker.run_forever(stop)
        self.assertTrue(stop.is_set())

    def test_service_does_not_hide_other_database_errors(self):
        worker = TaskWorker(poll_seconds=0.01)
        with patch.object(worker, 'run_once', side_effect=OperationalError('no such table: missing')):
            with self.assertRaisesRegex(OperationalError, 'no such table'):
                worker.run_forever()
