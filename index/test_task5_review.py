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

from net.models import Computer, ComputerAnalysisProfile, ComputerLogFile, RecordStatus
from net.services.computer_analysis import analyze_log
from net.services import computer_logs


class SelectedItemSchemaTests(TestCase):
    NORMAL = {
        'activation': {'Windows激活信息': {'许可证状态': '已授权'}},
        'software': {'已安装软件列表': [{'软件名': 'Editor'}]},
        'processes': {'当前运行进程清单': [{'进程名': 'explorer'}]},
        'bitlocker': {'BitLocker状态': {'磁盘卷信息': [{'卷': 'C', '转换状态': '完全加密'}]}},
        'defender': {'WindowsDefender状态': {'当前病毒库版本': '1.2.3', '上次更新时间': '2026-08-31 09:00:00'}},
        'patches': {'系统更新历史': [{'补丁名称': 'KB123', '日期': '2026-08-31 09:00:00'}]},
        'domain': {'已应用策略': {}, '当前与域服务器通讯情况': '正常通讯'},
        'resource': {'计算机硬件资源情况': {'当前CPU占用率': '23%', '当前内存使用率': '48%'}},
        'event_findings': {'事件发现': [{'级别': 'Information', '消息': 'Started'}]},
    }

    def setUp(self):
        Computer.objects.create(computer_name='SCHEMA-PC')
        self.log = ComputerLogFile.objects.create(
            source_path='fixture.json', modified_at=timezone.now(),
            content_hash='a' * 64, import_status='imported', payload={},
        )

    def analyze(self, item, fields):
        self.log.payload = {'系统信息概览': {'计算机名': 'SCHEMA-PC'}, **fields}
        return analyze_log(self.log, [item])

    def test_every_selected_item_absent_is_failed_and_explicitly_missing(self):
        for item in self.NORMAL:
            with self.subTest(item=item):
                result = self.analyze(item, {})
                self.assertEqual(result.status, RecordStatus.FAILED)
                self.assertEqual(result.exceptions[0]['analysis_item'], item)
                self.assertEqual(result.exceptions[0]['data_state'], 'missing')

    def test_every_selected_item_unknown_or_wrong_type_is_not_healthy(self):
        for item, fields in self.NORMAL.items():
            for unknown in (None, '未知', {'error': '未采集'}, [None]):
                with self.subTest(item=item, unknown=unknown):
                    result = self.analyze(item, {key: unknown for key in fields})
                    self.assertEqual(result.status, RecordStatus.FAILED)
                    self.assertEqual(result.exceptions[0]['data_state'], 'unknown')

    def test_every_selected_item_normal_schema_can_be_analyzed(self):
        for item, fields in self.NORMAL.items():
            with self.subTest(item=item):
                result = self.analyze(item, fields)
                self.assertEqual(result.status, RecordStatus.SUCCESS)
                self.assertEqual(result.exceptions, [])
                self.assertEqual(set(result.details), {item})

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
        self.assertEqual(result.status, RecordStatus.FAILED)
        self.assertEqual(result.exceptions[0]['data_state'], 'empty')

    def test_nested_unknown_status_and_resource_values_are_not_healthy(self):
        cases = {
            'activation': {'Windows激活信息': {'许可证状态': '未知'}},
            'defender': {'WindowsDefender状态': {'当前病毒库版本': '', '上次更新时间': ''}},
            'resource': {'计算机硬件资源情况': {'当前CPU占用率': '获取失败', '当前内存使用率': '未知'}},
            'domain': {'已应用策略': {}, '当前与域服务器通讯情况': '未知'},
            'event_findings': {'事件发现': [{'级别': '未知'}]},
        }
        for item, fields in cases.items():
            with self.subTest(item=item):
                result = self.analyze(item, fields)
                self.assertEqual(result.status, RecordStatus.FAILED)
                self.assertEqual(result.exceptions[0]['data_state'], 'unknown')

    def test_empty_primary_events_do_not_fall_through_to_legacy_alias(self):
        result = self.analyze('event_findings', {
            '事件发现': [], '事件日志': [{'级别': 'error', '消息': 'old data'}],
        })
        self.assertEqual(result.status, RecordStatus.SUCCESS)
        self.assertEqual(result.details['event_findings'], [])


