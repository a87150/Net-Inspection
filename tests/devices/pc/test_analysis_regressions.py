"""Regressions for the four Task 5 review findings."""

import hashlib
import json
import os
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connections, transaction
from django.db.models.query import QuerySet
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from tests.devices.pc.helpers import create_log_file

from net.models import Computer, ComputerAnalysisProfile, ComputerLogFile, RecordStatus
from net.devices.pc.analysis import analyze_log
from net.devices.pc import logs as computer_logs


class SelectedItemSchemaTests(TestCase):
    NORMAL = {
        'activation': {'Windows激活信息': {'许可证状态': '已授权'}},
        'software': {'已安装软件列表': [{'软件名': 'Editor'}]},
        'processes': {'当前运行进程清单': [{'进程名': 'explorer'}]},
        'bitlocker': {'BitLocker状态': {'磁盘卷信息': [{'卷': 'C', '转换状态': '完全加密'}]}},
        'defender': {'WindowsDefender状态': {'当前病毒库版本': '1.2.3', '上次更新时间': '2026-08-31 09:00:00'}},
        'patches': {'系统更新历史': [{'补丁名称': 'KB123', '日期': '2026-08-31 09:00:00'}]},
        'domain_trust': {'当前与域服务器通讯情况': '正常通讯'},
        'group_policy': {'已应用策略': {'计算机策略': [], '用户策略': []}},
        'resource': {'计算机硬件资源情况': {'当前CPU占用率': '23%', '当前内存使用率': '48%'}},
        'event_findings': {'事件发现': [{'级别': 'Information', '消息': 'Started'}]},
    }

    def setUp(self):
        computer = Computer.objects.create(computer_name='SCHEMA-PC')
        self.log = create_log_file(
            computer=computer, source_path='fixture.json', modified_at=timezone.now(),
            content_hash='a' * 64, import_status='imported', payload={},
        )

    def analyze(self, item, fields):
        self.log.payload = {'系统信息概览': {'计算机名': 'SCHEMA-PC'}, **fields}
        return analyze_log(self.log, [item])

    def test_every_selected_item_absent_is_failed_and_explicitly_missing(self):
        for item in self.NORMAL:
            with self.subTest(item=item):
                result = self.analyze(item, {})
                self.assertEqual(result.result_level, 'info')
                self.assertEqual(result.exceptions[0]['analysis_item'], item)
                self.assertEqual(result.exceptions[0]['data_state'], 'missing')

    def test_every_selected_item_unknown_or_wrong_type_is_not_healthy(self):
        for item, fields in self.NORMAL.items():
            for unknown in (None, '未知', {'error': '未采集'}, [None]):
                with self.subTest(item=item, unknown=unknown):
                    result = self.analyze(item, {key: unknown for key in fields})
                    self.assertEqual(result.result_level, 'info')
                    self.assertEqual(result.exceptions[0]['data_state'], 'unknown')

    def test_every_selected_item_normal_schema_can_be_analyzed(self):
        for item, fields in self.NORMAL.items():
            with self.subTest(item=item):
                result = self.analyze(item, fields)
                self.assertEqual(result.status, RecordStatus.SUCCESS)
                self.assertEqual(result.exceptions, [])
                self.assertEqual(
                    set(result.details) - {
                        'collection_diagnostics', 'enrichment', 'rules', 'platform',
                        'severity_counts', 'health_status',
                    },
                    {item},
                )

    def test_present_empty_collections_are_distinct_from_missing(self):
        for item, key in (
            ('software', '已安装软件列表'), ('processes', '当前运行进程清单'),
            ('patches', '系统更新历史'), ('event_findings', '事件发现'),
        ):
            with self.subTest(item=item):
                result = self.analyze(item, {key: []})
                self.assertEqual(result.status, RecordStatus.SUCCESS)
                self.assertEqual(result.details[item], [])
        result = self.analyze('bitlocker', {'BitLocker状态': {'磁盘卷信息': []}})
        self.assertEqual(result.result_level, 'info')
        self.assertEqual(result.exceptions[0]['data_state'], 'empty')

    def test_nested_unknown_status_and_resource_values_are_not_healthy(self):
        cases = {
            'activation': {'Windows激活信息': {'许可证状态': '未知'}},
            'defender': {'WindowsDefender状态': {'当前病毒库版本': '', '上次更新时间': ''}},
            'resource': {'计算机硬件资源情况': {'当前CPU占用率': '获取失败', '当前内存使用率': '未知'}},
            'domain_trust': {'当前与域服务器通讯情况': '未知'},
            'event_findings': {'事件发现': [{'级别': '未知'}]},
        }
        for item, fields in cases.items():
            with self.subTest(item=item):
                result = self.analyze(item, fields)
                self.assertEqual(result.result_level, 'info')
                self.assertEqual(result.exceptions[0]['data_state'], 'unknown')

    def test_empty_primary_events_do_not_fall_through_to_legacy_alias(self):
        result = self.analyze('event_findings', {
            '事件发现': [], '事件日志': [{'级别': 'error', '消息': 'old data'}],
        })
        self.assertEqual(result.status, RecordStatus.SUCCESS)
        self.assertEqual(result.details['event_findings'], [])


