from tests.devices.pc.helpers import create_log_file
import csv

from tests import response_body
from io import StringIO

from django.core.paginator import Paginator
from django.db import connection
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from tests.auth import login_reader
from django.utils import timezone

from index.common.table_query import apply_table_filters
from index.common.table_registry import project_record_definition
from index.inspections.records import _computer_analysis_records
from net.models import Computer, ComputerAnalysis, ComputerAnalysisProfile, ComputerLogFile, TaskRun, TaskTargetRun


class PeopleResultPaginationTests(TestCase):
    def setUp(self):
        login_reader(self.client)
        profile = ComputerAnalysisProfile.objects.create(name='people pages', matching_mode='people')
        self.roster = [dict(id=str(i), employee_id=f'E{i:03}', name=f'Person {i:03}', department='IT')
                       for i in range(1, 24)]
        self.task = TaskRun.objects.create(task_type='computer_analysis', source='manual', analysis_profile=profile,
            profile_snapshot={'matching_mode': 'people'}, parameters_snapshot={'personnel_roster': self.roster})
        self.factory = RequestFactory()
        self.records = []
        # One person owns multiple logs, one owns a single log, 21 have no log.
        for i in range(45):
            person = self.roster[0 if i < 44 else 22]
            computer = Computer.objects.create(computer_name=f'PC-{i:03}', user_name='alice')
            log = create_log_file(source_path=f'{i}.json', content_hash=f'{i:064x}',
                modified_at=timezone.now(), import_status='success', payload={'large': 'x' * 10000})
            target = TaskTargetRun.objects.create(task=self.task, target_type='computer_log', target_id=str(log.pk))
            self.records.append(ComputerAnalysis.objects.create(computer=computer, log_file=log, task_target=target,
                details={'large': 'x' * 10000, 'enrichment': {'personnel_id': person['id'],
                    'employee_number': person['employee_id'], 'personnel_name': person['name'], 'department': 'IT'}}))

    def rows(self, **params):
        return apply_table_filters(self.factory.get('/', params), _computer_analysis_records(task=self.task),
                                   project_record_definition('computers'))

    def test_second_page_only_fetches_the_analysis_slice(self):
        with CaptureQueriesContext(connection) as queries:
            source = _computer_analysis_records(task=self.task)
            self.assertFalse(isinstance(source, list), 'people source must not materialize analyses')
            rows, state = apply_table_filters(self.factory.get('/', {'sort': 'employee_number', 'order': 'asc'}),
                                               source, project_record_definition('computers'))
            page = Paginator(rows, 20).page(2)
            actual = list(page)
        self.assertEqual(page.paginator.count, 66)
        self.assertEqual([r.pk for r in actual], sorted(r.pk for r in self.records[:44])[20:40])
        row_queries = [q['sql'] for q in queries if '"log_file_id"' in q['sql']]
        self.assertEqual(len(row_queries), 1)
        self.assertIn('LIMIT 20 OFFSET 20', row_queries[0])
        for payload in ('"details"', '"exceptions"', '"payload"'):
            self.assertNotIn(payload, row_queries[0])
        self.assertEqual(len(state['field_options']['employee_number']), 23)

    def test_page_boundary_merges_placeholders_and_multiple_logs(self):
        rows, _ = self.rows(sort='employee_number', order='asc')
        page = list(Paginator(rows, 20).page(3))
        self.assertEqual([r.pk for r in page[:4]], sorted(r.pk for r in self.records[:44])[40:44])
        self.assertEqual([r.report_enrichment['employee_number'] for r in page[4:]],
                         [f'E{i:03}' for i in range(2, 18)])
        last = list(Paginator(rows, 20).page(4))
        self.assertEqual([r.report_enrichment['employee_number'] for r in last],
                         ['E018', 'E019', 'E020', 'E021', 'E022', 'E023'])
        self.assertEqual(last[-1].pk, self.records[-1].pk)

    def test_filter_does_not_fabricate_placeholder_when_real_log_is_filtered_out(self):
        rows, state = self.rows(filter_status='warning', sort='employee_number', order='asc')
        result = list(rows)
        self.assertEqual(len(result), 21)
        self.assertTrue(all(row.missing_log for row in result))
        self.assertEqual(result[0].report_enrichment['employee_number'], 'E002')
        self.assertIn(('E001', 'E001'), state['field_options']['employee_number'])
        rows, _ = self.rows(filter_employee_number='E00', filter_status='warning', sort='employee_number', order='desc')
        self.assertEqual([r.report_enrichment['employee_number'] for r in rows],
                         ['E009', 'E008', 'E007', 'E006', 'E005', 'E004', 'E003', 'E002'])

    def test_null_sorts_and_equal_values_have_stable_cross_page_order(self):
        for order in ('asc', 'desc'):
            rows, _ = self.rows(sort='computer_name', order=order)
            page = list(Paginator(rows, 20).page(2))
            if order == 'asc':
                self.assertEqual([r.pk for r in page], [r.pk for r in self.records[20:40]])
            else:
                self.assertTrue(page[0].missing_log)
                self.assertEqual([r.pk for r in page[1:]], [r.pk for r in self.records[::-1][:19]])
        rows, _ = self.rows(sort='department', order='asc')
        result = list(rows)
        self.assertEqual([r.pk for r in result[:45]], sorted(r.pk for r in self.records))
        self.assertEqual([r.report_enrichment['employee_number'] for r in result[45:]],
                         [f'E{i:03}' for i in range(2, 23)])

    def test_csv_exports_all_merged_pages(self):
        response = self.client.get(reverse('table_export', args=['computer_inspections']),
            {'task': str(self.task.pk), 'sort': 'employee_number', 'order': 'asc', 'page': 2, 'page_size': 20})
        result = list(csv.DictReader(StringIO(response_body(response).decode('utf-8-sig'))))
        self.assertEqual(len(result), 66)
        self.assertEqual([r['工号'] for r in result], ['E001'] * 44 + [f'E{i:03}' for i in range(2, 24)])

    def test_orphans_empty_filters_and_missing_json_sort_values(self):
        orphan = self.records[-1]
        orphan.details = {'enrichment': {'personnel_id': 'not-in-roster', 'employee_number': 'ORPHAN'}}
        orphan.save(update_fields=['details'])
        rows, state = self.rows(sort='employee_number', order='asc')
        self.assertEqual(rows.count(), 66)
        self.assertNotIn(('ORPHAN', 'ORPHAN'), state['field_options']['employee_number'])
        self.assertTrue(list(rows)[-1].missing_log)
        rows, state = self.rows(filter_employee_number='does not exist')
        self.assertEqual(rows.count(), 0)
        self.assertEqual(rows[:20], [])
        self.assertIn(('does not exist', 'does not exist（当前筛选）'), state['field_options']['employee_number'])
        # Real analyses with missing department and placeholder department None
        # are NULL ties; ascending real ties stay before placeholder ties.
        record = self.records[0]
        del record.details['enrichment']['department']
        record.save(update_fields=['details'])
        self.roster[1]['department'] = None
        self.task.parameters_snapshot = {'personnel_roster': self.roster}
        rows, _ = self.rows(sort='department', order='asc')
        result = list(rows)
        self.assertEqual(result[-2].pk, record.pk)
        self.assertEqual(result[-1].report_enrichment['employee_number'], 'E002')

    def test_options_keep_literal_current_filter_suffix_and_every_placeholder(self):
        self.roster[1]['department'] = 'Literal（当前筛选）'
        self.task.parameters_snapshot = {'personnel_roster': self.roster}
        _, state = self.rows(filter_status='normal')
        self.assertIn(('Literal（当前筛选）', 'Literal（当前筛选）'), state['field_options']['department'])

    def test_every_sort_and_direction_pages_like_full_merged_iteration(self):
        definition = project_record_definition('computers')
        for field in definition.fields:
            if not field.sortable:
                continue
            for order in ('asc', 'desc'):
                with self.subTest(field=field.key, order=order):
                    rows, _ = self.rows(sort=field.key, order=order)
                    identify = lambda r: str(r.pk) if r.pk else r.report_enrichment['employee_number']
                    whole = [identify(r) for r in rows]
                    pages = Paginator(rows, 20)
                    paged = [identify(row) for page in pages.page_range for row in pages.page(page)]
                    self.assertEqual(len(whole), 66)
                    self.assertEqual(len(set(whole)), 66)
                    self.assertEqual(paged, whole)

    def test_explicit_json_null_real_and_placeholder_sort_together(self):
        record = self.records[0]
        record.details['enrichment']['department'] = None
        record.save(update_fields=['details'])
        self.roster[1]['department'] = None
        self.task.parameters_snapshot = {'personnel_roster': self.roster}
        for order in ('asc', 'desc'):
            rows, state = self.rows(sort='department', order=order)
            result = list(rows)
            null_rows = result[-2:] if order == 'asc' else result[:2]
            self.assertEqual(null_rows[0].pk, record.pk)
            self.assertEqual(null_rows[1].report_enrichment['employee_number'], 'E002')
            self.assertEqual(state['field_options']['department'], (('IT', 'IT'),))


