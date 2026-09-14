"""Final demo contract: deterministic, offline, ownership-safe and exportable."""
from collections import Counter
from contextlib import ExitStack
from datetime import date, time
from io import BytesIO, StringIO
from unittest.mock import patch
from zipfile import ZipFile

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from net.data_exchange.configuration import build_configuration_zip, latest_configuration
from net.models.domain import DomainOperationSecret
from net.management.commands.seed_demo_data import _demo_uuid
from net.models import (AlertDelivery, AlertEvent, ComputerAnalysis,
                        ComputerAnalysisProfile, InspectionProfile, SecurityDevice,
                        Network_Device, People, PeopleSyncSource, Schedule,
                        Network_Device_Inspection, DomainOperation, TaskRun,
                        TaskTargetRun)


@override_settings(DEVICE_BACKUP_ENCRYPTION_KEY='ZGVtby1maXh0dXJlLWtleS1ub3QtcmVhbC0wMDAwMDA=')
class FinalDemoTests(TestCase):
    def test_domain_operation_history_is_terminal_non_sensitive_and_idempotent(self):
        self.seed()
        self.seed()
        operations = list(DomainOperation.objects.order_by('action'))
        self.assertEqual([operation.action for operation in operations], ['enable', 'move_ou'])
        self.assertTrue(all(
            operation.status in TaskRun.TERMINAL_STATUSES
            and operation.task.status in TaskRun.TERMINAL_STATUSES
            for operation in operations
        ))
        self.assertTrue(all(operation.parameter_summary for operation in operations))
        self.assertFalse(any(
            any('password' in key.casefold() or 'secret' in key.casefold()
                for key in operation.parameter_summary)
            for operation in operations
        ))
        self.assertFalse(DomainOperationSecret.objects.exists())
        self.assertEqual(
            TaskTargetRun.objects.filter(
                task__task_type=TaskRun.TaskType.DOMAIN_OPERATION,
            ).count(),
            2,
        )

    def test_successful_demo_targets_link_to_successful_saved_records(self):
        self.seed()
        targets = TaskTargetRun.objects.filter(
            task__task_type=TaskRun.TaskType.INSPECTION,
            task__parameters_snapshot__demo_only=True,
            status='success',
        )
        self.assertEqual(targets.count(), 2)
        for target in targets:
            self.assertEqual(Network_Device_Inspection.objects.get(pk=target.result_id).status, 'success')

    def test_reset_restores_changed_schedule_and_analysis_modes(self):
        self.seed()
        Schedule.objects.filter(pk=_demo_uuid('demo-schedule-interval')).update(
            kind='daily', daily_time=time(12), interval_value=None, interval_unit='', is_enabled=True)
        Schedule.objects.filter(pk=_demo_uuid('demo-schedule-daily')).update(
            kind='interval', daily_time=None, interval_value=5, interval_unit='minutes', is_enabled=True)
        ComputerAnalysisProfile.objects.update(
            analysis_items=['activation'], concurrent_workers=1)
        self.seed(reset=True)
        interval = Schedule.objects.get(pk=_demo_uuid('demo-schedule-interval'))
        daily = Schedule.objects.get(pk=_demo_uuid('demo-schedule-daily'))
        self.assertEqual((interval.kind, interval.interval_value, interval.daily_time), ('interval', 30, None))
        self.assertEqual((daily.kind, daily.interval_value, daily.interval_unit), ('daily', None, ''))
        analysis = ComputerAnalysisProfile.objects.get(pk=_demo_uuid('demo-profile-analysis'))
        self.assertEqual(analysis.analysis_items, ['resource', 'event_findings'])
        self.assertEqual(analysis.concurrent_workers, 4)
        for instance in (interval, daily, analysis):
            instance.full_clean()

    def test_seeded_analysis_items_are_selectable_in_manual_form(self):
        from index.inspections.forms import analysis_item_choices
        self.seed()
        self.assertTrue(ComputerAnalysisProfile.objects.exists())
        allowed = {key for key, _ in analysis_item_choices()}
        for profile in ComputerAnalysisProfile.objects.all():
            self.assertTrue(set(profile.analysis_items) <= allowed)

    def seed(self, **kwargs):
        output = StringIO()
        with ExitStack() as stack:
            guards = [stack.enter_context(patch(target, side_effect=AssertionError('outbound I/O')))
                      for target in ('requests.sessions.Session.request', 'aiohttp.ClientSession._request',
                                     'smtplib.SMTP', 'smtplib.SMTP_SSL', 'paramiko.SSHClient.connect',
                                     'ldap3.Connection.bind')]
            call_command('seed_demo_data', stdout=output, **kwargs)
            for guard in guards:
                guard.assert_not_called()
        return output.getvalue()

    def test_complete_counts_idempotence_and_disabled_external_sources(self):
        for reset in (False, False, True):
            with self.subTest(reset=reset):
                output = self.seed(reset=reset)
                self.assertEqual(PeopleSyncSource.objects.count(), 2)
                self.assertEqual(People.objects.count(), 7)
                self.assertEqual(ComputerAnalysis.objects.count(), 4)
                self.assertEqual(InspectionProfile.objects.count(), 3)
                self.assertEqual(ComputerAnalysisProfile.objects.count(), 1)
                self.assertEqual(Schedule.objects.count(), 2)
                self.assertFalse(Schedule.objects.filter(is_enabled=True).exists())
                self.assertFalse(PeopleSyncSource.objects.filter(is_enabled=True).exists())
                for source in PeopleSyncSource.objects.all():
                    self.assertEqual(source.people.filter(is_active=True).count(), 1)
                    self.assertEqual(source.people.filter(is_active=False).count(), 1)
                    self.assertTrue(all(value.startswith('DEMO-ONLY-') for value in source.credentials.values()))
                self.assertEqual(Counter(TaskRun.objects.values_list('status', flat=True)),
                                 {'queued': 1, 'running': 1, 'success': 5, 'partial': 1, 'failed': 1, 'cancelled': 1})
                self.assertEqual(TaskTargetRun.objects.count(), 11)
                self.assertEqual(Counter(AlertEvent.objects.values_list('event_type', flat=True)),
                                 {'abnormal': 1, 'recovery': 1})
                self.assertEqual(Counter(AlertDelivery.objects.values_list('status', flat=True)),
                                 {'sent': 2, 'failed': 1})
                self.assertIn('目录来源 2', output)
                self.assertIn('计划 2', output)
                self.assertIn('任务 10（含域操作 2）', output)
                for model in (Schedule, TaskRun, TaskTargetRun):
                    for instance in model.objects.all():
                        instance.full_clean()

    def test_saved_native_configs_and_explicit_unsupported_outcomes(self):
        self.seed()
        for model, ip, status, content in (
            (Network_Device, '192.0.2.11', 'missing', b''),
            (Network_Device, '192.0.2.12', 'success', b'return'),
            (Network_Device, '192.0.2.13', 'success', b'end'),
            (SecurityDevice, '203.0.113.31', 'missing', b''),
            (SecurityDevice, '203.0.113.32', 'missing', b''),
            (SecurityDevice, '203.0.113.33', 'missing', b''),
        ):
            with self.subTest(ip=ip):
                result = latest_configuration(model.objects.get(ip=ip))
                self.assertEqual(result.status, status)
                self.assertIn(content, result.content)
        from net.models import DeviceConfigurationBackup
        self.assertEqual(DeviceConfigurationBackup.objects.count(), 2)
        self.assertFalse(DeviceConfigurationBackup.objects.filter(device_type='monitor').exists())
        for backup in DeviceConfigurationBackup.objects.all():
            self.assertNotIn(b'hostname', bytes(backup.ciphertext))
            self.assertNotIn(b'sysname', bytes(backup.ciphertext))
        self.seed()
        self.assertEqual(DeviceConfigurationBackup.objects.count(), 2)
        with ZipFile(BytesIO(build_configuration_zip(Network_Device.objects.all()))) as archive:
            self.assertEqual(len(archive.namelist()), 3)
            self.assertIn(b'missing', archive.read('manifest.csv'))

    def test_reset_preserves_unrelated_source_people_and_schedule(self):
        self.seed()
        source = PeopleSyncSource.objects.create(name='User source', source_key='user', source_type='feishu',
                                                credentials={'app_id': 'user', 'app_secret': 'user'})
        person = People.objects.create(employee_id='USER', sync_source=source)
        profile = InspectionProfile.objects.create(name='User profile', device_type='server')
        schedule = Schedule.objects.create(inspection_profile=profile, kind='interval',
                                           interval_value=10, interval_unit='minutes')
        self.seed(reset=True)
        self.assertTrue(PeopleSyncSource.objects.filter(pk=source.pk).exists())
        self.assertTrue(People.objects.filter(pk=person.pk, sync_source=source).exists())
        self.assertTrue(Schedule.objects.filter(pk=schedule.pk, inspection_profile=profile).exists())

    def test_new_source_uuid_collision_aborts_atomically(self):
        source = PeopleSyncSource.objects.create(id=_demo_uuid('people-source-feishu'), name='User source',
                                                source_key='user', source_type='feishu',
                                                credentials={'app_id': 'user', 'app_secret': 'user'})
        with self.assertRaises(CommandError):
            self.seed(reset=True)
        self.assertEqual(People.objects.count(), 0)
        source.refresh_from_db()
        self.assertEqual(source.source_key, 'user')

    def test_reset_does_not_cascade_user_targets_on_demo_task(self):
        self.seed()
        self.assertTrue(TaskRun.objects.filter(pk=_demo_uuid('demo-task-success')).exists())
        task = TaskRun.objects.get(pk=_demo_uuid('demo-task-success'))
        extra = TaskTargetRun.objects.create(task=task, target_type='network_device', target_id='user-target')
        # Refuse a destructive reset of a graph whose ownership is no longer exclusive.
        with self.assertRaises(CommandError):
            self.seed(reset=True)
        self.assertTrue(TaskTargetRun.objects.filter(pk=extra.pk).exists())
