from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.test import TestCase
from net.devices.pc.connectors.base import PCLogConnectionError
from net.devices.pc.remote_ingestion import fetch_remote_logs, recover_remote_archives
from net.devices.pc.logs import LogLeaseLost
from net.models import ComputerLogFile, ComputerLogTransfer, ComputerAnalysisProfile
from net.inspections.queue import enqueue_computer_fetch_task, claim_next_task, cancel_task
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

    def test_changed_server_does_not_reuse_old_pending_transfer(self):
        old = memory_connector({'incoming/a.json': payload()})
        old.fail_moves = True
        self.fetch(old)
        previous = ComputerLogTransfer.objects.get()
        self.source.host = 'replacement.test'
        self.source.save()
        new = memory_connector({'incoming/a.json': payload(name='PC02')})
        new.modified_at = old.modified_at
        summary = self.fetch(new)
        previous.refresh_from_db()
        self.assertEqual(summary.imported, 1)
        self.assertEqual(previous.stage, 'failed')
        self.assertEqual(ComputerLogTransfer.objects.count(), 2)
        self.assertTrue(all(p.startswith('processed/') for p in new.paths))

    def test_only_old_owned_staging_files_are_cleaned(self):
        import os
        import time
        root = Path(self.folder.name)
        stale = root / ('a' * 32 + '.part')
        fresh = root / ('b' * 32 + '.part')
        unrelated = root / 'operator-note.part'
        for file in (stale, fresh, unrelated):
            file.write_bytes(b'temporary')
        old = time.time() - 2 * 86400
        for file in (stale, unrelated):
            os.utime(file, (old, old))
        self.fetch(memory_connector({}))
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(unrelated.exists())

    def test_recovery_keeps_original_archive_directory(self):
        connector = memory_connector({'incoming/a.json': payload()})
        with patch('net.devices.pc.remote_ingestion._archive_one',
                   side_effect=PCLogConnectionError('interrupted before archive path')):
            self.fetch(connector)
        self.source.remote_processed_directory = 'new-processed'
        self.source.save()
        recover_remote_archives(self.source, connector=connector)
        self.assertTrue(all(p.startswith('processed/') for p in connector.paths))

    def test_parallel_downloads_are_bounded_and_use_independent_connections(self):
        import threading
        import time
        backend = memory_connector({f'incoming/{i}.json': payload(name=f'PC{i}') for i in range(4)})
        backend.modified_at = __import__('django.utils.timezone', fromlist=['now']).now()
        lock = threading.Lock()
        state = {'active': 0, 'maximum': 0}
        clients = []
        def factory(source):
            client = memory_connector({})
            client.files = backend.files
            client.modified_at = backend.modified_at
            original = client.download
            def download(path, destination):
                with lock:
                    state['active'] += 1
                    state['maximum'] = max(state['maximum'], state['active'])
                try:
                    time.sleep(0.025)
                    return original(path, destination)
                finally:
                    with lock:
                        state['active'] -= 1
            client.download = download
            clients.append(client)
            return client
        with patch('net.devices.pc.remote_ingestion.build_connector', side_effect=factory):
            summary = fetch_remote_logs(self.source, task_target=None, max_download_workers=2)
        self.assertEqual(summary.imported, 4)
        self.assertEqual(state['maximum'], 2)
        self.assertEqual(len(clients), 3)  # listing/archive connection plus two download workers
        self.assertEqual(list(Path(self.folder.name).iterdir()), [])

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
        profile = ComputerAnalysisProfile.objects.create(name='test')
        task = enqueue_computer_fetch_task(profile, 'manual')
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
