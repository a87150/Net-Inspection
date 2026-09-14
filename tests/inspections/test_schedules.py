from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from threading import Barrier
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.db import connections
from django.core.exceptions import ValidationError
from django.test import TestCase, TransactionTestCase

from tests.devices.pc.helpers import create_log_file

from net.models import (
    ComputerAnalysisProfile,
    ComputerLogFile,
    InspectionProfile,
    Schedule,
    Server,
    TaskRun,
)
from net.inspections.queue import enqueue_task
from net.inspections.schedules import _record_schedule_failure, enqueue_due_schedules, next_run_at


SHANGHAI = ZoneInfo('Asia/Shanghai')


class ScheduleCalculationTests(TestCase):
    def setUp(self):
        self.profile = InspectionProfile.objects.create(
            name='调度服务器巡检',
            device_type=InspectionProfile.DeviceType.SERVER,
            selected_items=['cpu'],
        )

    def test_interval_minutes_are_strictly_after_the_reference_time(self):
        schedule = Schedule.objects.create(
            inspection_profile=self.profile,
            kind=Schedule.Kind.INTERVAL,
            interval_value=15,
            interval_unit=Schedule.IntervalUnit.MINUTES,
        )
        reference = datetime(2026, 8, 31, 9, 30, tzinfo=SHANGHAI)

        self.assertEqual(
            next_run_at(schedule, reference),
            datetime(2026, 8, 31, 9, 45, tzinfo=SHANGHAI),
        )

    def test_interval_hours_are_strictly_after_the_reference_time(self):
        schedule = Schedule.objects.create(
            inspection_profile=self.profile,
            kind=Schedule.Kind.INTERVAL,
            interval_value=2,
            interval_unit=Schedule.IntervalUnit.HOURS,
        )
        reference = datetime(2026, 8, 31, 9, 30, tzinfo=SHANGHAI)

        self.assertEqual(
            next_run_at(schedule, reference),
            datetime(2026, 8, 31, 11, 30, tzinfo=SHANGHAI),
        )

    def test_daily_schedule_uses_shanghai_wall_clock_and_skips_equal_time(self):
        schedule = Schedule.objects.create(
            inspection_profile=self.profile,
            kind=Schedule.Kind.DAILY,
            daily_time=time(9, 30),
        )
        reference = datetime(2026, 8, 31, 1, 30, tzinfo=ZoneInfo('UTC'))

        self.assertEqual(
            next_run_at(schedule, reference),
            datetime(2026, 9, 1, 9, 30, tzinfo=SHANGHAI),
        )

    def test_daily_schedule_returns_same_local_day_when_fixed_time_is_still_ahead(self):
        schedule = Schedule.objects.create(
            inspection_profile=self.profile,
            kind=Schedule.Kind.DAILY,
            daily_time=time(9, 30),
        )
        reference = datetime(2026, 8, 31, 9, 29, 59, tzinfo=SHANGHAI)

        self.assertEqual(
            next_run_at(schedule, reference),
            datetime(2026, 8, 31, 9, 30, tzinfo=SHANGHAI),
        )


