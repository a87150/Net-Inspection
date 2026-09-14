from unittest.mock import patch
import sqlite3

from django.contrib.auth import get_user_model
from django.db import OperationalError, connection, transaction
from django.test import TransactionTestCase
from django.urls import reverse

from net.domain.tasks import enqueue_domain_operation
from net.models import Domain_Account, Domain_Controller_Config, DomainOperation, TaskRun, TaskTargetRun


class DomainEnqueueLockingTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'sqlite':
            self.skipTest('SQLite contention regression')
        self.user = get_user_model().objects.create_user(username='lock-admin', is_staff=True)
        Domain_Controller_Config.objects.create(base_dn='DC=example,DC=test')
        self.account = Domain_Account.objects.create(login_name='fixture', is_active=False,
            distinguished_name='CN=Fixture,OU=Users,DC=example,DC=test')

    def enqueue(self):
        return enqueue_domain_operation(requested_by=self.user, object_type='account', action='enable',
            target_ids=[str(self.account.pk)], parameters={})

    def test_acquires_sqlite_write_lock_before_reading_task_state(self):
        sql = []
        def capture(execute, statement, params, many, context):
            sql.append(statement)
            return execute(statement, params, many, context)
        with connection.execute_wrapper(capture):
            operation = self.enqueue()
        statements = [item for item in sql if item.lstrip().upper().startswith(('SELECT', 'UPDATE', 'INSERT'))]
        self.assertTrue(statements[0].lstrip().upper().startswith('UPDATE'), statements[0])
        self.assertEqual(operation.task.status, 'queued')

    def test_transient_busy_rolls_back_and_recreates_one_complete_task(self):
        original = TaskTargetRun.save
        attempts = []
        def fail_after_insert(target, *args, **kwargs):
            original(target, *args, **kwargs)
            attempts.append(target.pk)
            if len(attempts) == 1:
                raise OperationalError('database is locked')
        with patch.object(TaskTargetRun, 'save', fail_after_insert):
            operation = self.enqueue()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(TaskRun.objects.count(), 1)
        self.assertEqual(DomainOperation.objects.count(), 1)
        self.assertEqual(TaskTargetRun.objects.count(), 1)
        self.assertEqual(operation.task.target_runs.get().target_id, str(self.account.pk))

    def test_independent_sqlite_writer_cannot_interleave_after_first_read(self):
        other = sqlite3.connect(connection.settings_dict['NAME'], uri=True, timeout=0)
        attempted = []
        def contend(execute, statement, params, many, context):
            if not attempted and statement.lstrip().upper().startswith('SELECT'):
                try:
                    other.execute('BEGIN IMMEDIATE')
                except sqlite3.OperationalError as exc:
                    self.assertIn(exc.sqlite_errorcode & 255, (5, 6))
                    attempted.append('blocked')
                else:
                    attempted.append('acquired')
            return execute(statement, params, many, context)
        try:
            with connection.execute_wrapper(contend):
                operation = self.enqueue()
            self.assertEqual(attempted, ['blocked'])
            self.assertEqual(operation.task.target_runs.count(), 1)
        finally:
            other.rollback()
            other.close()

    def test_persistent_busy_is_bounded_and_leaves_no_partial_task(self):
        with patch.object(TaskRun, 'save', side_effect=OperationalError('database is locked')) as save:
            with self.assertRaises(OperationalError):
                self.enqueue()
        self.assertEqual(save.call_count, 3)
        self.assertEqual(TaskRun.objects.count(), 0)
        self.assertEqual(DomainOperation.objects.count(), 0)

    def test_other_errors_and_outer_transactions_are_not_replayed(self):
        for error in ('no such column: broken', 'database is locked'):
            with self.subTest(error=error), patch.object(TaskRun, 'save', side_effect=OperationalError(error)) as save:
                with self.assertRaises(OperationalError), transaction.atomic():
                    self.enqueue()
                self.assertEqual(save.call_count, 1)

    def test_busy_post_shows_actionable_message_instead_of_http_500(self):
        self.client.force_login(self.user)
        with patch('index.domain.operations.enqueue_domain_operation', side_effect=OperationalError('database is locked')):
            response = self.client.post(reverse('domain_operation_create'), {
                'object_type': 'account', 'action': 'enable', 'target_ids': [str(self.account.pk)],
            }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '数据库正忙')
        self.assertContains(response, '未创建')
        self.assertEqual(TaskRun.objects.count(), 0)
