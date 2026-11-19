"""首页 SQL 条数不能随历史任务增长。"""

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from net.models import ComputerAnalysisProfile, TaskRun, TaskTargetRun
from tests.auth import login_admin


class HomeQueryBudgetTests(TestCase):
    budget = 20

    def setUp(self):
        login_admin(self.client)
        self.profile = ComputerAnalysisProfile.objects.create(
            name='首页查询预算', analysis_items=['activation'],
        )

    def _people_task(self, index):
        task = TaskRun.objects.create(
            task_type=TaskRun.TaskType.COMPUTER_ANALYSIS,
            source=TaskRun.Source.MANUAL,
            analysis_profile=self.profile,
            total_targets=1,
            target_scope_snapshot={
                'targets': [{'target_type': 'computer_log', 'target_id': str(index)}],
            },
            profile_snapshot={'matching_mode': 'people'},
            parameters_snapshot={
                'personnel_roster': [{'id': 'P-%d' % index, 'name': '占位'}],
            },
        )
        TaskTargetRun.objects.create(
            task=task,
            target_type=TaskTargetRun.TargetType.COMPUTER_LOG,
            target_id=str(index),
            status=TaskRun.Status.SUCCESS,
            result_snapshot={'status': TaskRun.Status.SUCCESS},
        )
        return task

    def _assert_flat(self):
        """断言上限而不是精确值：模板里加一条无关查询不该让这个测试变红。"""
        with CaptureQueriesContext(connection) as context:
            response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(context.captured_queries), self.budget)

    def test_home_query_count_is_flat_regardless_of_history(self):
        for index in range(10):
            self._people_task(index)
        self._assert_flat()

        # 再加二十个。defer 字段若逐行回查，这里必然爆预算。
        for index in range(10, 30):
            self._people_task(index)
        self._assert_flat()
