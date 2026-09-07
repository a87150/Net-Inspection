from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.test import TestCase
from net.devices.pc.connectors.base import PCLogConnectionError
from net.devices.pc.remote_ingestion import fetch_remote_logs, recover_remote_archives
from net.devices.pc.logs import LogLeaseLost
from net.models import ComputerLogFile, ComputerLogTransfer, ComputerAnalysisProfile
from net.inspections.queue import enqueue_computer_scan_task, claim_next_task, cancel_task
from .connector_fakes import memory_connector
from .test_source_models import valid_smb_source
from .test_remote_import import payload


class RemoteIngestionTests(TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.source = valid_smb_source(local_staging_directory=self.folder.name)

    def fetch(self, connector, **kwargs):
        return fetch_remote_logs(self.source, connector=connector,
                                 now=connector.modified_at, task_target=kwargs.get('task_target'))

    def test_daily_duplicate_archives_both_files_but_imports_once(self):
        connector = memory_connector({'incoming/a.json': payload(), 'incoming/b.json': payload(hour=12)})
        summary = self.fetch(connector)
        self.assertEqual((summary.imported, summary.duplicate), (1, 1))
        self.assertEqual(ComputerLogFile.objects.count(), 1)
        self.assertEqual(len(connector.paths), 2)
        self.assertTrue(all(p.startswith('processed/') for p in connector.paths))
        self.assertEqual(ComputerLogTransfer.objects.filter(stage='completed').count(), 2)

    def test_invalid_json_archives_failed_network_error_keeps_source(self):
        connector = memory_connector({'incoming/bad.json': b'{broken',
                                      'incoming/retry.json': PCLogConnectionError('unavailable')})
        summary = self.fetch(connector)
        self.assertEqual((summary.failed, summary.skipped), (1, 1))
        self.assertIn('incoming/retry.json', connector.paths)
        self.assertTrue(any(p.startswith('failed/') for p in connector.paths))
        self.assertFalse(ComputerLogFile.objects.filter(remote_source_path='incoming/retry.json').exists())

    def test_archive_outage_retries_without_second_import(self):
        connector = memory_connector({'incoming/a.json': payload()})
        connector.fail_moves = True
        summary = self.fetch(connector)
        self.assertEqual((summary.imported, summary.move_failures), (1, 1))
        self.assertEqual(ComputerLogTransfer.objects.get().stage, 'archive_pending')
        connector.fail_moves = False
        recovered = recover_remote_archives(self.source, connector=connector)
        self.assertEqual(recovered.move_failures, 0)
        self.assertEqual(ComputerLogFile.objects.count(), 1)
        self.assertEqual(ComputerLogTransfer.objects.get().stage, 'completed')

    def test_cancel_during_download_does_not_import_or_move(self):
        profile = ComputerAnalysisProfile.objects.create(name='test', scan_directories=[self.folder.name])
        task = enqueue_computer_scan_task(profile, 'manual')
        claim_next_task('fetch-worker', 60)
        task.refresh_from_db()
        target = task.target_runs.get()
        target.task = task
        connector = memory_connector({'incoming/a.json': payload()})
        connector.after_download = lambda: cancel_task(task.pk)
        with self.assertRaises(LogLeaseLost):
            self.fetch(connector, task_target=target)
        self.assertEqual(connector.paths, ['incoming/a.json'])
        self.assertEqual(ComputerLogFile.objects.count(), 0)
        self.assertEqual(list(Path(self.folder.name).iterdir()), [])

    def test_crash_after_remote_move_recovers_from_archive_bytes(self):
        from net.devices.pc import remote_ingestion
        from django.db import OperationalError
        connector = memory_connector({'incoming/a.json': payload()})
        save = remote_ingestion._save_transfer
        def crash_on_complete(*args, **kwargs):
            if kwargs.get('stage') == 'completed':
                raise OperationalError('database offline')
            return save(*args, **kwargs)
        with patch.object(remote_ingestion, '_save_transfer', side_effect=crash_on_complete):
            summary = self.fetch(connector)
        self.assertEqual(summary.move_failures, 1)
        self.assertFalse(any(p.startswith('incoming/') for p in connector.paths))
        recovered = recover_remote_archives(self.source, connector=connector)
        self.assertEqual(recovered.move_failures, 0)
        self.assertEqual(len(connector.moves), 1)
        self.assertEqual(ComputerLogTransfer.objects.get().stage, 'completed')

    def test_new_remote_version_is_not_blocked_by_old_pending_archive(self):
        connector = memory_connector({'incoming/a.json': payload()})
        connector.fail_moves = True
        self.fetch(connector)
        connector.fail_moves = False
        connector.files['incoming/a.json'] = payload(name='PC02')
        summary = self.fetch(connector)
        self.assertEqual(summary.imported, 1)
        self.assertEqual(ComputerLogFile.objects.filter(import_status='imported').count(), 2)
        self.assertFalse(any(p.startswith('incoming/') for p in connector.paths))
