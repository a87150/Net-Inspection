"""Regression contracts for the six Task 6 review findings."""
from concurrent.futures import ThreadPoolExecutor
from datetime import time, timedelta
from threading import Barrier
from unittest.mock import patch

from django.db import IntegrityError, connections, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from tests.auth import login_admin
from django.utils import timezone

from tests.devices.pc.helpers import create_log_file

from net.models import (ComputerAnalysisProfile, ComputerLogFile, InspectionProfile,
                        Schedule, Server, Server_Inspection, TaskRun)
from net.devices.pc.remote_ingestion import FetchSummary
from net.inspections.queue import enqueue_computer_fetch_task
from net.inspections.queue import claim_next_task
from net.devices.pc import executor as scan_executor
from net.inspections.executor import _begin_target


class ReviewUiTests(TestCase):
    def setUp(self):
        login_admin(self.client)
        self.cpu = InspectionProfile.objects.create(name='A CPU', device_type='server', selected_items=['cpu'])
        self.memory = InspectionProfile.objects.create(name='B Memory', device_type='server', selected_items=['memory'], timeout_seconds=123, concurrent_workers=7)
        Schedule.objects.create(inspection_profile=self.memory, kind='daily', daily_time=time(4, 25))
        self.first = Server.objects.create(name='First Linux', ip='192.0.2.1', server_type='linux')
        self.second = Server.objects.create(name='Second Linux', ip='192.0.2.2', server_type='linux')
        for _ in range(2):
            Server_Inspection.objects.create(server=self.first, status='success', is_reachable=True)
        Server_Inspection.objects.create(server=self.second, status='failed', is_reachable=False)

    def test_switch_disjoint_profiles_loads_full_matching_state_and_preserves_other(self):
        for modal in ('run', 'profile'):
            response = self.client.get('/assets/servers/', {'task_profile': str(self.memory.pk), 'task_modal': modal})
            self.assertEqual(response.context['task_default_profile'].pk, self.memory.pk)
            self.assertEqual(response.context['task_default_items'], ['memory'])
            self.assertEqual(response.context['task_default_schedule'].daily_time, time(4, 25))
            self.assertContains(response, 'value="123"')
            self.assertContains(response, 'value="7"')
        self.client.post('/tasks/profiles/inspection/', {
            'profile_id': self.memory.pk, 'name': 'B Changed', 'selected_items': ['memory'],
            'timeout_seconds': 124, 'concurrent_workers': 6, 'schedule_enabled': 'on',
            'schedule_kind': 'daily', 'daily_time': '05:30', 'next': '/assets/servers/',
        })
        self.cpu.refresh_from_db()
        self.assertEqual((self.cpu.name, self.cpu.selected_items, self.cpu.timeout_seconds), ('A CPU', ['cpu'], 60))

    def test_switch_computer_profiles_loads_rules_and_schedule(self):
        ComputerAnalysisProfile.objects.create(name='A', analysis_items=['activation'])
        other = ComputerAnalysisProfile.objects.create(name='B', analysis_items=['resource'], cpu_temperature_max_celsius=91, site_ip_prefixes={'192.0.2': 'Test Site'})
        Schedule.objects.create(analysis_profile=other, kind='interval', interval_value=2, interval_unit='hours')
        response = self.client.get('/assets/computers/', {'task_profile': str(other.pk), 'task_modal': 'profile'})
        self.assertEqual(response.context['task_default_items'], ['resource'])
        self.assertContains(response, 'value="91"')
        self.assertContains(response, 'Test Site')
        self.assertEqual(response.context['task_default_schedule'].interval_value, 2)

    def test_record_filters_resolve_deduplicated_assets(self):
        cases = [({'filter_status': 'normal'}, [self.first]),
                 ({'filter_asset': 'Second'}, [self.second]),
                 ({'target': str(self.first.pk)}, [self.first]),
                 ({'filter_asset': 'Missing'}, [])]
        for filters, expected in cases:
            with self.subTest(filters=filters):
                response = self.client.post('/tasks/manual/', {
                    'profile_id': self.cpu.pk, 'target_mode': 'filtered', 'target_source': 'records',
                    'selected_items': ['cpu'], 'next': '/records/servers/', **filters,
                })
                tasks = TaskRun.objects.all()
                if expected:
                    self.assertEqual(tasks.count(), 1)
                    self.assertEqual(set(tasks.get().target_runs.values_list('target_id', flat=True)), {str(asset.pk) for asset in expected})
                    tasks.get().target_runs.all().delete()
                    tasks.delete()
                else:
                    self.assertEqual(tasks.count(), 0)
                    self.assertEqual(response.status_code, 302)

    def test_visible_entrypoints_link_to_configured_execution(self):
        home = self.client.get('/')
        detail = self.client.get(reverse('asset_detail', args=['servers', self.first.pk]))
        self.assertNotContains(home, 'action="/actions/run-infrastructure-inspection/"')
        self.assertContains(home, '/assets/servers/?task_modal=run')
        self.assertNotContains(detail, 'action="/actions/run-infrastructure-inspection/"')
        self.assertContains(detail, f'task_targets={self.first.pk}')

    def test_row_execution_can_restore_exactly_one_selected_asset(self):
        response = self.client.get('/assets/servers/', {'task_targets': str(self.second.pk), 'task_mode': 'selected', 'task_modal': 'run'})
        self.assertContains(response, f'task_single_target={self.second.pk}" data-single-device-run')
        self.assertEqual(response.context['task_selected_ids'], [str(self.second.pk)])
        self.client.post('/tasks/manual/', {'profile_id': self.memory.pk, 'target_mode': 'selected', 'target_ids': [self.second.pk], 'selected_items': ['memory']})
        self.assertEqual(list(TaskRun.objects.get().target_runs.values_list('target_id', flat=True)), [str(self.second.pk)])