class ScheduleEnqueueTests(TestCase):
    now = datetime(2026, 8, 31, 10, 0, tzinfo=SHANGHAI)

    def setUp(self):
        self.first_server = Server.objects.create(
            name='计划服务器 A',
            ip='192.0.2.151',
            os='Ubuntu 24.04',
        )
        self.second_server = Server.objects.create(
            name='计划服务器 B',
            ip='192.0.2.152',
            os='Ubuntu 22.04',
        )
        self.profile = InspectionProfile.objects.create(
            name='计划服务器配置',
            device_type=InspectionProfile.DeviceType.SERVER,
            selected_items=['cpu', 'memory'],
            target_selector={
                'mode': 'selected',
                'target_ids': [str(self.second_server.pk), str(self.first_server.pk)],
            },
        )

    def _interval_schedule(self, **overrides):
        values = {
            'inspection_profile': self.profile,
            'kind': Schedule.Kind.INTERVAL,
            'interval_value': 30,
            'interval_unit': Schedule.IntervalUnit.MINUTES,
            'next_run_at': self.now - timedelta(seconds=1),
        }
        values.update(overrides)
        return Schedule.objects.create(**values)

    def test_due_schedule_enqueues_resolved_targets_and_advances_from_actual_enqueue_time(self):
        schedule = self._interval_schedule()

        tasks = enqueue_due_schedules(now=self.now)

        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task.source, TaskRun.Source.SCHEDULED)
        self.assertEqual(task.schedule_id, schedule.pk)
        self.assertEqual(task.available_at, self.now)
        self.assertEqual(task.selected_items_snapshot, ['cpu', 'memory'])
        self.assertEqual(
            set(task.target_runs.values_list('target_id', flat=True)),
            {str(self.first_server.pk), str(self.second_server.pk)},
        )
        self.assertEqual(
            task.target_scope_snapshot['targets'],
            [
                {'target_type': 'server', 'target_id': target_id}
                for target_id in sorted(
                    [str(self.first_server.pk), str(self.second_server.pk)]
                )
            ],
        )

        schedule.refresh_from_db()
        self.assertEqual(schedule.last_enqueued_at, self.now)
        self.assertEqual(
            schedule.next_run_at,
            datetime(2026, 8, 31, 10, 30, tzinfo=SHANGHAI),
        )

    def test_new_schedule_without_next_run_is_enqueued_immediately(self):
        schedule = self._interval_schedule(next_run_at=None)

        tasks = enqueue_due_schedules(now=self.now)

        self.assertEqual([task.schedule_id for task in tasks], [schedule.pk])
        schedule.refresh_from_db()
        self.assertEqual(schedule.last_enqueued_at, self.now)
        self.assertEqual(
            schedule.next_run_at,
            datetime(2026, 8, 31, 10, 30, tzinfo=SHANGHAI),
        )

    def test_missed_daily_schedule_enqueues_once_then_skips_backlog(self):
        schedule = Schedule.objects.create(
            inspection_profile=self.profile,
            kind=Schedule.Kind.DAILY,
            daily_time=time(8, 30),
            next_run_at=datetime(2026, 8, 30, 8, 30, tzinfo=SHANGHAI),
        )

        first_poll = enqueue_due_schedules(now=self.now)
        second_poll = enqueue_due_schedules(now=self.now)

        self.assertEqual(len(first_poll), 1)
        self.assertEqual(second_poll, [])
        schedule.refresh_from_db()
        self.assertEqual(schedule.last_enqueued_at, self.now)
        self.assertEqual(
            schedule.next_run_at,
            datetime(2026, 9, 1, 8, 30, tzinfo=SHANGHAI),
        )

    def test_disabled_schedule_is_not_enqueued_or_advanced(self):
        original_due_at = self.now - timedelta(minutes=1)
        schedule = self._interval_schedule(
            is_enabled=False,
            next_run_at=original_due_at,
        )

        self.assertEqual(enqueue_due_schedules(now=self.now), [])

        schedule.refresh_from_db()
        self.assertIsNone(schedule.last_enqueued_at)
        self.assertEqual(schedule.next_run_at, original_due_at)

    def test_existing_active_scope_does_not_advance_schedule_and_is_retryable(self):
        schedule = self._interval_schedule()
        existing = enqueue_task(
            self.profile,
            [self.first_server.pk, self.second_server.pk],
            TaskRun.Source.MANUAL,
        )

        self.assertEqual(enqueue_due_schedules(now=self.now), [])

        schedule.refresh_from_db()
        self.assertIsNone(schedule.last_enqueued_at)
        self.assertEqual(schedule.next_run_at, self.now - timedelta(seconds=1))
        self.assertEqual(TaskRun.objects.count(), 1)
        self.assertEqual(TaskRun.objects.get().pk, existing.pk)

    def test_active_task_block_is_persisted_for_operators(self):
        schedule = self._interval_schedule()
        enqueue_task(self.profile, [self.first_server.pk, self.second_server.pk], TaskRun.Source.MANUAL)

        self.assertEqual(enqueue_due_schedules(now=self.now), [])

        schedule.refresh_from_db()
        self.assertEqual(schedule.last_schedule_status, 'blocked')
        self.assertEqual(schedule.last_schedule_error, '等待活动任务结束。')
        self.assertEqual(schedule.last_schedule_attempt_at, self.now)

    def test_stale_failure_cannot_overwrite_a_newer_successful_attempt(self):
        schedule = self._interval_schedule()
        Schedule.objects.filter(pk=schedule.pk).update(
            last_schedule_attempt_at=self.now,
            last_schedule_status='queued',
            last_schedule_error='',
            last_enqueued_at=self.now,
            next_run_at=self.now + timedelta(minutes=30),
        )

        _record_schedule_failure(schedule, self.now, ValidationError('password=secret-value'))

        schedule.refresh_from_db()
        self.assertEqual(schedule.last_schedule_status, 'queued')
        self.assertEqual(schedule.last_schedule_error, '')

    def test_failure_diagnostic_never_scans_global_configuration_secrets(self):
        schedule = self._interval_schedule()

        with patch('net.infrastructure.sanitization.configuration_secrets', side_effect=AssertionError):
            _record_schedule_failure(schedule, self.now, ValidationError('无效目标范围'))

        schedule.refresh_from_db()
        self.assertEqual(schedule.last_schedule_status, 'error')

    def test_persisted_schedule_failure_redacts_secret_shaped_validation_text(self):
        schedule = self._interval_schedule()

        _record_schedule_failure(schedule, self.now, ValidationError('password=secret-value'))

        schedule.refresh_from_db()
        self.assertEqual(schedule.last_schedule_status, 'error')
        self.assertNotIn('secret-value', schedule.last_schedule_error)
        self.assertIn('[REDACTED]', schedule.last_schedule_error)

    def test_invalid_due_schedule_persists_a_configuration_error(self):
        invalid_profile = InspectionProfile.objects.create(
            name='无效持久化配置', device_type=InspectionProfile.DeviceType.SERVER,
            selected_items=['cpu'], target_selector={'mode': 'selected', 'target_ids': ['bad-id']},
        )
        schedule = Schedule.objects.create(
            inspection_profile=invalid_profile, kind=Schedule.Kind.INTERVAL,
            interval_value=30, interval_unit=Schedule.IntervalUnit.MINUTES,
            next_run_at=self.now - timedelta(seconds=1),
        )

        self.assertEqual(enqueue_due_schedules(now=self.now), [])

        schedule.refresh_from_db()
        self.assertEqual(schedule.last_schedule_status, 'error')
        self.assertTrue(schedule.last_schedule_error)
        self.assertEqual(schedule.last_schedule_attempt_at, self.now)

    def test_invalid_due_schedule_does_not_stop_other_due_schedules(self):
        valid_schedule = self._interval_schedule()
        invalid_profile = InspectionProfile.objects.create(
            name='无效目标配置',
            device_type=InspectionProfile.DeviceType.SERVER,
            selected_items=['cpu'],
            target_selector={
                'mode': 'selected',
                'target_ids': ['00000000-0000-0000-0000-000000000001'],
            },
        )
        invalid_schedule = Schedule.objects.create(
            inspection_profile=invalid_profile,
            kind=Schedule.Kind.INTERVAL,
            interval_value=30,
            interval_unit=Schedule.IntervalUnit.MINUTES,
            next_run_at=self.now - timedelta(seconds=1),
        )

        tasks = enqueue_due_schedules(now=self.now)

        self.assertEqual([task.schedule_id for task in tasks], [valid_schedule.pk])
        valid_schedule.refresh_from_db()
        invalid_schedule.refresh_from_db()
        self.assertEqual(valid_schedule.last_enqueued_at, self.now)
        self.assertIsNone(invalid_schedule.last_enqueued_at)
        self.assertEqual(invalid_schedule.next_run_at, self.now - timedelta(seconds=1))

    def test_malformed_filter_value_does_not_stop_a_later_due_schedule(self):
        invalid_profile = InspectionProfile.objects.create(
            name='端口类型错误配置',
            device_type=InspectionProfile.DeviceType.SERVER,
            selected_items=['cpu'],
            target_selector={
                'mode': 'filtered',
                'filters': {'port': 'not-a-number'},
            },
        )
        invalid_due_at = self.now - timedelta(minutes=2)
        invalid_schedule = Schedule.objects.create(
            inspection_profile=invalid_profile,
            kind=Schedule.Kind.INTERVAL,
            interval_value=30,
            interval_unit=Schedule.IntervalUnit.MINUTES,
            next_run_at=invalid_due_at,
        )
        valid_schedule = self._interval_schedule(
            next_run_at=self.now - timedelta(minutes=1),
        )

        tasks = enqueue_due_schedules(now=self.now)

        self.assertEqual([task.schedule_id for task in tasks], [valid_schedule.pk])
        invalid_schedule.refresh_from_db()
        valid_schedule.refresh_from_db()
        self.assertIsNone(invalid_schedule.last_enqueued_at)
        self.assertEqual(invalid_schedule.next_run_at, invalid_due_at)
        self.assertEqual(valid_schedule.last_enqueued_at, self.now)
        self.assertEqual(
            valid_schedule.next_run_at,
            datetime(2026, 8, 31, 10, 30, tzinfo=SHANGHAI),
        )

    def test_filtered_selector_resolves_only_matching_current_assets(self):
        excluded_server = Server.objects.create(
            name='计划 Windows 服务器',
            ip='192.0.2.153',
            server_type='windows',
        )
        profile = InspectionProfile.objects.create(
            name='Linux 筛选配置',
            device_type=InspectionProfile.DeviceType.SERVER,
            selected_items=['cpu'],
            target_selector={'mode': 'filtered', 'server_type': 'linux'},
        )
        schedule = Schedule.objects.create(
            inspection_profile=profile,
            kind=Schedule.Kind.INTERVAL,
            interval_value=30,
            interval_unit=Schedule.IntervalUnit.MINUTES,
            next_run_at=self.now,
        )

        tasks = enqueue_due_schedules(now=self.now)

        self.assertEqual(len(tasks), 1)
        task = next(task for task in tasks if task.schedule_id == schedule.pk)
        self.assertEqual(
            set(task.target_runs.values_list('target_id', flat=True)),
            {str(self.first_server.pk), str(self.second_server.pk)},
        )
        self.assertNotIn(
            str(excluded_server.pk),
            task.target_runs.values_list('target_id', flat=True),
        )

    def test_analysis_schedule_enqueues_worker_owned_scan_instead_of_log_targets(self):
        from tests.devices.pc.test_source_models import valid_smb_source
        source = valid_smb_source()
        log_file = create_log_file(
            source_path='C:/logs/SCHEDULE-PC-01.json',
            modified_at=self.now,
            content_hash='a' * 64,
            import_status='imported',
        )
        profile = ComputerAnalysisProfile.objects.create(
            name='计划日志分析',
            analysis_items=['event_findings'],
        )
        schedule = Schedule.objects.create(
            analysis_profile=profile,
            kind=Schedule.Kind.INTERVAL,
            interval_value=1,
            interval_unit=Schedule.IntervalUnit.HOURS,
            next_run_at=self.now,
        )

        tasks = enqueue_due_schedules(now=self.now)

        self.assertEqual(len(tasks), 1)
        task = next(task for task in tasks if task.schedule_id == schedule.pk)
        self.assertEqual(task.task_type, TaskRun.TaskType.COMPUTER_FETCH)
        self.assertEqual(task.selected_items_snapshot, ['event_findings'])
        self.assertEqual(
            list(task.target_runs.values_list('target_type', 'target_id')),
            [('computer_source', str(source.pk))],
        )
        self.assertTrue(ComputerLogFile.objects.filter(pk=log_file.pk).exists())

    def test_analysis_profile_accepts_a_scheduled_log_task(self):
        log_file = create_log_file(
            source_path='C:/logs/SCHEDULE-PC-02.json',
            modified_at=self.now,
            content_hash='b' * 64,
            import_status='imported',
        )
        profile = ComputerAnalysisProfile.objects.create(
            name='直接计划日志分析',
            analysis_items=['event_log'],
        )
        schedule = Schedule.objects.create(
            analysis_profile=profile,
            kind=Schedule.Kind.INTERVAL,
            interval_value=1,
            interval_unit=Schedule.IntervalUnit.HOURS,
        )

        task = enqueue_task(
            profile,
            [log_file.pk],
            TaskRun.Source.SCHEDULED,
            overrides={'available_at': self.now, 'schedule': schedule},
        )

        self.assertEqual(task.schedule_id, schedule.pk)
        self.assertEqual(task.task_type, TaskRun.TaskType.COMPUTER_ANALYSIS)


