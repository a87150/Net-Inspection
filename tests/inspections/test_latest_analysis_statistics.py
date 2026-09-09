from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.db import connection
from django.test.utils import CaptureQueriesContext

from net.models import Computer, ComputerAnalysis, ComputerAnalysisProfile, TaskRun, TaskTargetRun
from tests.auth import login_reader
from tests.devices.pc.helpers import create_log_file


class LatestAnalysisStatisticsTests(TestCase):
    def setUp(self):
        login_reader(self.client)

    def task(self):
        profile = ComputerAnalysisProfile.objects.create(name=f'Stats {TaskRun.objects.count()}', analysis_items=['software'])
        return TaskRun.objects.create(task_type='computer_analysis', source='manual', analysis_profile=profile)

    def analysis(self, task, findings):
        index = Computer.objects.count()
        pc = Computer.objects.create(computer_name=f'PRIVATE-PC-{index}')
        log = create_log_file(source_path=f'/logs/stat-{index}.json', content_hash=f'{index:064x}', modified_at=timezone.now())
        target = TaskTargetRun.objects.create(task=task, target_type='computer_log', target_id=str(log.pk), status='success')
        return ComputerAnalysis.objects.create(computer=pc, log_file=log, task_target=target,
            status='success', exceptions=findings, summary='PRIVATE-RECORD-TEXT')

    def test_latest_task_statistics_ignore_old_records_and_old_table_filters(self):
        old = self.task()
        self.analysis(old, [{'analysis_item': 'defender', 'severity': 'critical'}])
        latest = self.task()
        self.analysis(latest, [])
        self.analysis(latest, [{'analysis_item': 'software', 'severity': 'info'}])
        self.analysis(latest, [{'analysis_item': 'software', 'severity': 'warning'},
                               {'analysis_item': 'processes', 'severity': 'warning'},
                               {'analysis_item': 'cpu_health', 'severity': 'critical'}])
        TaskTargetRun.objects.create(task=latest, target_type='computer_log', target_id='broken', status='failed')
        response = self.client.get(reverse('computer_analysis_list'), {'filter_result_level': 'normal', 'task_page': 2})
        stats = response.context['latest_analysis_statistics']
        self.assertEqual(stats['total'], 3)
        self.assertEqual(stats['levels'], {'normal': 1, 'info': 1, 'warning': 0, 'critical': 1})
        self.assertEqual(stats['execution_failed'], 1)
        categories = {row['label']: row['count'] for row in stats['categories']}
        self.assertEqual(categories['软件'], 2)
        self.assertEqual(categories['硬件'], 1)
        self.assertEqual(categories['杀毒'], 0)
        self.assertContains(response, '最新任务统计')
        self.assertContains(response, reverse('task_detail', args=[latest.pk]))
        self.assertNotContains(response, 'PRIVATE-RECORD-TEXT')
        self.assertNotContains(response, 'PRIVATE-PC-')
        self.assertNotContains(response, '最新任务日志分析记录')
        self.assertNotIn('page_obj', response.context)

    def test_new_empty_task_does_not_reuse_previous_results(self):
        old = self.task()
        self.analysis(old, [{'analysis_item': 'software', 'severity': 'warning'}])
        latest = self.task()
        response = self.client.get(reverse('computer_analysis_list'))
        self.assertEqual(response.context['latest_task'], latest)
        self.assertEqual(response.context['latest_analysis_statistics']['total'], 0)
        self.assertContains(response, '尚无分析结果')

    def test_without_tasks_shows_empty_statistics(self):
        response = self.client.get(reverse('computer_analysis_list'))
        self.assertEqual(response.context['latest_analysis_statistics']['total'], 0)
        self.assertContains(response, '暂无日志分析任务')

    def test_summary_reads_aggregates_not_log_payloads(self):
        from index.inspections.analysis_summary import latest_analysis_statistics
        task = self.task()
        self.analysis(task, [{'analysis_item': 'patches', 'severity': 'warning'}])
        with CaptureQueriesContext(connection) as queries:
            stats = latest_analysis_statistics(task)
        self.assertEqual(stats['total'], 1)
        self.assertLessEqual(len(queries), 3)
        for query in queries:
            self.assertNotIn('"exceptions"', query['sql'])
            self.assertNotIn('"payload"', query['sql'])
            self.assertNotIn('"details"', query['sql'])
