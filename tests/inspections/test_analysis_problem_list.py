from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from tests.auth import login_reader
from tests.inspections import test_latest_analysis_statistics as statistics_tests


class AnalysisProblemListTests(TestCase):
    task = statistics_tests.LatestAnalysisStatisticsTests.task
    analysis = statistics_tests.LatestAnalysisStatisticsTests.analysis

    def setUp(self):
        login_reader(self.client)
        self.run = self.task()
        self.url = f'/computers/analyses/tasks/{self.run.pk}/problems/'

    def test_reader_sees_only_requested_category_and_task_including_info(self):
        record = self.analysis(self.run, [
            {'analysis_item': 'software', 'severity': 'info', '问题类型': '软件未采集', '详细问题': '没有软件清单'},
            {'analysis_item': 'cpu_health', 'severity': 'critical', '详细问题': 'UNRELATED-HARDWARE'},
        ])
        self.analysis(self.task(), [{'analysis_item': 'software', '详细问题': 'OLD-TASK-ISSUE'}])
        response = self.client.get(self.url, {'category': '软件'})
        self.assertContains(response, '<th scope="col">PC 名称</th>', html=True)
        self.assertNotContains(response, '<th scope="col">人员</th>', html=True)
        self.assertContains(response, record.computer.computer_name)
        self.assertContains(response, '没有软件清单')
        self.assertContains(response, '提示')
        self.assertContains(response, reverse('computer_analysis_detail', args=[record.pk]))
        self.assertNotContains(response, 'UNRELATED-HARDWARE')
        self.assertNotContains(response, 'OLD-TASK-ISSUE')

    def test_guest_cannot_read_details(self):
        self.client.logout()
        response = self.client.get(self.url, {'category': '软件'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_category_requires_exact_token(self):
        self.analysis(self.run, [{'analysis_item': 'software', '详细问题': 'PRIVATE-ISSUE'}])
        response = self.client.get(self.url, {'category': '软'})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'PRIVATE-ISSUE')
        self.assertContains(response, '暂无')

    def test_database_paginates_before_loading_findings(self):
        for i in range(21):
            self.analysis(self.run, [{'analysis_item': 'software', '详细问题': f'问题-{i}'}])
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url, {'category': '软件', 'page': 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_obj'].paginator.count, 21)
        self.assertEqual(len(response.context['rows']), 1)
        detail_queries = [q['sql'] for q in queries if '"exceptions"' in q['sql']]
        self.assertEqual(len(detail_queries), 1)
        self.assertIn('LIMIT 1 OFFSET 20', detail_queries[0])
        self.assertNotIn('"payload"', detail_queries[0])
        self.assertNotIn('"details"', detail_queries[0])

    def test_untrusted_issue_content_is_escaped(self):
        self.analysis(self.run, [{'analysis_item': 'software', '详细问题': '<script>alert(1)</script>'}])
        response = self.client.get(self.url, {'category': '软件'})
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_summary_renders_clickable_category_without_preloading_details(self):
        self.analysis(self.run, [{'analysis_item': 'software', '详细问题': 'NOT-PRELOADED'}])
        response = self.client.get(reverse('computer_analysis_list'))
        self.assertContains(response, 'data-bs-target="#analysisProblemsModal"')
        self.assertContains(response, self.url)
        self.assertNotContains(response, 'NOT-PRELOADED')