class ScheduleConcurrencyTests(TransactionTestCase):
    reset_sequences = True
    now = datetime(2026, 8, 31, 10, 0, tzinfo=SHANGHAI)

    def setUp(self):
        self.servers = [
            Server.objects.create(
                name=f'并发计划服务器 {index}',
                ip=f'198.51.100.{index}',
            )
            for index in range(1, 33)
        ]
        self.profile = InspectionProfile.objects.create(
            name='并发计划服务器配置',
            device_type=InspectionProfile.DeviceType.SERVER,
            selected_items=['cpu'],
            target_selector={'mode': 'all'},
        )
        self.schedule = Schedule.objects.create(
            inspection_profile=self.profile,
            kind=Schedule.Kind.INTERVAL,
            interval_value=30,
            interval_unit=Schedule.IntervalUnit.MINUTES,
            next_run_at=self.now,
        )

    def test_two_pollers_create_one_task_and_advance_the_schedule_once(self):
        start = Barrier(2)

        def poll():
            connection = connections['default']
            connection.close()
            try:
                start.wait(timeout=5)
                return [
                    str(task.pk)
                    for task in enqueue_due_schedules(now=self.now)
                ]
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(poll) for _index in range(2)]
            poll_results = [future.result(timeout=10) for future in futures]

        scheduled_tasks = TaskRun.objects.filter(
            source=TaskRun.Source.SCHEDULED,
            schedule=self.schedule,
        )
        self.assertEqual(scheduled_tasks.count(), 1)
        self.assertEqual(sum(len(result) for result in poll_results), 1)
        self.assertEqual(
            {task_id for result in poll_results for task_id in result},
            {str(scheduled_tasks.get().pk)},
        )
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.last_enqueued_at, self.now)
        self.assertEqual(
            self.schedule.next_run_at,
            datetime(2026, 8, 31, 10, 30, tzinfo=SHANGHAI),
        )
