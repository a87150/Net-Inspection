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


class FinalRuntimeTests(TestCase):
    def test_fresh_demo_log_evidence_can_be_reanalyzed(self):
        from django.core.management import call_command
        from io import StringIO
        from net.models import ComputerLogFile
        from net.devices.pc.analysis import analyze_log
        call_command('seed_demo_data', stdout=StringIO())
        profile = ComputerAnalysisProfile.objects.get(name='演示日志分析')
        for log in ComputerLogFile.objects.all():
            self.assertEqual(analyze_log(log, profile.analysis_items).status, 'success')
    def test_application_import_rejects_python_311_before_startup(self):
        with patch('sys.version_info', (3, 11, 9)), self.assertRaisesRegex(RuntimeError, '3.12'):
            runpy.run_path(str(settings.BASE_DIR / 'net/__init__.py'))
