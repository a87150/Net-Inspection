from datetime import date, time, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from net import models as net_models


class TaskModelPublicApiTests(TestCase):
    def test_task_models_are_exported_through_flat_public_module(self):
        for model_name in (
            'InspectionProfile',
            'ComputerAnalysisProfile',
            'Schedule',
            'TaskRun',
            'TaskTargetRun',
        ):
            self.assertIsNotNone(
                getattr(net_models, model_name, None),
                f'{model_name} is not exported through net.models',
            )


InspectionProfile = net_models.InspectionProfile
ComputerAnalysisProfile = net_models.ComputerAnalysisProfile
Schedule = net_models.Schedule
TaskRun = net_models.TaskRun
TaskTargetRun = net_models.TaskTargetRun


class TaskProfileContractTests(SimpleTestCase):
    def test_profiles_expose_configuration_and_safe_snapshot_fields(self):
        inspection_fields = {field.name for field in InspectionProfile._meta.fields}
        analysis_fields = {field.name for field in ComputerAnalysisProfile._meta.fields}

        self.assertTrue(
            {
                'name',
                'device_type',
                'is_enabled',
                'selected_items',
                'target_selector',
                'timeout_seconds',
                'concurrent_workers',
                'alert_policy_mode',
            }
            <= inspection_fields
        )
        self.assertTrue(
            {
                'name',
                'is_enabled',
                'cpu_temperature_max_celsius',
                'site_ip_prefixes',
                'analysis_items',
                'software_policy_path',
                'minimum_windows_release',
                'defender_update_max_days',
                'defender_scan_max_days',
                'patch_max_days',
                'uptime_max_hours',
                'cpu_max_percent',
                'memory_max_percent',
                'kms_servers',
                'concurrent_workers',
                'alert_policy_mode',
            }
            <= analysis_fields
        )

    def test_profile_json_defaults_are_isolated_and_have_expected_shapes(self):
        first = InspectionProfile()
        second = InspectionProfile()
        first.selected_items.append('cpu')
        first.target_selector['mode'] = 'all'

        analysis_first = ComputerAnalysisProfile()
        analysis_second = ComputerAnalysisProfile()
        analysis_first.site_ip_prefixes['192.0.2'] = 'Site'
        analysis_first.analysis_items.append('activation')

        self.assertEqual(second.selected_items, [])
        self.assertEqual(second.target_selector, {})
        self.assertEqual(analysis_second.site_ip_prefixes, {})
        self.assertEqual(analysis_second.analysis_items, [])
        self.assertEqual(analysis_second.kms_servers, [])

    def test_inspection_profile_rejects_duplicate_item_keys(self):
        profile = InspectionProfile(
            name='服务器巡检',
            device_type='server',
            selected_items=['cpu', 'cpu'],
        )

        with self.assertRaises(ValidationError) as caught:
            profile.full_clean(validate_unique=False, validate_constraints=False)

        self.assertEqual(set(caught.exception.message_dict), {'selected_items'})

    def test_inspection_profile_requires_object_target_selector(self):
        profile = InspectionProfile(
            name='服务器巡检',
            device_type='server',
            target_selector=[],
        )

        with self.assertRaises(ValidationError) as caught:
            profile.full_clean(validate_unique=False, validate_constraints=False)

        self.assertEqual(set(caught.exception.message_dict), {'target_selector'})

    def test_profile_list_contracts_cannot_be_bypassed_with_empty_objects(self):
        inspection = InspectionProfile(
            name='服务器巡检',
            device_type='server',
            selected_items={},
        )
        analysis = ComputerAnalysisProfile(
            name='终端日志分析',
            kms_servers={},
            analysis_items={},
        )

        with self.assertRaises(ValidationError) as inspection_error:
            inspection.full_clean(validate_unique=False, validate_constraints=False)
        with self.assertRaises(ValidationError) as analysis_error:
            analysis.full_clean(validate_unique=False, validate_constraints=False)

        self.assertEqual(
            set(inspection_error.exception.message_dict),
            {'selected_items'},
        )
        self.assertEqual(
            set(analysis_error.exception.message_dict),
            {'kms_servers', 'analysis_items'},
        )

    def test_profile_limits_timeout_and_concurrency_to_positive_values(self):
        profile = InspectionProfile(
            name='服务器巡检',
            device_type='server',
            timeout_seconds=0,
            concurrent_workers=0,
        )

        with self.assertRaises(ValidationError) as caught:
            profile.full_clean(validate_unique=False, validate_constraints=False)

        self.assertEqual(
            set(caught.exception.message_dict),
            {'timeout_seconds', 'concurrent_workers'},
        )

    def test_computer_source_recent_days_mode_requires_a_window(self):
        profile = net_models.PCLogSourceConfig(
            file_time_mode='recent_days',
            recent_days=None,
        )

        with self.assertRaises(ValidationError) as caught:
            profile.clean()

        self.assertEqual(set(caught.exception.message_dict), {'recent_days'})

    def test_computer_source_date_range_requires_ordered_boundaries(self):
        profile = net_models.PCLogSourceConfig(
            file_time_mode='date_range',
            recent_days=None,
            range_start_date=date(2026, 8, 31),
            range_end_date=date(2026, 8, 1),
        )

        with self.assertRaises(ValidationError) as caught:
            profile.clean()

        self.assertEqual(
            set(caught.exception.message_dict),
            {'range_start_date', 'range_end_date'},
        )

    def test_computer_analysis_profile_requires_list_json_contracts(self):
        profile = ComputerAnalysisProfile(
            name='终端日志分析',
            kms_servers={'server': 'kms.test'},
            analysis_items=['activation', 1],
        )

        with self.assertRaises(ValidationError) as caught:
            profile.full_clean(validate_unique=False, validate_constraints=False)

        self.assertEqual(
            set(caught.exception.message_dict),
            {'kms_servers', 'analysis_items'},
        )


