import json
import runpy
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from net.models import ComputerAnalysisProfile
from net.devices.pc.logs import _reject_links


class FinalRuntimeTests(TestCase):
    def test_fresh_demo_log_evidence_can_be_reanalyzed(self):
        from django.core.management import call_command
        from io import StringIO
        from net.models import ComputerLogFile
        from net.devices.pc.analysis import analyze_log
        call_command('seed_demo_data', stdout=StringIO())
        profile = ComputerAnalysisProfile.objects.get(name='演示日志分析')
        for log in ComputerLogFile.objects.all():
            self.assertEqual(log.import_status, 'imported')
            self.assertEqual(analyze_log(log, profile.analysis_items).status, 'success')
    def test_application_import_rejects_python_311_before_startup(self):
        with patch('sys.version_info', (3, 11, 9)), self.assertRaisesRegex(RuntimeError, '3.12'):
            runpy.run_path(str(settings.BASE_DIR / 'net/__init__.py'))

    def test_documented_incoming_archive_layout_and_runtime_path_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'incoming'
            root.mkdir()
            _reject_links(root)
            from tests.devices.pc.test_source_models import valid_smb_source
            from tests.devices.pc.connector_fakes import MemoryConnector
            from net.devices.pc.remote_ingestion import fetch_remote_logs
            source = valid_smb_source(local_staging_directory=str(root), recursive=True,
                                      remote_processed_directory='incoming/processed',
                                      remote_failed_directory='incoming/failed')
            connector = MemoryConnector({
                'incoming/valid.json': json.dumps({'日志时间': '2026-09-07 10:00:00', '系统信息概览': {'计算机名': 'LAYOUT'}}).encode(),
                'incoming/invalid.json': b'invalid JSON',
            })
            connector.modified_at = timezone.now()
            first = fetch_remote_logs(source, task_target=None, connector=connector)
            self.assertEqual((first.imported, first.failed), (1, 1))
            second = fetch_remote_logs(source, task_target=None, connector=connector)
            self.assertEqual((second.imported, second.failed, second.duplicate), (0, 0, 0))
            self.assertEqual(sum(path.startswith('incoming/processed/') for path in connector.paths), 1)
            self.assertEqual(sum(path.startswith('incoming/failed/') for path in connector.paths), 1)
