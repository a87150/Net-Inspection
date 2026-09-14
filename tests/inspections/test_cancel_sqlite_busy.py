from django.contrib.messages import get_messages
from django.db import OperationalError, connection, transaction
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from net.models import ComputerAnalysisProfile, TaskRun
from net.inspections.queue import cancel_task, claim_next_task, enqueue_task, renew_lease
from net.inspections.state import save_target
from tests.auth import login_admin
from tests.devices.pc.helpers import create_log_file


class CancelSQLiteBusyTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'sqlite':
            self.skipTest('SQLite lock handling')
        profile = ComputerAnalysisProfile.objects.create(name='Cancel analysis', analysis_items=['resource'])
        logs = [create_log_file(source_path=f'cancel-{i}.json', content_hash=f'{i:064x}',
            modified_at=timezone.now(), import_status='imported') for i in range(3)]
        self.task = enqueue_task(profile, [log.pk for log in logs], 'manual')
        claim_next_task('cancel-worker', 60)
        self.finished = self.task.target_runs.order_by('pk').first()
        self.finished.status = 'success'
        self.finished.finished_at = timezone.now()
        self.finished.result_snapshot = {'kept': 'completed evidence'}
        save_target(self.finished, {'status', 'finished_at', 'result_snapshot'})

    def test_transient_lock_retries_entire_transaction_preserving_completed_results(self):
        writes = 0
        def lock_second_target_write(execute, sql, params, many, context):
            nonlocal writes
            if sql.startswith('UPDATE "net_tasktargetrun"'):
                writes += 1
                if writes == 2:
                    raise OperationalError('database is locked')
            return execute(sql, params, many, context)
        with connection.execute_wrapper(lock_second_target_write):
            cancelled = cancel_task(self.task.pk)
        self.assertEqual(cancelled.status, 'cancelled')
        self.assertEqual(cancelled.completed_targets, 3)
        self.assertEqual(cancelled.successful_targets, 1)
        self.assertEqual(cancelled.failed_targets, 0)
        self.assertEqual(self.task.target_runs.filter(status='cancelled').count(), 2)
        self.finished.refresh_from_db()
        self.assertEqual(self.finished.result_snapshot, {'kept': 'completed evidence'})
        self.assertIsNone(cancelled.lease_expires_at)
        self.assertFalse(renew_lease(self.task.pk, 'cancel-worker', 60))

    def test_persistent_lock_returns_actionable_message_not_500_or_success(self):
        login_admin(self.client)
        writes = 0
        def lock_writes(execute, sql, params, many, context):
            nonlocal writes
            if sql.startswith('UPDATE "net_task'):
                writes += 1
                raise OperationalError('database is locked')
            return execute(sql, params, many, context)
        with connection.execute_wrapper(lock_writes):
            response = self.client.post(reverse('task_cancel', args=[self.task.pk]))
        self.assertEqual(response.status_code, 302)
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any('数据库正忙' in str(message) for message in messages))
        self.assertFalse(any(message.tags == 'success' for message in messages))
        self.assertLessEqual(writes, 3)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'running')
        self.assertEqual(self.task.target_runs.filter(status='queued').count(), 2)

    def test_other_database_errors_are_not_hidden_or_retried(self):
        writes = 0
        def broken_write(execute, sql, params, many, context):
            nonlocal writes
            if sql.startswith('UPDATE "net_task'):
                writes += 1
                raise OperationalError('no such table: missing')
            return execute(sql, params, many, context)
        with connection.execute_wrapper(broken_write):
            with self.assertRaisesRegex(OperationalError, 'no such table'):
                cancel_task(self.task.pk)
        self.assertEqual(writes, 1)

    def test_does_not_retry_inside_callers_transaction(self):
        writes = 0
        def locked(execute, sql, params, many, context):
            nonlocal writes
            if sql.startswith('UPDATE "net_task'):
                writes += 1
                raise OperationalError('database is locked')
            return execute(sql, params, many, context)
        with transaction.atomic(), connection.execute_wrapper(locked):
            with self.assertRaises(OperationalError):
                cancel_task(self.task.pk)
        self.assertEqual(writes, 1)
