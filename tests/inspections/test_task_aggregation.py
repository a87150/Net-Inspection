from datetime import timedelta
from django.test import TestCase, RequestFactory
from django.utils import timezone

from net.models import ComputerAnalysisProfile, TaskRun, TaskTargetRun
from net.inspections.task_summary import project_task_queryset, build_project_task_metrics, summarize_task


class TaskAggregationTests(TestCase):
    def setUp(self):
        profile = ComputerAnalysisProfile.objects.create(name='Aggregation', analysis_items=['system'])
        self.tasks = [TaskRun.objects.create(task_type='computer_analysis', source='manual',
            analysis_profile=profile, total_targets=3,
            target_scope_snapshot={'targets': [{'target_type': 'computer_log', 'target_id': str(i)}]}) for i in range(9)]
        for i, task in enumerate(self.tasks):
            TaskRun.objects.filter(pk=task.pk).update(created_at=timezone.now() + timedelta(seconds=i))
        for task in self.tasks:
            for i, (status, health) in enumerate((('success', 'normal'), ('success', 'abnormal'), ('cancelled', ''))):
                TaskTargetRun.objects.create(task=task, target_type='computer_log', target_id=str(i),
                    status=status, result_snapshot={'status': status, 'health_status': health,
                                                   'details': {'large': 'x' * 10000}})

    def test_metrics_are_aggregated_without_evaluating_task_queryset(self):
        tasks = project_task_queryset('computers')
        metrics = build_project_task_metrics(tasks)
        self.assertEqual((metrics['task_count'], metrics['normal_count'], metrics['abnormal_count'],
                          metrics['failure_rate']), (9, 9, 9, 50.0))
        self.assertIsNone(tasks._result_cache)

    def test_task_summary_does_not_materialize_targets(self):
        task = project_task_queryset('computers').first()
        with self.assertNumQueries(1):
            summary = summarize_task(task)
        self.assertEqual((summary['normal'], summary['abnormal'], summary['cancelled']), (1, 1, 1))
        self.assertNotIn('target_runs', getattr(task, '_prefetched_objects_cache', {}))

    def test_history_is_paginated_before_loading_rows(self):
        from net.inspections.task_summary import project_workspace_context
        context = project_workspace_context(RequestFactory().get('/', {'task_page': 2}), 'computers')
        self.assertEqual(len(context['task_page'].object_list), 2)
        self.assertEqual(context['task_metrics']['task_count'], 9)
        self.assertEqual(context['latest_task'].pk, self.tasks[-1].pk)

    def test_missing_and_unknown_legacy_results_keep_existing_count_semantics(self):
        task = self.tasks[0]
        task.target_runs.all().delete()
        TaskTargetRun.objects.create(task=task, target_type='computer_log', target_id='legacy',
            status='success', result_snapshot={})
        summary = summarize_task(project_task_queryset('computers').get(pk=task.pk))
        self.assertEqual((summary['abnormal'], summary['pending'], summary['total']), (1, 2, 3))

    def test_report_backfill_uses_bounded_sql_batches(self):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from net.models import Network_Device, Network_Device_Inspection
        device = Network_Device.objects.create(device_name='Batch', ip='192.0.2.90')
        Network_Device_Inspection.objects.bulk_create([
            Network_Device_Inspection(device=device, details={'cpu': {'usage_percent': 12}})
            for _ in range(205)])
        Network_Device_Inspection.objects.update(report_metrics='stale')
        with CaptureQueriesContext(connection) as captured:
            import_module('net.migrations.0036_record_report_fields').backfill_reports(
                apps, SimpleNamespace(connection=connection))
        selects = [q['sql'] for q in captured if q['sql'].startswith('SELECT')
                   and 'FROM "net_network_device_inspection"' in q['sql']]
        self.assertEqual(len(selects), 3)
        self.assertTrue(all('LIMIT 200' in sql for sql in selects))
        self.assertEqual(Network_Device_Inspection.objects.filter(report_metrics='CPU 12%').count(), 205)

    def test_progress_counts_do_not_load_all_target_statuses(self):
        from net.inspections.state import target_progress
        task = self.tasks[0]
        TaskTargetRun.objects.create(task=task, target_type='computer_log', target_id='pending')
        targets = task.target_runs.all()
        self.assertEqual(target_progress(targets), {'total': 4, 'completed': 3, 'successful': 2})
        self.assertIsNone(targets._result_cache)
