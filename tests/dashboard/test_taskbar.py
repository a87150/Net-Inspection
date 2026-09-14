from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

from django.test import TestCase
from django.urls import reverse
from tests.auth import login_admin
from django.utils import timezone

from net.models import (
    ComputerAnalysisProfile,
    InspectionProfile,
    PeopleSyncSource,
    TaskRun,
    TaskTargetRun,
)
from net.inspections.task_summary import inspection_task_queryset, summarize_task, summarize_tasks


class TaskbarTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.headers = []
        self.rows = []
        self.links = []
        self._cells = None
        self._cell = None
        self._cell_tag = None

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href')
            if href:
                self.links.append(href)
        if tag == 'tr':
            self._cells = []
        elif tag in {'th', 'td'} and self._cells is not None:
            self._cell = []
            self._cell_tag = tag

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag == self._cell_tag and self._cell is not None:
            value = ''.join(self._cell).strip()
            self._cells.append(value)
            self._cell = None
            self._cell_tag = None
        elif tag == 'tr' and self._cells is not None:
            if self._cells and any(cell for cell in self._cells):
                if not self.headers:
                    self.headers = self._cells
                else:
                    self.rows.append(self._cells)
            self._cells = None


class HomeTaskbarTests(TestCase):
    def setUp(self):
        login_admin(self.client)
        self.profile = InspectionProfile.objects.create(
            name='首页巡检配置',
            device_type=InspectionProfile.DeviceType.SERVER,
            selected_items=['cpu'],
        )

    def make_tasks(self, count, *, task_type):
        for number in range(count):
            TaskRun.objects.create(
                task_type=task_type,
                source=TaskRun.Source.MANUAL,
                inspection_profile=self.profile,
                total_targets=1,
                target_scope_snapshot={
                    'targets': [{'target_type': 'server', 'target_id': str(number)}],
                },
            )

    def make_people_task(self):
        source = PeopleSyncSource.objects.create(
            name='人员目录同步预览',
            source_type=PeopleSyncSource.SourceType.FEISHU,
            source_key='home-taskbar-people',
        )
        TaskRun.objects.create(
            task_type=TaskRun.TaskType.PEOPLE_PREVIEW,
            source=TaskRun.Source.MANUAL,
            people_source=source,
            total_targets=1,
            target_scope_snapshot={
                'targets': [{'target_type': 'people_source', 'target_id': str(source.pk)}],
            },
        )

    def test_home_lists_ten_execution_tasks_and_paginates(self):
        """Dropping independent task pagination would hide inspection history."""
        self.make_tasks(12, task_type=TaskRun.TaskType.INSPECTION)

        response = self.client.get(reverse('index'))

        self.assertContains(response, '巡检任务栏')
        self.assertEqual(len(response.context['task_page'].object_list), 10)
        self.assertContains(response, '?task_page=2')
        self.assertContains(
            response,
            '<span class="page-link text-white">1 / 2</span>',
            html=True,
        )

    def test_task_page_two_preserves_unrelated_query_parameters(self):
        """Characterize task pagination as independent from other dashboard queries."""
        self.make_tasks(12, task_type=TaskRun.TaskType.INSPECTION)

        response = self.client.get(reverse('index'), {
            'task_page': '2',
            'filter': 'active',
            'q': 'switch',
        })
        parser = TaskbarTableParser()
        parser.feed(response.content.decode(response.charset))
        previous_link = next(
            link for link in parser.links
            if parse_qs(urlsplit(link).query).get('task_page') == ['1']
        )

        self.assertEqual(parse_qs(urlsplit(previous_link).query), {
            'task_page': ['1'],
            'filter': ['active'],
            'q': ['switch'],
        })

    def test_people_and_domain_tasks_are_excluded(self):
        """Showing directory operations here would mix non-asset work into inspection follow-up."""
        self.make_people_task()

        response = self.client.get(reverse('index'))

        self.assertNotContains(response, '人员目录同步预览')

    def test_task_summary_separates_analysis_health_from_scan_completion(self):
        """Counting a successful scan as normal would overstate PC analysis health."""
        profile = ComputerAnalysisProfile.objects.create(
            name='首页分析配置',
            analysis_items=['activation'],
        )
        analysis_task = TaskRun.objects.create(
            task_type=TaskRun.TaskType.COMPUTER_ANALYSIS,
            source=TaskRun.Source.MANUAL,
            analysis_profile=profile,
            total_targets=4,
            target_scope_snapshot={
                'targets': [
                    {'target_type': 'computer_log', 'target_id': str(number)}
                    for number in range(4)
                ],
            },
        )
        TaskTargetRun.objects.create(
            task=analysis_task,
            target_type=TaskTargetRun.TargetType.COMPUTER_LOG,
            target_id='0',
            status=TaskRun.Status.SUCCESS,
            result_snapshot={'status': TaskRun.Status.SUCCESS},
        )
        TaskTargetRun.objects.create(
            task=analysis_task,
            target_type=TaskTargetRun.TargetType.COMPUTER_LOG,
            target_id='1',
            status=TaskRun.Status.SUCCESS,
            result_snapshot={'status': TaskRun.Status.FAILED},
        )
        TaskTargetRun.objects.create(
            task=analysis_task,
            target_type=TaskTargetRun.TargetType.COMPUTER_LOG,
            target_id='2',
            status=TaskRun.Status.QUEUED,
        )
        TaskTargetRun.objects.create(
            task=analysis_task,
            target_type=TaskTargetRun.TargetType.COMPUTER_LOG,
            target_id='3',
            status=TaskRun.Status.CANCELLED,
        )
        scan_task = TaskRun.objects.create(
            task_type=TaskRun.TaskType.COMPUTER_FETCH,
            source=TaskRun.Source.MANUAL,
            analysis_profile=profile,
            total_targets=1,
            target_scope_snapshot={
                'targets': [{'target_type': 'computer_source', 'target_id': 'scan'}],
            },
        )
        TaskTargetRun.objects.create(
            task=scan_task,
            target_type=TaskTargetRun.TargetType.COMPUTER_SOURCE,
            target_id='scan',
            status=TaskRun.Status.SUCCESS,
        )

        with self.assertNumQueries(2):
            summaries = {
                summary['task'].pk: summary
                for summary in summarize_tasks(inspection_task_queryset())
            }

        analysis = summaries[analysis_task.pk]
        scan = summaries[scan_task.pk]
        self.assertEqual(
            {
                key: analysis[key]
                for key in ('total', 'normal', 'abnormal', 'pending', 'cancelled')
            },
            {'total': 4, 'normal': 1, 'abnormal': 1, 'pending': 1, 'cancelled': 1},
        )
        self.assertEqual(scan['fetch_success'], 1)
        self.assertEqual(scan['normal'], 0)
        self.assertEqual(scan['abnormal'], 0)

    def test_task_summary_marks_unmaterialized_targets_as_pending(self):
        """Using loaded rows as the total would silently lose queued task targets."""
        task = TaskRun.objects.create(
            task_type=TaskRun.TaskType.INSPECTION,
            source=TaskRun.Source.MANUAL,
            inspection_profile=self.profile,
            total_targets=3,
            target_scope_snapshot={
                'targets': [
                    {'target_type': 'server', 'target_id': str(number)}
                    for number in range(3)
                ],
            },
        )
        TaskTargetRun.objects.create(
            task=task,
            target_type=TaskTargetRun.TargetType.SERVER,
            target_id='0',
            status=TaskRun.Status.SUCCESS,
            result_snapshot={'status': TaskRun.Status.SUCCESS},
        )

        summary = summarize_task(inspection_task_queryset().get(pk=task.pk))

        self.assertEqual(summary['total'], 3)
        self.assertEqual(summary['normal'], 1)
        self.assertEqual(summary['pending'], 2)
        self.assertEqual(
            summary['integrity_warning'],
            '目标计数完整性警告：缺少 2 条目标明细，已计入待处理',
        )
        self.assertEqual(
            sum(
                summary[key]
                for key in ('normal', 'abnormal', 'pending', 'cancelled', 'fetch_success')
            ),
            summary['total'],
        )

        response = self.client.get(reverse('index'))
        self.assertContains(response, summary['integrity_warning'])

    def test_task_summary_conserves_surplus_materialized_targets(self):
        """Legacy surplus rows must increase the displayed total and raise a warning."""
        task = TaskRun.objects.create(
            task_type=TaskRun.TaskType.INSPECTION,
            source=TaskRun.Source.MANUAL,
            inspection_profile=self.profile,
            total_targets=1,
            target_scope_snapshot={
                'targets': [{'target_type': 'server', 'target_id': 'declared'}],
            },
        )
        for target_id, result_status in (
            ('materialized-1', TaskRun.Status.SUCCESS),
            ('materialized-2', TaskRun.Status.FAILED),
        ):
            TaskTargetRun.objects.create(
                task=task,
                target_type=TaskTargetRun.TargetType.SERVER,
                target_id=target_id,
                status=TaskRun.Status.SUCCESS,
                result_snapshot={'status': result_status},
            )

        summary = summarize_task(inspection_task_queryset().get(pk=task.pk))

        self.assertEqual(summary['total'], 2)
        self.assertEqual(summary['normal'], 1)
        self.assertEqual(summary['abnormal'], 1)
        self.assertEqual(
            summary['integrity_warning'],
            '目标计数完整性警告：多出 1 条目标明细，已按明细展示',
        )
        self.assertEqual(
            sum(
                summary[key]
                for key in ('normal', 'abnormal', 'pending', 'cancelled', 'fetch_success')
            ),
            summary['total'],
        )

        response = self.client.get(reverse('index'))
        self.assertContains(response, summary['integrity_warning'])

    def test_home_taskbar_shows_created_and_finished_times(self):
        """A single time column cannot distinguish task creation from completion."""
        unfinished = TaskRun.objects.create(
            task_type=TaskRun.TaskType.INSPECTION,
            source=TaskRun.Source.MANUAL,
            inspection_profile=self.profile,
            total_targets=0,
            target_scope_snapshot={
                'targets': [{'target_type': 'server', 'target_id': 'unfinished'}],
            },
        )
        finished_at = timezone.now().replace(microsecond=0)
        finished = TaskRun.objects.create(
            task_type=TaskRun.TaskType.INSPECTION,
            source=TaskRun.Source.MANUAL,
            inspection_profile=self.profile,
            status=TaskRun.Status.CANCELLED,
            finished_at=finished_at,
            total_targets=0,
            target_scope_snapshot={
                'targets': [{'target_type': 'server', 'target_id': 'finished'}],
            },
        )

        response = self.client.get(reverse('index'))
        parser = TaskbarTableParser()
        parser.feed(response.content.decode(response.charset))
        created_column = parser.headers.index('创建时间')
        finished_column = parser.headers.index('完成时间')

        self.assertTrue(all(row[created_column] != '—' for row in parser.rows))
        self.assertIn('—', [row[finished_column] for row in parser.rows])
        self.assertIn(
            timezone.localtime(finished_at).strftime('%Y-%m-%d %H:%M:%S'),
            [row[finished_column] for row in parser.rows],
        )
        self.assertTrue(TaskRun.objects.filter(pk=unfinished.pk).exists())

    def test_home_renders_cancelled_count_in_its_own_column(self):
        """Folding cancellation into pending hides completed cancellations from operators."""
        for number, status in enumerate((TaskRun.Status.CANCELLED, TaskRun.Status.QUEUED)):
            task = TaskRun.objects.create(
                task_type=TaskRun.TaskType.INSPECTION,
                source=TaskRun.Source.MANUAL,
                inspection_profile=self.profile,
                total_targets=1,
                target_scope_snapshot={
                    'targets': [{'target_type': 'server', 'target_id': f'cancel-{number}'}],
                },
            )
            TaskTargetRun.objects.create(
                task=task,
                target_type=TaskTargetRun.TargetType.SERVER,
                target_id=f'cancel-{number}',
                status=status,
            )

        response = self.client.get(reverse('index'))
        parser = TaskbarTableParser()
        parser.feed(response.content.decode(response.charset))
        self.assertIn('已取消', parser.headers)
        cancelled_column = parser.headers.index('已取消')

        self.assertCountEqual(
            [row[cancelled_column] for row in parser.rows],
            ['1', '0'],
        )

    def test_home_empty_taskbar_spans_the_cancelled_column(self):
        """A stale colspan would leave the empty state misaligned with the taskbar header."""
        response = self.client.get(reverse('index'))

        self.assertContains(response, '<td colspan="12">')
