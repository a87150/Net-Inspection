"""Final-review producer-to-consumer regressions (all I/O local)."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from net.models import (AlertChannel, AlertDelivery, AlertEvent, AlertPolicy,
                        AlertState, Computer, ComputerAnalysis, ComputerAnalysisProfile,
                        ComputerLogFile, People, TaskRun)
from net.inspections.queue import enqueue_task, enqueue_computer_scan_task, claim_next_task, finish_task
from net.devices.pc.executor import execute_computer_target, execute_computer_scan_target


class FinalPipelineTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = ComputerAnalysisProfile.objects.create(
            name='final pipeline', scan_directories=[str(self.root)],
            analysis_items=['activation', 'bitlocker', 'domain', 'event_findings'],
            concurrent_workers=8)
        self.channel = AlertChannel.objects.create(name='frozen', channel_type='feishu')
        self.policy = AlertPolicy.objects.create(is_default=True, mode='override')
        self.policy.channels.add(self.channel)

    def run_payload(self, payload, sequence):
        from net.devices.pc.logs import import_log_file
        path = self.root / f'{sequence}.json'
        path.write_text(json.dumps({'系统信息概览': {'计算机名': 'FINAL-PC'}, **payload}), encoding='utf-8')
        log = import_log_file(self.profile, path)
        task = enqueue_task(self.profile, [log.pk], 'manual')
        claim_next_task('final-worker', 60)
        target = task.target_runs.get()
        execute_computer_target(target, worker_id='final-worker')
        finish_task(task.pk, 'final-worker')
        return task, log

    def test_actual_missing_abnormal_normal_share_item_identity(self):
        self.run_payload({}, 1)
        bad = {'Windows激活信息': {'许可证状态': '未授权'},
               'BitLocker状态': {'磁盘卷信息': [{'卷': 'C:', '转换状态': '未加密'}]},
               '已应用策略': {}, '当前与域服务器通讯情况': '无法访问',
               '事件发现': [{'级别': 'error', '消息': 'bad'}]}
        task, _ = self.run_payload(bad, 2)
        self.assertFalse(task.alert_events.filter(event_type='recovery').exists())
        self.assertEqual(set(AlertState.objects.values_list('finding_key', flat=True)),
                         {'analysis.activation', 'analysis.bitlocker', 'analysis.domain', 'analysis.event_findings'})
        good = {**bad, 'Windows激活信息': {'许可证状态': '已授权'},
                'BitLocker状态': {'磁盘卷信息': [{'卷': 'C:', '转换状态': '完全加密'}]},
                '当前与域服务器通讯情况': '正常通讯', '事件发现': []}
        task, _ = self.run_payload(good, 3)
        self.assertEqual(len(task.alert_events.get(event_type='recovery').findings), 4)
        self.assertEqual(ComputerAnalysis.objects.count(), 3)

    def test_routing_membership_is_frozen_before_worker_result(self):
        Computer.objects.create(computer_name='FINAL-PC')
        log = ComputerLogFile.objects.create(source_path='fixture', modified_at=timezone.now(),
                content_hash='a'*64, import_status='imported', payload={'系统信息概览': {'计算机名': 'FINAL-PC'}})
        task = enqueue_task(self.profile, [log.pk], 'manual')
        other = AlertChannel.objects.create(name='later', channel_type='feishu')
        self.policy.channels.set([other])
        claim_next_task('final-worker', 60)
        execute_computer_target(task.target_runs.get(), worker_id='final-worker')
        self.assertEqual(list(AlertDelivery.objects.values_list('channel_id', flat=True)), [self.channel.pk])

    def test_live_disable_vetoes_new_event_and_existing_delivery(self):
        from net.alerts.service import deliver_due_alerts
        from net.devices.pc.logs import import_log_file
        self.run_payload({}, 'first-delivery')
        path = self.root / 'veto.json'
        path.write_text(json.dumps({'系统信息概览': {'计算机名': 'VETO-PC'}}), encoding='utf-8')
        log = import_log_file(self.profile, path)
        task = enqueue_task(self.profile, [log.pk], 'manual')
        self.assertEqual(task.profile_snapshot['alert_routing']['channel_ids'], [str(self.channel.pk)])
        self.channel.is_enabled = False
        self.channel.save()
        claim_next_task('final-worker', 60)
        execute_computer_target(task.target_runs.get(), worker_id='final-worker')
        self.assertFalse(task.alert_events.get().deliveries.exists())
        with patch('requests.post') as http:
            deliver_due_alerts(limit=10)
        http.assert_not_called()
        self.assertEqual(AlertDelivery.objects.get().status, 'failed')

    def test_archived_scan_replay_recovers_only_intended_logs_and_concurrency(self):
        path = self.root / 'scan.json'
        path.write_text(json.dumps({'系统信息概览': {'计算机名': 'FINAL-PC'}}), encoding='utf-8')
        task = enqueue_computer_scan_task(self.profile, 'manual', overrides={'parameters': {'concurrent_workers': 2}})
        claim_next_task('final-worker', 60)
        with patch('net.devices.pc.executor._persist_scan', side_effect=RuntimeError('crash after archive')):
            with self.assertRaises(RuntimeError):
                execute_computer_scan_target(task.target_runs.get(), worker_id='final-worker')
        self.assertFalse(path.exists())
        ComputerLogFile.objects.create(source_path='historical', modified_at=timezone.now(),
            content_hash='b'*64, import_status='imported', payload={})
        from datetime import timedelta
        TaskRun.objects.filter(pk=task.pk).update(lease_expires_at=timezone.now()-timedelta(seconds=1))
        claim_next_task('final-worker', 60)
        target = task.target_runs.get()
        execute_computer_scan_target(target, worker_id='final-worker')
        target.refresh_from_db()
        self.assertTrue(target.result_id, 'archived evidence lost its analysis handoff')
        child = TaskRun.objects.get(pk=target.result_id)
        self.assertEqual(child.total_targets, 1)
        self.assertEqual(child.parameters_snapshot['concurrent_workers'], 2)
        self.assertEqual(child.profile_snapshot, task.profile_snapshot)

    def test_parent_link_exception_is_recoverable_by_worker_failure_handler(self):
        from net.devices.pc.executor import persist_computer_scan_failure
        path = self.root / 'handoff.json'
        path.write_text(json.dumps({'系统信息概览': {'计算机名': 'FINAL-PC'}}), encoding='utf-8')
        task = enqueue_computer_scan_task(self.profile, 'manual')
        claim_next_task('final-worker', 60)
        with patch('net.devices.pc.executor._persist_scan', side_effect=RuntimeError('link write')):
            with self.assertRaises(RuntimeError):
                execute_computer_scan_target(task.target_runs.get(), worker_id='final-worker')
        persist_computer_scan_failure(task.target_runs.get(), worker_id='final-worker', error='link write')
        self.assertEqual(task.target_runs.get().status, 'running')

    def test_scheduled_scan_child_retains_frozen_parameters_and_profile(self):
        from net.models import Schedule
        schedule = Schedule.objects.create(analysis_profile=self.profile, kind='interval',
                                           interval_value=1, interval_unit='hours')
        (self.root / 'scheduled.json').write_text(json.dumps({'系统信息概览': {'计算机名': 'FINAL-PC'}}), encoding='utf-8')
        task = enqueue_computer_scan_task(self.profile, 'scheduled', overrides={
            'schedule': schedule, 'parameters': {'concurrent_workers': 3, 'context': 'original'}})
        self.profile.analysis_items = ['resource']
        self.profile.concurrent_workers = 1
        self.profile.save()
        claim_next_task('final-worker', 60)
        execute_computer_scan_target(task.target_runs.get(), worker_id='final-worker')
        child = TaskRun.objects.get(pk=task.target_runs.get().result_id)
        self.assertEqual(child.parameters_snapshot, task.parameters_snapshot)
        self.assertEqual(child.profile_snapshot, task.profile_snapshot)
        self.assertEqual(child.selected_items_snapshot, task.selected_items_snapshot)
        self.assertEqual(child.schedule_id, schedule.pk)

    def test_archived_intent_survives_unavailable_input_directory_on_replay(self):
        incoming = self.root / 'incoming'
        incoming.mkdir()
        self.profile.scan_directories = [str(incoming)]
        self.profile.save()
        (incoming / 'evidence.json').write_text(json.dumps({'系统信息概览': {'计算机名': 'FINAL-PC'}}), encoding='utf-8')
        task = enqueue_computer_scan_task(self.profile, 'manual')
        claim_next_task('final-worker', 60)
        with patch('net.devices.pc.executor._persist_scan', side_effect=RuntimeError('archive committed')):
            with self.assertRaises(RuntimeError):
                execute_computer_scan_target(task.target_runs.get(), worker_id='final-worker')
        incoming.rename(self.root / 'temporarily-unavailable')
        TaskRun.objects.filter(pk=task.pk).update(lease_expires_at=timezone.now()-timezone.timedelta(seconds=1))
        claim_next_task('replacement-worker', 60)
        execute_computer_scan_target(task.target_runs.get(), worker_id='replacement-worker')
        target = task.target_runs.get()
        self.assertTrue(target.result_id)
        child = TaskRun.objects.get(pk=target.result_id)
        self.assertEqual(child.parameters_snapshot['concurrent_workers'], 8)
        self.assertEqual(child.total_targets, 1)

    def test_removed_people_post_is_not_routed_and_never_writes(self):
        person = People.objects.create(employee_id='protected', name='original')
        from net.models import PeopleSyncSource
        for name, kind in [('a', 'feishu'), ('b', 'feishu'), ('c', 'dingtalk')]:
            source = PeopleSyncSource.objects.create(name=name, source_key=name, source_type=kind)
            People.objects.create(employee_id=name, name=name, source=kind, sync_source=source)
        People.objects.create(employee_id='csv', source='csv')
        original = list(People.objects.order_by('pk').values())
        response = self.client.post('/api/upload_people/', json.dumps([{'工号': 'new', '姓名': 'new'}]), content_type='application/json')
        self.assertEqual(response.status_code, 404)
        person.refresh_from_db()
        self.assertTrue(person.is_active)
        self.assertEqual(list(People.objects.order_by('pk').values()), original)

    def test_upload_is_sanitized_import_queue_then_worker_and_reanalysis(self):
        payload = {'系统信息概览': {'计算机名': 'FINAL-PC'}, 'password': 'never-persist'}
        response = self.client.post('/api/computer_inspection/', json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 202)
        self.assertEqual(ComputerAnalysis.objects.count(), 0)
        log = ComputerLogFile.objects.get()
        self.assertEqual(log.import_status, 'imported')
        self.assertNotIn('never-persist', json.dumps(log.payload))
        task = TaskRun.objects.get(pk=response.json()['task_id'])
        claim_next_task('final-worker', 60)
        execute_computer_target(task.target_runs.get(), worker_id='final-worker')
        finish_task(task.pk, 'final-worker')
        self.assertEqual(ComputerAnalysis.objects.count(), 1)
        self.assertTrue(AlertEvent.objects.exists())
        duplicate = self.client.post('/api/computer_inspection/', json.dumps(payload), content_type='application/json')
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(TaskRun.objects.count(), 1)
        second = enqueue_task(self.profile, [log.pk], 'manual')
        claim_next_task('final-worker', 60)
        execute_computer_target(second.target_runs.get(), worker_id='final-worker')
        self.assertEqual(ComputerAnalysis.objects.count(), 2)

    def test_upload_default_profile_is_editable_without_scan_directories(self):
        from index.inspections.forms import ComputerAnalysisProfileConfigForm
        self.profile.is_enabled = False
        self.profile.save()
        response = self.client.post('/api/computer_inspection/', json.dumps({'系统信息概览': {'计算机名': 'UPLOAD-DEFAULT'}}), content_type='application/json')
        self.assertEqual(response.status_code, 202)
        profile = TaskRun.objects.get(pk=response.json()['task_id']).analysis_profile
        form = ComputerAnalysisProfileConfigForm({'name': profile.name, 'scan_directories_text': '',
            'analysis_items': ['resource'], 'concurrent_workers': 2, 'file_time_mode': 'recent_days',
            'recent_days': 7}, instance=profile)
        self.assertTrue(form.is_valid(), form.errors)
        data = {**form.data, 'schedule_enabled': True, 'schedule_kind': 'daily', 'daily_time': '09:00'}
        self.assertFalse(ComputerAnalysisProfileConfigForm(data, instance=profile).is_valid())

    def test_upload_retries_whole_import_transaction_on_unique_race(self):
        from django.db import IntegrityError
        from net.api.views import _refresh_static_computer
        calls = []
        def competing(payload, at):
            calls.append(1)
            if len(calls) == 1:
                raise IntegrityError('concurrent identity insert')
            return _refresh_static_computer(payload, at)
        with patch('net.api.views._refresh_static_computer', side_effect=competing):
            response = self.client.post('/api/computer_inspection/', json.dumps({'系统信息概览': {'计算机名': 'RACE'}}), content_type='application/json')
        self.assertEqual(response.status_code, 202)
        self.assertEqual((ComputerLogFile.objects.count(), TaskRun.objects.count()), (1, 1))

    def test_upload_of_file_imported_evidence_returns_durable_queue_receipt(self):
        from net.devices.pc.logs import import_log_file
        payload = {'系统信息概览': {'计算机名': 'DUAL-INGEST'}}
        path = self.root / 'canonical.json'
        path.write_bytes(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
        log = import_log_file(self.profile, path)
        response = self.client.post('/api/computer_inspection/', json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['task_id'])
        self.assertEqual(response.json()['log_id'], log.pk)
        self.assertEqual(ComputerLogFile.objects.count(), 1)

    def test_both_profile_types_freeze_override_mode_membership_and_live_veto(self):
        from net.models import InspectionProfile, Server
        from net.alerts.service import process_target_findings, deliver_due_alerts
        from net.alerts.base import DeliveryResult
        from net.inspections.worker import TaskWorker
        server = Server.objects.create(name='route', ip='192.0.2.7')
        inspection = InspectionProfile.objects.create(name='route', device_type='server', selected_items=['cpu'])
        log = ComputerLogFile.objects.create(source_path='routing', modified_at=timezone.now(),
            content_hash='f'*64, import_status='imported', payload={})
        for profile, obj, field in [(self.profile, log, 'analysis_profile'), (inspection, server, 'inspection_profile')]:
            policy = AlertPolicy.objects.create(mode='override', **{field: profile})
            policy.channels.add(self.channel)
            task = enqueue_task(profile, [obj.pk], 'manual')
            self.assertEqual(task.profile_snapshot['alert_policy_mode'], 'override')
            policy.mode = 'inherit'
            policy.save()
            policy.channels.clear()
            self.policy.channels.clear()
            target = task.target_runs.get()
            process_target_findings(target, [{'key': 'fixture', 'severity': 'critical', 'title': 'failure', 'detail': 'test'}])
            self.assertEqual(list(task.alert_events.get().deliveries.values_list('channel_id', flat=True)), [self.channel.pk])
        self.channel.settings = {'webhook_url': 'https://example.invalid/rotated'}
        self.channel.save()
        seen = []
        def sender(channel, message):
            seen.append(channel.settings['webhook_url'])
            return DeliveryResult(True, 'fixture', False)
        with patch('net.alerts.service.send_alert', side_effect=sender):
            deliver_due_alerts(limit=10)
        self.assertEqual(seen, ['https://example.invalid/rotated'] * 2)
        # Newly observed queued targets retain route IDs but live disable vetoes them.
        self.channel.is_enabled = False
        self.channel.save()
        for task in TaskRun.objects.all():
            process_target_findings(task.target_runs.get(), [{'key': 'new', 'severity': 'critical', 'title': 'failure', 'detail': 'test'}])
        self.assertEqual(AlertDelivery.objects.count(), 2)
        self.assertEqual(TaskWorker(threads=1)._max_workers(task), 1)
