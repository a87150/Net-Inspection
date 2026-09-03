from datetime import time

from django.core.exceptions import ValidationError
from django.test import TestCase

from net.models import InspectionProfile, PeopleSyncSource, Schedule, TaskRun


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
