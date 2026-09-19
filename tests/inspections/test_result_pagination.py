from tests.devices.pc.helpers import create_log_file
import csv
from io import StringIO

from django.core.paginator import Paginator
from django.db import connection
from django.db.models import QuerySet
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from tests.auth import login_reader
from django.utils import timezone

from index.common.table_query import apply_table_filters
from index.common.table_registry import get_table_definition, project_record_definition
from index.inspections.records import _computer_analysis_records, _error_records, _infrastructure_records
from net.models import (Computer, ComputerAnalysis, ComputerLogFile, Error_Computer,
                        Network_Device, Network_Device_Inspection, Server,
                        Server_Inspection)


class ResultPaginationTests(TestCase):
    def setUp(self):
        login_reader(self.client)
        self.factory = RequestFactory()

    def test_global_problem_records_include_successful_business_warning(self):
        """A successful collection with a warning is still an actionable problem."""
        server = Server.objects.create(name='DB-01', ip='192.0.2.40', server_type='linux')
        Server_Inspection.objects.create(
            server=server, status='success', is_reachable=True,
            summary='CPU 使用率过高',
            details={'issue_findings': [
                {'analysis_item': 'cpu', 'project': 'servers', 'severity': 'warning'},
            ]},
        )

        response = self.client.get(reverse('error_records'))

        records = response.context['page_obj'].object_list
        self.assertEqual([(record['category'], record['type'], record['message']) for record in records], [
            ('服务器', '业务告警', 'CPU 使用率过高'),
        ])

    def test_global_records_page_uses_union_limit_without_reading_all_rows(self):
        computer = Computer.objects.create(computer_name='PC-PAGE')
        log = create_log_file(source_path='page.json', modified_at=timezone.now(),
            content_hash='p' * 64, import_status='success')
        ComputerAnalysis.objects.bulk_create([
            ComputerAnalysis(computer=computer, log_file=log, summary=f'row {index}')
            for index in range(25)
        ])

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('inspection_records'), {'page_size': '20'})
            self.assertEqual(len(response.context['page_obj'].object_list), 20)

        listing = [query['sql'] for query in queries if 'UNION ALL' in query['sql'] and 'LIMIT 20' in query['sql']]
        self.assertEqual(len(listing), 1)

    def test_global_problem_records_label_failed_collection_before_business_severity(self):
        server = Server.objects.create(name='DB-PARTIAL', ip='192.0.2.41', server_type='linux')
        Server_Inspection.objects.create(server=server, status='partial', is_reachable=True,
            details={'issue_findings': [{'analysis_item': 'cpu', 'project': 'servers', 'severity': 'critical'}]})

        rows, _ = apply_table_filters(self.factory.get('/'), _error_records(),
            get_table_definition('error_records'))

        self.assertEqual(rows[0]['type'], '采集异常')

    def test_global_error_rows_keep_duplicate_pc_errors_and_dynamic_categories(self):
        computer = Computer.objects.create(computer_name='PC-DUP')
        log = create_log_file(source_path='dup.json', modified_at=timezone.now(),
            content_hash='d' * 64, import_status='success')
        analysis = ComputerAnalysis.objects.create(computer=computer, log_file=log)
        Error_Computer.objects.bulk_create([
            Error_Computer(inspection=analysis, error_type='disk', error_message='full'),
            Error_Computer(inspection=analysis, error_type='disk', error_message='full'),
        ])

        rows, state = apply_table_filters(self.factory.get('/', {'filter_category': 'PC'}),
            _error_records(), get_table_definition('error_records'))

        self.assertEqual([(row['category'], row['type'], row['message']) for row in rows], [
            ('PC', 'disk', 'full'), ('PC', 'disk', 'full'),
        ])
        self.assertIn(('PC', 'PC'), state['field_options']['category'])

    def test_network_filters_and_pages_in_sql_without_payloads(self):
        device = Network_Device.objects.create(device_name='Edge', ip='192.0.2.10')
        Network_Device_Inspection.objects.bulk_create([
            Network_Device_Inspection(device=device, summary=f'row {i:03}',
                                      details={'cpu': {'usage_percent': i}, 'large': 'x' * 10000})
            for i in range(105)
        ])
        with CaptureQueriesContext(connection) as queries:
            source = _infrastructure_records('networks', latest_only=False)
            self.assertIsInstance(source, QuerySet)
            rows, state = apply_table_filters(self.factory.get('/', {'page': 2}), source,
                                              project_record_definition('networks'))
            page = Paginator(rows, 20).page(2)
            result = list(page)
        self.assertEqual(page.paginator.count, 105)
        self.assertEqual(len(result), 20)
        self.assertEqual(len(state['field_options']['summary']), 105)
        selects = [q['sql'] for q in queries if 'LIMIT 20 OFFSET 20' in q['sql']]
        self.assertEqual(len(selects), 1)
        self.assertNotIn('"details"', selects[0])
        self.assertNotIn('"raw_output"', selects[0])
        self.assertTrue(result[0]['url'].endswith(f"/{result[0]['pk']}/"))

    def test_pc_query_defers_payload_and_keeps_error_severity_and_enrichment(self):
        computer = Computer.objects.create(computer_name='PC-1', user_name='alice')
        log = create_log_file(source_path='a.json', modified_at=timezone.now(),
                                            content_hash='a' * 64, import_status='success',
                                            payload={'large': 'x' * 10000})
        record = ComputerAnalysis.objects.create(computer=computer, log_file=log,
            details={'enrichment': {'department': 'Research', 'employee_number': '001'},
                     'resource': {'当前CPU占用率': '12%'}, 'large': 'x' * 10000})
        Error_Computer.objects.create(inspection=record, error_type='disk', error_message='full')
        with CaptureQueriesContext(connection) as queries:
            source = _computer_analysis_records()
            self.assertIsInstance(source, QuerySet)
            rows, state = apply_table_filters(self.factory.get('/', {
                'filter_department': 'sear', 'filter_status': 'warning', 'sort': 'employee_number',
            }), source, project_record_definition('computers'))
            page = Paginator(rows, 20).page(1)
            result = list(page)
            self.assertEqual([r.pk for r in result], [record.pk])
            self.assertEqual(result[0].error_count, 1)
            self.assertEqual(result[0].key_metrics, 'CPU 12%')
            self.assertEqual(result[0].result_level, 'warning')
            self.assertEqual(result[0].task_source, '未关联任务')
        self.assertIn(('Research', 'Research'), state['field_options']['department'])
        listing = next(q['sql'] for q in queries if 'LIMIT 1' in q['sql'] and 'net_computeranalysis' in q['sql'])
        self.assertNotIn('"payload"', listing)
        self.assertNotIn('"details"', listing)
        self.assertNotIn('"exceptions"', listing)

    def test_filtered_csv_exports_every_page(self):
        device = Network_Device.objects.create(device_name='Export', ip='192.0.2.11')
        Network_Device_Inspection.objects.bulk_create([
            Network_Device_Inspection(device=device, summary=f'include {i:02}') for i in range(25)
        ] + [Network_Device_Inspection(device=device, summary='excluded')])
        response = self.client.get(reverse('table_export_scoped', args=['inspection_records', 'networks']),
                                   {'filter_summary': 'include', 'page': 2, 'page_size': 20})
        rows = list(csv.DictReader(StringIO(b''.join(response.streaming_content).decode('utf-8-sig'))))
        self.assertEqual(len(rows), 25)
        self.assertTrue(all('include' in row['摘要'] for row in rows))

    def test_legacy_category_and_search_across_display_columns_are_preserved(self):
        device = Network_Device.objects.create(device_name='Edge', ip='192.0.2.12')
        record = Network_Device_Inspection.objects.create(device=device, summary='hello')
        source = _infrastructure_records('networks', latest_only=False)
        definition = project_record_definition('networks')
        rows, _ = apply_table_filters(self.factory.get('/', {'category': '服务器'}), source, definition)
        self.assertEqual(list(rows), [])
        rows, _ = apply_table_filters(self.factory.get('/', {'q': '(192.0.2.12) hello'}), source, definition)
        self.assertEqual([row['pk'] for row in rows], [record.pk])

    def test_pc_nullable_sort_keeps_missing_values_last_when_ascending(self):
        computer = Computer.objects.create(computer_name='PC-SORT')
        log = create_log_file(source_path='sort.json', modified_at=timezone.now(),
                                            content_hash='b' * 64, import_status='success')
        missing = ComputerAnalysis.objects.create(computer=computer, log_file=log)
        present = ComputerAnalysis.objects.create(computer=computer, log_file=log,
                    details={'enrichment': {'department': 'Research'}})
        rows, _ = apply_table_filters(self.factory.get('/', {'sort': 'department', 'order': 'asc'}),
                                      _computer_analysis_records(), project_record_definition('computers'))
        self.assertEqual([row.pk for row in rows], [present.pk, missing.pk])

    def test_rendering_twenty_records_has_no_per_row_relation_queries(self):
        from net.models import InspectionProfile, TaskRun, TaskTargetRun
        profile = InspectionProfile.objects.create(name='pagination', device_type='network_device')
        task = TaskRun.objects.create(task_type='inspection', source='scheduled', inspection_profile=profile)
        device = Network_Device.objects.create(device_name='Edge', ip='192.0.2.13')
        for index in range(21):
            target = TaskTargetRun.objects.create(task=task, target_type='network_device', target_id=str(index),
                                                 target_snapshot={'large': 'x' * 10000})
            Network_Device_Inspection.objects.create(device=device, task_target=target, status='partial')
        with self.assertNumQueries(1):
            rows = list(_infrastructure_records('networks', task=task)[:20])
            self.assertEqual([row['task_source'] for row in rows], ['定时执行'] * 20)
            self.assertEqual([row['error_count'] for row in rows], [1] * 20)
            self.assertEqual([row['result_level'] for row in rows], ['critical'] * 20)

    def test_report_projection_tracks_partial_save_and_bulk_creation(self):
        device = Network_Device.objects.create(device_name='Edge', ip='192.0.2.14')
        record = Network_Device_Inspection.objects.create(device=device)
        record.details = {'cpu': {'usage_percent': 98}, 'issue_findings': [
            {'analysis_item': 'cpu', 'project': 'networks', 'severity': 'warning'},
        ]}
        record.save(update_fields=['details'])
        row = _infrastructure_records('networks', latest_only=False).get(pk=record.pk)
        self.assertEqual(row['result_level'], 'warning')
        self.assertFalse(row['ok'])
        self.assertEqual(row['key_metrics'], 'CPU 98%')

    def test_migration_backfill_is_frozen_and_preserves_error_only_results(self):
        from importlib import import_module
        from unittest.mock import patch
        from django.apps import apps
        from types import SimpleNamespace
        computer = Computer.objects.create(computer_name='PC-OLD')
        log = create_log_file(source_path='old.json', modified_at=timezone.now(),
                                            content_hash='c' * 64, import_status='success')
        record = ComputerAnalysis.objects.create(computer=computer, log_file=log,
            details={'resource': {'当前CPU占用率': '8%'}})
        Error_Computer.objects.create(inspection=record, error_type='old', error_message='old error')
        ComputerAnalysis.objects.filter(pk=record.pk).update(report_metrics='')
        migration = import_module('net.migrations.0036_record_report_fields')
        with patch('net.models.records.record_report_values', side_effect=AssertionError('live helper called')):
            migration.backfill_reports(apps, SimpleNamespace(connection=connection))
        row = _computer_analysis_records().get(pk=record.pk)
        self.assertEqual(row.result_level, 'warning')
        self.assertEqual(row.error_count, 1)
        self.assertEqual(row.key_metrics, 'CPU 8%')

    def test_all_infrastructure_filters_and_sorts_are_sql_fields(self):
        device = Network_Device.objects.create(device_name='Filter', ip='192.0.2.15')
        record = Network_Device_Inspection.objects.create(device=device, summary='needle', details={
            'issue_findings': [{'analysis_item': 'cpu', 'project': 'networks', 'severity': 'warning'}],
        })
        definition = project_record_definition('networks')
        params = {'filter_problem_types': 'CPU', 'filter_result_level': 'warning',
                  'filter_category': '网络设备', 'filter_asset': 'Filter', 'filter_status': 'abnormal',
                  'filter_execution_status': '成功', 'filter_task_source': '未关联任务',
                  'filter_summary': 'needle', 'filter_time_from': timezone.localdate().isoformat(),
                  'filter_time_to': timezone.localdate().isoformat()}
        rows, state = apply_table_filters(self.factory.get('/', params),
            _infrastructure_records('networks', latest_only=False), definition)
        self.assertEqual([row['pk'] for row in rows], [record.pk])
        self.assertEqual(len(state['filters']), 9)
        for field in definition.fields:
            if field.sortable:
                for order in ('asc', 'desc'):
                    with self.subTest(sort=field.key, order=order):
                        rows, _ = apply_table_filters(self.factory.get('/', {'sort': field.key, 'order': order}),
                            _infrastructure_records('networks', latest_only=False), definition)
                        self.assertEqual([row['pk'] for row in rows], [record.pk])

    def test_all_pc_filters_and_sorts_are_sql_fields(self):
        computer = Computer.objects.create(computer_name='PC-FILTER', user_name='alice')
        log = create_log_file(source_path='filter.json', modified_at=timezone.now(),
                                            content_hash='d' * 64, import_status='success')
        enrichment = {'employee_number': '001', 'personnel_name': 'Alice', 'department': 'IT',
                      'user_ou': 'OU=Users', 'computer_ou': 'OU=PCs', 'site': 'North'}
        record = ComputerAnalysis.objects.create(computer=computer, log_file=log,
            details={'enrichment': enrichment}, exceptions=[{'analysis_item': 'system', 'severity': 'info'}])
        definition = project_record_definition('computers')
        params = {f'filter_{key}': value for key, value in enrichment.items()}
        params.update(filter_computer_name='PC-FILTER', filter_user_name='alice', filter_status='info',
                      filter_problem_types='操作系统', filter_log_time_from=timezone.localdate().isoformat(),
                      filter_created_at_to=timezone.localdate().isoformat())
        rows, state = apply_table_filters(self.factory.get('/', params), _computer_analysis_records(), definition)
        self.assertEqual([row.pk for row in rows], [record.pk])
        self.assertEqual(len(state['filters']), 12)
        for field in definition.fields:
            if field.sortable:
                for order in ('asc', 'desc'):
                    with self.subTest(sort=field.key, order=order):
                        rows, _ = apply_table_filters(self.factory.get('/', {'sort': field.key, 'order': order}),
                                                       _computer_analysis_records(), definition)
                        self.assertEqual([row.pk for row in rows], [record.pk])

    def test_equal_sort_keys_use_primary_key_and_pages_do_not_overlap(self):
        device = Network_Device.objects.create(device_name='Stable', ip='192.0.2.16')
        records = Network_Device_Inspection.objects.bulk_create([
            Network_Device_Inspection(device=device) for _ in range(25)
        ])
        rows, _ = apply_table_filters(self.factory.get('/', {'sort': 'asset', 'order': 'desc'}),
            _infrastructure_records('networks', latest_only=False), project_record_definition('networks'))
        paginator = Paginator(rows, 20)
        identifiers = [row['pk'] for row in paginator.page(1)] + [row['pk'] for row in paginator.page(2)]
        self.assertEqual(identifiers, sorted(record.pk for record in records))

    def test_enrichment_json_null_empty_and_chinese_have_text_sort_and_clean_options(self):
        computer = Computer.objects.create(computer_name='PC-JSON')
        log = create_log_file(source_path='json.json', modified_at=timezone.now(),
                                            content_hash='e' * 64, import_status='success')
        records = [ComputerAnalysis.objects.create(computer=computer, log_file=log,
                   details={'enrichment': enrichment}) for enrichment in (
                       {}, {'department': None}, {'department': ''},
                       {'department': '研发'}, {'department': '运维'},
                   )]
        null_ids = sorted(record.pk for record in records[:2])
        for order, expected in (
            ('asc', [records[2].pk, records[3].pk, records[4].pk] + null_ids),
            ('desc', null_ids + [records[4].pk, records[3].pk, records[2].pk]),
        ):
            rows, state = apply_table_filters(self.factory.get('/', {'sort': 'department', 'order': order}),
                _computer_analysis_records(), project_record_definition('computers'))
            self.assertEqual([row.pk for row in rows], expected)
            self.assertEqual(state['field_options']['department'], (('研发', '研发'), ('运维', '运维')))

    def test_literal_null_enrichment_text_is_not_json_null(self):
        computer = Computer.objects.create(computer_name='PC-LITERAL')
        log = create_log_file(source_path='literal.json', modified_at=timezone.now(),
                                            content_hash='f' * 64, import_status='success')
        real = ComputerAnalysis.objects.create(computer=computer, log_file=log,
                                              details={'enrichment': {'department': 'null'}})
        ComputerAnalysis.objects.create(computer=computer, log_file=log,
                                       details={'enrichment': {'department': None}})
        rows, state = apply_table_filters(self.factory.get('/', {'filter_department': 'null'}),
            _computer_analysis_records(), project_record_definition('computers'))
        self.assertEqual([row.pk for row in rows], [real.pk])
        self.assertEqual(state['field_options']['department'], (('null', 'null'),))