class RemoteImporterReviewTests(TransactionTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        from tests.devices.pc.test_source_models import valid_smb_source
        from tests.devices.pc.connector_fakes import MemoryConnector
        self.source = valid_smb_source(local_staging_directory=self.tempdir.name)
        self.raw = json.dumps({'日志时间': '2026-09-07 10:00:00',
                               '系统信息概览': {'计算机名': 'RACE-PC'}}).encode()
        self.connector = MemoryConnector({'incoming/log.json': self.raw})
        self.connector.modified_at = timezone.now()

    def fetch(self):
        from net.devices.pc.remote_ingestion import fetch_remote_logs
        return fetch_remote_logs(self.source, task_target=None, connector=self.connector)

    def ingest(self, raw):
        from tests.devices.pc.helpers import import_payload
        return import_payload(raw)

    def test_changed_source_before_archive_retains_original_evidence_and_remote_source(self):
        self.connector.after_download = lambda: self.connector.files.update({'incoming/log.json': self.raw + b' '})
        result = self.fetch()
        self.assertEqual(result.imported, 1)
        self.assertEqual(result.move_failures, 1)
        self.assertIn('incoming/log.json', self.connector.files)
        self.assertEqual(ComputerLogFile.objects.get().content_hash, hashlib.sha256(self.raw).hexdigest())

    def test_failed_archive_retries_without_duplicate_evidence(self):
        self.connector.fail_moves = True
        first = self.fetch()
        self.assertEqual(first.imported, 1)
        self.assertEqual(first.move_failures, 1)
        self.assertIn('incoming/log.json', self.connector.files)
        self.connector.fail_moves = False
        self.fetch()
        self.assertEqual(ComputerLogFile.objects.count(), 1)
        self.assertNotIn('incoming/log.json', self.connector.files)
        self.assertEqual(len(self.connector.moves), 1)

    def test_archive_completion_failure_recovers_durable_transfer(self):
        from net.devices.pc import remote_ingestion
        original = remote_ingestion._save_transfer
        def save(transfer, target, **values):
            if values.get('stage') == 'completed':
                raise DatabaseError('crash after remote move')
            return original(transfer, target, **values)
        with patch.object(remote_ingestion, '_save_transfer', side_effect=save):
            self.fetch()
        self.assertEqual(len(self.connector.moves), 1)
        self.fetch()
        self.assertEqual(ComputerLogFile.objects.count(), 1)
        self.assertEqual(len(self.connector.moves), 1)

    def test_concurrent_failed_payload_imports_deduplicate(self):
        for raw in (b'{broken', b'{"not_system_info":true}'):
            barrier = threading.Barrier(2)
            def ingest():
                connections.close_all()
                try:
                    barrier.wait(timeout=5)
                    return self.ingest(raw).log_file.pk
                finally:
                    connections.close_all()
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(ingest), pool.submit(ingest)]
                ids = [future.result(timeout=10) for future in futures]
            self.assertEqual(ids[0], ids[1])
            self.assertEqual(ComputerLogFile.objects.get(pk=ids[0]).import_status, 'failed')
        self.assertFalse(Computer.objects.exists())

    def test_unique_conflict_retries_whole_transaction_for_valid_and_failed_evidence(self):
        for raw in (self.raw, b'{broken', b'{"not_system_info":true}'):
            original_create = QuerySet.create
            attempts = []
            def insert(queryset, **kwargs):
                if queryset.model is ComputerLogFile:
                    attempts.append(connections['default'].in_atomic_block)
                    if len(attempts) == 1:
                        raise IntegrityError('concurrent insert')
                return original_create(queryset, **kwargs)
            with patch.object(QuerySet, 'create', new=insert):
                result = self.ingest(raw)
            self.assertEqual(attempts, [True, True])
            self.assertEqual(result.log_file.content_hash, hashlib.sha256(raw).hexdigest())
            self.assertEqual(ComputerLogFile.objects.filter(content_hash=result.log_file.content_hash).count(), 1)

    def test_import_and_fetch_refuse_outer_transaction(self):
        with transaction.atomic():
            with self.assertRaises((ValidationError, RuntimeError)):
                self.ingest(self.raw)
            with self.assertRaises((ValidationError, RuntimeError)):
                self.fetch()
        self.assertFalse(ComputerLogFile.objects.exists())
        self.assertIn('incoming/log.json', self.connector.files)

    def test_one_bad_candidate_does_not_abort_other_remote_files(self):
        from net.devices.pc.connectors.base import PCLogConnectionError
        self.connector.files['incoming/bad.json'] = PCLogConnectionError('download unavailable')
        result = self.fetch()
        self.assertEqual(result.imported, 1)
        self.assertEqual(result.skipped, 1)
        self.assertIn('incoming/bad.json', self.connector.files)

    def test_nonfinite_and_excessively_nested_json_are_failed_evidence(self):
        for raw in (b'{"value":NaN}', b'{"value":' + b'[' * 2000 + b']' * 2000 + b'}'):
            result = self.ingest(raw)
            self.assertEqual(result.status, 'failed_schema')
            self.assertTrue(result.log_file.parse_error)

    def test_size_limit_is_enforced_before_database_import(self):
        with patch.object(computer_logs, 'MAX_LOG_FILE_BYTES', 8):
            with self.assertRaises(ValidationError):
                self.ingest(self.raw)
        self.assertFalse(ComputerLogFile.objects.exists())
