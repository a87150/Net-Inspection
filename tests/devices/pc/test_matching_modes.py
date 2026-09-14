from tests import response_body

from types import SimpleNamespace
from django.test import TestCase
from net.models import People, ComputerAnalysisProfile


class MatchingModeTests(TestCase):
    def test_personnel_mode_retains_person_without_log_and_excludes_orphan(self):
        from net.devices.pc.matching import join_analysis_rows
        roster = [{'id': '1', 'employee_id': 'A1', 'name': 'One', 'department': 'IT'},
                  {'id': '2', 'employee_id': 'A2', 'name': 'Two', 'department': 'IT'}]
        matched = SimpleNamespace(details={'enrichment': {'personnel_id': '1'}})
        orphan = SimpleNamespace(details={'enrichment': {'personnel_id': ''}})
        rows = join_analysis_rows([matched, orphan], roster, 'people')
        self.assertEqual(len(rows), 2)
        self.assertIs(rows[0], matched)
        self.assertTrue(rows[1].missing_log)
        self.assertEqual(rows[1].details['enrichment']['employee_number'], 'A2')
        self.assertEqual(rows[1].result_level, 'warning')
        self.assertEqual(join_analysis_rows([matched, orphan], roster, 'logs'), [matched, orphan])

    def test_employee_number_wins_and_duplicate_names_are_not_guessed(self):
        from net.devices.pc.matching import match_person
        roster = [{'id': '1', 'employee_id': 'A1', 'name': 'Same'},
                  {'id': '2', 'employee_id': 'A2', 'name': 'Same'}]
        person, state = match_person({'当前登录用户工号': 'DOMAIN\\a1', '当前登录用户名': 'Same'}, roster)
        self.assertEqual(person['id'], '1')
        self.assertEqual(state, 'matched')
        person, state = match_person({'当前登录用户名': 'Same'}, roster)
        self.assertIsNone(person)
        self.assertEqual(state, 'ambiguous')

    def test_mode_is_saved_and_frozen_in_task_profile(self):
        from net.inspections.queue import _task_context_for_profile
        profile = ComputerAnalysisProfile.objects.create(name='test', matching_mode='people', analysis_items=['processes'])
        snapshot = _task_context_for_profile(profile)[3]
        self.assertEqual(snapshot['matching_mode'], 'people')

    def test_configuration_form_saves_and_reopens_mode(self):
        from index.inspections.forms import ComputerAnalysisProfileConfigForm
        form = ComputerAnalysisProfileConfigForm(data={'name': 'mode configuration',
            'matching_mode': 'people', 'analysis_items': ['processes'], 'concurrent_workers': 2})
        self.assertTrue(form.is_valid(), form.errors)
        profile = ComputerAnalysisProfile.objects.create(**form.profile_values())
        reopened = ComputerAnalysisProfileConfigForm(instance=profile)
        self.assertEqual(reopened['matching_mode'].value(), 'people')

    def test_left_join_page_and_export_keep_missing_person_and_filter(self):
        from tests.auth import login_admin
        from django.urls import reverse
        from net.inspections.queue import enqueue_task, claim_next_task, finish_task
        from net.devices.pc.executor import execute_computer_target
        from tests.devices.pc.helpers import import_payload
        People.objects.create(employee_id='M1', name='Matched', department='IT')
        People.objects.create(employee_id='M2', name='Missing', department='IT')
        profile = ComputerAnalysisProfile.objects.create(name='joined', matching_mode='people', analysis_items=['processes'])
        log = import_payload({'日志时间': '2026-09-07 12:00:00', '系统信息概览': {
            '计算机名': 'PC1', '当前登录用户工号': 'M1'}, '当前运行进程清单': ['test']}).log_file
        task = enqueue_task(profile, [log.pk], 'manual')
        claim_next_task('join-worker', 60)
        execute_computer_target(task.target_runs.get(), worker_id='join-worker')
        finish_task(task.pk, 'join-worker')
        login_admin(self.client, username='join-user')
        response = self.client.get(reverse('computer_analysis_list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['latest_analysis_statistics']['total'], 2)
        self.assertContains(response, 'name="matching_mode"')
        response = self.client.get(reverse('task_detail', args=[task.pk]))
        self.assertEqual(response.context['page_obj'].paginator.count, 2)
        self.assertContains(response, '未匹配日志')
        exported = self.client.get(reverse('table_export', args=['computer_inspections']))
        self.assertEqual(exported.status_code, 200)
        self.assertIn('M2', response_body(exported).decode('utf-8-sig'))
        from index.common.table_query import apply_table_filters
        from index.common.table_registry import get_table_definition
        from index.inspections.records import _computer_analysis_records
        from django.test import RequestFactory
        filtered, _ = apply_table_filters(RequestFactory().get('/', {'filter_status': 'warning'}),
                        _computer_analysis_records(), get_table_definition('computer_inspections'), include_legacy_status=False)
        self.assertEqual([row.result_level for row in filtered], ['warning'])
