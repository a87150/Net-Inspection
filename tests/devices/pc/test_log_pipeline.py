"""Remote producer-to-worker alert and durable handoff regressions."""
import json
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from net.models import (AlertChannel, AlertDelivery, AlertEvent, AlertPolicy,
                        AlertState, ComputerAnalysis, ComputerAnalysisProfile,
                        ComputerLogFile, People, Schedule, TaskRun)
from net.inspections.queue import enqueue_task, enqueue_computer_fetch_task, claim_next_task, finish_task
from net.devices.pc.executor import (execute_computer_target, execute_computer_fetch_target,
                                     persist_computer_fetch_failure)
from tests.devices.pc.helpers import import_payload, create_log_file
from tests.devices.pc.test_source_models import valid_smb_source
from tests.devices.pc.connector_fakes import MemoryConnector


class FinalPipelineTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = valid_smb_source(local_staging_directory=self.tmp.name)
        self.connector = MemoryConnector({})
        self.connector.modified_at = timezone.now()
        connector_patch = patch('net.devices.pc.remote_ingestion.build_connector', return_value=self.connector)
        connector_patch.start()
        self.addCleanup(connector_patch.stop)
        self.profile = ComputerAnalysisProfile.objects.create(
            name='final pipeline', analysis_items=['activation', 'bitlocker', 'domain_trust', 'group_policy', 'event_findings'],
            concurrent_workers=8)
        self.channel = AlertChannel.objects.create(name='frozen', channel_type='feishu')
        self.policy = AlertPolicy.objects.create(is_default=True, mode='override')
        self.policy.channels.add(self.channel)
        self.sequence = 0

    def payload(self, extra=None, name='FINAL-PC'):
        self.sequence += 1
        collected = timezone.localtime() - timedelta(days=self.sequence)
        return {'日志时间': collected.strftime('%Y-%m-%d %H:%M:%S'),
                '系统信息概览': {'计算机名': name}, **(extra or {})}

    def complete_summary(self, task):
        from net.alerts.task_summaries import process_task_summary
        task.refresh_from_db()
        if task.status not in TaskRun.TERMINAL_STATUSES:
            finish_task(task.pk, 'final-worker')
        event = process_task_summary(task)
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, 'summary')
        self.assertFalse(task.alert_events.exclude(event_type='summary').filter(deliveries__isnull=False).exists())
        return event

    def remote_file(self, name='evidence.json'):
        self.connector.files['incoming/' + name] = json.dumps(self.payload()).encode()

    def run_payload(self, payload):
        log = import_payload(self.payload(payload)).log_file
        task = enqueue_task(self.profile, [log.pk], 'manual')
        claim_next_task('final-worker', 60)
        execute_computer_target(task.target_runs.get(), worker_id='final-worker')
        finish_task(task.pk, 'final-worker')
        return task, log

    def test_actual_missing_abnormal_normal_share_item_identity(self):
        People.objects.create(employee_id='FINAL-PC', name='Pipeline Person')
        self.run_payload({})
        bad = {'Windows激活信息': {'许可证状态': '未授权'},
               'BitLocker状态': {'磁盘卷信息': [{'卷': 'C:', '转换状态': '未加密'}]},
               '已应用策略': {'计算机策略': [], '用户策略': []}, '当前与域服务器通讯情况': '无法访问',
               '事件发现': [{'级别': 'error', '消息': 'bad'}]}
        task, _ = self.run_payload(bad)
        self.assertFalse(task.alert_events.filter(event_type='recovery').exists())
        self.assertEqual(set(AlertState.objects.values_list('finding_key', flat=True)),
                         {'analysis.activation', 'analysis.bitlocker', 'analysis.domain_trust', 'analysis.group_policy', 'analysis.event_findings'})
        good = {**bad, 'Windows激活信息': {'许可证状态': '已授权'},
                'BitLocker状态': {'磁盘卷信息': [{'卷': 'C:', '转换状态': '完全加密'}]},
                '当前与域服务器通讯情况': '正常通讯', '事件发现': []}
        task, _ = self.run_payload(good)
        self.assertEqual(len(task.alert_events.get(event_type='recovery').findings), 4)
        self.assertEqual(ComputerAnalysis.objects.count(), 3)

    def test_routing_membership_is_frozen_before_worker_result(self):
        log = import_payload(self.payload({'当前与域服务器通讯情况': '失败'})).log_file
        task = enqueue_task(self.profile, [log.pk], 'manual')
        other = AlertChannel.objects.create(name='later', channel_type='feishu')
        self.policy.channels.set([other])
        claim_next_task('final-worker', 60)
        execute_computer_target(task.target_runs.get(), worker_id='final-worker')
        self.assertFalse(AlertDelivery.objects.exists())
        event = self.complete_summary(task)
        self.assertEqual(list(event.deliveries.values_list('channel_id', flat=True)), [self.channel.pk])

    def test_live_disable_vetoes_new_event_and_existing_delivery(self):
        from net.alerts.service import deliver_due_alerts
        first, _ = self.run_payload({'当前与域服务器通讯情况': '失败'})
        self.complete_summary(first)
        log = import_payload(self.payload({'当前与域服务器通讯情况': '失败'}, name='VETO-PC')).log_file
        task = enqueue_task(self.profile, [log.pk], 'manual')
        self.assertEqual(task.profile_snapshot['alert_routing']['channel_ids'], [str(self.channel.pk)])
        self.channel.is_enabled = False
        self.channel.save()
        claim_next_task('final-worker', 60)
        execute_computer_target(task.target_runs.get(), worker_id='final-worker')
        self.assertFalse(self.complete_summary(task).deliveries.exists())
        with patch('requests.post') as http:
            deliver_due_alerts(limit=10)
        http.assert_not_called()
        self.assertEqual(AlertDelivery.objects.get().status, 'failed')

    def crash_after_archive(self, **overrides):
        self.remote_file()
        task = enqueue_computer_fetch_task(self.profile, 'manual', overrides=overrides)
        claim_next_task('final-worker', 60)
        with patch('net.devices.pc.executor._persist_scan', side_effect=RuntimeError('crash after archive')):
            with self.assertRaises(RuntimeError):
                execute_computer_fetch_target(task.target_runs.get(), worker_id='final-worker')
        self.assertNotIn('incoming/evidence.json', self.connector.files)
        self.assertEqual(task.target_runs.get().fetched_logs.count(), 1)
        return task

    def test_archived_fetch_replay_recovers_only_intended_logs_and_concurrency(self):
        task = self.crash_after_archive(parameters={'concurrent_workers': 2})
        import_payload(self.payload(name='UNRELATED-HISTORICAL'))
        TaskRun.objects.filter(pk=task.pk).update(lease_expires_at=timezone.now()-timedelta(seconds=1))
        claim_next_task('replacement-worker', 60)
        execute_computer_fetch_target(task.target_runs.get(), worker_id='replacement-worker')
        target = task.target_runs.get()
        child = TaskRun.objects.get(pk=target.result_id)
        self.assertEqual(child.total_targets, 1)
        self.assertEqual(child.target_runs.get().target_id, str(target.fetched_logs.get().pk))
        self.assertEqual(child.parameters_snapshot['concurrent_workers'], 2)
        self.assertEqual(child.profile_snapshot, task.profile_snapshot)

    def test_parent_link_exception_is_recoverable_by_worker_failure_handler(self):
        task = self.crash_after_archive()
        result = persist_computer_fetch_failure(task.target_runs.get(), worker_id='final-worker', error='link write')
        self.assertTrue(result.stale)
        self.assertEqual(task.target_runs.get().status, 'running')

    def test_scheduled_fetch_child_retains_frozen_parameters_and_profile(self):
        person = People.objects.create(employee_id='frozen-person', name='Original')
        schedule = Schedule.objects.create(analysis_profile=self.profile, kind='interval',
                                           interval_value=1, interval_unit='hours')
        self.remote_file()
        task = enqueue_computer_fetch_task(self.profile, 'scheduled', overrides={
            'schedule': schedule, 'parameters': {'concurrent_workers': 3, 'context': 'original'}})
        task.refresh_from_db()
        frozen_roster = task.parameters_snapshot['personnel_roster']
        self.assertEqual(len(frozen_roster), 1)
        self.assertEqual(frozen_roster[0]['name'], 'Original')
        person.name = 'Changed after enqueue'
        person.save(update_fields=['name'])
        People.objects.create(employee_id='later-person', name='Later')
        self.profile.analysis_items = ['resource']
        self.profile.concurrent_workers = 1
        self.profile.save()
        claim_next_task('final-worker', 60)
        with patch('net.devices.pc.matching.personnel_snapshot', side_effect=AssertionError('Personnel must remain frozen')):
            execute_computer_fetch_target(task.target_runs.get(), worker_id='final-worker')
        child = TaskRun.objects.get(pk=task.target_runs.get().result_id)
        self.assertEqual(child.parameters_snapshot['concurrent_workers'], 3)
        self.assertEqual(child.parameters_snapshot['context'], 'original')
        self.assertEqual(child.parameters_snapshot['personnel_roster'], frozen_roster)
        self.assertEqual(child.parameters_snapshot, task.parameters_snapshot)
        self.assertEqual(child.profile_snapshot, task.profile_snapshot)
        self.assertEqual(child.selected_items_snapshot, task.selected_items_snapshot)
        self.assertEqual(child.schedule_id, schedule.pk)

    def test_archived_intent_survives_remote_source_unavailability_on_replay(self):
        from net.devices.pc.connectors.base import PCLogConnectionError
        task = self.crash_after_archive()
        TaskRun.objects.filter(pk=task.pk).update(lease_expires_at=timezone.now()-timedelta(seconds=1))
        claim_next_task('replacement-worker', 60)
        with patch('net.devices.pc.remote_ingestion.build_connector', side_effect=PCLogConnectionError('offline')):
            execute_computer_fetch_target(task.target_runs.get(), worker_id='replacement-worker')
        target = task.target_runs.get()
        self.assertEqual(target.status, 'partial')
        child = TaskRun.objects.get(pk=target.result_id)
        self.assertEqual(child.parameters_snapshot['concurrent_workers'], 8)
        self.assertEqual(child.total_targets, 1)

    def test_retired_uploads_are_404_and_never_write(self):
        person = People.objects.create(employee_id='protected', name='original')
        for url in ['/api/upload_people/', '/api/computer_inspection/']:
            response = self.client.post(url, json.dumps(self.payload()), content_type='application/json')
            self.assertEqual(response.status_code, 404)
        person.refresh_from_db()
        self.assertTrue(person.is_active)
        self.assertFalse(ComputerLogFile.objects.exists())
        self.assertFalse(TaskRun.objects.exists())

    def test_remote_import_is_sanitized_queued_then_reanalyzed(self):
        People.objects.create(employee_id='FINAL-PC', name='Pipeline Person')
        payload = self.payload({'password': 'never-persist'})
        outcome = import_payload(payload)
        log = outcome.log_file
        self.assertNotIn('never-persist', json.dumps(log.payload))
        self.assertFalse(ComputerAnalysis.objects.exists())
        first = enqueue_task(self.profile, [log.pk], 'manual')
        claim_next_task('final-worker', 60)
        execute_computer_target(first.target_runs.get(), worker_id='final-worker')
        finish_task(first.pk, 'final-worker')
        self.assertFalse(AlertEvent.objects.exists())
        duplicate = import_payload(payload)
        self.assertEqual(duplicate.status, 'duplicate_content')
        self.assertEqual(duplicate.log_file.pk, log.pk)
        second = enqueue_task(self.profile, [log.pk], 'manual')
        claim_next_task('final-worker', 60)
        execute_computer_target(second.target_runs.get(), worker_id='final-worker')
        self.assertEqual(ComputerAnalysis.objects.count(), 2)

    def test_remote_import_retries_whole_transaction_on_unique_race(self):
        from net.devices.pc.logs import _refresh_static_computer
        calls = []
        def competing(payload, at):
            calls.append(1)
            if len(calls) == 1:
                raise IntegrityError('concurrent identity insert')
            return _refresh_static_computer(payload, at)
        with patch('net.devices.pc.logs._refresh_static_computer', side_effect=competing):
            result = import_payload(self.payload())
        self.assertEqual(result.status, 'imported')
        self.assertEqual(ComputerLogFile.objects.count(), 1)
        self.assertEqual(len(calls), 2)

    def test_both_profile_types_freeze_override_membership_and_live_veto(self):
        from net.models import InspectionProfile, Server
        from net.alerts.service import process_target_findings, deliver_due_alerts
        from net.alerts.base import DeliveryResult
        server = Server.objects.create(name='route', ip='192.0.2.7')
        inspection = InspectionProfile.objects.create(name='route', device_type='server', selected_items=['cpu'])
        log = import_payload(self.payload()).log_file
        for profile, obj, field in [(self.profile, log, 'analysis_profile'), (inspection, server, 'inspection_profile')]:
            policy = AlertPolicy.objects.create(mode='override', **{field: profile})
            policy.channels.add(self.channel)
            task = enqueue_task(profile, [obj.pk], 'manual')
            self.assertEqual(task.profile_snapshot['alert_policy_mode'], 'override')
            policy.mode = 'inherit'
            policy.save()
            policy.channels.clear()
            self.policy.channels.clear()
            claim_next_task('final-worker', 60)
            process_target_findings(task.target_runs.get(), [{'key': 'fixture', 'severity': 'critical', 'title': 'failure', 'detail': 'test'}])
            self.assertFalse(task.alert_events.get().deliveries.exists())
            # This test supplies normalized evidence directly; complete its persistence boundary.
            task.target_runs.update(status='success', finished_at=timezone.now(),
                                    alert_processed_at=timezone.now(),
                                    result_snapshot={'health_status': 'abnormal'})
            event = self.complete_summary(task)
            self.assertEqual(list(event.deliveries.values_list('channel_id', flat=True)), [self.channel.pk])
        self.channel.settings = {'webhook_url': 'https://example.invalid/rotated'}
        self.channel.save()
        seen = []
        def sender(channel, message):
            seen.append(channel.settings['webhook_url'])
            return DeliveryResult(True, 'fixture', False)
        with patch('net.alerts.service.send_alert', side_effect=sender):
            deliver_due_alerts(limit=10)
        self.assertEqual(seen, ['https://example.invalid/rotated'] * 2)
        self.channel.is_enabled = False
        self.channel.save()
        for task in TaskRun.objects.all():
            process_target_findings(task.target_runs.get(), [{'key': 'new', 'severity': 'critical', 'title': 'failure', 'detail': 'test'}])
        self.assertEqual(AlertDelivery.objects.count(), 2)
