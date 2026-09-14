import csv

from tests import response_body
import hashlib
from io import StringIO

from django.test import TestCase
from django.urls import NoReverseMatch, reverse
from tests.auth import login_reader
from django.utils import timezone

from tests.devices.pc.helpers import create_log_file

from net.models import (
    Computer,
    ComputerAnalysis,
    ComputerAnalysisProfile,
    ComputerLogFile,
    InspectionProfile,
    Network_Device,
    Network_Device_Inspection,
    RecordStatus,
    Server,
    Server_Inspection,
    TaskRun,
    TaskTargetRun,
)


class ProjectRecordWorkspaceTests(TestCase):
    def setUp(self):
        login_reader(self.client)
        self.analysis_profile = ComputerAnalysisProfile.objects.create(
            name='记录页分析配置',
            analysis_items=['system'],
        )
        self.network_profile = InspectionProfile.objects.create(
            name='记录页网络配置',
            device_type=InspectionProfile.DeviceType.NETWORK_DEVICE,
            selected_items=['cpu'],
        )
        self.server_profile = InspectionProfile.objects.create(
            name='记录页服务器配置',
            device_type=InspectionProfile.DeviceType.SERVER,
            selected_items=['cpu'],
        )

    def make_task(self, *, profile, task_type, target_type, target_id, outcome='normal'):
        task = TaskRun.objects.create(
            task_type=task_type,
            source=TaskRun.Source.MANUAL,
            inspection_profile=(profile if task_type == TaskRun.TaskType.INSPECTION else None),
            analysis_profile=(profile if task_type != TaskRun.TaskType.INSPECTION else None),
            total_targets=1,
            target_scope_snapshot={
                'targets': [{'target_type': target_type, 'target_id': target_id}],
            },
        )
        target = TaskTargetRun.objects.create(
            task=task,
            target_type=target_type,
            target_id=target_id,
            status=TaskRun.Status.SUCCESS,
            result_snapshot={
                'status': (
                    TaskRun.Status.SUCCESS if outcome == 'normal'
                    else TaskRun.Status.FAILED
                ),
            },
        )
        return task, target

    def make_network_record(self, name, *, task, target, status=RecordStatus.SUCCESS):
        device = Network_Device.objects.create(
            device_name=name,
            ip=f'192.0.2.{Network_Device.objects.count() + 10}',
        )
        record = Network_Device_Inspection.objects.create(
            device=device,
            task_target=target,
            status=status,
            summary=f'{name} result',
        )
        TaskTargetRun.objects.filter(pk=target.pk).update(
            result_type='network_device_inspection', result_id=str(record.pk),
        )
        return record

    def make_analysis(self, name, *, task, target, status=RecordStatus.SUCCESS):
        computer = Computer.objects.create(computer_name=name)
        log = create_log_file(
            source_path=f'C:/logs/{name}.json',
            modified_at=timezone.now(),
            content_hash=hashlib.sha256(name.encode()).hexdigest(),
            import_status='success',
        )
        analysis = ComputerAnalysis.objects.create(
            computer=computer,
            log_file=log,
            task_target=target,
            status=status,
            summary=f'{name} result',
        )
        TaskTargetRun.objects.filter(pk=target.pk).update(
            result_type='computer_analysis', result_id=str(analysis.pk),
        )
        return analysis

    def test_infrastructure_page_and_export_only_use_latest_task_records(self):
        old_task, old_target = self.make_task(
            profile=self.network_profile,
            task_type=TaskRun.TaskType.INSPECTION,
            target_type=TaskTargetRun.TargetType.NETWORK_DEVICE,
            target_id='old-network',
        )
        old_record = self.make_network_record(
            'SW-OLD', task=old_task, target=old_target,
        )
        latest_task, latest_target = self.make_task(
            profile=self.network_profile,
            task_type=TaskRun.TaskType.INSPECTION,
            target_type=TaskTargetRun.TargetType.NETWORK_DEVICE,
            target_id='latest-network',
        )
        latest_record = self.make_network_record(
            'SW-LATEST', task=latest_task, target=latest_target,
        )

        response = self.client.get(reverse('record_list', args=['networks']))

        self.assertEqual(response.context['latest_task'], latest_task)
        self.assertEqual(
            [row['summary'] for row in response.context['page_obj']],
            [latest_record.summary],
        )
        self.assertNotContains(response, old_record.summary)
        export = self.client.get(reverse(
            'table_export_scoped', args=['inspection_records', 'networks'],
        ))
        rows = list(csv.DictReader(StringIO(response_body(export).decode('utf-8-sig'))))
        self.assertEqual([row['摘要'] for row in rows], [latest_record.summary])

    def test_pc_page_excludes_scan_tasks_and_only_lists_latest_analysis_results(self):
        old_task, old_target = self.make_task(
            profile=self.analysis_profile,
            task_type=TaskRun.TaskType.COMPUTER_ANALYSIS,
            target_type=TaskTargetRun.TargetType.COMPUTER_LOG,
            target_id='old-log',
        )
        old_analysis = self.make_analysis(
            'PC-OLD', task=old_task, target=old_target,
        )
        scan_task, _ = self.make_task(
            profile=self.analysis_profile,
            task_type=TaskRun.TaskType.COMPUTER_FETCH,
            target_type=TaskTargetRun.TargetType.COMPUTER_SOURCE,
            target_id='scan-wrapper',
        )
        latest_task, latest_target = self.make_task(
            profile=self.analysis_profile,
            task_type=TaskRun.TaskType.COMPUTER_ANALYSIS,
            target_type=TaskTargetRun.TargetType.COMPUTER_LOG,
            target_id='latest-log',
        )
        latest_analysis = self.make_analysis(
            'PC-LATEST', task=latest_task, target=latest_target,
        )

        response = self.client.get(reverse('computer_analysis_list'))

        task_ids = [summary['task'].pk for summary in response.context['task_page']]
        self.assertEqual(task_ids, [latest_task.pk, old_task.pk])
        self.assertNotIn(scan_task.pk, task_ids)
        self.assertEqual(
            response.context['latest_analysis_statistics']['total'], 1,
        )
        self.assertNotContains(response, old_analysis.summary)
        detail = self.client.get(reverse('task_detail', args=[latest_task.pk]))
        self.assertEqual(list(detail.context['page_obj']), [latest_analysis])

    def test_task_metrics_exclude_pending_and_cancelled_from_failure_rate(self):
        task = TaskRun.objects.create(
            task_type=TaskRun.TaskType.INSPECTION,
            source=TaskRun.Source.MANUAL,
            inspection_profile=self.server_profile,
            total_targets=4,
            target_scope_snapshot={
                'targets': [
                    {'target_type': 'server', 'target_id': str(index)}
                    for index in range(4)
                ],
            },
        )
        for target_id, status, result_status in (
            ('0', TaskRun.Status.SUCCESS, TaskRun.Status.SUCCESS),
            ('1', TaskRun.Status.SUCCESS, TaskRun.Status.FAILED),
            ('2', TaskRun.Status.QUEUED, None),
            ('3', TaskRun.Status.CANCELLED, None),
        ):
            TaskTargetRun.objects.create(
                task=task,
                target_type=TaskTargetRun.TargetType.SERVER,
                target_id=target_id,
                status=status,
                result_snapshot=({'status': result_status} if result_status else {}),
            )

        response = self.client.get(reverse('record_list', args=['servers']))
        metrics = response.context['task_metrics']

        self.assertEqual(metrics['task_count'], 1)
        self.assertEqual(metrics['completed_count'], 2)
        self.assertEqual(metrics['abnormal_count'], 1)
        self.assertEqual(metrics['failure_rate'], 50.0)
        self.assertEqual(metrics['latest_task_at'], task.created_at)

    def test_project_task_list_uses_seven_rows_per_page(self):
        tasks = []
        for index in range(9):
            task, _ = self.make_task(
                profile=self.server_profile,
                task_type=TaskRun.TaskType.INSPECTION,
                target_type=TaskTargetRun.TargetType.SERVER,
                target_id=f'server-{index}',
            )
            tasks.append(task)

        first = self.client.get(reverse('record_list', args=['servers']))
        second = self.client.get(
            reverse('record_list', args=['servers']), {'task_page': 2},
        )

        self.assertEqual(len(first.context['task_page']), 7)
        self.assertEqual(len(second.context['task_page']), 2)
        self.assertContains(first, 'task_page=2')

    def test_old_computer_statistics_route_is_removed(self):
        with self.assertRaises(NoReverseMatch):
            reverse('detail', args=['computers'])
        self.assertEqual(self.client.get('/detail/computers/').status_code, 404)

    def test_home_uses_the_former_statistics_button_style_for_pc_records(self):
        response = self.client.get(reverse('index'))
        computer = next(
            item for item in response.context['items'] if item['key'] == 'computers'
        )

        self.assertNotIn('stats_url', computer)
        self.assertContains(
            response,
            '<a href="/computers/analyses/" class="btn btn-outline-secondary btn-sm">'
            '日志分析记录</a>',
            html=True,
        )
        self.assertNotContains(response, '统计详情')
