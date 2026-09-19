"""API-ingested computer log analysis contracts."""

import json
import os
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from net.models import (
    Computer,
    ComputerAnalysis,
    ComputerAnalysisProfile,
    ComputerLogFile,
    Error_Computer,
    RecordStatus,
    Schedule,
    TaskRun,
)
from net.devices.pc.analysis import analyze_log
from tests.devices.pc.helpers import import_fixture_path
from net.inspections.queue import enqueue_task
from net.devices.pc.executor import execute_computer_target
from net.inspections.queue import claim_next_task
from net.inspections.schedules import enqueue_due_schedules
from net.inspections.worker import TaskWorker


SHANGHAI = ZoneInfo('Asia/Shanghai')
ANALYSIS_METADATA = {
    'collection_diagnostics', 'enrichment', 'rules', 'platform',
    'severity_counts', 'health_status',
}


class ComputerApiIngestionTests(TestCase):
    def test_api_ingestion_persists_metadata_and_static_snapshot(self):
        from tests.system.test_application import valid_payload
        from tests.devices.pc.helpers import import_payload
        outcome = import_payload(valid_payload('REMOTE-STATIC'))
        log = outcome.log_file
        self.assertEqual(outcome.status, 'created')
        self.assertEqual(log.computer.login_account, 'H000001')
        self.assertEqual(log.computer.ip_addresses, '192.0.2.10')
        self.assertEqual(log.computer.os_build, '10.0.26100.4770')
        self.assertEqual(log.collected_date, timezone.localdate(log.computer.last_report_at))

    def test_older_api_evidence_does_not_replace_latest_static_snapshot(self):
        from tests.system.test_application import valid_payload
        from tests.devices.pc.helpers import import_payload
        newer = valid_payload('REMOTE-ORDER')
        newer['系统信息概览']['当前登录用户工号'] = 'NEWER'
        import_payload(newer)
        older = valid_payload('REMOTE-ORDER', timezone.localtime() - timedelta(days=1))
        older['系统信息概览']['当前登录用户工号'] = 'OLDER'
        outcome = import_payload(older)
        self.assertEqual(Computer.objects.get(computer_name='REMOTE-ORDER').login_account, 'NEWER')
        self.assertEqual(outcome.status, 'created')
        self.assertEqual(ComputerLogFile.objects.count(), 2)