class ScheduleUniquenessTests(TransactionTestCase):
    def test_database_rejects_second_schedule_for_each_profile_type(self):
        profiles = [('inspection_profile', InspectionProfile.objects.create(name='S', device_type='server')),
                    ('analysis_profile', ComputerAnalysisProfile.objects.create(name='C'))]
        for field, profile in profiles:
            Schedule.objects.create(**{field: profile}, kind='daily', daily_time=time(1))
            with self.assertRaises(IntegrityError), transaction.atomic():
                Schedule.objects.create(**{field: profile}, kind='daily', daily_time=time(2))

    def test_two_requests_save_one_schedule(self):
        profile = InspectionProfile.objects.create(name='Concurrent', device_type='server', selected_items=['cpu'])
        barrier = Barrier(2)
        clients = {hour: Client() for hour in (1, 2)}
        for hour, client in clients.items():
            login_admin(client, username=f'schedule-admin-{hour}')
        def save(hour):
            connections.close_all()
            try:
                barrier.wait(timeout=5)
                return clients[hour].post('/tasks/profiles/inspection/', {
                    'profile_id': profile.pk, 'name': 'Concurrent', 'selected_items': ['cpu'],
                    'timeout_seconds': 60, 'concurrent_workers': 2, 'schedule_enabled': 'on',
                    'schedule_kind': 'daily', 'daily_time': f'{hour:02d}:00', 'next': '/assets/servers/',
                }).status_code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(save, [1, 2]))
        self.assertEqual(results, [302, 302])
        self.assertEqual(Schedule.objects.filter(inspection_profile=profile).count(), 1)


class ScanFenceTests(TransactionTestCase):
    def setUp(self):
        from tests.devices.pc.test_source_models import valid_smb_source
        valid_smb_source()
        profile = ComputerAnalysisProfile.objects.create(name='Scan', analysis_items=['activation'])
        self.task = enqueue_computer_fetch_task(profile, 'manual')
        claim_next_task('scan-owner', 60)
        self.target = self.task.target_runs.get()
        _begin_target(self.target.pk, 'scan-owner')
        log = create_log_file(source_path='fixture.json', modified_at=timezone.now(), content_hash='a'*64, import_status='imported')
        self.summary = FetchSummary(imported=1, log_files=[log])

    def test_lease_loss_immediately_before_child_enqueue_creates_no_orphan(self):
        original = scan_executor._enqueue_scanned_analyses
        def expire_then_enqueue(task, ids):
            TaskRun.objects.filter(pk=task.pk).update(lease_expires_at=timezone.now()-timedelta(seconds=1))
            return original(task, ids)
        with patch.object(scan_executor, '_enqueue_scanned_analyses', side_effect=expire_then_enqueue):
            result = scan_executor._persist_scan(self.target.pk, 'scan-owner', self.summary)
        self.assertTrue(result.stale)
        self.assertFalse(TaskRun.objects.filter(task_type='computer_analysis').exists())
        self.target.refresh_from_db()
        self.assertEqual(self.target.result_id, '')

    def test_parent_link_write_failure_rolls_back_child(self):
        with patch.object(scan_executor, 'save_target', side_effect=RuntimeError('write failed')):
            with self.assertRaises(RuntimeError):
                scan_executor._persist_scan(self.target.pk, 'scan-owner', self.summary)
        self.assertFalse(TaskRun.objects.filter(task_type='computer_analysis').exists())
