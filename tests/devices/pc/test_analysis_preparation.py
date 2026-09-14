"""Preparation must not hold the writer lock or publish stale results."""
from datetime import timedelta
from threading import Event
from unittest.mock import patch

from django.db import connection
from django.db.models import F
from django.db.models.query import QuerySet
from django.db.models.signals import post_save
from django.test import TransactionTestCase
from django.utils import timezone

from net.devices.pc.analysis import analyze_log, prepare_log, persist_analysis, _details_for_item
from net.devices.pc.executor import execute_computer_target
from net.inspections.executor import _SQLITE_EXECUTION_LOCK
from net.inspections.queue import claim_next_task, enqueue_task, cancel_task
from net.inspections.state import save_target
from net.models import ComputerAnalysis, ComputerAnalysisProfile, Error_Computer, TaskRun
from tests.devices.pc.helpers import create_log_file


class AnalysisPreparationTests(TransactionTestCase):
    def setUp(self):
        self.log = create_log_file(
            source_path='preparation.json', modified_at=timezone.now(),
            content_hash='d' * 64, import_status='imported',
            payload={'系统信息概览': {'计算机名': 'PREP-PC'},
                     'Windows激活信息': {'许可证状态': '未授权'},
                     '已安装软件列表': [{'软件名': 'Editor'}]},
        )
        self.profile = ComputerAnalysisProfile.objects.create(
            name='preparation', analysis_items=['activation'])
        from net.models import People
        People.objects.create(employee_id='PREP-PC', name='Preparation fixture')
        self.task = enqueue_task(self.profile, [self.log.pk], 'manual')
        claim_next_task('prep-worker', 60)
        self.target = self.task.target_runs.get()

    def execute(self, **kwargs):
        return execute_computer_target(self.target, worker_id='prep-worker', **kwargs)

    def assert_no_result(self):
        self.assertFalse(ComputerAnalysis.objects.exists())
        self.assertFalse(Error_Computer.objects.exists())
        self.target.refresh_from_db()
        self.assertEqual(self.target.result_id, '')

    def test_preparation_outside_transaction_and_sqlite_guard(self):
        def compute(*args, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            self.assertFalse(_SQLITE_EXECUTION_LOCK._is_owned())
            prepared = prepare_log(*args, **kwargs)
            self.assert_no_result()
            return prepared
        with patch('net.devices.pc.executor.prepare_log', side_effect=compute):
            outcome = self.execute()
        self.assertFalse(outcome.stale)
        self.assertEqual(ComputerAnalysis.objects.count(), 1)
        self.assertTrue(Error_Computer.objects.exists())
        self.assert_compact_snapshot('abnormal')

    def assert_compact_snapshot(self, health):
        self.target.refresh_from_db()
        analysis = ComputerAnalysis.objects.get(pk=self.target.result_id)
        snapshot = self.target.result_snapshot
        self.assertEqual(snapshot['snapshot_version'], 2)
        self.assertEqual(snapshot['result_type'], 'computer_analysis')
        self.assertEqual(snapshot['result_id'], str(analysis.pk))
        self.assertEqual(snapshot['status'], analysis.status)
        self.assertEqual(snapshot['summary'], analysis.summary)
        self.assertEqual(snapshot['health_status'], health)
        self.assertEqual(snapshot['health_status'], analysis.details['health_status'])
        self.assertTrue({'details', 'exceptions', 'analysis_items'}.isdisjoint(snapshot))
        self.assertTrue(analysis.details)
        self.assertTrue(analysis.analysis_items)
        self.assertTrue(analysis.exceptions)

    def test_compact_snapshot_preserves_normal_health_for_info_findings(self):
        TaskRun.objects.filter(pk=self.task.pk).update(selected_items_snapshot=['processes'])
        self.assertFalse(self.execute().stale)
        self.assert_compact_snapshot('normal')

    def test_cancel_during_compute_discards_result_and_alerts(self):
        def compute(*args, **kwargs):
            prepared = prepare_log(*args, **kwargs)
            cancel_task(self.task.pk)
            return prepared
        with patch('net.devices.pc.executor.prepare_log', side_effect=compute), patch(
            'net.alerts.service.process_persisted_target'
        ) as alerts:
            self.assertTrue(self.execute().stale)
        alerts.assert_not_called()
        self.assert_no_result()

    def test_expired_replaced_or_retried_lease_discards_result(self):
        for changes in (
            {'lease_expires_at': timezone.now() - timedelta(seconds=1)},
            {'worker_id': 'replacement'},
            {'attempt_count': F('attempt_count') + 1},
        ):
            with self.subTest(changes=changes):
                self.target.status = 'queued'
                self.target.save(update_fields=['status'])
                TaskRun.objects.filter(pk=self.task.pk).update(
                    worker_id='prep-worker', lease_expires_at=timezone.now() + timedelta(seconds=60))
                def compute(*args, **kwargs):
                    prepared = prepare_log(*args, **kwargs)
                    TaskRun.objects.filter(pk=self.task.pk).update(**changes)
                    return prepared
                with patch('net.devices.pc.executor.prepare_log', side_effect=compute):
                    self.assertTrue(self.execute().stale)
                self.assert_no_result()

    def test_guard_set_during_compute_discards_result(self):
        guard = Event()
        def compute(*args, **kwargs):
            prepared = prepare_log(*args, **kwargs)
            guard.set()
            return prepared
        with patch('net.devices.pc.executor.prepare_log', side_effect=compute):
            self.assertTrue(self.execute(lease_guard=guard).stale)
        self.assert_no_result()

    def test_preparation_error_fails_target(self):
        with patch('net.devices.pc.executor.prepare_log', side_effect=ValueError('bad evidence')):
            result = self.execute()
        self.assertEqual(result.status, 'failed')
        self.assertIn('bad evidence', result.error_message)
        self.assert_no_result()

    def test_cancelled_preparation_error_cannot_overwrite_cancel(self):
        def compute(*args, **kwargs):
            cancel_task(self.task.pk)
            raise ValueError('bad evidence')
        with patch('net.devices.pc.executor.prepare_log', side_effect=compute):
            self.assertTrue(self.execute().stale)
        self.assert_no_result()
        self.assertEqual(self.target.status, 'cancelled')

    def test_frozen_software_content_does_not_read_live_path(self):
        rules = {'software_policy_path': 'changed.ini', 'software_policy_snapshot': {
            'content': {'WHITELIST': {'names': ['Editor']}}, 'sha256': 'a' * 64}}
        with patch('net.devices.pc.analysis.load_config_as_dict') as read:
            result = analyze_log(self.log, ['software'], rules=rules)
        read.assert_not_called()
        self.assertEqual(result.exceptions, [])
        self.assertEqual(result.details['rules']['software_policy_snapshot'], {'sha256': 'a' * 64})
        self.assertIn('content', rules['software_policy_snapshot'])

    def test_frozen_error_does_not_fall_back_to_live_path(self):
        with patch('net.devices.pc.analysis.load_config_as_dict') as read:
            result = analyze_log(self.log, ['software'], rules={
                'software_policy_path': 'fixed.ini',
                'software_policy_snapshot': {'error': 'unreadable policy', 'sha256': ''}})
        read.assert_not_called()
        self.assertEqual(result.exceptions[0]['问题类型'], '软件策略问题')
        self.assertIn('unreadable policy', result.exceptions[0]['详细问题'])
        self.assertEqual(result.details['rules']['software_policy_snapshot'],
                         {'error': 'unreadable policy', 'sha256': ''})

    def test_analysis_finish_time_is_after_checks(self):
        before = timezone.now()
        after = before + timedelta(seconds=5)
        with patch('net.devices.pc.analysis.timezone.now', return_value=before) as clock:
            def check(*args, **kwargs):
                result = _details_for_item(*args, **kwargs)
                clock.return_value = after
                return result
            with patch('net.devices.pc.analysis._details_for_item', side_effect=check):
                result = analyze_log(self.log, ['activation'])
        self.assertEqual(result.started_at, before)
        self.assertEqual(result.finished_at, after)

    def test_target_finish_time_is_after_result_persistence(self):
        persisted_at = []
        def persist(prepared):
            result = persist_analysis(prepared)
            persisted_at.append(timezone.now())
            return result
        with patch('net.devices.pc.executor.persist_analysis', side_effect=persist):
            self.execute()
        self.target.refresh_from_db()
        self.assertGreaterEqual(self.target.finished_at, persisted_at[0])

    def test_historical_rules_still_read_policy_path(self):
        with patch('net.devices.pc.analysis.load_config_as_dict', return_value={}) as read:
            result = analyze_log(self.log, ['software'], rules={'software_policy_path': 'legacy.ini'})
        read.assert_called_once_with('legacy.ini')
        self.assertEqual(result.exceptions[0]['问题类型'], '软件问题')

    def test_unconfigured_frozen_policy_preserves_no_check_behavior(self):
        with patch('net.devices.pc.analysis.load_config_as_dict') as read:
            result = analyze_log(self.log, ['software'], rules={
                'software_policy_snapshot': {'content': {}, 'sha256': 'empty-digest'}})
        read.assert_not_called()
        self.assertEqual(result.exceptions, [])

    def test_target_retry_during_compute_discards_result(self):
        def compute(*args, **kwargs):
            prepared = prepare_log(*args, **kwargs)
            type(self.target).objects.filter(pk=self.target.pk).update(
                attempt_count=F('attempt_count') + 1)
            return prepared
        with patch('net.devices.pc.executor.prepare_log', side_effect=compute):
            self.assertTrue(self.execute().stale)
        self.assert_no_result()

    def test_error_row_failure_rolls_back_analysis_and_fails_target(self):
        with patch('net.devices.pc.analysis.Error_Computer.objects.bulk_create',
                   side_effect=ValueError('invalid finding')):
            result = self.execute()
        self.assertEqual(result.status, 'failed')
        self.assert_no_result()

    def test_lease_loss_during_result_or_target_write_rolls_back(self):
        for stage in ('result', 'target'):
            for loss in ('guard', 'expiry'):
                with self.subTest(stage=stage, loss=loss):
                    guard = Event()
                    expired = timezone.now() + timedelta(minutes=5)
                    with patch('net.devices.pc.executor.timezone.now', wraps=timezone.now) as clock:
                        def lose_lease():
                            if loss == 'guard':
                                guard.set()
                            else:
                                clock.return_value = expired
                        def persist(prepared):
                            result = persist_analysis(prepared)
                            if stage == 'result':
                                lose_lease()
                            return result
                        def save(target, fields):
                            result = save_target(target, fields)
                            if stage == 'target':
                                lose_lease()
                            return result
                        with patch('net.devices.pc.executor.persist_analysis', side_effect=persist), patch(
                            'net.devices.pc.executor.save_target', side_effect=save
                        ), patch('net.alerts.service.process_persisted_target') as alerts:
                            self.assertTrue(self.execute(lease_guard=guard).stale)
                        alerts.assert_not_called()
                    self.assert_no_result()
                    self.assertEqual(self.target.status, 'running')
                    self.target.status = 'queued'
                    self.target.save(update_fields=['status'])

    def test_exception_after_record_insert_rolls_back_record_and_findings(self):
        def persist(prepared):
            persist_analysis(prepared)
            raise ValueError('after insertion')
        with patch('net.devices.pc.executor.persist_analysis', side_effect=persist):
            self.assertEqual(self.execute().status, 'failed')
        self.assert_no_result()

    def test_real_post_save_lease_loss_rolls_back_analysis_errors_and_target(self):
        for model in (ComputerAnalysis, type(self.target)):
            with self.subTest(model=model.__name__):
                guard = Event()
                def lose_lease(sender, instance, **kwargs):
                    if instance.status == 'success':
                        guard.set()
                post_save.connect(lose_lease, sender=model, weak=False)
                try:
                    with patch('net.alerts.service.process_persisted_target') as alerts:
                        self.assertTrue(self.execute(lease_guard=guard).stale)
                    alerts.assert_not_called()
                finally:
                    post_save.disconnect(lose_lease, sender=model)
                self.assert_no_result()
                self.assertEqual(self.target.status, 'running')
                self.assertEqual(self.target.result_snapshot, {})
                self.target.status = 'queued'
                self.target.save(update_fields=['status'])

    def test_failure_write_losing_lease_rolls_back_without_alerts(self):
        guard = Event()
        def save(target, fields):
            save_target(target, fields)
            guard.set()
        with patch('net.devices.pc.executor.prepare_log', side_effect=ValueError('invalid')), patch(
            'net.devices.pc.executor.save_target', side_effect=save
        ), patch('net.alerts.service.process_persisted_target') as alerts:
            self.assertTrue(self.execute(lease_guard=guard).stale)
        alerts.assert_not_called()
        self.assert_no_result()
        self.assertEqual(self.target.status, 'running')
        self.assertEqual(self.target.error_message, '')

    def test_exception_after_success_write_rolls_back_everything(self):
        def save(target, fields):
            save_target(target, fields)
            raise RuntimeError('after target write')
        with patch('net.devices.pc.executor.save_target', side_effect=save), patch(
            'net.alerts.service.process_persisted_target'
        ) as alerts:
            with self.assertRaisesRegex(RuntimeError, 'after target write'):
                self.execute()
        alerts.assert_not_called()
        self.assert_no_result()
        self.assertEqual(self.target.status, 'running')

    def test_pc_persistence_and_failure_lock_parent_before_target_without_join(self):
        from net.devices.pc.executor import _failure, _persist_analysis
        from net.inspections.executor import _begin_target
        started = _begin_target(self.target.pk, 'prep-worker')
        prepared = prepare_log(self.log, ['activation'], task_target=started)
        original = QuerySet.first
        for fail in (True, False):
            locks = []
            def first(queryset):
                if queryset.query.select_for_update:
                    locks.append(queryset.model)
                    self.assertFalse(queryset.query.select_related)
                return original(queryset)
            with patch.object(QuerySet, 'first', first):
                if fail:
                    _failure(self.target.pk, 'prep-worker', 'test')
                else:
                    _persist_analysis(self.target.pk, 'prep-worker', prepared,
                                      (started.task.attempt_count, started.attempt_count))
            self.assertEqual(locks[:2], [TaskRun, type(self.target)])
            type(self.target).objects.filter(pk=self.target.pk).update(status='running')
