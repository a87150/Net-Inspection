"""Behavioral contracts for Phase 3 alert state, delivery, and Worker wiring."""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from tests.devices.pc.helpers import create_log_file

from net.models import (
    AlertChannel,
    AlertDelivery,
    AlertEvent,
    AlertPolicy,
    Computer,
    ComputerAnalysis,
    ComputerAnalysisProfile,
    ComputerLogFile,
    InspectionProfile,
    Server,
    Server_Inspection,
    TaskRun,
    TaskTargetRun,
)
from net.inspections.queue import enqueue_task


class AlertServiceTests(TestCase):
    def setUp(self):
        self.profile = InspectionProfile.objects.create(
            name='alert service profile',
            device_type=InspectionProfile.DeviceType.SERVER,
            selected_items=['cpu'],
        )
        self.server = Server.objects.create(name='ALERT-SRV', ip='192.0.2.240')
        self.channel = AlertChannel.objects.create(
            name='primary alert channel',
            channel_type=AlertChannel.ChannelType.FEISHU,
            settings={'webhook_url': 'https://open.feishu.test/hook'},
        )
        self.policy = AlertPolicy.objects.create(
            name='default alert policy',
            is_default=True,
            mode=AlertPolicy.Mode.OVERRIDE,
        )
        self.policy.channels.add(self.channel)

    def target(self):
        task = enqueue_task(self.profile, [self.server.pk], TaskRun.Source.MANUAL)
        target = task.target_runs.get()
        now = timezone.now()
        TaskRun.objects.filter(pk=task.pk).update(
            status=TaskRun.Status.SUCCESS,
            active_scope_key=None,
            progress=100,
            completed_targets=1,
            successful_targets=1,
            finished_at=now,
        )
        target.__class__.objects.filter(pk=target.pk).update(
            status=TaskRun.Status.SUCCESS,
            started_at=now,
            finished_at=now,
        )
        target.refresh_from_db()
        return target

    def finding(self, *, state='abnormal', key='collector.cpu'):
        return {
            'key': key,
            'severity': 'critical',
            'title': 'CPU collection failure',
            'detail': 'collector reported a failure',
            'state': state,
        }

    def summary_event(self):
        from net.alerts.service import process_target_findings
        from net.alerts.task_summaries import process_task_summary

        target = self.target()
        process_target_findings(target, [self.finding()])
        TaskTargetRun.objects.filter(pk=target.pk).update(
            alert_processed_at=timezone.now(),
            result_snapshot={'alert_observation': {'abnormal': True, 'info': False}},
        )
        event = process_task_summary(target.task_id)
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, 'summary')
        self.assertEqual(event.summary_task_id, target.task_id)
        self.assertIsNone(event.target_run_id)
        return event

    def test_normal_to_abnormal_creates_one_merged_recorded_event_without_fanout(self):
        from net.alerts.service import process_target_findings

        target = self.target()
        events = process_target_findings(target, [
            self.finding(),
            self.finding(key='collector.memory'),
        ])

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.event_type, AlertEvent.EventType.ABNORMAL)
        self.assertEqual({item['key'] for item in event.findings}, {'collector.cpu', 'collector.memory'})
        self.assertEqual(event.status, 'recorded')
        self.assertFalse(AlertDelivery.objects.exists())

    def test_normal_to_normal_creates_no_event(self):
        from net.alerts.service import process_target_findings

        self.assertEqual(process_target_findings(self.target(), [self.finding(state='normal')]), [])
        self.assertEqual(AlertEvent.objects.count(), 0)

    def test_recovery_requires_an_explicit_reevaluated_normal_finding(self):
        from net.alerts.service import process_target_findings

        first = self.target()
        process_target_findings(first, [self.finding()])
        unevaluated = self.target()
        self.assertEqual(process_target_findings(unevaluated, []), [])
        recovered = self.target()
        events = process_target_findings(recovered, [self.finding(state='normal')])

        self.assertEqual([event.event_type for event in events], [AlertEvent.EventType.RECOVERY])
        self.assertEqual(events[0].status, 'recorded')
        self.assertFalse(AlertDelivery.objects.exists())

    def test_delivery_retries_only_retryable_failures_with_a_finite_attempt_limit(self):
        from net.alerts.base import DeliveryResult
        from net.alerts.service import deliver_event

        event = self.summary_event()
        with patch('net.alerts.service.send_alert', return_value=DeliveryResult(False, 'timed out', True)):
            delivery = deliver_event(event)[0]
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, AlertDelivery.Status.RETRY)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertGreater(delivery.next_attempt_at, timezone.now() - timedelta(seconds=1))

        for _ in range(delivery.max_attempts - 1):
            AlertDelivery.objects.filter(pk=delivery.pk).update(next_attempt_at=timezone.now())
            with patch('net.alerts.service.send_alert', return_value=DeliveryResult(False, 'timed out', True)):
                deliver_event(event)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, AlertDelivery.Status.FAILED)
        self.assertEqual(delivery.attempt_count, delivery.max_attempts)

    def test_inherited_and_override_policies_select_their_own_multichannel_fanout(self):
        email = AlertChannel.objects.create(
            name='override email channel',
            channel_type=AlertChannel.ChannelType.EMAIL,
            settings={
                'smtp_host': 'smtp.example.test', 'smtp_port': 25,
                'use_tls': False, 'use_ssl': False, 'from_email': 'ops@example.test',
                'recipients': ['ops@example.test'],
            },
        )
        inherited = AlertPolicy.objects.create(
            name='profile inherits default', inspection_profile=self.profile,
            mode=AlertPolicy.Mode.INHERIT,
        )
        inherited_event = self.summary_event()
        self.assertEqual(set(inherited_event.deliveries.values_list('channel_id', flat=True)), {self.channel.pk})

        inherited.mode = AlertPolicy.Mode.OVERRIDE
        inherited.save(update_fields={'mode', 'updated_at'})
        inherited.channels.add(self.channel, email)
        override_event = self.summary_event()
        self.assertEqual(
            set(override_event.deliveries.values_list('channel_id', flat=True)),
            {self.channel.pk, email.pk},
        )

    def test_one_channel_failure_does_not_hide_another_channel_success(self):
        from net.alerts.base import DeliveryResult
        from net.alerts.service import deliver_event

        email = AlertChannel.objects.create(
            name='secondary email channel', channel_type=AlertChannel.ChannelType.EMAIL,
            settings={
                'smtp_host': 'smtp.example.test', 'smtp_port': 25,
                'use_tls': False, 'use_ssl': False, 'from_email': 'ops@example.test',
                'recipients': ['ops@example.test'],
            },
        )
        self.policy.channels.add(email)
        event = self.summary_event()
        with patch('net.alerts.service.send_alert', side_effect=[
            DeliveryResult(True, 'accepted', False),
            DeliveryResult(False, 'rejected', False),
        ]):
            deliver_event(event)

        event.refresh_from_db()
        self.assertEqual(event.status, AlertEvent.Status.PARTIAL)
        self.assertEqual(event.deliveries.filter(status=AlertDelivery.Status.SENT).count(), 1)
        self.assertEqual(event.deliveries.filter(status=AlertDelivery.Status.FAILED).count(), 1)

    def test_replay_and_an_older_observation_cannot_undo_newer_abnormal_state(self):
        from net.alerts.service import process_target_findings

        first = self.target()
        process_target_findings(first, [self.finding()])
        self.assertEqual(process_target_findings(first, [self.finding()]), [])
        older = self.target()
        older.__class__.objects.filter(pk=older.pk).update(finished_at=first.finished_at - timedelta(seconds=1))
        self.assertEqual(process_target_findings(older, [self.finding(state='normal')]), [])
        state = AlertEvent.objects.get(target_run=first).states.get()
        self.assertEqual(state.status, 'abnormal')
        self.assertEqual(AlertEvent.objects.filter(event_type=AlertEvent.EventType.RECOVERY).count(), 0)

    def test_two_logs_for_one_persisted_computer_share_alert_state_and_recover(self):
        from net.alerts.service import process_target_findings

        profile = ComputerAnalysisProfile.objects.create(
            name='computer alert profile', analysis_items=['activation'], )
        computer = Computer.objects.create(computer_name='PC-ALERT')

        def analyzed_target(suffix, *, exceptions):
            log = create_log_file(
                source_path=f'C:/logs/{suffix}.json', modified_at=timezone.now(),
                content_hash=suffix * 64, import_status='imported', payload={},
            )
            task = enqueue_task(profile, [log.pk], TaskRun.Source.MANUAL)
            target = task.target_runs.get()
            analysis = ComputerAnalysis.objects.create(
                computer=computer, log_file=log, task_target=target,
                status=TaskRun.Status.FAILED if exceptions else TaskRun.Status.SUCCESS,
                analysis_items=['activation'], exceptions=exceptions,
            )
            now = timezone.now()
            TaskRun.objects.filter(pk=task.pk).update(
                status=TaskRun.Status.SUCCESS, active_scope_key=None, progress=100,
                completed_targets=1, successful_targets=1, finished_at=now,
            )
            target.__class__.objects.filter(pk=target.pk).update(
                status=analysis.status, started_at=now, finished_at=now,
                result_type='computer_analysis', result_id=str(analysis.pk),
                result_snapshot={'status': analysis.status},
            )
            target.refresh_from_db()
            return target

        abnormal = analyzed_target('a', exceptions=[{
            '问题类型': 'activation failure', '详细问题': 'not activated', 'analysis_item': 'activation',
        }])
        event = process_target_findings(abnormal, __import__('net.alerts.service', fromlist=['findings_for_target']).findings_for_target(abnormal))[0]
        normal = analyzed_target('b', exceptions=[])
        recovery = process_target_findings(normal, __import__('net.alerts.service', fromlist=['findings_for_target']).findings_for_target(normal))[0]

        self.assertEqual(event.target_type, 'computer')
        self.assertEqual(event.target_id, str(computer.pk))
        self.assertEqual(recovery.event_type, AlertEvent.EventType.RECOVERY)
        self.assertEqual(recovery.target_id, str(computer.pk))
        self.assertNotEqual(event.target_run_id, recovery.target_run_id)
        self.assertEqual((event.status, recovery.status), ('recorded', 'recorded'))
        self.assertFalse(AlertDelivery.objects.exists())

    def test_worker_delivers_due_alerts_when_no_inspection_task_remains(self):
        from net.alerts.base import DeliveryResult
        from net.inspections.worker import TaskWorker

        event = self.summary_event()
        with patch('net.alerts.service.send_alert', return_value=DeliveryResult(True, 'accepted', False)):
            self.assertTrue(TaskWorker(worker_id='alert-only-worker', threads=1).run_once())

        self.assertEqual(event.deliveries.get().status, AlertDelivery.Status.SENT)

    def test_adapter_exception_redacts_configured_url_and_password_before_summary(self):
        from net.alerts.service import deliver_event

        url = 'https://open.feishu.test/hook/private-path-token?access_token=query-token'
        password = 'configured-password-value'
        self.channel.settings = {'webhook_url': url, 'password': password}
        self.channel.save(update_fields=['settings'])
        event = self.summary_event()
        with patch('net.alerts.service.send_alert', side_effect=RuntimeError(
            f'Transport rejected {url} using {password}',
        )):
            deliver_event(event)
        delivery = event.deliveries.get()
        self.assertEqual(delivery.status, AlertDelivery.Status.RETRY)
        for summary in (delivery.response_summary, delivery.error_summary):
            self.assertIn('Transport rejected', summary)
            for secret in (url, 'private-path-token', 'query-token', password):
                self.assertNotIn(secret, summary)

    def test_reconcile_advances_past_no_event_history_to_post_record_gap(self):
        from net.alerts.service import reconcile_terminal_targets
        from net.infrastructure.collection import CollectionResult
        from net.inspections.executor import execute_target
        from net.inspections.queue import claim_next_task, finish_task

        # More than one batch of normal/no-result history must not hide the gap.
        history = [self.target() for _ in range(5)]
        normal_record = Server_Inspection.objects.create(
            server=self.server, task_target=history[0], status='success', summary='collection normal',
        )
        TaskTargetRun.objects.filter(pk=history[0].pk).update(
            result_type='server_inspection', result_id=str(normal_record.pk),
            result_snapshot={'status': 'success', 'summary': normal_record.summary},
        )
        self.server.username, self.server.password = 'reader', 'test-only'
        self.server.save(update_fields=['username', 'password'])
        task = enqueue_task(self.profile, [self.server.pk], TaskRun.Source.MANUAL)
        claim_next_task('record-gap', 60)
        target = task.target_runs.get()
        with patch('net.inspections.executor.collect_linux_ssh', return_value=CollectionResult(
            False, 'failed', message='explicit collection failure',
        )), patch('net.alerts.service.process_persisted_target', return_value=[]):
            execute_target(target, worker_id='record-gap')
        finish_task(task.pk, 'record-gap')
        record = Server_Inspection.objects.get(task_target=target)
        self.assertEqual(record.status, 'failed')
        self.assertFalse(target.alert_events.exists())

        for _ in range(3):
            reconcile_terminal_targets(limit=2)
        event = target.alert_events.get()
        self.assertEqual(event.findings[0]['key'], 'inspection.collection')
        self.assertEqual(event.status, 'recorded')
        self.assertFalse(event.deliveries.exists())
        for done in [*history, target]:
            done.refresh_from_db()
            self.assertIsNotNone(done.alert_processed_at)
        self.assertFalse(AlertEvent.objects.filter(target_run__in=history).exists())
        reconcile_terminal_targets(limit=2)
        self.assertEqual(target.alert_events.count(), 1)
        record.refresh_from_db()
        self.assertEqual(record.status, 'failed')

    def test_worker_reconciles_generic_computer_failure_beyond_first_batch(self):
        from net.alerts.base import DeliveryResult
        from net.inspections.worker import TaskWorker

        for _ in range(9):
            self.target()
        profile = ComputerAnalysisProfile.objects.create(
            name='generic failure profile', analysis_items=['activation'], )
        log = create_log_file(
            source_path='C:/logs/generic.json', modified_at=timezone.now(),
            content_hash='f' * 64, import_status='imported', payload={},
        )
        task = enqueue_task(profile, [log.pk], TaskRun.Source.MANUAL)
        # Exercise the Worker's actual generic failure fallback and persistence.
        worker = TaskWorker(worker_id='generic-failure', threads=1)
        from threading import Event
        from net.inspections.queue import claim_next_task, finish_task
        claim_next_task(worker.worker_id, 60)
        with patch('net.inspections.worker.execute_computer_target', side_effect=RuntimeError('executor crash')):
            worker._run_target(task.target_runs.get(), task.task_type, Event())
        finish_task(task.pk, worker.worker_id)
        target = task.target_runs.get()
        self.assertEqual(target.status, 'failed')
        self.assertFalse(target.alert_events.exists())
        with patch('net.alerts.service.send_alert', return_value=DeliveryResult(True, 'accepted', False)):
            # Normal history now also has summaries, so drain multiple outbox batches.
            for _ in range(12):
                worker.run_once()
                if AlertDelivery.objects.filter(event__summary_task=task, status='sent').exists():
                    break
        event = target.alert_events.get()
        self.assertEqual(event.findings[0]['key'], 'execution.failure')
        self.assertEqual(event.status, 'recorded')
        self.assertFalse(event.deliveries.exists())
        summary = AlertEvent.objects.get(summary_task=task)
        self.assertEqual(summary.event_type, 'summary')
        self.assertEqual(summary.deliveries.get().status, 'sent')
        target.refresh_from_db()
        self.assertEqual(target.status, 'failed')

    def test_poison_target_does_not_starve_newer_targets_and_can_be_retried(self):
        from net.alerts.service import findings_for_target, reconcile_terminal_targets

        poison, newer = self.target(), self.target()
        TaskTargetRun.objects.filter(pk=newer.pk).update(status='failed', error_message='new failure')

        def fail_old(target):
            if target.pk == poison.pk:
                raise ValueError('poison data')
            return findings_for_target(target)

        with patch('net.alerts.service.findings_for_target', side_effect=fail_old):
            reconcile_terminal_targets(limit=1)
            reconcile_terminal_targets(limit=1)
        self.assertEqual(newer.alert_events.count(), 1)
        poison.refresh_from_db()
        self.assertIsNone(poison.alert_processed_at)
        self.assertIsNotNone(poison.alert_attempted_at)
        self.assertIn('ValueError', poison.alert_processing_error)
        reconcile_terminal_targets(limit=1)
        poison.refresh_from_db()
        self.assertIsNotNone(poison.alert_processed_at)
        self.assertEqual(poison.alert_processing_error, '')

    def test_processing_marker_and_alert_rows_roll_back_together_on_crash(self):
        from net.alerts.service import process_persisted_target, process_target_findings, reconcile_terminal_targets

        target = self.target()
        TaskTargetRun.objects.filter(pk=target.pk).update(status='failed', error_message='persisted failure')

        def crash_after_events(*args):
            process_target_findings(*args)
            raise SystemExit('simulated process exit')

        with patch('net.alerts.service.process_target_findings', side_effect=crash_after_events):
            with self.assertRaises(SystemExit):
                process_persisted_target(target)
        self.assertFalse(target.alert_events.exists())
        reconcile_terminal_targets(limit=1)
        self.assertEqual(target.alert_events.count(), 1)
        self.assertEqual(target.alert_events.get().status, 'recorded')
        self.assertFalse(target.alert_events.get().deliveries.exists())
        target.refresh_from_db()
        self.assertIsNotNone(target.alert_processed_at)
