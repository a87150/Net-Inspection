from datetime import time, timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from net.inspections.schedules import enqueue_due_schedules
from net.inspections.worker import TaskWorker
from net.models import InspectionProfile, People, PeopleSyncSource, Schedule, TaskRun
from net.people.directory import DirectoryPerson
from tests.people.test_directory_sync import SnapshotAdapter


class PeopleScheduleModelTests(TestCase):
    def setUp(self):
        self.source = PeopleSyncSource.objects.create(
            name='飞书', source_key='people-provider-feishu', source_type='feishu',
            credentials={'app_id': 'app', 'app_secret': 'secret'},
        )

    def test_schedule_accepts_exactly_one_people_source(self):
        schedule = Schedule(
            people_source=self.source,
            kind=Schedule.Kind.INTERVAL,
            interval_value=30,
            interval_unit=Schedule.IntervalUnit.MINUTES,
        )
        schedule.full_clean()
        schedule.save()

        self.assertEqual(schedule.people_source_id, self.source.pk)
        self.assertEqual(str(schedule), '飞书：飞书 - 间隔执行')

    def test_schedule_rejects_mixed_profile_and_duplicate_people_source(self):
        profile = InspectionProfile.objects.create(
            name='网络', device_type='network_device', selected_items=['device_info'],
        )
        with self.assertRaises(ValidationError):
            Schedule(
                people_source=self.source,
                inspection_profile=profile,
                kind=Schedule.Kind.DAILY,
                daily_time=time(8, 0),
            ).full_clean()
        Schedule.objects.create(
            people_source=self.source,
            kind=Schedule.Kind.DAILY,
            daily_time=time(8, 0),
        )
        with self.assertRaises(ValidationError):
            Schedule(
                people_source=self.source,
                kind=Schedule.Kind.DAILY,
                daily_time=time(9, 0),
            ).full_clean()

    def test_scheduled_people_task_requires_matching_schedule_and_source(self):
        schedule = Schedule.objects.create(
            people_source=self.source,
            kind=Schedule.Kind.INTERVAL,
            interval_value=1,
            interval_unit=Schedule.IntervalUnit.HOURS,
        )
        scope = {'targets': [{'target_type': 'people_source', 'target_id': str(self.source.pk)}]}
        task = TaskRun(
            task_type=TaskRun.TaskType.PEOPLE_SYNC,
            source=TaskRun.Source.SCHEDULED,
            people_source=self.source,
            schedule=schedule,
            total_targets=1,
            profile_snapshot=self.source.public_data(),
            parameters_snapshot={'concurrent_workers': 1},
            target_scope_snapshot=scope,
        )
        task.scope_key = TaskRun.build_scope_key(
            task_type=task.task_type,
            profile_id=self.source.pk,
            target_scope_snapshot=scope,
        )
        task.active_scope_key = task.scope_key
        task.full_clean()

        schedule.people_source = PeopleSyncSource.objects.create(
            name='钉钉', source_key='people-provider-dingtalk', source_type='dingtalk',
            credentials={'app_key': 'key', 'app_secret': 'secret'},
        )
        task.schedule = schedule
        with self.assertRaises(ValidationError):
            task.full_clean()


class PeopleScheduledEnqueueTests(TestCase):
    def setUp(self):
        self.source = PeopleSyncSource.objects.create(
            name='飞书', source_key='people-provider-feishu', source_type='feishu',
            credentials={'app_id': 'app', 'app_secret': 'secret'},
        )
        self.now = timezone.now()
        PeopleSyncSource.objects.filter(pk=self.source.pk).update(last_tested_at=self.now)
        self.source.refresh_from_db()
        self.schedule = Schedule.objects.create(
            people_source=self.source,
            kind=Schedule.Kind.INTERVAL,
            interval_value=30,
            interval_unit=Schedule.IntervalUnit.MINUTES,
            next_run_at=self.now,
        )

    def test_due_tested_source_enqueues_immutable_scheduled_task(self):
        tasks = enqueue_due_schedules(now=self.now)

        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(
            (task.task_type, task.source),
            (TaskRun.TaskType.PEOPLE_SYNC, TaskRun.Source.SCHEDULED),
        )
        self.assertEqual(task.people_source_id, self.source.pk)
        self.assertEqual(task.schedule_id, self.schedule.pk)
        self.assertNotIn('credentials', task.profile_snapshot)
        self.assertNotIn('owner_session_digest', task.parameters_snapshot)
        self.assertEqual(
            task.target_scope_snapshot['targets'][0]['target_type'],
            'people_source',
        )
        self.assertEqual(task.target_runs.count(), 1)

    def test_changed_source_remains_due_until_successful_retest(self):
        changed_at = self.now + timedelta(seconds=1)
        PeopleSyncSource.objects.filter(pk=self.source.pk).update(
            root_department_ids=['changed'], updated_at=changed_at,
        )

        self.assertEqual(enqueue_due_schedules(now=changed_at), [])
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.next_run_at, self.now)
        self.assertIsNone(self.schedule.last_enqueued_at)

        retested_at = changed_at + timedelta(seconds=1)
        PeopleSyncSource.objects.filter(pk=self.source.pk).update(last_tested_at=retested_at)
        tasks = enqueue_due_schedules(now=retested_at)
        self.assertEqual(len(tasks), 1)

    def test_active_duplicate_is_not_enqueued_or_advanced_twice(self):
        first = enqueue_due_schedules(now=self.now)
        self.assertEqual(len(first), 1)
        Schedule.objects.filter(pk=self.schedule.pk).update(next_run_at=self.now)

        second = enqueue_due_schedules(now=self.now + timedelta(seconds=1))

        self.assertEqual(second, [])
        self.assertEqual(TaskRun.objects.filter(task_type=TaskRun.TaskType.PEOPLE_SYNC).count(), 1)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.next_run_at, self.now)


