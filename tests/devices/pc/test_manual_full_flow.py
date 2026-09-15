import json
from datetime import datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError

from net.models import ComputerAnalysisProfile, ComputerAnalysis, ComputerLogFile, ComputerLogTransfer, TaskRun
from net.inspections.queue import enqueue_computer_fetch_task, cancel_task
from net.devices.pc.configuration import source_snapshot
from .test_source_models import valid_smb_source
from .helpers import import_payload
from .connector_fakes import memory_connector


def raw_log(name):
    return json.dumps({'日志时间': timezone.localtime().strftime('%Y-%m-%d %H:%M:%S'),
        '系统信息概览': {'计算机名': name},
        '已安装软件列表': [{'软件名': 'BlockedGame'}, {'软件名': 'UnlistedEditor'}]}, ensure_ascii=False).encode()


class StoredScopeTests(TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.source = valid_smb_source(local_staging_directory=self.folder.name, recent_days=2)
        self.profile = ComputerAnalysisProfile.objects.create(name='Full flow', analysis_items=['software'])

    def stored(self, name, *, age=0, path=None, origin=None):
        stamp = timezone.now() - timedelta(days=age)
        log = import_payload(raw_log(name), source_path=path or f'incoming/{name}.json', modified_at=stamp).log_file
        ComputerLogTransfer.objects.create(source=self.source, log_file=log, stage='archived',
            observed_mtime=stamp, remote_source_path=path or f'incoming/{name}.json',
            source_snapshot=origin or source_snapshot(self.source))
        return log

    def test_manual_freezes_only_valid_current_source_window_without_loading_payloads(self):
        valid = self.stored('valid')
        self.stored('old', age=3)
        self.stored('elsewhere', path='incoming-other/file.json')
        self.stored('nested', path='incoming/sub/file.json')
        self.stored('old-source', origin={**source_snapshot(self.source), 'host': 'other.invalid'})
        self.stored('archive', path='processed/file.json')
        self.source.recursive=False; self.source.save()
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as queries:
            task = enqueue_computer_fetch_task(self.profile, 'manual')
        self.assertEqual(list(task.target_runs.get().fetched_logs.values_list('pk', flat=True)), [valid.pk])
        self.assertEqual(task.parameters_snapshot['reused_log_count'], 1)
        self.assertFalse(any('"payload"' in q['sql'] for q in queries))
        self.stored('added-later')
        self.assertEqual(task.target_runs.get().fetched_logs.count(), 1)

    def test_scheduled_fetch_does_not_reanalyze_stored_logs(self):
        self.stored('previously-analyzed')
        from net.models import Schedule
        schedule = Schedule.objects.create(analysis_profile=self.profile, kind='interval',
                                           interval_value=2, interval_unit='hours')
        task = enqueue_computer_fetch_task(self.profile, 'scheduled', {'schedule': schedule})
        self.assertEqual(task.target_runs.get().fetched_logs.count(), 0)
        self.assertNotIn('analysis_scope', task.parameters_snapshot)

    def test_explicit_date_window_recursive_scope_and_duplicate_transfers(self):
        from net.devices.pc.analysis_scope import stored_log_ids
        now = timezone.now()
        today = timezone.localdate(now)
        self.source.file_time_mode = 'date_range'
        self.source.range_start_date = today - timedelta(days=2)
        self.source.range_end_date = today - timedelta(days=1)
        self.source.recursive = True
        self.source.save()
        valid = self.stored('nested-valid', path='incoming/sub/valid.json')
        transfer = ComputerLogTransfer.objects.get(log_file=valid)
        transfer.observed_mtime = datetime.combine(self.source.range_start_date, time.min,
                                                   tzinfo=timezone.get_current_timezone())
        transfer.save()
        transfer.pk = None
        transfer.remote_source_path = 'incoming/duplicate.json'
        transfer.save()
        self.stored('too-early', age=3)
        self.stored('too-late')
        self.assertEqual(stored_log_ids(self.source, now=now), [valid.pk])

    def test_cancel_before_fetch_never_creates_analysis(self):
        self.stored('cancelled')
        task = enqueue_computer_fetch_task(self.profile, 'manual')
        cancel_task(task.pk)
        self.assertFalse(TaskRun.objects.filter(task_type='computer_analysis').exists())


class ManualFullFlowTests(TransactionTestCase):
    def setUp(self):
        from tests.auth import login_admin
        login_admin(self.client)
        self.folder=TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        valid_smb_source(local_staging_directory=self.folder.name)
        policy = Path(self.folder.name) / 'policy.ini'
        policy.write_text('[WHITELIST]\nbasic = Browser\n[BLACKLIST]\nkeywords = BlockedGame\n')
        self.profile=ComputerAnalysisProfile.objects.create(name='Manual full flow',
            analysis_items=['software'], software_policy_path=str(policy))
        self.connector=memory_connector({'incoming/PC.json':raw_log('PC-FULL')})
        self.connector.modified_at=timezone.now()
        from net.inspections.worker import TaskWorker
        self.worker=TaskWorker(worker_id='full-flow-worker', threads=1)

    def click(self):
        response=self.client.post(reverse('manual_task_create'), {'profile_id':str(self.profile.pk)})
        self.assertEqual(response.status_code,302)
        return TaskRun.objects.get(pk=response['Location'].strip('/').split('/')[-1])

    def run_worker(self):
        with patch('net.devices.pc.remote_ingestion.build_connector', return_value=self.connector):
            self.assertTrue(self.worker.run_once())

    def test_one_click_reanalyzes_archived_logs_with_new_rules_and_tracks_child(self):
        first=self.click(); self.run_worker(); self.run_worker()
        self.assertEqual(ComputerAnalysis.objects.count(),1)
        self.profile.software_policy_mode='blacklist';self.profile.save()
        second=self.click(); self.run_worker()
        target=second.target_runs.get()
        self.assertEqual(target.result_snapshot['discovered'],0)
        self.assertEqual(target.result_snapshot['reused'],1)
        self.assertIsNotNone(target.analysis_handoff_task_id)
        child=target.analysis_handoff_task
        self.assertEqual(child.profile_snapshot['software_policy_mode'],'blacklist')
        page=self.client.get(reverse('task_detail',args=[second.pk]))
        self.assertContains(page,'分析阶段')
        self.assertContains(page,reverse('task_detail',args=[child.pk]))
        with self.assertRaises(ValidationError):enqueue_computer_fetch_task(self.profile,'manual')
        self.run_worker()
        result=ComputerAnalysis.objects.get(task_target__task=child)
        self.assertEqual([x['详细问题'] for x in result.exceptions if x['问题类型']=='软件问题'],['BlockedGame'])
        self.assertEqual(ComputerLogFile.objects.count(),1)
        self.assertEqual(ComputerAnalysis.objects.count(),2)

    def test_manual_combines_archived_evidence_and_new_downloads(self):
        self.click()
        self.run_worker()
        self.run_worker()
        self.connector.files['incoming/PC-NEW.json'] = raw_log('PC-NEW')
        task = self.click()
        self.run_worker()
        target = task.target_runs.get()
        self.assertEqual(target.result_snapshot['reused'], 1)
        self.assertEqual(target.result_snapshot['imported'], 1)
        self.assertEqual(target.analysis_handoff_task.total_targets, 2)
        self.run_worker()
        self.assertEqual(ComputerLogFile.objects.count(), 2)
        self.assertEqual(ComputerAnalysis.objects.count(), 3)

    def test_fetch_failure_still_analyzes_frozen_evidence_and_retains_fetch_failure(self):
        self.click();self.run_worker();self.run_worker()
        task=self.click()
        with patch('net.devices.pc.remote_ingestion.build_connector', side_effect=OSError('offline')):
            self.assertTrue(self.worker.run_once())
        task.refresh_from_db();self.assertEqual(task.status,'partial')
        self.assertTrue(task.target_runs.get().analysis_handoff_task_id)
        self.run_worker();self.assertEqual(ComputerAnalysis.objects.count(),2)

    def test_empty_source_explains_no_analysis_instead_of_claiming_completion(self):
        self.connector.files.clear()
        task=self.click();self.run_worker()
        self.assertFalse(TaskRun.objects.filter(task_type='computer_analysis').exists())
        page=self.client.get(reverse('task_detail',args=[task.pk]))
        self.assertContains(page,'没有可分析的有效日志')