class ComputerLogAnalysisTests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / 'incoming'
        self.root.mkdir(parents=True)
        self.profile = ComputerAnalysisProfile.objects.create(
            name='分析日志配置',
            analysis_items=['activation', 'resource', 'event_findings'],
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _import_payload(self, *, activation='已授权', event_findings=None):
        path = self.root / 'analysis.json'
        path.write_text(json.dumps({
            '日志时间': '2026-08-31 11:30:00',
            '系统信息概览': {
                '计算机名': 'PC-ANALYSIS-01',
                '当前登录用户姓名': '李四',
                '系统主要版本名': '24H2',
            },
            '网络信息': [],
            '计算机硬件资源情况': {'当前CPU占用率': '23%', '当前内存使用率': '48%'},
            'Windows激活信息': {'许可证状态': activation},
            'KMS服务器连通情况': '正常通讯',
            '事件发现': event_findings or [],
            '已安装软件列表': [{'软件名': '正常软件'}],
        }, ensure_ascii=False), encoding='utf-8')
        return import_fixture_path(path), path

    def test_explicit_selection_limits_details_and_ignores_unselected_findings(self):
        log_file, _path = self._import_payload(activation='未授权')

        analysis = analyze_log(log_file, ['resource'])

        self.assertEqual(analysis.status, RecordStatus.SUCCESS)
        self.assertEqual(analysis.analysis_items, ['resource'])
        self.assertEqual(analysis.details['resource'], {'当前CPU占用率': '23%', '当前内存使用率': '48%'})
        self.assertNotIn('activation', analysis.details)
        self.assertEqual(analysis.exceptions, [])
        self.assertEqual(Error_Computer.objects.count(), 0)

    def test_selected_activation_creates_an_abnormal_analysis_and_error_record(self):
        log_file, _path = self._import_payload(activation='未授权')

        analysis = analyze_log(log_file, ['activation'])

        self.assertEqual(analysis.status, RecordStatus.SUCCESS)
        self.assertEqual(analysis.details['health_status'], 'abnormal')
        self.assertEqual(analysis.details['severity_counts'], {'info': 0, 'warning': 1, 'critical': 0})
        self.assertEqual(set(analysis.details) - ANALYSIS_METADATA, {'activation'})
        self.assertEqual(len(analysis.exceptions), 1)
        self.assertEqual(analysis.exceptions[0]['问题类型'], '系统激活问题')
        self.assertEqual(analysis.exceptions[0]['severity'], 'warning')
        self.assertEqual(Error_Computer.objects.get().error_type, '系统激活问题')
        self.assertEqual(Error_Computer.objects.get().inspection_id, analysis.pk)

    def test_selected_bitlocker_reports_missing_or_unprotected_volume_without_unrelated_findings(self):
        log_file, _path = self._import_payload()

        analysis = analyze_log(log_file, ['bitlocker'])

        self.assertEqual(analysis.status, RecordStatus.SUCCESS)
        self.assertEqual(analysis.details['health_status'], 'normal')
        self.assertEqual(analysis.details['severity_counts'], {'info': 1, 'warning': 0, 'critical': 0})
        self.assertEqual(set(analysis.details) - ANALYSIS_METADATA, {'bitlocker'})
        self.assertEqual(len(analysis.exceptions), 1)
        self.assertEqual(analysis.exceptions[0]['问题类型'], 'BitLocker数据缺失')
        self.assertEqual(analysis.exceptions[0]['severity'], 'info')
        self.assertEqual(analysis.exceptions[0]['data_state'], 'missing')
        self.assertFalse(Error_Computer.objects.filter(inspection=analysis).exists())

    def test_selected_defender_and_patches_run_only_their_missing_data_rules(self):
        log_file, _path = self._import_payload()

        analysis = analyze_log(log_file, ['defender', 'patches'])

        self.assertEqual(analysis.status, RecordStatus.SUCCESS)
        self.assertEqual(analysis.details['health_status'], 'normal')
        self.assertEqual(analysis.details['severity_counts'], {'info': 2, 'warning': 0, 'critical': 0})
        self.assertEqual(set(analysis.details) - ANALYSIS_METADATA, {'defender', 'patches'})
        self.assertEqual(len(analysis.exceptions), 2)
        self.assertTrue(all(issue['severity'] == 'info' and issue['data_state'] == 'missing'
                            for issue in analysis.exceptions))
        self.assertFalse(Error_Computer.objects.filter(inspection=analysis).exists())
        self.assertEqual(
            {issue['问题类型'] for issue in analysis.exceptions},
            {'WindowsDefender数据缺失', '系统更新数据缺失'},
        )

    def test_existing_log_can_be_reanalyzed_without_rescanning_or_reimporting(self):
        log_file, path = self._import_payload()

        first = analyze_log(log_file, ['activation'])
        second = analyze_log(log_file, ['resource'])

        self.assertTrue(path.exists())
        self.assertEqual(ComputerLogFile.objects.count(), 1)
        self.assertEqual(ComputerAnalysis.objects.count(), 2)
        self.assertEqual(first.log_file_id, log_file.pk)
        self.assertEqual(second.log_file_id, log_file.pk)
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(second.analysis_items, ['resource'])

    def test_unknown_analysis_item_is_rejected(self):
        log_file, _path = self._import_payload()

        with self.assertRaises(ValidationError):
            analyze_log(log_file, ['not-a-real-analysis'])

    def test_configured_rules_turn_collected_values_into_actionable_findings(self):
        policy = self.root / 'software-policy.ini'
        policy.write_text('[BLACKLIST]\nkeywords = Forbidden Tool\n', encoding='utf-8')
        log_file, _path = self._import_payload()
        log_file.collected_at = datetime(2026, 9, 1, 12, tzinfo=SHANGHAI)
        payload = log_file.payload
        payload.update({
            '系统信息概览': {
                '计算机名': 'PC-ANALYSIS-01',
                '系统主要版本名': '22H2',
                '开机时间': '2026-08-01 08:00:00',
            },
            'Windows激活信息': {
                '许可证状态': '已授权',
                '描述': 'VOLUME_KMSCLIENT channel',
                'KMS 计算机 IP 地址': '192.0.2.99',
            },
            'WindowsDefender状态': {
                '当前病毒库版本': '1.2.3',
                '上次更新时间': '2026-08-01 08:00:00',
                '扫描信息': {'时间': '2026-08-02 08:00:00'},
            },
            '系统更新历史': [{'日期': '2026-08-01', '补丁名称': 'KB5000001'}],
            '计算机硬件资源情况': {
                '当前CPU占用率': '95%', '当前内存使用率': '91%',
            },
            '已安装软件列表': [{'软件名': 'Forbidden Tool Pro'}],
        })
        log_file.payload = payload
        log_file.save()

        analysis = analyze_log(
            log_file,
            ['activation', 'software', 'defender', 'patches', 'system', 'uptime', 'resource'],
            rules={
                'software_policy_path': str(policy),
                'minimum_windows_release': '23H2',
                'defender_update_max_days': 7,
                'defender_scan_max_days': 7,
                'patch_max_days': 30,
                'uptime_max_hours': 168,
                'cpu_max_percent': 90,
                'memory_max_percent': 90,
                'kms_servers': ['192.0.2.10'],
            },
        )

        self.assertEqual(analysis.status, RecordStatus.SUCCESS)
        self.assertEqual(analysis.details['health_status'], 'abnormal')
        self.assertEqual(set(analysis.details) - ANALYSIS_METADATA, set(analysis.analysis_items))
        self.assertTrue(all(issue['severity'] == 'warning' for issue in analysis.exceptions), analysis.exceptions)
        self.assertEqual(analysis.details['severity_counts'],
                         {'info': 0, 'warning': len(analysis.exceptions), 'critical': 0})
        self.assertCountEqual(
            Error_Computer.objects.filter(inspection=analysis).values_list('error_type', 'error_message'),
            [(issue['问题类型'], issue['详细问题']) for issue in analysis.exceptions])
        self.assertEqual(
            {issue['问题类型'] for issue in analysis.exceptions},
            {
                '系统激活问题', '软件问题', 'WindowsDefender问题',
                '系统更新历史问题', '系统版本过旧', '长时间未关机',
                '资源使用问题',
            },
        )

    def test_missing_software_policy_is_a_finding_instead_of_a_worker_error(self):
        log_file, _path = self._import_payload()

        analysis = analyze_log(
            log_file,
            ['software'],
            rules={'software_policy_path': str(self.root / 'missing.ini')},
        )

        self.assertEqual(analysis.status, RecordStatus.SUCCESS)
        self.assertEqual(analysis.exceptions[0]['问题类型'], '软件策略问题')
        self.assertEqual(len(analysis.exceptions), 1)
        # An unreadable configured policy is a rule configuration fault,
        # distinct from optional evidence absent from the collected log.
        self.assertEqual(analysis.exceptions[0]['severity'], 'warning')
        self.assertEqual(analysis.details['health_status'], 'abnormal')
        self.assertEqual(analysis.details['severity_counts'], {'info': 0, 'warning': 1, 'critical': 0})
        error = Error_Computer.objects.get(inspection=analysis)
        self.assertEqual(error.error_type, '软件策略问题')
        self.assertEqual(error.error_message, analysis.exceptions[0]['详细问题'])

    def test_domain_prefix_is_removed_before_matching_special_software_whitelist(self):
        policy = self.root / 'software-policy.ini'
        policy.write_text('[SPECIAL_WHITELIST]\nAdminTool = H1\n', encoding='utf-8')
        log_file, _path = self._import_payload()
        payload = log_file.payload
        payload['系统信息概览']['当前登录用户工号'] = 'DOMAIN\\H1'
        payload['已安装软件列表'] = [{'软件名': 'AdminTool'}]
        log_file.payload = payload
        log_file.save()

        analysis = analyze_log(
            log_file,
            ['software'],
            rules={'software_policy_path': str(policy)},
        )

        self.assertEqual(analysis.status, RecordStatus.SUCCESS)
        self.assertEqual(analysis.exceptions, [])


class ComputerAnalysisWorkerTests(TransactionTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / 'incoming'
        self.root.mkdir(parents=True)
        self.profile = ComputerAnalysisProfile.objects.create(
            name='Worker 分析配置',
            analysis_items=['activation'],
            concurrent_workers=1,
        )
        path = self.root / 'worker.json'
        path.write_text(json.dumps({
            '日志时间': '2026-08-31 12:00:00',
            '系统信息概览': {'计算机名': 'PC-WORKER-01'},
            '网络信息': [],
            '计算机硬件资源情况': {},
            'Windows激活信息': {'许可证状态': '未授权'},
        }, ensure_ascii=False), encoding='utf-8')
        self.log_file = import_fixture_path(path)
        from net.models import People
        People.objects.create(employee_id='PC-WORKER-01', name='Worker fixture')

    def tearDown(self):
        self.tempdir.cleanup()

    def test_worker_dispatches_computer_target_and_links_immutable_result(self):
        task = enqueue_task(self.profile, [self.log_file.pk], TaskRun.Source.MANUAL)
        from net.devices.pc.analysis import prepare_log, persist_analysis
        phases = []

        def prepare(*args, **kwargs):
            phases.append(('prepare', connections['default'].in_atomic_block))
            result = prepare_log(*args, **kwargs)
            self.assertFalse(ComputerAnalysis.objects.exists())
            self.assertFalse(Error_Computer.objects.exists())
            return result

        def persist(*args, **kwargs):
            phases.append(('persist', connections['default'].in_atomic_block))
            return persist_analysis(*args, **kwargs)

        with patch('net.devices.pc.executor.prepare_log', side_effect=prepare), \
                patch('net.devices.pc.executor.persist_analysis', side_effect=persist):
            self.assertTrue(TaskWorker(
                worker_id='computer-worker', threads=1, lease_seconds=30,
            ).run_once())
        self.assertEqual(phases, [('prepare', False), ('persist', True)])

        task.refresh_from_db()
        target = task.target_runs.get()
        analysis = ComputerAnalysis.objects.get()
        self.assertEqual(task.status, TaskRun.Status.SUCCESS)
        self.assertEqual(target.status, RecordStatus.SUCCESS)
        self.assertEqual(analysis.status, RecordStatus.SUCCESS)
        self.assertEqual(analysis.details['health_status'], 'abnormal')
        self.assertEqual(analysis.details['severity_counts'], {'info': 0, 'warning': 1, 'critical': 0})
        self.assertEqual(analysis.exceptions[0]['severity'], 'warning')
        self.assertEqual(Error_Computer.objects.get(inspection=analysis).error_type, '系统激活问题')
        self.assertEqual(target.error_message, '')
        self.assertEqual(target.result_snapshot['health_status'], 'abnormal')
        self.assertNotIn('details', target.result_snapshot)
        self.assertNotIn('exceptions', target.result_snapshot)
        self.assertEqual(analysis.task_target_id, target.pk)
        self.assertEqual(target.result_type, 'computer_analysis')
        self.assertEqual(target.result_id, str(analysis.pk))
        from net.inspections.result_storage import expanded_result_snapshot
        self.assertEqual(expanded_result_snapshot(target)['details'], analysis.details)
        self.assertEqual(expanded_result_snapshot(target)['exceptions'], analysis.exceptions)
        self.assertEqual(expanded_result_snapshot(target)['analysis_items'], ['activation'])

    def test_worker_uses_the_queued_rule_snapshot_after_profile_changes(self):
        self.profile.kms_servers = ['kms-approved-when-queued.example.test']
        self.profile.save(update_fields=['kms_servers'])
        payload = dict(self.log_file.payload)
        payload['Windows激活信息'] = {
            '许可证状态': '已授权',
            '描述': 'VOLUME_KMSCLIENT channel',
            '已注册的 KMS 计算机名称': 'kms-current.example.test:1688',
        }
        payload['KMS服务器连通情况'] = '正常通讯'
        self.log_file.payload = payload
        self.log_file.save()
        task = enqueue_task(self.profile, [self.log_file.pk], TaskRun.Source.MANUAL)
        self.profile.kms_servers = ['kms-current.example.test']
        self.profile.save(update_fields=['kms_servers'])

        self.assertTrue(TaskWorker(
            worker_id='snapshot-rules-worker', threads=1, lease_seconds=30,
        ).run_once())

        analysis = ComputerAnalysis.objects.get()
        self.assertEqual(analysis.status, RecordStatus.SUCCESS)
        self.assertEqual(analysis.details['health_status'], 'abnormal')
        self.assertEqual(analysis.details['severity_counts'], {'info': 0, 'warning': 1, 'critical': 0})
        self.assertEqual(len(analysis.exceptions), 1)
        self.assertEqual(analysis.exceptions[0]['severity'], 'warning')
        self.assertEqual(Error_Computer.objects.get(inspection=analysis).error_type, '系统激活问题')
        self.assertIn('KMS', analysis.exceptions[0]['详细问题'])

    def test_expired_owner_cannot_persist_a_computer_analysis_result(self):
        task = enqueue_task(self.profile, [self.log_file.pk], TaskRun.Source.MANUAL)
        claimed = claim_next_task(
            'expired-computer-worker', 30,
            task_type=TaskRun.TaskType.COMPUTER_ANALYSIS,
        )
        target = claimed.target_runs.get()
        TaskRun.objects.filter(pk=task.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )

        outcome = execute_computer_target(target, worker_id='expired-computer-worker')

        self.assertTrue(outcome.stale)
        self.assertEqual(ComputerAnalysis.objects.count(), 0)
        target.refresh_from_db()
        self.assertEqual(target.status, TaskRun.Status.QUEUED)

    @patch('net.inspections.worker.execute_computer_target', side_effect=RuntimeError('analysis executor crashed'))
    def test_worker_isolates_unexpected_computer_executor_failure(self, execute):
        task = enqueue_task(self.profile, [self.log_file.pk], TaskRun.Source.MANUAL)

        self.assertTrue(TaskWorker(
            worker_id='computer-executor-error', threads=1, lease_seconds=30,
        ).run_once())

        task.refresh_from_db()
        target = task.target_runs.get()
        self.assertEqual(task.status, TaskRun.Status.FAILED)
        self.assertEqual(target.status, TaskRun.Status.FAILED)
        self.assertIn('RuntimeError', target.error_message)


class ConcurrentComputerLogImportTests(TransactionTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / 'incoming'
        self.root.mkdir(parents=True)
        self.profile = ComputerAnalysisProfile.objects.create(
            name='并发导入配置',
            analysis_items=['activation'],
        )
        self.path = self.root / 'concurrent.json'
        self.path.write_text(json.dumps({
            '日志时间': '2026-08-31 13:00:00',
            '系统信息概览': {'计算机名': 'PC-CONCURRENT-01'},
            '网络信息': [],
            '计算机硬件资源情况': {},
            'Windows激活信息': {'许可证状态': '已授权'},
        }, ensure_ascii=False), encoding='utf-8')

    def tearDown(self):
        self.tempdir.cleanup()

    def test_concurrent_same_hash_imports_return_one_evidence_row_without_lock_error(self):
        start = threading.Barrier(2)

        def import_once():
            connections['default'].close()
            try:
                start.wait(timeout=5)
                return import_fixture_path(self.path).pk
            finally:
                connections['default'].close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = [
                future.result(timeout=10)
                for future in (executor.submit(import_once), executor.submit(import_once))
            ]

        self.assertEqual(first, second)
        self.assertEqual(ComputerLogFile.objects.count(), 1)
        self.assertEqual(Computer.objects.filter(computer_name='PC-CONCURRENT-01').count(), 1)