class PeopleScheduledWorkerTests(TransactionTestCase):
    def setUp(self):
        from tests.auth import login_reader
        login_reader(self.client)
        self.source = PeopleSyncSource.objects.create(
            name='飞书', source_key='people-provider-feishu', source_type='feishu',
            credentials={'app_id': 'app', 'app_secret': 'secret'},
        )
        self.now = timezone.now()
        PeopleSyncSource.objects.filter(pk=self.source.pk).update(last_tested_at=self.now)
        self.source.refresh_from_db()
        self.schedule = Schedule.objects.create(
            people_source=self.source,
            kind=Schedule.Kind.INTERVAL,
            interval_value=30,
            interval_unit=Schedule.IntervalUnit.MINUTES,
            next_run_at=self.now,
        )
        self.task = enqueue_due_schedules(now=self.now)[0]

    def _run(self, people, *, skipped_records=()):
        def factory(source):
            return SnapshotAdapter(source, people, skipped_records=skipped_records)

        with patch('net.people.executor.build_directory_adapter', side_effect=factory):
            self.assertTrue(TaskWorker(worker_id='people-sync-worker', threads=2).run_once())
        self.task.refresh_from_db()
        return self.task.target_runs.get()

    def test_scheduled_worker_previews_and_applies_complete_snapshot(self):
        target = self._run(
            [DirectoryPerson(employee_id='E001', name='员工一', external_user_id='u-1')],
            skipped_records=({'external_user_id': 'u-no-id', 'reason': 'missing_employee_id'},),
        )

        self.assertEqual(self.task.status, TaskRun.Status.SUCCESS)
        self.assertEqual(target.result_snapshot['counts'], {
            'created': 1, 'updated': 0, 'unchanged': 0, 'deactivated': 0, 'skipped': 1,
        })
        self.assertTrue(People.objects.filter(employee_id='E001', is_active=True).exists())
        response = self.client.get(f'/tasks/{self.task.pk}/')
        self.assertContains(response, '新增 1 · 更新 0 · 未变化 0 · 停用 0 · 跳过 1')

    def test_successful_sync_keeps_connection_test_valid_for_next_schedule(self):
        configured_at = self.source.updated_at
        tested_at = self.source.last_tested_at
        people = [DirectoryPerson(employee_id='E001', name='员工一', external_user_id='u-1')]
        self._run(people)
        self.source.refresh_from_db()
        self.assertEqual(self.source.updated_at, configured_at)
        self.assertEqual(self.source.last_tested_at, tested_at)
        self.assertIsNotNone(self.source.last_synced_at)
        self.assertTrue(self.source.public_data()['connection_test_current'])
        Schedule.objects.filter(pk=self.schedule.pk).update(next_run_at=timezone.now())
        self.task = enqueue_due_schedules()[0]
        target = self._run(people)
        self.assertEqual(self.task.status, TaskRun.Status.SUCCESS)
        self.assertEqual(target.result_snapshot['counts']['unchanged'], 1)
        self.assertEqual(People.objects.filter(employee_id='E001').count(), 1)
        self.source.refresh_from_db()
        self.assertTrue(self.source.public_data()['connection_test_current'])

    def test_validation_failure_writes_no_personnel_changes(self):
        before = list(People.objects.values_list('employee_id', 'name', 'is_active'))
        target = self._run([
            DirectoryPerson(employee_id='E002', name='甲', external_user_id='u-2'),
            DirectoryPerson(employee_id='E002', name='乙', external_user_id='u-3'),
        ])

        self.assertEqual(self.task.status, TaskRun.Status.FAILED)
        self.assertEqual(
            list(People.objects.values_list('employee_id', 'name', 'is_active')),
            before,
        )
        self.assertIn('validation', target.result_snapshot.get('error_category', ''))

    def test_apply_exception_is_safe_and_rolls_back(self):
        def factory(source):
            return SnapshotAdapter(source, [
                DirectoryPerson(employee_id='E003', name='员工三', external_user_id='u-3'),
            ])

        with (
            patch('net.people.executor.build_directory_adapter', side_effect=factory),
            patch('net.people.executor.apply_people_sync', side_effect=RuntimeError('secret-value')),
        ):
            self.assertTrue(TaskWorker(worker_id='people-sync-worker', threads=1).run_once())

        self.task.refresh_from_db()
        target = self.task.target_runs.get()
        self.assertEqual(self.task.status, TaskRun.Status.FAILED)
        self.assertFalse(People.objects.filter(employee_id='E003').exists())
        self.assertNotIn('secret-value', target.error_message)
        self.assertIn('RuntimeError', target.error_message)

    def test_scheduled_task_is_not_a_browser_operation(self):
        client = Client()
        from tests.auth import login_admin
        login_admin(client)
        self.assertEqual(client.get('/integrations/people/tasks/status/').json()['tasks'], [])
        response = client.get(f'/tasks/{self.task.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('/integrations/people/operations/', response.url if response.status_code == 302 else '')
