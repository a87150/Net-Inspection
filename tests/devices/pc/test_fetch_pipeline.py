from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from net.models import ComputerAnalysisProfile, ComputerLogFile, TaskRun
from net.inspections.queue import enqueue_computer_fetch_task, claim_next_task
from net.devices.pc.executor import execute_computer_fetch_target
from .test_source_models import valid_smb_source
from .connector_fakes import memory_connector
from .test_remote_import import payload
from .helpers import analysis_task_url


class FetchPipelineTests(TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.source = valid_smb_source(local_staging_directory=self.folder.name)
        self.profile = ComputerAnalysisProfile.objects.create(name='PC analysis', analysis_items=['resource'])

    def test_enqueue_freezes_source_and_uses_singleton_target(self):
        task = enqueue_computer_fetch_task(self.profile, 'manual')
        self.assertEqual(task.task_type, 'computer_fetch')
        self.assertEqual(task.target_runs.get().target_type, 'computer_source')
        self.assertEqual(task.profile_snapshot['log_source']['host'], 'files.test')
        self.source.host = 'changed.test'
        self.source.save()
        task.refresh_from_db()
        self.assertEqual(task.profile_snapshot['log_source']['host'], 'files.test')
        self.assertNotIn('password', str(task.profile_snapshot).lower())

    def test_different_profiles_cannot_fetch_the_same_source_concurrently(self):
        enqueue_computer_fetch_task(self.profile, 'manual')
        second = ComputerAnalysisProfile.objects.create(name='second')
        with self.assertRaises(ValidationError):
            enqueue_computer_fetch_task(second, 'manual')

    def test_executor_imports_and_enqueues_analysis_child(self):
        task = enqueue_computer_fetch_task(self.profile, 'manual')
        claim_next_task('fetch-worker', 60)
        connector = memory_connector({'incoming/a.json': payload()})
        connector.modified_at = timezone.now()
        with patch('net.devices.pc.remote_ingestion.build_connector', return_value=connector):
            outcome = execute_computer_fetch_target(task.target_runs.get(), worker_id='fetch-worker')
        self.assertEqual(outcome.status, 'success')
        self.assertEqual(ComputerLogFile.objects.filter(import_status='imported').count(), 1)
        child = TaskRun.objects.get(task_type='computer_analysis')
        self.assertEqual(child.selected_items_snapshot, ['resource'])
        self.assertEqual(child.target_runs.count(), 1)
        self.assertTrue(all(path.startswith('processed/') for path in connector.paths))


class RemoteFullStoryTests(TransactionTestCase):
    def test_exhausted_fetch_keeps_and_recovers_durable_analysis_handoff(self):
        from datetime import timedelta
        from net.devices.pc.logs import import_log_bytes
        from net.inspections.queue import recover_expired_tasks
        from net.inspections.worker import TaskWorker
        from net.models import ComputerAnalysis
        with TemporaryDirectory() as folder:
            valid_smb_source(local_staging_directory=folder)
            profile = ComputerAnalysisProfile.objects.create(name='recovery', analysis_items=['cpu_health'])
            parent = enqueue_computer_fetch_task(profile, 'manual')
            claim_next_task('crashed', 60)
            target = parent.target_runs.get()
            target.status = 'running'
            target.save()
            log = import_log_bytes(raw=payload(), source_path='test.json', modified_at=timezone.now(),
                                   source_protocol='smb', remote_source_path='incoming/test.json',
                                   transfer= None).log_file
            target.fetched_logs.add(log)
            TaskRun.objects.filter(pk=parent.pk).update(
                attempt_count=3, lease_expires_at=timezone.now()-timedelta(seconds=1))
            recover_expired_tasks()
            parent.refresh_from_db()
            self.assertEqual(parent.status, 'failed')
            with patch('net.devices.pc.remote_ingestion.build_connector') as connect:
                worker = TaskWorker(worker_id='handoff-recovery', threads=2)
                with patch('net.devices.pc.executor._enqueue_scanned_analyses',
                           side_effect=ValidationError('temporarily overlapping analysis')):
                    worker.run_once()
                self.assertEqual(TaskRun.objects.filter(task_type='computer_analysis').count(), 0)
                worker.run_once()
                worker.run_once()
            connect.assert_not_called()
            self.assertEqual(ComputerAnalysis.objects.count(), 1)
            self.assertEqual(TaskRun.objects.filter(task_type='computer_analysis').count(), 1)
            target.refresh_from_db()
            self.assertTrue(target.analysis_handoff_task_id)

    def test_frozen_personnel_fields_filter_render_and_export(self):
        from tests.auth import login_reader
        login_reader(self.client)
        from django.urls import reverse
        from net.devices.pc.logs import import_log_bytes
        from net.models import ComputerAnalysis
        log = import_log_bytes(raw=payload(), source_path='test.json', modified_at=timezone.now(),
                               source_protocol='smb', remote_source_path='incoming/test.json',
                               transfer=None).log_file
        ComputerAnalysis.objects.create(computer=log.computer, log_file=log, details={
            'enrichment': {'employee_number': 'TEST-001', 'personnel_name': '测试姓名',
                           'department': '研发组', 'site': '长沙'}})
        query = {'filter_department': '研发组'}
        response = self.client.get(analysis_task_url(), query)
        self.assertContains(response, 'TEST-001')
        self.assertContains(response, '研发组')
        exported = self.client.get(reverse('table_export', args=['computer_inspections']), query)
        self.assertContains(exported, 'TEST-001')
        self.assertContains(exported, '研发组')
        empty = self.client.get(reverse('table_export', args=['computer_inspections']),
                                {'filter_department': '不存在'})
        self.assertNotContains(empty, 'TEST-001')

    def test_vendor_prefixed_windows_caption_resolves_build(self):
        from net.devices.pc.checks import check_system_version
        issues = []
        check_system_version({'系统信息概览': {
            '系统主要版本名': 'Microsoft Windows 11 Pro',
            '系统详细版本': '10.0.26100'}}, issues, '23H2')
        self.assertEqual(issues, [])

    def test_worker_fetches_analyzes_and_records_alert(self):
        import json
        from pathlib import Path
        from net.inspections.worker import TaskWorker
        from net.models import ComputerAnalysis, Error_Computer, AlertPolicy, AlertEvent
        with TemporaryDirectory() as folder:
            valid_smb_source(local_staging_directory=folder)
            profile = ComputerAnalysisProfile.objects.create(
                name='full story', analysis_items=['cpu_health'], concurrent_workers=2)
            AlertPolicy.objects.create(name='default', is_default=True, mode='override')
            data = json.loads((Path(__file__).parent / 'fixtures/terminal_log_windows.json').read_text(encoding='utf-8'))
            data['计算机硬件资源情况']['当前CPU温度'] = '99°C'
            connector = memory_connector({'incoming/PC.json': json.dumps(data).encode()})
            connector.modified_at = timezone.now()
            enqueue_computer_fetch_task(profile, 'manual')
            worker = TaskWorker(worker_id='story', threads=2)
            with patch('net.devices.pc.remote_ingestion.build_connector', return_value=connector):
                self.assertTrue(worker.run_once())
                self.assertTrue(worker.run_once())
            self.assertEqual(ComputerAnalysis.objects.count(), 1)
            self.assertTrue(Error_Computer.objects.exists())
            analysis = ComputerAnalysis.objects.get()
            analysis_task = analysis.task_target.task
            self.assertEqual(AlertEvent.objects.filter(event_type='abnormal', task=analysis_task).count(), 1)
            self.assertEqual(AlertEvent.objects.filter(event_type='summary', task=analysis_task).count(), 1)
            self.assertFalse(AlertEvent.objects.filter(task__task_type='computer_fetch').exists())
            self.assertFalse(AlertEvent.objects.exclude(event_type='summary').filter(deliveries__isnull=False).exists())
            self.assertTrue(all(p.startswith('processed/') for p in connector.paths))
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_retired_upload_endpoint_does_not_accept_data(self):
        self.assertEqual(self.client.post('/api/computer_inspection/', {},
                         content_type='application/json').status_code, 404)