class ScheduleContractTests(SimpleTestCase):
    def test_schedule_exposes_only_interval_and_daily_configuration(self):
        fields = {field.name for field in Schedule._meta.fields}

        self.assertTrue(
            {
                'inspection_profile',
                'analysis_profile',
                'kind',
                'interval_value',
                'interval_unit',
                'daily_time',
                'is_enabled',
                'next_run_at',
                'last_enqueued_at',
            }
            <= fields
        )

    def _full_clean_without_database(self, schedule):
        schedule.full_clean(
            exclude={'inspection_profile', 'analysis_profile'},
            validate_unique=False,
            validate_constraints=False,
        )

    def test_only_supported_schedule_kinds_are_valid(self):
        schedule = Schedule(
            inspection_profile=InspectionProfile(),
            kind='cron',
        )

        with self.assertRaises(ValidationError) as caught:
            self._full_clean_without_database(schedule)

        self.assertIn('kind', caught.exception.message_dict)

    def test_schedule_requires_exactly_one_profile(self):
        schedule = Schedule(
            inspection_profile=InspectionProfile(),
            analysis_profile=ComputerAnalysisProfile(),
            kind='daily',
            daily_time=time(2, 30),
        )

        with self.assertRaises(ValidationError) as caught:
            self._full_clean_without_database(schedule)

        self.assertEqual(
            set(caught.exception.message_dict),
            {'inspection_profile', 'analysis_profile', 'people_source'},
        )

    def test_interval_schedule_requires_interval_fields_only(self):
        schedule = Schedule(
            inspection_profile=InspectionProfile(),
            kind='interval',
            daily_time=time(2, 30),
        )

        with self.assertRaises(ValidationError) as caught:
            self._full_clean_without_database(schedule)

        self.assertEqual(
            set(caught.exception.message_dict),
            {'interval_value', 'interval_unit', 'daily_time'},
        )

    def test_daily_schedule_requires_daily_time_only(self):
        schedule = Schedule(
            analysis_profile=ComputerAnalysisProfile(),
            kind='daily',
            interval_value=2,
            interval_unit='hours',
        )

        with self.assertRaises(ValidationError) as caught:
            self._full_clean_without_database(schedule)

        self.assertEqual(
            set(caught.exception.message_dict),
            {'interval_value', 'interval_unit', 'daily_time'},
        )