class PeopleQueryBackendCompilationTests(SimpleTestCase):
    def test_mysql_and_mariadb_compile_json_null_and_rank_without_connecting(self):
        from unittest.mock import patch
        from django.db.backends.mysql.base import DatabaseWrapper
        from django.db.models import Count, F, Q
        from index.inspections.result_query import computer_queryset
        for version in ('8.0.36', '10.11.6-MariaDB'):
            with self.subTest(version=version):
                backend = DatabaseWrapper({
                    'NAME': 'compile_only', 'ENGINE': 'django.db.backends.mysql',
                    'OPTIONS': {}, 'TIME_ZONE': None, 'USE_TZ': True,
                }, alias='compile_only')
                backend.mysql_server_data = {
                    'version': version, 'sql_mode': 'STRICT_TRANS_TABLES',
                    'default_storage_engine': 'InnoDB', 'sql_auto_is_null': False,
                    'lower_case_table_names': False, 'has_zoneinfo_database': True,
                }
                with patch.object(backend, 'ensure_connection', side_effect=AssertionError('No live connection allowed')):
                    query = computer_queryset().filter(_report_enrichment_department__icontains='研发').annotate(
                        rank=Count('pk', filter=Q(_report_enrichment_department__gte='研发')),
                    ).order_by(F('_report_enrichment_department').asc(nulls_last=True), 'pk').query
                    sql, params = query.get_compiler(connection=backend).as_sql()
                self.assertIn('JSON_TYPE(JSON_EXTRACT(', sql)
                self.assertIn('COUNT(CASE WHEN', sql)
                self.assertNotIn('研发', sql)
                self.assertIn('研发', params)
