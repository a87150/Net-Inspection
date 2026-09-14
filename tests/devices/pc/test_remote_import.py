import json
from datetime import datetime, timezone
from django.test import TestCase
from net.devices.pc.logs import import_log_bytes
from net.models import ComputerLogFile


def payload(name='PC01', hour=8, **extra):
    return json.dumps({
        '日志时间': f'2026-09-07 {hour:02}:00:00',
        '系统信息概览': {'计算机名': name, '系统版本类型': 'Windows 11'},
        '计算机硬件资源情况': {'CPU型号': 'Test CPU', '当前内存容量': '16GB'},
        **extra,
    }, ensure_ascii=False).encode('utf-8')


class RemoteImportTests(TestCase):
    def ingest(self, raw, **kwargs):
        return import_log_bytes(raw=raw, source_path='smb://source/incoming/a.json',
                                modified_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
                                source_protocol='smb', remote_source_path='incoming/a.json',
                                transfer=None, **kwargs)

    def test_import_sets_daily_identity_and_static_inventory(self):
        outcome = self.ingest(b'\xef\xbb\xbf' + payload())
        self.assertEqual(outcome.status, 'imported')
        self.assertEqual(str(outcome.log_file.collected_date), '2026-09-07')
        self.assertEqual(outcome.log_file.computer.cpu_model, 'Test CPU')
        self.assertEqual(outcome.log_file.platform, 'windows')

    def test_same_day_different_content_does_not_replace_evidence(self):
        first = self.ingest(payload())
        duplicate = self.ingest(payload(hour=12))
        self.assertEqual(duplicate.status, 'duplicate_day')
        self.assertEqual(duplicate.log_file.pk, first.log_file.pk)
        self.assertEqual(ComputerLogFile.objects.count(), 1)
        first.log_file.refresh_from_db()
        self.assertEqual(first.log_file.payload['日志时间'], '2026-09-07 08:00:00')

    def test_identical_bytes_are_content_duplicate(self):
        self.ingest(payload())
        self.assertEqual(self.ingest(payload()).status, 'duplicate_content')

    def test_invalid_time_is_failed_not_mtime_fallback(self):
        outcome = self.ingest(payload(**{'日志时间': 'bad'}))
        self.assertEqual(outcome.status, 'failed_schema')
        self.assertIsNone(outcome.log_file.computer_id)
        self.assertIn('日志时间', outcome.log_file.parse_error)

    def test_original_terminal_logs_without_timestamp_use_file_modified_time(self):
        data = json.loads(payload())
        del data['日志时间']
        outcome = self.ingest(json.dumps(data, ensure_ascii=False).encode('utf-8'))
        self.assertEqual(outcome.status, 'imported')
        self.assertEqual(str(outcome.log_file.collected_date), '2026-09-07')
        self.assertNotIn('日志时间', outcome.log_file.payload)
        self.assertEqual(outcome.log_file.computer.cpu_model, 'Test CPU')

    def test_non_finite_or_non_object_json_is_failed(self):
        for raw in (b'{"x": NaN}', b'[]', b'{broken'):
            with self.subTest(raw=raw):
                self.assertEqual(self.ingest(raw).status, 'failed_schema')