class TaskRunContractTests(SimpleTestCase):
    def test_task_run_has_status_progress_lease_and_snapshot_fields(self):
        fields = {field.name for field in TaskRun._meta.fields}

        self.assertTrue(
            {
                'task_type',
                'source',
                'inspection_profile',
                'analysis_profile',
                'schedule',
                'status',
                'progress',
                'available_at',
                'started_at',
                'finished_at',
                'lease_expires_at',
                'worker_id',
                'attempt_count',
                'error_summary',
                'profile_snapshot',
                'parameters_snapshot',
                'selected_items_snapshot',
                'target_scope_snapshot',
                'scope_key',
                'active_scope_key',
                'total_targets',
                'completed_targets',
                'successful_targets',
                'failed_targets',
                'created_at',
                'updated_at',
            }
            <= fields
        )

    def test_task_status_values_are_fixed(self):
        self.assertEqual(
            {value for value, _label in TaskRun.Status.choices},
            {'queued', 'running', 'success', 'partial', 'failed', 'cancelled'},
        )

    def test_target_run_has_immutable_target_and_result_reference_fields(self):
        fields = {field.name for field in TaskTargetRun._meta.fields}

        self.assertTrue(
            {
                'task',
                'target_type',
                'target_id',
                'target_snapshot',
                'status',
                'attempt_count',
                'started_at',
                'finished_at',
                'result_type',
                'result_id',
                'result_snapshot',
                'error_message',
                'created_at',
                'updated_at',
            }
            <= fields
        )

    def test_task_models_define_queue_and_schedule_indexes(self):
        task_indexes = {tuple(index.fields) for index in TaskRun._meta.indexes}
        target_indexes = {tuple(index.fields) for index in TaskTargetRun._meta.indexes}

        self.assertTrue(
            {
                ('status', 'available_at'),
                ('lease_expires_at',),
                ('schedule', 'created_at'),
            }
            <= task_indexes
        )
        self.assertIn(('task', 'status'), target_indexes)

    def test_portable_active_scope_slot_is_unique(self):
        active_scope_field = TaskRun._meta.get_field('active_scope_key')

        self.assertTrue(active_scope_field.unique)

    def test_task_target_identity_has_an_unconditional_unique_constraint(self):
        constrained_fields = {
            tuple(constraint.fields)
            for constraint in TaskTargetRun._meta.constraints
            if hasattr(constraint, 'fields')
        }

        self.assertIn(('task', 'target_type', 'target_id'), constrained_fields)

    def test_task_run_exposes_a_scope_key_builder(self):
        self.assertTrue(callable(getattr(TaskRun, 'build_scope_key', None)))

    def test_scope_key_is_stable_for_json_order_and_distinguishes_profiles(self):
        first = TaskRun.build_scope_key(
            task_type='inspection',
            profile_id='profile-a',
            target_scope_snapshot={'mode': 'selected', 'target_ids': ['b', 'a']},
        )
        reordered = TaskRun.build_scope_key(
            task_type='inspection',
            profile_id='profile-a',
            target_scope_snapshot={'target_ids': ['a', 'b'], 'mode': 'selected'},
        )
        other_profile = TaskRun.build_scope_key(
            task_type='inspection',
            profile_id='profile-b',
            target_scope_snapshot={'mode': 'selected', 'target_ids': ['b', 'a']},
        )

        self.assertEqual(first, reordered)
        self.assertNotEqual(first, other_profile)
        self.assertEqual(len(first), 64)

    def test_scope_key_ignores_selection_metadata_for_the_same_actual_targets(self):
        selected = TaskRun.build_scope_key(
            task_type='inspection',
            profile_id='profile-a',
            target_scope_snapshot={
                'mode': 'selected',
                'target_type': 'server',
                'target_ids': ['server-b', 'server-a'],
            },
        )
        filtered = TaskRun.build_scope_key(
            task_type='inspection',
            profile_id='profile-a',
            target_scope_snapshot={
                'mode': 'filtered',
                'filter_query': 'os=linux',
                'source': 'list-page',
                'target_type': 'server',
                'target_ids': ['server-a', 'server-b'],
            },
        )
        all_targets = TaskRun.build_scope_key(
            task_type='inspection',
            profile_id='profile-a',
            target_scope_snapshot={
                'mode': 'all',
                'target_type': 'server',
                'target_ids': ['server-a', 'server-b'],
            },
        )
        other_type = TaskRun.build_scope_key(
            task_type='inspection',
            profile_id='profile-a',
            target_scope_snapshot={
                'mode': 'selected',
                'target_type': 'monitor',
                'target_ids': ['server-a', 'server-b'],
            },
        )

        self.assertEqual(selected, filtered)
        self.assertEqual(selected, all_targets)
        self.assertNotEqual(selected, other_type)

    def _full_clean_without_database(self, task):
        task.full_clean(
            exclude={'inspection_profile', 'analysis_profile', 'schedule'},
            validate_unique=False,
            validate_constraints=False,
        )

    def test_task_type_requires_the_matching_profile(self):
        task = TaskRun(
            task_type='inspection',
            analysis_profile=ComputerAnalysisProfile(),
            scope_key='a' * 64,
        )

        with self.assertRaises(ValidationError) as caught:
            self._full_clean_without_database(task)

        self.assertTrue(
            {'inspection_profile', 'analysis_profile'}
            <= set(caught.exception.message_dict)
        )

    def test_scheduled_source_requires_a_schedule(self):
        task = TaskRun(
            task_type='inspection',
            source='scheduled',
            inspection_profile=InspectionProfile(),
            scope_key='a' * 64,
        )

        with self.assertRaises(ValidationError) as caught:
            self._full_clean_without_database(task)

        self.assertIn('schedule', caught.exception.message_dict)

    def test_scheduled_task_must_use_the_schedule_profile(self):
        scheduled_profile = InspectionProfile(name='计划配置', device_type='server')
        selected_profile = InspectionProfile(name='任务配置', device_type='server')
        schedule = Schedule(
            inspection_profile=scheduled_profile,
            kind='interval',
            interval_value=1,
            interval_unit='hours',
        )
        task = TaskRun(
            task_type='inspection',
            source='scheduled',
            inspection_profile=selected_profile,
            schedule=schedule,
            scope_key='a' * 64,
        )

        with self.assertRaises(ValidationError) as caught:
            self._full_clean_without_database(task)

        self.assertEqual(set(caught.exception.message_dict), {'schedule'})

    def test_scheduled_analysis_must_use_the_schedule_profile(self):
        scheduled_profile = ComputerAnalysisProfile(name='计划配置')
        selected_profile = ComputerAnalysisProfile(name='任务配置')
        schedule = Schedule(
            analysis_profile=scheduled_profile,
            kind='daily',
            daily_time=time(2, 30),
        )
        task = TaskRun(
            task_type='computer_analysis',
            source='scheduled',
            analysis_profile=selected_profile,
            schedule=schedule,
            scope_key='a' * 64,
        )

        with self.assertRaises(ValidationError) as caught:
            self._full_clean_without_database(task)

        self.assertEqual(set(caught.exception.message_dict), {'schedule'})

    def test_task_snapshots_enforce_list_and_object_contracts(self):
        task = TaskRun(
            task_type='inspection',
            inspection_profile=InspectionProfile(),
            profile_snapshot=[],
            parameters_snapshot=[],
            selected_items_snapshot=['cpu', 1],
            target_scope_snapshot=[],
            scope_key='a' * 64,
        )

        with self.assertRaises(ValidationError) as caught:
            self._full_clean_without_database(task)

        self.assertEqual(
            set(caught.exception.message_dict),
            {
                'profile_snapshot',
                'parameters_snapshot',
                'selected_items_snapshot',
                'target_scope_snapshot',
            },
        )

    def test_task_and_target_snapshot_defaults_are_isolated(self):
        first_task = TaskRun()
        second_task = TaskRun()
        first_target = TaskTargetRun()
        second_target = TaskTargetRun()
        first_task.parameters_snapshot['threads'] = 8
        first_task.selected_items_snapshot.append('cpu')
        first_target.target_snapshot['ip'] = '192.0.2.1'

        self.assertEqual(second_task.parameters_snapshot, {})
        self.assertEqual(second_task.selected_items_snapshot, [])
        self.assertEqual(second_target.target_snapshot, {})

    def test_task_snapshot_rejects_non_json_compatible_values(self):
        task = TaskRun(
            task_type='inspection',
            inspection_profile=InspectionProfile(),
            parameters_snapshot={'invalid': {1, 2}},
            scope_key='a' * 64,
        )

        with self.assertRaises(ValidationError) as caught:
            self._full_clean_without_database(task)

        self.assertIn('parameters_snapshot', caught.exception.message_dict)

    def test_running_task_requires_started_time_worker_and_lease(self):
        task = TaskRun(
            task_type='inspection',
            inspection_profile=InspectionProfile(),
            status='running',
            scope_key='a' * 64,
        )

        with self.assertRaises(ValidationError) as caught:
            self._full_clean_without_database(task)

        self.assertEqual(
            set(caught.exception.message_dict),
            {'started_at', 'worker_id', 'lease_expires_at'},
        )

    def test_running_task_rejects_a_finished_timestamp(self):
        now = timezone.now()
        task = TaskRun(
            task_type='inspection',
            inspection_profile=InspectionProfile(),
            status='running',
            started_at=now,
            finished_at=now,
            worker_id='worker-a',
            lease_expires_at=now + timedelta(minutes=1),
            scope_key='a' * 64,
        )

        with self.assertRaises(ValidationError) as caught:
            self._full_clean_without_database(task)

        self.assertEqual(set(caught.exception.message_dict), {'finished_at'})

    def test_every_terminal_task_requires_a_finished_timestamp(self):
        for status in ('success', 'partial', 'failed', 'cancelled'):
            with self.subTest(status=status):
                task = TaskRun(
                    task_type='inspection',
                    inspection_profile=InspectionProfile(),
                    status=status,
                    progress=100,
                    scope_key='a' * 64,
                )
                with self.assertRaises(ValidationError) as caught:
                    self._full_clean_without_database(task)
                self.assertIn('finished_at', caught.exception.message_dict)

    def test_completed_outcome_states_require_full_aggregation(self):
        now = timezone.now()
        invalid_tasks = (
            TaskRun(
                task_type='inspection',
                inspection_profile=InspectionProfile(),
                status='success',
                finished_at=now,
                progress=25,
                total_targets=4,
                completed_targets=1,
                successful_targets=1,
                scope_key='a' * 64,
            ),
            TaskRun(
                task_type='inspection',
                inspection_profile=InspectionProfile(),
                status='partial',
                finished_at=now,
                progress=100,
                total_targets=2,
                completed_targets=2,
                successful_targets=2,
                failed_targets=0,
                scope_key='b' * 64,
            ),
            TaskRun(
                task_type='inspection',
                inspection_profile=InspectionProfile(),
                status='failed',
                finished_at=now,
                progress=100,
                total_targets=2,
                completed_targets=2,
                successful_targets=1,
                failed_targets=1,
                scope_key='c' * 64,
            ),
        )

        for task in invalid_tasks:
            with self.subTest(status=task.status):
                with self.assertRaises(ValidationError):
                    self._full_clean_without_database(task)

    def test_valid_terminal_aggregation_and_cancelled_partial_progress(self):
        now = timezone.now()
        valid_tasks = (
            TaskRun(
                task_type='inspection', inspection_profile=InspectionProfile(),
                status='success', finished_at=now, progress=100,
                total_targets=2, completed_targets=2,
                successful_targets=2, failed_targets=0, scope_key='a' * 64,
            ),
            TaskRun(
                task_type='inspection', inspection_profile=InspectionProfile(),
                status='partial', finished_at=now, progress=100,
                total_targets=2, completed_targets=2,
                successful_targets=1, failed_targets=1, scope_key='b' * 64,
            ),
            TaskRun(
                task_type='inspection', inspection_profile=InspectionProfile(),
                status='failed', finished_at=now, progress=100,
                total_targets=2, completed_targets=2,
                successful_targets=0, failed_targets=2, scope_key='c' * 64,
            ),
            TaskRun(
                task_type='inspection', inspection_profile=InspectionProfile(),
                status='cancelled', finished_at=now, progress=50,
                total_targets=2, completed_targets=1,
                successful_targets=1, failed_targets=0, scope_key='d' * 64,
            ),
        )

        for task in valid_tasks:
            with self.subTest(status=task.status):
                self._full_clean_without_database(task)

    def test_task_progress_counts_and_timing_are_consistent(self):
        now = timezone.now()
        task = TaskRun(
            task_type='inspection',
            inspection_profile=InspectionProfile(),
            progress=101,
            total_targets=1,
            completed_targets=2,
            successful_targets=2,
            failed_targets=1,
            started_at=now,
            finished_at=now - timedelta(seconds=1),
            scope_key='a' * 64,
        )

        with self.assertRaises(ValidationError) as caught:
            self._full_clean_without_database(task)

        self.assertTrue(
            {
                'progress',
                'completed_targets',
                'successful_targets',
                'failed_targets',
                'finished_at',
            }
            <= set(caught.exception.message_dict)
        )

    def test_dynamic_records_link_to_target_runs_without_owning_history(self):
        for record_model in (
            net_models.ComputerAnalysis,
            net_models.Network_Device_Inspection,
            net_models.Server_Inspection,
            net_models.Monitor_Inspection,
        ):
            field_names = {field.name for field in record_model._meta.fields}
            self.assertIn('task_target', field_names, record_model.__name__)
            task_target_field = record_model._meta.get_field('task_target')
            self.assertIs(task_target_field.remote_field.model, TaskTargetRun)

    def _full_clean_target_without_database(self, target):
        target.full_clean(
            exclude={'task'},
            validate_unique=False,
            validate_constraints=False,
        )

    def test_target_snapshots_require_json_objects(self):
        target = TaskTargetRun(
            task=TaskRun(),
            target_type='server',
            target_id='server-a',
            target_snapshot=[],
            result_snapshot=[],
        )

        with self.assertRaises(ValidationError) as caught:
            self._full_clean_target_without_database(target)

        self.assertEqual(
            set(caught.exception.message_dict),
            {'target_snapshot', 'result_snapshot'},
        )

    def test_target_result_reference_requires_type_and_id_together(self):
        target = TaskTargetRun(
            task=TaskRun(),
            target_type='server',
            target_id='server-a',
            result_type='server_inspection',
        )

        with self.assertRaises(ValidationError) as caught:
            self._full_clean_target_without_database(target)

        self.assertEqual(set(caught.exception.message_dict), {'result_id'})

    def test_target_running_and_terminal_states_require_timestamps(self):
        running = TaskTargetRun(
            task=TaskRun(),
            target_type='server',
            target_id='server-a',
            status='running',
        )
        terminal = TaskTargetRun(
            task=TaskRun(),
            target_type='server',
            target_id='server-b',
            status='success',
        )

        with self.assertRaises(ValidationError) as running_error:
            self._full_clean_target_without_database(running)
        with self.assertRaises(ValidationError) as terminal_error:
            self._full_clean_target_without_database(terminal)

        self.assertEqual(
            set(running_error.exception.message_dict),
            {'started_at'},
        )
        self.assertEqual(
            set(terminal_error.exception.message_dict),
            {'finished_at'},
        )

    def test_target_type_must_match_the_task_profile(self):
        inspection_types = ('network_device', 'server', 'monitor')
        all_target_types = (*inspection_types, 'computer_log')

        for profile_type in inspection_types:
            task = TaskRun(
                task_type='inspection',
                inspection_profile=InspectionProfile(device_type=profile_type),
            )
            for target_type in all_target_types:
                target = TaskTargetRun(
                    task=task,
                    target_type=target_type,
                    target_id=f'{target_type}-a',
                )
                with self.subTest(task_type='inspection', profile_type=profile_type,
                                  target_type=target_type):
                    if target_type == profile_type:
                        self._full_clean_target_without_database(target)
                    else:
                        with self.assertRaises(ValidationError) as caught:
                            self._full_clean_target_without_database(target)
                        self.assertEqual(
                            set(caught.exception.message_dict),
                            {'target_type'},
                        )

        analysis_task = TaskRun(
            task_type='computer_analysis',
            analysis_profile=ComputerAnalysisProfile(),
        )
        for target_type in all_target_types:
            target = TaskTargetRun(
                task=analysis_task,
                target_type=target_type,
                target_id=f'{target_type}-a',
            )
            with self.subTest(task_type='computer_analysis', target_type=target_type):
                if target_type == 'computer_log':
                    self._full_clean_target_without_database(target)
                else:
                    with self.assertRaises(ValidationError) as caught:
                        self._full_clean_target_without_database(target)
                    self.assertEqual(
                        set(caught.exception.message_dict),
                        {'target_type'},
                    )


