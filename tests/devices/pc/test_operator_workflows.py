from tests import response_body

import json
from datetime import datetime, time, timezone as dt_timezone
from unittest.mock import patch
from django.test import TestCase, Client
from django.urls import reverse
from tests.auth import login_admin, login_reader
from django.utils import timezone
from tests.devices.pc.helpers import create_log_file, analysis_task_url

from net.models import (Computer, ComputerAnalysisProfile, ComputerLogFile, InspectionProfile,
                        People, Schedule, Server, Server_Inspection, TaskRun)
from net.inspections.schedules import enqueue_due_schedules


class FinalOperatorTests(TestCase):
    def setUp(self):
        login_admin(self.client)
        self.server = Server.objects.create(name='operator', ip='192.0.2.80', server_type='linux')
        self.profile = InspectionProfile.objects.create(name='operator', device_type='server', selected_items=['cpu'])
        self.now = datetime(2026, 9, 1, 0, 0, tzinfo=dt_timezone.utc)

    def save_profile(self, **changes):
        data = {'profile_id': str(self.profile.pk), 'device_type': 'server', 'name': 'operator',
                'selected_items': ['cpu'], 'timeout_seconds': 60, 'concurrent_workers': 2,
                'schedule_enabled': 'on', 'schedule_kind': 'daily', 'daily_time': '15:00'}
        data.update(changes)
        return self.client.post('/tasks/profiles/inspection/', data, follow=True)

    def test_timing_edit_recalculates_but_unrelated_save_preserves_boundary(self):
        schedule = Schedule.objects.create(inspection_profile=self.profile, kind='daily', daily_time=time(9),
            next_run_at=datetime(2026, 9, 1, 1, tzinfo=dt_timezone.utc))
        with patch('django.utils.timezone.now', return_value=self.now):
            self.save_profile()
        schedule.refresh_from_db()
        self.assertEqual(schedule.next_run_at, datetime(2026, 9, 1, 7, tzinfo=dt_timezone.utc))

        with patch('django.utils.timezone.now', return_value=self.now.replace(day=2)):
            self.save_profile(name='unrelated edit')
        schedule.refresh_from_db()
        self.assertEqual(schedule.next_run_at, datetime(2026, 9, 1, 7, tzinfo=dt_timezone.utc))

    def test_daily_earlier_interval_kind_switch_and_reenable(self):
        schedule = Schedule.objects.create(inspection_profile=self.profile, kind='daily', daily_time=time(9),
                                           next_run_at=self.now)
        cases = [({'daily_time': '07:00'}, self.now.replace(day=2, hour=23)-timezone.timedelta(days=1)),
                 ({'schedule_kind': 'interval', 'interval_value': 45, 'interval_unit': 'minutes'}, self.now.replace(minute=45)),
                 ({'schedule_kind': 'interval', 'interval_value': 2, 'interval_unit': 'hours'}, self.now.replace(hour=2))]
        for changes, expected in cases:
            with self.subTest(changes=changes), patch('django.utils.timezone.now', return_value=self.now):
                self.save_profile(**changes)
                schedule.refresh_from_db()
                self.assertEqual(schedule.next_run_at, expected)
        Schedule.objects.filter(pk=schedule.pk).update(is_enabled=False, next_run_at=self.now)
        with patch('django.utils.timezone.now', return_value=self.now):
            self.save_profile()
        schedule.refresh_from_db()
        self.assertEqual(schedule.next_run_at, self.now.replace(hour=7))

    def test_foreign_deleted_and_malformed_selected_ids_do_not_change_rule(self):
        from net.models import Network_Device
        foreign = Network_Device.objects.create(device_name='foreign', ip='192.0.2.91')
        for value in (str(foreign.pk), '00000000-0000-0000-0000-000000000000', 'not-an-id'):
            with self.subTest(value=value):
                self.save_profile(target_rule_mode='selected', target_rule_ids=[value])
                self.profile.refresh_from_db()
                self.assertEqual(self.profile.target_selector, {})
    def test_selected_rule_round_trips_and_scheduler_freezes_targets(self):
        other = Server.objects.create(name='excluded', ip='192.0.2.81', server_type='windows')
        self.save_profile(target_rule_mode='selected', target_rule_ids=[str(self.server.pk)])
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.target_selector, {'mode': 'selected', 'target_ids': [str(self.server.pk)]})
        schedule = Schedule.objects.get(inspection_profile=self.profile)
        tasks = enqueue_due_schedules(now=schedule.next_run_at)
        self.assertEqual(list(tasks[0].target_runs.values_list('target_id', flat=True)), [str(self.server.pk)])
        self.save_profile(target_rule_mode='selected', target_rule_ids=[str(other.pk)])
        self.assertEqual(list(tasks[0].target_runs.values_list('target_id', flat=True)), [str(self.server.pk)])
        page = self.client.get(reverse('asset_list', args=['servers']), {'task_profile': str(self.profile.pk)})
        self.assertContains(page, 'target_rule_mode')
        self.assertContains(page, f'name="target_rule_ids" value="{other.pk}" checked')

    def test_filtered_rules_use_public_fields_only(self):
        self.save_profile(target_rule_mode='filtered', rule_server_type='linux')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.target_selector, {'mode': 'filtered', 'filters': {'server_type': 'linux'}})
        from django.core.exceptions import ValidationError
        from net.inspections.schedules import _selected_target_ids
        self.profile.target_selector = {'mode': 'filtered', 'filters': {'password': 'guess'}}
        with self.assertRaises(ValidationError):
            _selected_target_ids(self.profile)

    def test_optional_filter_choice_does_not_silently_select_model_default(self):
        from index.inspections.forms import InspectionProfileConfigForm
        form = InspectionProfileConfigForm(device_type='server', instance=self.profile)
        self.assertEqual(list(form.fields['rule_server_type'].choices)[0][0], '')
        self.assertIn('value="" selected', str(form['rule_server_type']))

    def test_local_midnight_record_filter_and_csv_agree(self):
        login_reader(self.client)
        record = Server_Inspection.objects.create(server=self.server, summary='local-next-day')
        Server_Inspection.objects.filter(pk=record.pk).update(created_at=datetime(2026, 8, 31, 17, tzinfo=dt_timezone.utc))
        query = {'filter_time_from': '2026-09-01', 'filter_time_to': '2026-09-01'}
        page = self.client.get('/records/servers/', query)
        self.assertEqual([row['summary'] for row in page.context['page_obj']], ['local-next-day'])
        csv = self.client.get('/tables/inspection_records/servers/export/', query)
        self.assertIn('local-next-day', response_body(csv).decode())

    def test_provenance_latest_and_record_metrics(self):
        login_reader(self.client)
        person = People.objects.create(employee_id='CSV', source='csv', platform_user_id='safe-platform')
        self.assertContains(self.client.get(reverse('person_detail', args=[person.pk])), 'safe-platform')
        old = Server_Inspection.objects.create(server=self.server, status='failed', summary='old failure')
        Server_Inspection.objects.filter(pk=old.pk).update(created_at=datetime(2025, 1, 1, tzinfo=dt_timezone.utc))
        record = Server_Inspection.objects.create(server=self.server, status='partial', summary='new partial',
            details={'cpu': {'usage_percent': 12}, 'memory': {'used_percent': 34}})
        page = self.client.get(reverse('asset_detail', args=['servers', self.server.pk]))
        self.assertContains(page, 'new partial')
        self.assertNotContains(page, 'old failure')
        listing = self.client.get('/records/servers/')
        self.assertContains(listing, '部分成功')
        self.assertContains(listing, 'CPU 12%')
        csv = self.client.get('/tables/inspection_records/servers/export/')
        self.assertIn('CPU 12%', response_body(csv).decode())

    def test_analysis_list_renders_shared_summary_columns(self):
        login_reader(self.client)
        from net.devices.pc.analysis import analyze_log
        Computer.objects.create(computer_name='METRICS')
        log = create_log_file(source_path='metrics.json', modified_at=timezone.now(),
            content_hash='e'*64, import_status='imported', payload={'系统信息概览': {'计算机名': 'METRICS'},
            '计算机硬件资源情况': {'当前CPU占用率': '23%', '当前内存使用率': '34%'}})
        analysis = analyze_log(log, ['resource', 'activation'])
        self.assertEqual(analysis.status, 'success')
        self.assertEqual(analysis.result_level, 'info')
        self.assertEqual(analysis.details['severity_counts'], {'info': 1, 'warning': 0, 'critical': 0})
        self.assertEqual(analysis.exceptions[0]['analysis_item'], 'activation')
        self.assertFalse(analysis.errors.exists())
        page = self.client.get(analysis_task_url(analysis))
        self.assertContains(page, 'CPU 23%')
        self.assertContains(page, '<td data-column-key="execution_status">成功</td>', html=True)
        self.assertContains(page, '<td data-column-key="status"><span class="badge status-badge text-bg-info" data-status="info">提示</span></td>', html=True)
