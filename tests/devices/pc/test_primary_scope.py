from django.test import TestCase

from net.models import People, ComputerAnalysisProfile, ComputerAnalysis
from net.inspections.queue import enqueue_task, claim_next_task, finish_task
from net.inspections.task_summary import build_project_task_metrics, summarize_task
from net.devices.pc.executor import execute_computer_target
from index.inspections.analysis_summary import latest_analysis_statistics
from index.inspections.records import _computer_analysis_records
from tests.devices.pc.helpers import import_payload


class PrimaryScopeTests(TestCase):
    def test_historical_orphan_is_not_counted_in_people_statistics(self):
        task = self.run_mode('logs')
        task.profile_snapshot['matching_mode'] = 'people'
        # Reproduce legacy persisted people tasks that still included orphan analyses.
        type(task).objects.filter(pk=task.pk).update(profile_snapshot=task.profile_snapshot)
        metrics = build_project_task_metrics(type(task).objects.filter(pk=task.pk))
        self.assertEqual((metrics['normal_count'], metrics['abnormal_count']), (1, 1))
        summary = summarize_task(task)
        self.assertEqual((summary['total'], summary['abnormal']), (2, 1))
        self.assertEqual(latest_analysis_statistics(task)['total'], 2)

    def run_mode(self, mode):
        People.objects.create(employee_id='A1', name='One')
        People.objects.create(employee_id='A2', name='Two')
        logs = [import_payload({'日志时间': '2026-09-07 12:00:00',
            '系统信息概览': {'计算机名': identity, '当前登录用户工号': identity},
            '当前运行进程清单': ['test']}).log_file for identity in ('A1', 'ORPHAN')]
        profile = ComputerAnalysisProfile.objects.create(name=mode, matching_mode=mode,
                                                        analysis_items=['processes'])
        task = enqueue_task(profile, [log.pk for log in logs], 'manual')
        claim_next_task('scope-worker', 60)
        for target in task.target_runs.all():
            execute_computer_target(target, worker_id='scope-worker')
        finish_task(task.pk, 'scope-worker')
        task.refresh_from_db()
        return task

    def test_people_mode_ignores_orphan_and_reports_missing_person_log(self):
        task = self.run_mode('people')
        self.assertEqual(ComputerAnalysis.objects.count(), 1)
        rows = list(_computer_analysis_records(task=task))
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(bool(getattr(row, 'missing_log', False)) for row in rows), 1)
        statistics = latest_analysis_statistics(task)
        self.assertEqual(statistics['total'], 2)
        self.assertEqual(statistics['levels']['warning'], 1)
        metrics = build_project_task_metrics(type(task).objects.filter(pk=task.pk))
        self.assertEqual((metrics['normal_count'], metrics['abnormal_count']), (1, 1))
        summary = summarize_task(task)
        self.assertEqual((summary['total'], summary['normal'], summary['abnormal']), (2, 1, 1))
        self.assertFalse(task.alert_events.exists())
        from tests.auth import login_admin
        from django.urls import reverse
        login_admin(self.client, username='scope-admin')
        response = self.client.get(reverse('analysis_problem_list', args=[task.pk]),
                                   {'category': '人员缺少日志'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<th scope="col">人员</th>', html=True)
        self.assertNotContains(response, '<th scope="col">PC 名称</th>', html=True)
        self.assertNotContains(response, '同一 PC 可能有多条记录')
        self.assertContains(response, 'Two · A2')
        self.assertContains(response, 'A2')

    def test_logs_mode_reports_orphan_but_not_person_without_log(self):
        task = self.run_mode('logs')
        rows = list(_computer_analysis_records(task=task))
        self.assertEqual(len(rows), 2)
        self.assertFalse(any(getattr(row, 'missing_log', False) for row in rows))
        orphan = ComputerAnalysis.objects.get(computer__computer_name='ORPHAN')
        self.assertTrue(any(issue['问题类型'] == '日志未匹配人员' for issue in orphan.exceptions))
        metrics = build_project_task_metrics(type(task).objects.filter(pk=task.pk))
        self.assertEqual((metrics['normal_count'], metrics['abnormal_count']), (1, 1))

    def test_departed_people_are_excluded_only_from_new_people_tasks(self):
        People.objects.create(employee_id='LEFT', name='Departed', is_active=False)
        People.objects.create(employee_id='ORPHAN', name='Departed with log', is_active=False)
        task = self.run_mode('people')
        self.assertEqual(ComputerAnalysis.objects.count(), 1)
        self.assertNotIn('LEFT', [p['employee_id'] for p in task.parameters_snapshot['personnel_roster']])
        self.assertEqual(latest_analysis_statistics(task)['total'], 2)
        People.objects.filter(employee_id='A2').update(is_active=False)
        self.assertEqual(latest_analysis_statistics(task)['total'], 2)

    def test_logs_mode_keeps_departed_people_in_frozen_context(self):
        People.objects.create(employee_id='LEFT', name='Departed', is_active=False)
        task = self.run_mode('logs')
        self.assertIn('LEFT', [p['employee_id'] for p in task.parameters_snapshot['personnel_roster']])
