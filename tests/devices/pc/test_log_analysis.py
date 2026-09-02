"""Filesystem-backed contracts for Phase 2 computer log analysis."""

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
from net.devices.pc.logs import import_log_file, scan_log_directory
from net.inspections.queue import enqueue_task
from net.devices.pc.executor import execute_computer_target
from net.inspections.queue import claim_next_task
from net.inspections.schedules import enqueue_due_schedules
from net.inspections.worker import TaskWorker


SHANGHAI = ZoneInfo('Asia/Shanghai')


class ComputerLogImportTests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / 'incoming'
        self.processed = self.root / 'processed'
        self.failed = self.root / 'failed'
        self.root.mkdir(parents=True)
        self.profile = ComputerAnalysisProfile.objects.create(
            name='目录导入基础配置',
            scan_directories=[str(self.root)],
            processed_directory=str(self.processed),
            failed_directory=str(self.failed),
            analysis_items=['activation'],
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _write_log(self, name='PC-LOG-01.json', directory=None, computer_name='PC-LOG-01'):
        directory = self.root if directory is None else directory
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text(json.dumps({
            '日志时间': '2026-08-31 09:30:00',
            '系统信息概览': {
                '计算机名': computer_name,
                '当前登录用户工号': 'DOMAIN\\zhangsan',
                '当前登录用户姓名': '张三',
                '系统安装日期': '2025-01-01 08:00:00',
                '系统主要版本名': '24H2',
                '系统版本类型': 'Professional',
                '系统详细版本': '26100.1',
            },
            '网络信息': [{'IP地址': '192.0.2.80', 'MAC地址': 'AA-BB-CC-DD-EE-FF'}],
            '计算机硬件资源情况': {'当前CPU占用率': '12%'},
            'Windows激活信息': {'许可证状态': '已授权'},
            'KMS服务器连通情况': '正常通讯',
        }, ensure_ascii=False), encoding='utf-8')
        return path

    def _set_mtime(self, path, value):
        os.utime(path, (value.timestamp(), value.timestamp()))

    def test_import_persists_payload_metadata_and_static_computer_snapshot(self):
        path = self._write_log()

        log_file = import_log_file(self.profile, path)

        self.assertEqual(ComputerLogFile.objects.count(), 1)
        self.assertEqual(log_file.source_path, str(path.resolve()))
        self.assertEqual(log_file.file_size, path.stat().st_size)
        self.assertEqual(log_file.import_status, 'imported')
        self.assertEqual(log_file.payload['系统信息概览']['计算机名'], 'PC-LOG-01')
        self.assertTrue(timezone.is_aware(log_file.modified_at))
        computer = Computer.objects.get(computer_name='PC-LOG-01')
        self.assertEqual(computer.login_account, 'DOMAIN\\zhangsan')
        self.assertEqual(computer.ip_addresses, '192.0.2.80')
        self.assertEqual(computer.mac_addresses, 'AA-BB-CC-DD-EE-FF')
        self.assertEqual(computer.os_version, '24H2')
        self.assertEqual(computer.os_build, '26100.1')
        self.assertEqual(
            timezone.localtime(computer.last_report_at).isoformat(),
            '2026-08-31T09:30:00+08:00',
        )

    def test_scan_uses_recent_day_window_and_does_not_reprocess_archives(self):
        recent = self._write_log('recent.json')
        old = self._write_log('old.json')
        now = datetime(2026, 9, 1, 12, tzinfo=SHANGHAI)
        self._set_mtime(recent, datetime(2026, 8, 31, 12, tzinfo=SHANGHAI))
        self._set_mtime(old, datetime(2026, 8, 29, 11, 59, 59, tzinfo=SHANGHAI))
        self.profile.recent_days = 2
        self.profile.save(update_fields=['recent_days'])

        first = scan_log_directory(self.profile, now=now)
        second = scan_log_directory(self.profile, now=now)

        self.assertEqual(first.imported, 1)
        self.assertEqual(first.skipped, 1)
        self.assertEqual(second.imported, 0)
        self.assertEqual(second.duplicate, 0)
        self.assertTrue(old.exists())
        log_file = ComputerLogFile.objects.get()
        self.assertFalse(recent.exists())
        self.assertTrue(Path(log_file.archived_path).is_file())
        self.assertTrue(Path(log_file.archived_path).is_relative_to(self.processed.resolve()))

    def test_scan_uses_inclusive_local_dates_and_optional_recursion(self):
        lower = self._write_log('lower.json', computer_name='PC-LOWER')
        upper = self._write_log('upper.json', computer_name='PC-UPPER')
        nested = self._write_log('nested.json', self.root / 'nested', 'PC-NESTED')
        outside = self._write_log('outside.json', computer_name='PC-OUTSIDE')
        self._set_mtime(lower, datetime(2026, 8, 30, 0, 0, tzinfo=SHANGHAI))
        self._set_mtime(upper, datetime(2026, 8, 30, 23, 59, 59, tzinfo=SHANGHAI))
        self._set_mtime(nested, datetime(2026, 8, 30, 12, tzinfo=SHANGHAI))
        self._set_mtime(outside, datetime(2026, 8, 31, 0, 0, tzinfo=SHANGHAI))
        self.profile.file_time_mode = ComputerAnalysisProfile.FileTimeMode.DATE_RANGE
        self.profile.recent_days = None
        self.profile.range_start_date = datetime(2026, 8, 30).date()
        self.profile.range_end_date = datetime(2026, 8, 30).date()
        self.profile.save(update_fields=[
            'file_time_mode', 'recent_days', 'range_start_date', 'range_end_date',
        ])

        top_level = scan_log_directory(self.profile, now=datetime(2026, 9, 1, tzinfo=SHANGHAI))
        self.assertEqual(top_level.imported, 2)
        self.assertEqual(top_level.skipped, 1)
        self.assertTrue(nested.exists())
        self.profile.recursive = True
        self.profile.save(update_fields=['recursive'])
        recursive = scan_log_directory(self.profile, now=datetime(2026, 9, 1, tzinfo=SHANGHAI))

        self.assertEqual(recursive.imported, 1)
        self.assertFalse(nested.exists())
        self.assertTrue(outside.exists())

    def test_scan_hash_deduplicates_and_moves_each_copy_without_overwrite(self):
        first = self._write_log('same.json')
        second = self._write_log('same-copy.json')

        summary = scan_log_directory(self.profile, now=timezone.now() + timedelta(seconds=1))

        self.assertEqual(summary.imported, 1)
        self.assertEqual(summary.duplicate, 1)
        self.assertEqual(ComputerLogFile.objects.count(), 1)
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        archived = list(self.processed.glob('*.json'))
        self.assertEqual(len(archived), 2)
        self.assertEqual(len({path.name for path in archived}), 2)

    def test_malformed_json_is_preserved_as_failed_evidence_and_moved_to_failed(self):
        bad = self.root / 'broken.json'
        bad.write_text('{not valid json', encoding='utf-8')

        summary = scan_log_directory(self.profile, now=timezone.now() + timedelta(seconds=1))

        self.assertEqual(summary.failed, 1)
        self.assertFalse(bad.exists())
        log_file = ComputerLogFile.objects.get()
        self.assertEqual(log_file.import_status, 'failed')
        self.assertIn('JSON 解析失败', log_file.parse_error)
        self.assertTrue(Path(log_file.archived_path).is_file())
        self.assertTrue(Path(log_file.archived_path).is_relative_to(self.failed.resolve()))

    def test_source_or_archive_outside_scan_root_is_rejected_before_read_or_move(self):
        outside_root = Path(self.tempdir.name) / 'outside'
        outside_root.mkdir()
        outside = self._write_log('outside.json', outside_root)

        with self.assertRaises(ValidationError):
            import_log_file(self.profile, outside)

        self.profile.processed_directory = str(outside_root)
        self.profile.save(update_fields=['processed_directory'])
        inside = self._write_log('inside.json')
        with self.assertRaises(ValidationError):
            scan_log_directory(self.profile)
        self.assertTrue(inside.exists())
        self.assertEqual(ComputerLogFile.objects.count(), 0)

    @patch('net.devices.pc.logs._rename_noreplace', side_effect=OSError('archive unavailable'))
    def test_archive_failure_keeps_committed_evidence_and_source_file(self, move):
        source = self._write_log('move-failure.json')

        summary = scan_log_directory(self.profile, now=timezone.now() + timedelta(seconds=1))

        self.assertEqual(summary.imported, 1)
        self.assertEqual(summary.move_failures, 1)
        self.assertTrue(source.exists())
        log_file = ComputerLogFile.objects.get()
        self.assertEqual(log_file.import_status, 'imported')
        self.assertEqual(log_file.archived_path, '')
        self.assertIn('归档失败', summary.errors[0])


class ComputerLogAnalysisTests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / 'incoming'
        self.root.mkdir(parents=True)
        self.profile = ComputerAnalysisProfile.objects.create(
            name='分析日志配置',
            scan_directories=[str(self.root)],
            processed_directory=str(self.root / 'processed'),
            failed_directory=str(self.root / 'failed'),
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
        return import_log_file(self.profile, path), path

    def test_explicit_selection_limits_details_and_ignores_unselected_findings(self):
        log_file, _path = self._import_payload(activation='未授权')

        analysis = analyze_log(log_file, ['resource'])

        self.assertEqual(analysis.status, RecordStatus.SUCCESS)
        self.assertEqual(analysis.analysis_items, ['resource'])
        self.assertEqual(analysis.details, {
            'resource': {'当前CPU占用率': '23%', '当前内存使用率': '48%'},
        })
        self.assertEqual(analysis.exceptions, [])
        self.assertEqual(Error_Computer.objects.count(), 0)

    def test_selected_activation_creates_an_abnormal_analysis_and_error_record(self):
        log_file, _path = self._import_payload(activation='未授权')

        analysis = analyze_log(log_file, ['activation'])

        self.assertEqual(analysis.status, RecordStatus.FAILED)
        self.assertEqual(set(analysis.details), {'activation'})
        self.assertEqual(len(analysis.exceptions), 1)
        self.assertEqual(analysis.exceptions[0]['问题类型'], '系统激活问题')
        self.assertEqual(Error_Computer.objects.get().inspection_id, analysis.pk)

    def test_selected_bitlocker_reports_missing_or_unprotected_volume_without_unrelated_findings(self):
        log_file, _path = self._import_payload()

        analysis = analyze_log(log_file, ['bitlocker'])

        self.assertEqual(analysis.status, RecordStatus.FAILED)
        self.assertEqual(set(analysis.details), {'bitlocker'})
        self.assertEqual(analysis.exceptions[0]['问题类型'], 'BitLocker数据缺失')
        self.assertEqual(Error_Computer.objects.get().error_type, 'BitLocker数据缺失')

    def test_selected_defender_and_patches_run_only_their_missing_data_rules(self):
        log_file, _path = self._import_payload()

        analysis = analyze_log(log_file, ['defender', 'patches'])

        self.assertEqual(analysis.status, RecordStatus.FAILED)
        self.assertEqual(set(analysis.details), {'defender', 'patches'})
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
        log_file.modified_at = datetime(2026, 9, 1, 12, tzinfo=SHANGHAI)
        log_file.payload.update({
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
        log_file.save(update_fields=['modified_at', 'payload'])

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

        self.assertEqual(analysis.status, RecordStatus.FAILED)
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

        self.assertEqual(analysis.status, RecordStatus.FAILED)
        self.assertEqual(analysis.exceptions[0]['问题类型'], '软件策略问题')

    def test_domain_prefix_is_removed_before_matching_special_software_whitelist(self):
        policy = self.root / 'software-policy.ini'
        policy.write_text('[SPECIAL_WHITELIST]\nAdminTool = H1\n', encoding='utf-8')
        log_file, _path = self._import_payload()
        log_file.payload['系统信息概览']['当前登录用户工号'] = 'DOMAIN\\H1'
        log_file.payload['已安装软件列表'] = [{'软件名': 'AdminTool'}]
        log_file.save(update_fields=['payload'])

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
            scan_directories=[str(self.root)],
            processed_directory=str(self.root / 'processed'),
            failed_directory=str(self.root / 'failed'),
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
        self.log_file = import_log_file(self.profile, path)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_worker_dispatches_computer_target_and_links_immutable_result(self):
        task = enqueue_task(self.profile, [self.log_file.pk], TaskRun.Source.MANUAL)

        self.assertTrue(TaskWorker(
            worker_id='computer-worker', threads=1, lease_seconds=30,
        ).run_once())

        task.refresh_from_db()
        target = task.target_runs.get()
        analysis = ComputerAnalysis.objects.get()
        self.assertEqual(task.status, TaskRun.Status.FAILED)
        self.assertEqual(target.status, RecordStatus.FAILED)
        self.assertEqual(analysis.task_target_id, target.pk)
        self.assertEqual(target.result_type, 'computer_analysis')
        self.assertEqual(target.result_id, str(analysis.pk))
        self.assertEqual(target.result_snapshot['details'], analysis.details)

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
        self.log_file.save(update_fields=['payload'])
        task = enqueue_task(self.profile, [self.log_file.pk], TaskRun.Source.MANUAL)
        self.profile.kms_servers = ['kms-current.example.test']
        self.profile.save(update_fields=['kms_servers'])

        self.assertTrue(TaskWorker(
            worker_id='snapshot-rules-worker', threads=1, lease_seconds=30,
        ).run_once())

        analysis = ComputerAnalysis.objects.get()
        self.assertEqual(analysis.status, RecordStatus.FAILED)
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
            scan_directories=[str(self.root)],
            processed_directory=str(self.root / 'processed'),
            failed_directory=str(self.root / 'failed'),
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
                return import_log_file(self.profile, self.path).pk
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


class ScheduledComputerScanTests(TransactionTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / 'incoming'
        self.root.mkdir(parents=True)
        self.now = timezone.now() - timedelta(seconds=1)
        self.profile = ComputerAnalysisProfile.objects.create(
            name='计划扫描配置',
            scan_directories=[str(self.root)],
            processed_directory=str(self.root / 'processed'),
            failed_directory=str(self.root / 'failed'),
            analysis_items=['activation'],
            concurrent_workers=1,
        )
        self.schedule = Schedule.objects.create(
            analysis_profile=self.profile,
            kind=Schedule.Kind.INTERVAL,
            interval_value=1,
            interval_unit=Schedule.IntervalUnit.HOURS,
            next_run_at=self.now,
        )
        self.source = self.root / 'scheduled.json'
        self.source.write_text(json.dumps({
            '日志时间': '2026-09-01 09:00:00',
            '系统信息概览': {'计算机名': 'PC-SCHEDULED-01'},
            '网络信息': [],
            '计算机硬件资源情况': {},
            'Windows激活信息': {'许可证状态': '已授权'},
        }, ensure_ascii=False), encoding='utf-8')
        os.utime(self.source, (self.now.timestamp(), self.now.timestamp()))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_worker_queues_scan_then_analyzes_new_log_without_scheduler_file_io(self):
        """Direct scheduler scanning would block polling and bypass the task lease."""
        tasks = enqueue_due_schedules(now=self.now)

        self.assertEqual(len(tasks), 1)
        self.assertTrue(self.source.exists())
        scan_task = tasks[0]
        self.assertEqual(scan_task.task_type, 'computer_scan')
        self.assertEqual(scan_task.target_runs.get().target_type, 'computer_scan')

        worker = TaskWorker(worker_id='scheduled-computer-worker', threads=1, lease_seconds=30)

        with patch('net.inspections.worker.enqueue_due_schedules', return_value=[]):
            self.assertTrue(worker.run_once())

        self.assertFalse(self.source.exists())
        log_file = ComputerLogFile.objects.get()
        self.assertTrue(Path(log_file.archived_path).is_file())
        scan_task.refresh_from_db()
        self.assertEqual(scan_task.status, TaskRun.Status.SUCCESS)
        task = TaskRun.objects.exclude(pk=scan_task.pk).get(schedule=self.schedule)
        self.assertEqual(task.task_type, TaskRun.TaskType.COMPUTER_ANALYSIS)
        self.assertEqual(task.status, TaskRun.Status.QUEUED)
        self.assertEqual(task.target_runs.get().target_id, str(log_file.pk))

        with patch('net.inspections.worker.enqueue_due_schedules', return_value=[]):
            self.assertTrue(worker.run_once())

        task.refresh_from_db()
        self.assertEqual(task.status, TaskRun.Status.SUCCESS)
        self.assertEqual(ComputerAnalysis.objects.get().log_file_id, log_file.pk)

    def test_due_scan_preserves_malformed_file_evidence_when_no_analysis_task_can_be_created(self):
        self.source.write_text('{broken scheduled JSON', encoding='utf-8')
        os.utime(self.source, (self.now.timestamp(), self.now.timestamp()))

        tasks = enqueue_due_schedules(now=self.now)

        self.assertEqual(len(tasks), 1)
        self.assertTrue(self.source.exists())
        with patch('net.inspections.worker.enqueue_due_schedules', return_value=[]):
            self.assertTrue(TaskWorker(
                worker_id='malformed-scan-worker', threads=1, lease_seconds=30,
            ).run_once())
        log_file = ComputerLogFile.objects.get()
        self.assertEqual(log_file.import_status, 'failed')
        self.assertIn('JSON 解析失败', log_file.parse_error)
        self.assertTrue(Path(log_file.archived_path).is_file())
        self.schedule.refresh_from_db()
        self.assertEqual(
            self.schedule.next_run_at,
            self.now + timedelta(hours=1),
        )