class ScannerReviewTests(TransactionTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.source = self.root / 'log.json'
        self.raw = json.dumps({'系统信息概览': {'计算机名': 'RACE-PC'}}).encode()
        self.source.write_bytes(self.raw)
        self.profile = ComputerAnalysisProfile.objects.create(
            name='review', scan_directories=[str(self.root)], analysis_items=['activation'],
        )

    def scan(self):
        # NTFS timestamp updates can be slightly ahead of the process clock.
        # Range semantics have dedicated fixed-time tests; races use a stable window.
        return computer_logs.scan_log_directory(self.profile, now=timezone.now() + timedelta(seconds=1))

    def test_growth_after_stat_is_transient_and_keeps_source_without_evidence(self):
        original = computer_logs._file_metadata

        def grow(path):
            metadata = original(path)
            path.write_bytes(b'x' * 257)
            return metadata

        with patch.object(computer_logs, 'MAX_LOG_FILE_BYTES', 256), patch.object(
            computer_logs, '_file_metadata', side_effect=grow,
        ):
            with self.assertRaises(ValidationError):
                computer_logs.import_log_file(self.profile, self.source)
        self.assertEqual(ComputerLogFile.objects.count(), 0)
        self.assertEqual(self.source.stat().st_size, 257)

    def test_descriptor_read_is_bounded_and_rejects_mutation_during_read(self):
        original = os.read
        limits = []

        def changing_read(fd, size):
            limits.append(size)
            raw = original(fd, size)
            self.source.write_bytes(self.raw + b' ')
            return raw

        with patch.object(computer_logs, 'MAX_LOG_FILE_BYTES', 256), patch('os.read', side_effect=changing_read):
            with self.assertRaises(ValidationError):
                computer_logs.import_log_file(self.profile, self.source)
        self.assertTrue(limits)
        self.assertLessEqual(max(limits), 257)
        self.assertEqual(ComputerLogFile.objects.count(), 0)
        self.assertTrue(self.source.exists())

    def test_replaced_identity_between_stat_and_read_is_rejected_even_with_same_bytes(self):
        original = computer_logs._file_metadata

        def replace(path):
            metadata = original(path)
            other = self.root / 'replacement.tmp'
            other.write_bytes(self.raw)
            os.utime(other, ns=(metadata[0].st_atime_ns, metadata[0].st_mtime_ns))
            os.replace(other, path)
            return metadata

        with patch.object(computer_logs, '_file_metadata', side_effect=replace):
            with self.assertRaises(ValidationError):
                computer_logs.import_log_file(self.profile, self.source)
        self.assertTrue(self.source.exists())
        self.assertEqual(ComputerLogFile.objects.count(), 0)

    def test_changed_source_between_import_and_archive_stays_for_retry(self):
        original = computer_logs._move_safely
        changed = json.dumps({'系统信息概览': {'计算机名': 'CHANGED-PC'}}).encode()

        def mutate(*args, **kwargs):
            self.source.write_bytes(changed)
            return original(*args, **kwargs)

        with patch.object(computer_logs, '_move_safely', side_effect=mutate):
            summary = self.scan()
        self.assertEqual(summary.move_failures, 1)
        self.assertEqual(self.source.read_bytes(), changed)
        log = ComputerLogFile.objects.get()
        self.assertEqual(log.content_hash, hashlib.sha256(self.raw).hexdigest())
        self.assertFalse(list((self.root / 'processed').glob('*.json')))
        retried = self.scan()
        self.assertEqual(retried.imported, 1)
        self.assertEqual(ComputerLogFile.objects.count(), 2)

    def test_database_failure_after_move_recovers_from_durable_pending_path(self):
        original = ComputerLogFile.save

        def fail_archive_save(instance, *args, **kwargs):
            if 'archived_path' in (kwargs.get('update_fields') or []):
                raise DatabaseError('database temporarily unavailable')
            return original(instance, *args, **kwargs)

        with patch.object(ComputerLogFile, 'save', new=fail_archive_save):
            summary = self.scan()
        self.assertEqual(summary.move_failures, 1)
        self.assertFalse(self.source.exists())
        log = ComputerLogFile.objects.get()
        pending = log.archives.get()
        self.assertEqual(pending.status, 'pending')
        self.assertEqual(Path(pending.destination_path).read_bytes(), self.raw)
        self.scan()
        log.refresh_from_db()
        pending.refresh_from_db()
        self.assertEqual(pending.status, 'completed')
        self.assertEqual(log.archived_path, pending.destination_path)
        self.assertEqual(ComputerLogFile.objects.count(), 1)

    def test_failed_move_has_pending_metadata_and_retries_without_new_evidence(self):
        with patch.object(computer_logs, '_move_safely', side_effect=OSError('offline')):
            summary = self.scan()
        self.assertEqual(summary.move_failures, 1)
        self.assertEqual(self.source.read_bytes(), self.raw)
        log = ComputerLogFile.objects.get()
        pending = log.archives.get()
        self.assertEqual(pending.status, 'pending')
        self.assertFalse(Path(pending.destination_path).exists())
        self.scan()
        self.assertFalse(self.source.exists())
        pending.refresh_from_db()
        self.assertEqual(pending.status, 'completed')
        self.assertEqual(ComputerLogFile.objects.count(), 1)

    def test_concurrent_malformed_and_static_invalid_imports_dedupe(self):
        for raw in (b'{broken', b'{"not_system_info":true}'):
            with self.subTest(raw=raw):
                self.source.write_bytes(raw)
                start = threading.Barrier(2)

                def run():
                    connections['default'].close()
                    try:
                        start.wait(timeout=5)
                        return computer_logs.import_log_file(self.profile, self.source).pk
                    finally:
                        connections['default'].close()

                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(run) for _ in range(2)]
                    ids = [future.result(timeout=10) for future in futures]
                self.assertEqual(ids[0], ids[1])
                row = ComputerLogFile.objects.get(pk=ids[0])
                self.assertEqual(row.import_status, 'failed')
                self.assertTrue(row.parse_error)
        self.assertEqual(ComputerLogFile.objects.count(), 2)
        self.assertFalse(Computer.objects.exists())

    def test_hash_conflict_restarts_transaction_before_lookup_for_all_payload_kinds(self):
        # InnoDB loser sees IntegrityError while the winning row is invisible in
        # the old snapshot. Publish that winner only AFTER a real outer rollback.
        db = connections['default']
        for raw in (self.raw, b'{broken', b'{"not_system_info":true}'):
            with self.subTest(raw=raw):
                self.source.write_bytes(raw)
                original_create, original_rollback = QuerySet.create, db.rollback
                winner = {}
                raced = False

                def insert(queryset, **kwargs):
                    nonlocal raced
                    if queryset.model is ComputerLogFile and not raced:
                        raced = True
                        winner.update(kwargs)
                        raise IntegrityError(1062, 'Duplicate entry for content_hash')
                    return original_create(queryset, **kwargs)

                def rollback():
                    original_rollback()
                    if winner:
                        # Connection is outside the failed atomic block; publish
                        # using autocommit, just as a different MySQL connection.
                        db.set_autocommit(True)
                        if raw == self.raw:
                            Computer.objects.get_or_create(computer_name='RACE-PC')
                        original_create(ComputerLogFile.objects.all(), **winner)
                        winner.clear()

                with patch.object(QuerySet, 'create', new=insert), patch.object(db, 'rollback', side_effect=rollback):
                    result = computer_logs.import_log_file(self.profile, self.source)
                self.assertEqual(result._import_outcome, 'duplicate')
                self.assertFalse(winner)
                self.assertEqual(ComputerLogFile.objects.filter(content_hash=hashlib.sha256(raw).hexdigest()).count(), 1)

    def test_import_refuses_outer_transaction_so_retry_never_reuses_snapshot(self):
        with transaction.atomic():
            with self.assertRaises((ValidationError, RuntimeError)):
                computer_logs.import_log_file(self.profile, self.source)
        self.assertFalse(ComputerLogFile.objects.exists())

    def test_persistent_expected_conflict_does_not_abort_other_candidates(self):
        second = self.root / 'second.json'
        second.write_bytes(json.dumps({'系统信息概览': {'计算机名': 'SECOND'}}).encode())
        original = QuerySet.create

        def conflict(queryset, **kwargs):
            if queryset.model is ComputerLogFile and kwargs.get('content_hash') == hashlib.sha256(self.raw).hexdigest():
                raise IntegrityError(1062, 'duplicate race')
            return original(queryset, **kwargs)

        with patch.object(QuerySet, 'create', new=conflict):
            result = self.scan()
        self.assertTrue(self.source.exists())
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.imported, 1, result)
        self.assertEqual(ComputerLogFile.objects.count(), 1)
        self.assertFalse(Computer.objects.filter(computer_name='RACE-PC').exists())

    def test_archive_rechecks_bytes_even_when_size_and_mtime_are_unchanged(self):
        original = computer_logs._move_safely

        def mutate(*args, **kwargs):
            stat = self.source.stat()
            self.source.write_bytes(self.raw.replace(b'RACE-PC', b'FAKE-PC'))
            os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            return original(*args, **kwargs)

        with patch.object(computer_logs, '_move_safely', side_effect=mutate):
            result = self.scan()
        self.assertEqual(result.move_failures, 1)
        self.assertIn(b'FAKE-PC', self.source.read_bytes())
        self.assertFalse(list((self.root / 'processed').glob('*.json')))
        self.scan()
        self.assertEqual(ComputerLogFile.objects.count(), 2)

    def test_destination_created_at_rename_boundary_is_never_overwritten(self):
        original = computer_logs._rename_noreplace
        destinations = []

        def collide(source, destination):
            Path(destination).write_bytes(b'existing evidence')
            destinations.append(destination)
            return original(source, destination)

        with patch.object(computer_logs, '_rename_noreplace', side_effect=collide):
            result = self.scan()
        self.assertEqual(result.move_failures, 1)
        self.assertEqual(Path(destinations[0]).read_bytes(), b'existing evidence')
        self.assertEqual(self.source.read_bytes(), self.raw)

    def test_changed_in_rename_window_is_restored_and_retried(self):
        original = computer_logs._rename_noreplace
        changed_once = False

        def mutate(source, destination):
            nonlocal changed_once
            if not changed_once:
                changed_once = True
                Path(source).write_bytes(self.raw.replace(b'RACE-PC', b'LATE-PC'))
            return original(source, destination)

        with patch.object(computer_logs, '_rename_noreplace', side_effect=mutate):
            result = self.scan()
        self.assertEqual(result.move_failures, 1)
        self.assertIn(b'LATE-PC', self.source.read_bytes())
        self.scan()
        self.assertEqual(ComputerLogFile.objects.count(), 2)

    def test_pending_move_failure_after_rename_and_new_source_never_strands_changed_bytes(self):
        original = computer_logs._rename_noreplace
        changed_once = False

        def mutate(source, destination):
            nonlocal changed_once
            result = original(source, destination)
            if not changed_once:
                changed_once = True
                Path(destination).write_bytes(self.raw.replace(b'RACE-PC', b'LATE-PC'))
                self.source.write_bytes(self.raw.replace(b'RACE-PC', b'NEXT-PC'))
            return result

        with patch.object(computer_logs, '_rename_noreplace', side_effect=mutate):
            self.scan()
        self.scan()
        names = {row.payload['系统信息概览']['计算机名'] for row in ComputerLogFile.objects.all()}
        self.assertEqual(names, {'RACE-PC', 'LATE-PC', 'NEXT-PC'})

    def test_profile_is_snapshotted_before_import(self):
        second = self.root / 'second.json'
        second.write_bytes(self.raw.replace(b'RACE-PC', b'NEXT-PC'))
        original = computer_logs.import_log_file

        def import_and_edit(profile, path, **kwargs):
            row = original(profile, path, **kwargs)
            self.profile.scan_directories = [str(self.root / 'does-not-exist')]
            return row

        with patch.object(computer_logs, 'import_log_file', side_effect=import_and_edit):
            result = self.scan()
        self.assertEqual(result.imported, 2, result)
        self.assertFalse(result.errors)

    def test_recursive_and_direct_import_reject_linked_directory_components(self):
        actual = self.root / 'actual'
        actual.mkdir()
        linked = self.root / 'linked'
        try:
            linked.symlink_to(actual, target_is_directory=True)
        except OSError as exc:
            if os.name != 'nt':
                self.skipTest(f'Symlink creation unavailable: {exc}')
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(linked), str(actual)],
                           check=True, capture_output=True)
        (actual / 'linked.json').write_bytes(self.raw)
        with self.assertRaises(ValidationError):
            computer_logs.import_log_file(self.profile, linked / 'linked.json')

    def test_pending_reconciliation_refuses_outer_transaction_before_moving(self):
        with patch.object(computer_logs, '_move_safely', side_effect=OSError('offline')):
            self.scan()
        with transaction.atomic():
            with self.assertRaises((ValidationError, RuntimeError)):
                self.scan()
        self.assertTrue(self.source.exists())

    def test_nonfinite_and_excessively_nested_json_are_failed_evidence_not_poll_crashes(self):
        for raw in (b'{"value":NaN}', b'{"value":' + b'[' * 2000 + b']' * 2000 + b'}'):
            with self.subTest(raw=raw[:20]):
                self.source.write_bytes(raw)
                result = self.scan()
                self.assertEqual(result.failed, 1, result)
                log = ComputerLogFile.objects.get(content_hash=hashlib.sha256(raw).hexdigest())
                self.assertEqual(log.import_status, 'failed')
                self.assertTrue(log.parse_error)
                self.assertEqual(Path(log.archived_path).read_bytes(), raw)
