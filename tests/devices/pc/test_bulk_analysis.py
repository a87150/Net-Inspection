from tests.auth import login_admin
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from net.models import Computer, ComputerLogFile, ComputerAnalysisProfile, TaskRun
from net.inspections.queue import enqueue_computer_fetch_task, cancel_task
from .test_source_models import valid_smb_source


class BulkLogAnalysisTests(TestCase):
    def setUp(self):
        login_admin(self.client, username='bulk-user')
        self.profile = ComputerAnalysisProfile.objects.create(name='bulk', analysis_items=['resource'])

    def log(self, index, prefix='match'):
        pc = Computer.objects.create(computer_name=f'{prefix}-{index}')
        return ComputerLogFile.objects.create(computer=pc, collected_date=timezone.localdate(),
            content_hash=f'{index:064x}', import_status='imported', modified_at=timezone.now(),
            source_path=f'{prefix}-{index}.json', payload={'系统信息概览': {'计算机名': pc.computer_name}})

    def post(self, mode, **extra):
        return self.client.post('/computers/logs/analyze-bulk/?q=match&page_size=20&page=2', {
            'mode': mode, 'profile_id': str(self.profile.pk), 'selected_items': ['resource'], **extra,
        })

    def test_filtered_analysis_includes_all_pages_and_excludes_nonmatches(self):
        logs = [self.log(i) for i in range(25)]
        self.log(30, 'other')
        response = self.post('filtered')
        self.assertEqual(response.status_code, 302)
        task = TaskRun.objects.get(task_type='computer_analysis')
        self.assertEqual(set(task.target_runs.values_list('target_id', flat=True)), {str(log.pk) for log in logs})
        self.assertEqual(task.status, 'queued')

    def test_latest_batch_ignores_table_filters(self):
        valid_smb_source()
        old = enqueue_computer_fetch_task(self.profile, 'manual')
        old.target_runs.get().fetched_logs.add(self.log(40))
        cancel_task(old.pk)
        latest = enqueue_computer_fetch_task(self.profile, 'manual')
        log = self.log(41, 'other')
        latest.target_runs.get().fetched_logs.add(log)
        cancel_task(latest.pk)
        response = self.post('latest', batch_id=str(latest.pk))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(TaskRun.objects.get(task_type='computer_analysis').target_runs.get().target_id, str(log.pk))

    def test_empty_selection_shows_modal_error_without_task(self):
        response = self.post('filtered')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['bulk_open'])
        self.assertTrue(response.context['bulk_error'])
        self.assertFalse(TaskRun.objects.exists())

    def test_buttons_and_configuration_window_render(self):
        self.log(1)
        response = self.client.get(reverse('computer_log_list') + '?bulk_mode=filtered')
        self.assertContains(response, '分析筛选结果')
        self.assertContains(response, '分析最新批次')
        self.assertContains(response, 'id="bulkAnalysisModal"')
        self.assertContains(response, 'name="selected_items"')

    def test_anonymous_cannot_enqueue(self):
        self.client.logout()
        self.assertEqual(self.post('filtered').status_code, 302)
        self.assertFalse(TaskRun.objects.exists())

    def test_empty_latest_batch_selects_latest_batch_with_imported_logs(self):
        valid_smb_source()
        old = enqueue_computer_fetch_task(self.profile, 'manual')
        log = self.log(50)
        old.target_runs.get().fetched_logs.add(log)
        cancel_task(old.pk)
        latest = enqueue_computer_fetch_task(self.profile, 'manual')
        cancel_task(latest.pk)
        page = self.client.get(reverse('computer_log_list') + '?bulk_mode=latest')
        self.assertEqual(page.context['bulk_batch'].pk, old.pk)
        self.assertEqual(page.context['bulk_count'], 1)
        response = self.post('latest', batch_id=str(old.pk))
        self.assertEqual(response.status_code, 302)
        task = TaskRun.objects.get(task_type='computer_analysis')
        self.assertEqual(task.target_runs.get().target_id, str(log.pk))

    def test_duplicate_submission_does_not_enqueue_second_task(self):
        self.log(51)
        self.assertEqual(self.post('filtered').status_code, 302)
        response = self.post('filtered')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['bulk_error'])
        self.assertEqual(TaskRun.objects.filter(task_type='computer_analysis').count(), 1)

    def test_filtered_analysis_excludes_failed_evidence(self):
        log = self.log(52)
        bad = self.log(53)
        bad.import_status = 'failed'
        bad.save(update_fields=['import_status'])
        self.assertEqual(self.post('filtered').status_code, 302)
        task = TaskRun.objects.get(task_type='computer_analysis')
        self.assertEqual(task.target_runs.get().target_id, str(log.pk))
