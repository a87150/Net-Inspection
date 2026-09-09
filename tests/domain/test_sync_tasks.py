from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from net.models import Domain_Controller_Config, TaskRun, Schedule
from net.models import Domain_Account
from django.utils import timezone
from django.core.exceptions import ValidationError
from net.domain.sync_tasks import enqueue_domain_sync, execute_domain_sync_target
from net.inspections.queue import claim_next_task, finish_task, cancel_task
from net.inspections.schedules import enqueue_due_schedules


class DomainSyncTaskTests(TestCase):
    def setUp(self):
        self.config = Domain_Controller_Config.objects.create(host='dc.test', base_dn='DC=test',
            bind_username='reader@test', bind_password='fixture-secret')
        self.client.force_login(get_user_model().objects.create_superuser('sync-admin', password='fixture'))

    def test_manual_sync_queues_without_ldap_in_web(self):
        with patch('net.domain.sync._connect', side_effect=AssertionError('Web must not connect')):
            response = self.client.post(reverse('domain_controller_settings'), {'action': 'sync'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TaskRun.objects.filter(task_type='domain_sync').count(), 1)
        task = TaskRun.objects.get()
        self.assertEqual(task.status, 'queued')
        self.assertEqual(task.target_runs.count(), 1)
        self.assertNotIn('fixture-secret', str(task.profile_snapshot) + str(task.parameters_snapshot))

    def test_schedule_saves_without_syncing_immediately(self):
        response = self.client.post(reverse('domain_controller_settings'), {
            'action': 'schedule', 'is_enabled': 'on', 'kind': 'interval',
            'interval_value': '2', 'interval_unit': 'hours',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Schedule.objects.count(), 1)
        self.assertFalse(TaskRun.objects.exists())
        schedule = Schedule.objects.get()
        self.assertEqual(schedule.domain_config_id, self.config.pk)
        self.assertIsNotNone(schedule.next_run_at)
        page = self.client.get(response['Location'])
        self.assertContains(page, 'id="domain-schedule-kind"')
        self.assertContains(page, '保存定时同步设置')
        self.assertContains(page, '域控同步任务')

    def test_due_schedule_enqueues_once_and_executes_local_snapshot(self):
        Schedule.objects.create(domain_config=self.config, kind='daily', daily_time='08:00',
                                next_run_at=timezone.now(), is_enabled=True)
        self.assertEqual(len(enqueue_due_schedules()), 1)
        self.assertEqual(enqueue_due_schedules(), [])
        claimed = claim_next_task('domain-fixture', 60)
        target = claimed.target_runs.get()
        snapshot = ([{'sAMAccountName': 'employee', 'displayName': '测试用户', 'userAccountControl': 512}], [], [])
        with patch('net.domain.sync_tasks.fetch_domain_snapshot', return_value=snapshot):
            execute_domain_sync_target(target, worker_id='domain-fixture')
        finished = finish_task(claimed.pk, 'domain-fixture')
        self.assertEqual(finished.status, 'success')
        self.assertTrue(Domain_Account.objects.filter(login_name='employee').exists())
        target.refresh_from_db()
        self.assertEqual(target.result_snapshot['accounts'], 1)
        page = self.client.get(reverse('task_detail', args=[claimed.pk]))
        self.assertContains(page, '1 个账号')

    def test_duplicate_manual_sync_is_rejected(self):
        enqueue_domain_sync()
        with self.assertRaises(ValidationError):
            enqueue_domain_sync()

    def test_cancellation_while_reading_prevents_local_writes(self):
        queued = enqueue_domain_sync()
        claimed = claim_next_task('domain-fixture', 60)
        def read_and_cancel(config):
            cancel_task(queued.pk)
            return ([{'sAMAccountName': 'must-not-save'}], [], [])
        with patch('net.domain.sync_tasks.fetch_domain_snapshot', side_effect=read_and_cancel):
            outcome = execute_domain_sync_target(claimed.target_runs.get(), worker_id='domain-fixture')
        self.assertTrue(outcome.stale)
        self.assertFalse(Domain_Account.objects.exists())

    def test_configuration_change_during_read_prevents_local_writes(self):
        enqueue_domain_sync()
        claimed = claim_next_task('domain-fixture', 60)
        def read_and_change(config):
            config.host = 'other.test'
            config.save()
            return ([{'sAMAccountName': 'must-not-save'}], [], [])
        with patch('net.domain.sync_tasks.fetch_domain_snapshot', side_effect=read_and_change):
            outcome = execute_domain_sync_target(claimed.target_runs.get(), worker_id='domain-fixture')
        self.assertEqual(outcome.status, 'failed')
        self.assertFalse(Domain_Account.objects.exists())

    def test_non_admin_cannot_queue_or_schedule(self):
        from tests.auth import login_reader
        login_reader(self.client)
        for action in ('sync', 'schedule'):
            response = self.client.post(reverse('domain_controller_settings'), {'action': action})
            self.assertEqual(response.status_code, 403)
        self.assertFalse(TaskRun.objects.exists())
        self.assertFalse(Schedule.objects.exists())


class DomainSyncWorkerTests(TransactionTestCase):
    def test_real_worker_dispatches_directory_sync_without_device_alerts(self):
        from net.inspections.worker import TaskWorker
        from net.models import AlertEvent
        Domain_Controller_Config.objects.create(host='dc.test', base_dn='DC=test', bind_username='reader')
        task = enqueue_domain_sync()
        with patch('net.domain.sync_tasks.fetch_domain_snapshot', return_value=([], [], [])):
            self.assertTrue(TaskWorker(worker_id='sync-worker').run_once())
        task.refresh_from_db()
        self.assertEqual(task.status, 'success')
        self.assertFalse(AlertEvent.objects.exists())
