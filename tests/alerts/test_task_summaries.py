from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from net.models import (AlertChannel, AlertPolicy, AlertEvent, AlertDelivery,
                        InspectionProfile, Server, Server_Inspection, TaskRun, TaskTargetRun)
from net.inspections.queue import enqueue_task
from net.alerts.service import process_persisted_target


class TaskSummaryTests(TestCase):
    def setUp(self):
        self.profile = InspectionProfile.objects.create(name='每日服务器巡检', device_type='server', selected_items=['cpu'])
        self.channel = AlertChannel.objects.create(name='summary', channel_type='feishu',
            settings={'webhook_url': 'https://open.feishu.test/hook'})
        policy = AlertPolicy.objects.create(name='default', is_default=True, mode='override')
        policy.channels.add(self.channel)

    def task(self, statuses=('failed', 'success'), finished=True):
        if not hasattr(self, 'assets'):
            self.assets = [Server.objects.create(name=f'server-{i}', ip=f'192.0.2.{i+10}') for i in range(len(statuses))]
        assets = self.assets
        task = enqueue_task(self.profile, [asset.pk for asset in assets], 'manual')
        now = timezone.now()
        for target, status in zip(task.target_runs.order_by('pk'), statuses):
            record = Server_Inspection.objects.create(server_id=target.target_id, task_target=target,
                status=status, details={}, summary='CPU collection result', is_reachable=True)
            TaskTargetRun.objects.filter(pk=target.pk).update(status=status, finished_at=now,
                result_type='server_inspection', result_id=str(record.pk))
        if finished:
            TaskRun.objects.filter(pk=task.pk).update(status='partial' if 'failed' in statuses else 'success',
                active_scope_key=None, finished_at=now, progress=100, completed_targets=len(statuses),
                successful_targets=statuses.count('success'), failed_targets=statuses.count('failed'))
        task.refresh_from_db()
        return task

    def test_target_processing_records_state_without_device_deliveries(self):
        task = self.task()
        for target in task.target_runs.all():
            process_persisted_target(target)
        self.assertTrue(AlertEvent.objects.filter(task=task, event_type='abnormal').exists())
        self.assertFalse(AlertDelivery.objects.exists())

    def test_finished_task_creates_one_summary_for_many_devices_and_replay(self):
        from net.alerts.task_summaries import process_task_summary
        task = self.task()
        for target in task.target_runs.all():
            process_persisted_target(target)
        event = process_task_summary(task)
        self.assertEqual(event.summary_data['total'], 2)
        self.assertEqual(event.summary_data['abnormal'], 1)
        self.assertEqual(event.summary_data['normal'], 1)
        self.assertEqual(event.summary_data['failed'], 1)
        process_task_summary(task)
        self.assertEqual(AlertEvent.objects.filter(task=task, event_type='summary').count(), 1)
        self.assertEqual(AlertDelivery.objects.count(), 1)

    def test_does_not_summarize_running_task_or_unprocessed_target(self):
        from net.alerts.task_summaries import process_task_summary
        task = self.task(finished=False)
        self.assertIsNone(process_task_summary(task))
        TaskRun.objects.filter(pk=task.pk).update(status='partial', active_scope_key=None, finished_at=timezone.now(),
            progress=100, completed_targets=2, successful_targets=1, failed_targets=1)
        self.assertIsNone(process_task_summary(task))
        self.assertFalse(AlertDelivery.objects.exists())

    def test_normal_task_still_sends_one_summary(self):
        from net.alerts.task_summaries import process_task_summary
        task = self.task(('success', 'success'))
        for target in task.target_runs.all():
            process_persisted_target(target)
        event = process_task_summary(task)
        self.assertEqual(event.summary_data['normal'], 2)
        self.assertEqual(event.summary_data['abnormal'], 0)
        self.assertEqual(AlertDelivery.objects.count(), 1)

    def test_retries_reuse_frozen_message_and_do_not_send_device_events(self):
        from net.alerts.task_summaries import process_task_summary
        from net.alerts.service import deliver_due_alerts
        from net.alerts.base import DeliveryResult
        task = self.task()
        for target in task.target_runs.all():
            process_persisted_target(target)
        event = process_task_summary(task)
        original = event.summary_data['message_text']
        messages = []
        def send(channel, message):
            messages.append(message.text)
            return DeliveryResult(len(messages) > 1, 'temporary', len(messages) == 1)
        with patch('net.alerts.service.send_alert', side_effect=send):
            deliver_due_alerts(limit=10)
            AlertDelivery.objects.filter(event=event).update(next_attempt_at=timezone.now())
            deliver_due_alerts(limit=10)
        self.assertEqual(messages, [original, original])
        self.assertEqual(AlertDelivery.objects.get(event=event).status, 'sent')

    def test_recovery_is_in_next_task_summary_not_a_separate_notification(self):
        from net.alerts.task_summaries import process_task_summary
        first = self.task()
        for target in first.target_runs.all():
            process_persisted_target(target)
        process_task_summary(first)
        second = self.task(('success', 'success'))
        for target in second.target_runs.all():
            process_persisted_target(target)
        event = process_task_summary(second)
        self.assertEqual(event.summary_data['recovered'], 1)
        self.assertEqual(event.summary_data['abnormal'], 0)
        self.assertEqual(AlertDelivery.objects.count(), 2)
        self.assertEqual(AlertDelivery.objects.exclude(event__event_type='summary').count(), 0)

    def test_cancelled_task_summarizes_without_claiming_devices_were_checked(self):
        from net.alerts.task_summaries import process_task_summary
        from net.inspections.queue import cancel_task
        server = Server.objects.create(name='cancel', ip='192.0.2.99')
        task = enqueue_task(self.profile, [server.pk], 'manual')
        cancel_task(task.pk)
        process_persisted_target(task.target_runs.get())
        event = process_task_summary(task)
        self.assertEqual(event.summary_data['cancelled'], 1)
        self.assertEqual(event.summary_data['normal'], 0)
        self.assertEqual(event.summary_data['abnormal'], 0)

    def test_render_failure_does_not_reserve_task_notification_and_can_retry(self):
        from net.alerts.task_summaries import process_task_summary
        task = self.task()
        for target in task.target_runs.all():
            process_persisted_target(target)
        with patch('net.alerts.templates.render_task_summary', side_effect=ValueError('test')):
            self.assertIsNone(process_task_summary(task))
        task.refresh_from_db()
        self.assertIsNone(task.alert_summary_processed_at)
        self.assertFalse(AlertDelivery.objects.exists())
        self.assertIsNotNone(process_task_summary(task))
        self.assertEqual(AlertDelivery.objects.count(), 1)

    def test_legacy_processed_business_failure_is_not_counted_as_normal(self):
        from net.alerts.task_summaries import process_task_summary
        from net.alerts.service import process_target_findings
        task = self.task(('success', 'success'))
        target = task.target_runs.first()
        process_target_findings(target, [{'key': 'inspection.cpu', 'severity': 'warning',
            'title': 'CPU超限', 'detail': 'CPU usage high', 'state': 'abnormal'}])
        task.target_runs.update(alert_processed_at=timezone.now())
        event = process_task_summary(task)
        self.assertEqual(event.summary_data['abnormal'], 1)
        self.assertEqual(event.summary_data['normal'], 1)
        self.assertIn('CPU超限', event.summary_data['issues'])
