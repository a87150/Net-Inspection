"""Opt-in real evidence checks. Source DB/share are read-only; writes use test DB."""
import json
import os
import sqlite3
from datetime import timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.test import TransactionTestCase
from django.utils.dateparse import parse_datetime
from net.devices.pc.logs import import_log_bytes
from net.devices.pc.configuration import source_from_snapshot
from net.devices.pc.connectors.windows import WindowsPCLogConnector
from net.inspections.queue import enqueue_task
from net.inspections.worker import TaskWorker
from net.models import ComputerAnalysis, ComputerAnalysisProfile


@skipUnless(os.environ.get('NET_TEST_REAL_PC_LOGS') == '1', 'Explicit read-only real-evidence check')
class RealLegacyLogsTests(TransactionTestCase):
    def test_actual_failed_evidence_and_one_shared_file(self):
        source_db = Path('demo-runtime/demo.sqlite3').resolve()
        db = sqlite3.connect(source_db.as_uri() + '?mode=ro', uri=True)
        try:
            rows = db.execute("SELECT payload,modified_at FROM net_computerlogfile WHERE import_status='failed' AND parse_error=?",
                              ('日志时间缺失或格式无效。',)).fetchall()
            transfer = db.execute("SELECT t.source_snapshot,t.remote_archive_path,l.modified_at FROM net_computerlogtransfer t JOIN net_computerlogfile l ON l.id=t.log_file_id WHERE l.import_status='failed' AND t.stage='completed' AND t.remote_archive_path<>'' ORDER BY t.created_at DESC LIMIT 1").fetchone()
        finally:
            db.close()
        self.assertTrue(rows)
        checked = 0
        for raw_payload, modified in rows:
            data = json.loads(raw_payload)
            if '日志时间' in data:
                continue
            outcome = import_log_bytes(raw=json.dumps(data, ensure_ascii=False).encode('utf-8'),
                source_path='real-evidence-validation', modified_at=parse_datetime(modified).replace(tzinfo=timezone.utc),
                source_protocol='smb', remote_source_path='real-evidence-validation', transfer=None)
            self.assertIn(outcome.status, ('imported', 'duplicate_day', 'duplicate_content'))
            self.assertEqual(outcome.log_file.import_status, 'imported')
            checked += 1
        self.assertGreater(checked, 0)
        self.assertIsNotNone(transfer)
        source = source_from_snapshot(json.loads(transfer[0]))
        source.smb_auth_mode = 'system'
        connector = WindowsPCLogConnector(source)
        try:
            with TemporaryDirectory() as directory:
                path = Path(directory) / 'sample.json'
                connector.download(transfer[1], path)
                outcome = import_log_bytes(raw=path.read_bytes(), source_path=str(path),
                    modified_at=parse_datetime(transfer[2]).replace(tzinfo=timezone.utc), source_protocol='smb',
                    remote_source_path=transfer[1], transfer=None)
        finally:
            connector.close()
        self.assertEqual(outcome.log_file.import_status, 'imported')
        profile = ComputerAnalysisProfile.objects.create(name='real-evidence-analysis', analysis_items=['resource'])
        task = enqueue_task(profile, [outcome.log_file.pk], 'manual')
        TaskWorker(worker_id='real-evidence-check').run_once()
        task.refresh_from_db()
        self.assertIn(task.status, ('success', 'failed'))
        analysis = ComputerAnalysis.objects.get(log_file=outcome.log_file)
        self.assertIn('resource', analysis.details)
        self.assertEqual(task.target_runs.get().result_id, str(analysis.pk))
        print(f'REAL_EVIDENCE_VALIDATED={checked}; SHARED_FILE_READ=1; WORKER_ANALYSES=1; SOURCE_WRITES=0')