class TaskModelDatabaseTests(TestCase):
    def setUp(self):
        self.profile = InspectionProfile.objects.create(
            name='服务器默认巡检',
            device_type='server',
            selected_items=['cpu'],
        )

    def _create_task(self, *, target_ids=None, status='queued', scope_snapshot=None):
        return TaskRun.objects.create(
            task_type='inspection',
            inspection_profile=self.profile,
            status=status,
            profile_snapshot={'name': self.profile.name},
            selected_items_snapshot=['cpu'],
            target_scope_snapshot=scope_snapshot
            or {
                'mode': 'selected',
                'target_type': 'server',
                'target_ids': target_ids or ['server-a'],
            },
            scope_key='',
        )

    def test_active_duplicate_scope_is_rejected_by_portable_unique_slot(self):
        first = self._create_task()

        self.assertEqual(len(first.scope_key), 64)
        self.assertEqual(first.active_scope_key, first.scope_key)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._create_task()

        different_scope = self._create_task(target_ids=['server-b'])
        self.assertNotEqual(first.scope_key, different_scope.scope_key)

    def test_same_actual_targets_from_different_modes_conflict_in_database(self):
        self._create_task(
            scope_snapshot={
                'mode': 'all',
                'target_type': 'server',
                'target_ids': ['server-a', 'server-b'],
            }
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._create_task(
                    scope_snapshot={
                        'mode': 'filtered',
                        'filter_query': 'os=linux',
                        'target_type': 'server',
                        'target_ids': ['server-b', 'server-a'],
                    }
                )

    def test_database_rejects_an_active_task_without_its_scope_slot(self):
        task = self._create_task()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TaskRun.objects.filter(pk=task.pk).update(active_scope_key=None)

    def test_database_requires_active_slot_to_equal_the_scope_key(self):
        task = self._create_task()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TaskRun.objects.filter(pk=task.pk).update(
                    active_scope_key='f' * 64,
                )

    def test_database_rejects_invalid_terminal_and_running_shapes(self):
        unfinished = self._create_task(target_ids=['server-a', 'server-b'])
        running_finished = self._create_task(target_ids=['server-c'])
        now = timezone.now()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TaskRun.objects.filter(pk=unfinished.pk).update(
                    status='success',
                    active_scope_key=None,
                    progress=25,
                    completed_targets=1,
                    successful_targets=1,
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TaskRun.objects.filter(pk=running_finished.pk).update(
                    status='running',
                    started_at=now,
                    finished_at=now,
                    worker_id='worker-a',
                    lease_expires_at=now + timedelta(minutes=1),
                )

    def test_database_accepts_valid_outcomes_and_cancelled_partial_progress(self):
        now = timezone.now()
        success = self._create_task(target_ids=['success-a', 'success-b'])
        partial = self._create_task(target_ids=['partial-a', 'partial-b'])
        failed = self._create_task(target_ids=['failed-a', 'failed-b'])
        cancelled = self._create_task(target_ids=['cancel-a', 'cancel-b'])

        TaskRun.objects.filter(pk=success.pk).update(
            status='success', active_scope_key=None, finished_at=now,
            progress=100, total_targets=2, completed_targets=2,
            successful_targets=2, failed_targets=0, lease_expires_at=None,
        )
        TaskRun.objects.filter(pk=partial.pk).update(
            status='partial', active_scope_key=None, finished_at=now,
            progress=100, total_targets=2, completed_targets=2,
            successful_targets=1, failed_targets=1, lease_expires_at=None,
        )
        TaskRun.objects.filter(pk=failed.pk).update(
            status='failed', active_scope_key=None, finished_at=now,
            progress=100, total_targets=2, completed_targets=2,
            successful_targets=0, failed_targets=2, lease_expires_at=None,
        )
        TaskRun.objects.filter(pk=cancelled.pk).update(
            status='cancelled', active_scope_key=None, finished_at=now,
            progress=50, total_targets=2, completed_targets=1,
            successful_targets=1, failed_targets=0, lease_expires_at=None,
        )

        self.assertEqual(
            set(TaskRun.objects.filter(active_scope_key__isnull=True).values_list('status', flat=True)),
            {'success', 'partial', 'failed', 'cancelled'},
        )

    def test_database_and_model_accept_parent_partial_with_no_successful_targets(self):
        task = self._create_task(target_ids=['partial-only'])
        now = timezone.now()

        TaskRun.objects.filter(pk=task.pk).update(
            status='partial', active_scope_key=None, finished_at=now,
            progress=100, total_targets=1, completed_targets=1,
            successful_targets=0, failed_targets=1, lease_expires_at=None,
        )

        task.refresh_from_db()
        task.full_clean()
        self.assertEqual(task.status, TaskRun.Status.PARTIAL)
        self.assertEqual(task.successful_targets, 0)
        self.assertEqual(task.failed_targets, 1)

    def test_terminal_task_releases_scope_for_a_later_run(self):
        task = self._create_task()
        task.status = TaskRun.Status.SUCCESS
        task.finished_at = timezone.now()
        task.progress = 100
        task.save(update_fields={'status', 'finished_at', 'progress'})

        self.assertIsNone(task.active_scope_key)
        replacement = self._create_task()
        self.assertEqual(replacement.scope_key, task.scope_key)

    def test_duplicate_target_identity_is_rejected(self):
        task = self._create_task()
        TaskTargetRun.objects.create(
            task=task,
            target_type='server',
            target_id='server-a',
            status='running',
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TaskTargetRun.objects.create(
                    task=task,
                    target_type='server',
                    target_id='server-a',
                    status='running',
                )

    def test_history_relations_are_protected_from_parent_deletion(self):
        task = self._create_task()
        target = TaskTargetRun.objects.create(
            task=task,
            target_type='server',
            target_id='server-a',
        )
        server = net_models.Server.objects.create(ip='192.0.2.90')
        net_models.Server_Inspection.objects.create(
            server=server,
            task_target=target,
        )

        with self.assertRaises(ProtectedError):
            self.profile.delete()
        with self.assertRaises(ProtectedError):
            task.delete()
        with self.assertRaises(ProtectedError):
            target.delete()

    def test_task_and_target_execution_snapshots_cannot_be_rewritten(self):
        task = self._create_task()
        target = TaskTargetRun.objects.create(
            task=task,
            target_type='server',
            target_id='server-a',
            target_snapshot={'ip': '192.0.2.90'},
        )
        task.parameters_snapshot = {'threads': 32}
        target.target_snapshot = {'ip': '192.0.2.91'}

        with self.assertRaises(ValidationError):
            task.save(update_fields={'parameters_snapshot'})
        with self.assertRaises(ValidationError):
            target.save(update_fields={'target_snapshot'})

        task.refresh_from_db()
        target.refresh_from_db()
        self.assertEqual(task.parameters_snapshot, {})
        self.assertEqual(target.target_snapshot, {'ip': '192.0.2.90'})

    def test_target_result_can_be_populated_and_retried_before_terminal(self):
        target = TaskTargetRun.objects.create(
            task=self._create_task(),
            target_type='server',
            target_id='server-a',
        )
        target.status = TaskRun.Status.RUNNING
        target.started_at = timezone.now()
        target.attempt_count = 1
        target.result_type = 'server_inspection'
        target.result_id = 'attempt-1'
        target.result_snapshot = {'cpu': 91}
        target.save()

        target.attempt_count = 2
        target.result_id = 'attempt-2'
        target.result_snapshot = {'cpu': 42}
        target.save()

        target.refresh_from_db()
        self.assertEqual(target.attempt_count, 2)
        self.assertEqual(target.result_id, 'attempt-2')
        self.assertEqual(target.result_snapshot, {'cpu': 42})

    def test_target_result_can_be_finalized_during_terminal_transition(self):
        target = TaskTargetRun.objects.create(
            task=self._create_task(),
            target_type='server',
            target_id='server-a',
            status=TaskRun.Status.RUNNING,
            started_at=timezone.now(),
            result_type='server_inspection',
            result_id='attempt-1',
        )

        target.status = TaskRun.Status.SUCCESS
        target.finished_at = timezone.now()
        target.result_id = 'final-result'
        target.result_snapshot = {'cpu': 20}
        target.save()

        target.refresh_from_db()
        self.assertEqual(target.status, TaskRun.Status.SUCCESS)
        self.assertEqual(target.result_id, 'final-result')
        self.assertEqual(target.result_snapshot, {'cpu': 20})

    def test_terminal_target_result_reference_and_snapshot_cannot_be_changed(self):
        target = TaskTargetRun.objects.create(
            task=self._create_task(),
            target_type='server',
            target_id='server-a',
            status=TaskRun.Status.SUCCESS,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            result_type='server_inspection',
            result_id='final-result',
            result_snapshot={'cpu': 20},
        )
        target.result_type = 'replacement'
        target.result_id = 'replacement-result'
        target.result_snapshot = {'cpu': 99}

        with self.assertRaises(ValidationError) as caught:
            target.save()

        self.assertEqual(
            set(caught.exception.message_dict),
            {'result_type', 'result_id', 'result_snapshot'},
        )
        target.refresh_from_db()
        self.assertEqual(target.result_type, 'server_inspection')
        self.assertEqual(target.result_id, 'final-result')
        self.assertEqual(target.result_snapshot, {'cpu': 20})

    def test_terminal_target_cannot_be_reopened_and_keeps_its_result(self):
        task = self._create_task()
        terminal_statuses = (
            TaskRun.Status.SUCCESS,
            TaskRun.Status.PARTIAL,
            TaskRun.Status.FAILED,
            TaskRun.Status.CANCELLED,
        )
        active_statuses = (TaskRun.Status.QUEUED, TaskRun.Status.RUNNING)

        for terminal_status in terminal_statuses:
            for active_status in active_statuses:
                with self.subTest(
                    terminal_status=terminal_status,
                    active_status=active_status,
                ):
                    target = TaskTargetRun.objects.create(
                        task=task,
                        target_type='server',
                        target_id=f'{terminal_status}-{active_status}',
                        status=terminal_status,
                        started_at=timezone.now(),
                        finished_at=timezone.now(),
                        result_type='server_inspection',
                        result_id='final-result',
                        result_snapshot={'cpu': 20},
                    )
                    target.status = active_status
                    target.finished_at = None

                    with self.assertRaises(ValidationError) as caught:
                        target.save()

                    self.assertEqual(
                        set(caught.exception.message_dict),
                        {'status'},
                    )
                    target.refresh_from_db()
                    self.assertEqual(target.status, terminal_status)
                    self.assertEqual(target.result_type, 'server_inspection')
                    self.assertEqual(target.result_id, 'final-result')
                    self.assertEqual(target.result_snapshot, {'cpu': 20})

    def test_database_rejects_unsupported_or_ambiguous_schedule_shapes(self):
        analysis_profile = ComputerAnalysisProfile.objects.create(
            name='终端日志分析',
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Schedule.objects.create(
                    inspection_profile=self.profile,
                    kind='cron',
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Schedule.objects.create(
                    inspection_profile=self.profile,
                    analysis_profile=analysis_profile,
                    kind='daily',
                    daily_time=time(2, 30),
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Schedule.objects.create(
                    inspection_profile=self.profile,
                    kind='interval',
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Schedule.objects.create(
                    inspection_profile=self.profile,
                    kind='interval',
                    interval_value=0,
                    interval_unit='minutes',
                )
