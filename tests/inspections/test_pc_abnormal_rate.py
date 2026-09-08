import hashlib
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from net.devices.pc.matching import join_analysis_rows
from net.inspections.task_summary import build_project_task_metrics
from net.models import (
    Computer,
    ComputerAnalysis,
    ComputerAnalysisProfile,
    TaskRun,
    TaskTargetRun,
)
from tests.devices.pc.helpers import create_log_file


class PCAbnormalRateTests(TestCase):
    def test_missing_log_placeholder_is_an_abnormal_warning(self):
        rows = join_analysis_rows([], [{
            'id': 'person-1',
            'employee_id': 'E001',
            'name': '无日志人员',
            'department': 'IT',
        }], 'people')

        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].missing_log)
        self.assertFalse(rows[0].ok)
        self.assertEqual(rows[0].result_level, 'warning')
        self.assertIn('判定为异常', rows[0].summary)

    def test_completed_people_task_counts_missing_log_as_abnormal(self):
        profile = ComputerAnalysisProfile.objects.create(
            name='人员异常率',
            matching_mode='people',
            analysis_items=['system'],
        )
        task = TaskRun.objects.create(
            task_type=TaskRun.TaskType.COMPUTER_ANALYSIS,
            source=TaskRun.Source.MANUAL,
            analysis_profile=profile,
            status=TaskRun.Status.SUCCESS,
            finished_at=timezone.now(),
            progress=100,
            profile_snapshot={'matching_mode': 'people'},
            parameters_snapshot={'personnel_roster': [
                {'id': 'person-1', 'employee_id': 'E001'},
                {'id': 'person-2', 'employee_id': 'E002'},
            ]},
            target_scope_snapshot={'targets': [
                {'target_type': TaskTargetRun.TargetType.COMPUTER_LOG, 'target_id': 'log-1'},
            ]},
            total_targets=1,
            completed_targets=1,
            successful_targets=1,
        )
        target = TaskTargetRun.objects.create(
            task=task,
            target_type=TaskTargetRun.TargetType.COMPUTER_LOG,
            target_id='log-1',
            status=TaskRun.Status.SUCCESS,
            result_snapshot={'health_status': 'normal'},
        )
        computer = Computer.objects.create(computer_name='PC-E001')
        log = create_log_file(
            source_path='C:/logs/PC-E001.json',
            modified_at=timezone.now(),
            content_hash=hashlib.sha256(b'PC-E001').hexdigest(),
            import_status='success',
        )
        ComputerAnalysis.objects.create(
            computer=computer,
            log_file=log,
            task_target=target,
            status='success',
            details={'enrichment': {'personnel_id': 'person-1'}},
        )

        metrics = build_project_task_metrics(TaskRun.objects.filter(pk=task.pk))

        self.assertEqual(metrics['normal_count'], 1)
        self.assertEqual(metrics['abnormal_count'], 1)
        self.assertEqual(metrics['completed_count'], 2)
        self.assertEqual(metrics['abnormal_rate'], 50.0)

    def test_running_people_task_does_not_count_missing_log_yet(self):
        profile = ComputerAnalysisProfile.objects.create(
            name='运行中的人员任务',
            matching_mode='people',
            analysis_items=['system'],
        )
        task = TaskRun.objects.create(
            task_type=TaskRun.TaskType.COMPUTER_ANALYSIS,
            source=TaskRun.Source.MANUAL,
            analysis_profile=profile,
            status=TaskRun.Status.RUNNING,
            profile_snapshot={'matching_mode': 'people'},
            started_at=timezone.now(),
            worker_id='rate-worker',
            lease_expires_at=timezone.now() + timedelta(minutes=1),
            parameters_snapshot={'personnel_roster': [
                {'id': 'person-1', 'employee_id': 'E001'},
            ]},
            target_scope_snapshot={'targets': []},
            total_targets=0,
        )

        metrics = build_project_task_metrics(TaskRun.objects.filter(pk=task.pk))

        self.assertEqual(metrics['abnormal_count'], 0)
        self.assertEqual(metrics['completed_count'], 0)
        self.assertEqual(metrics.get('abnormal_rate'), 0.0)
