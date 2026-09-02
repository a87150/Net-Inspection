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
from net.services.computer_logs import scan_log_directory, _reject_links


class FinalRuntimeTests(TestCase):
    def test_fresh_demo_log_evidence_can_be_reanalyzed(self):
        from django.core.management import call_command
        from io import StringIO
        from net.models import ComputerLogFile
        from net.services.computer_analysis import analyze_log
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
            profile = ComputerAnalysisProfile.objects.create(name='documented layout', recursive=True,
                scan_directories=[str(root)], processed_directory=str(root/'processed'), failed_directory=str(root/'failed'))
            (root/'valid.json').write_text(json.dumps({'系统信息概览': {'计算机名': 'LAYOUT'}}), encoding='utf-8')
            (root/'invalid.json').write_text('invalid JSON', encoding='utf-8')
            first = scan_log_directory(profile, now=timezone.now() + timedelta(seconds=1))
            self.assertEqual((first.imported, first.failed), (1, 1))
            second = scan_log_directory(profile)
            self.assertEqual((second.imported, second.failed, second.duplicate), (0, 0, 0))
            self.assertEqual(len(list((root/'processed').glob('*.json'))), 1)
            self.assertEqual(len(list((root/'failed').glob('*.json'))), 1)
