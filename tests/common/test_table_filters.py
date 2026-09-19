import csv

from tests import response_body
from html.parser import HTMLParser
from io import StringIO
from urllib.parse import parse_qs, urlsplit

from django.test import RequestFactory, TestCase
from django.urls import reverse
from tests.auth import login_reader
from tests.devices.pc.helpers import create_log_file
from django.utils import timezone

from index.common.table_options import build_field_option_context, build_field_options
from index.common.table_query import apply_table_filters
from index.common.table_registry import TABLE_DEFINITIONS, get_table_definition
from net.models import (
    Computer,
    ComputerAnalysis,
    ComputerAnalysisProfile,
    ComputerLogFile,
    Error_Computer,
    Network_Device,
    Network_Device_Inspection,
    People,
    RecordStatus,
    Server,
    TaskRun,
    TaskTargetRun,
)


class TableOptionGenerationTests(TestCase):
    def test_distinct_options_are_deduplicated_sorted_and_ignore_blanks(self):
        People.objects.bulk_create([
            People(name='甲', employee_id='P001', department='运维部'),
            People(name='乙', employee_id='P002', department='研发部'),
            People(name='丙', employee_id='P003', department='运维部'),
            People(name='丁', employee_id='P004', department=''),
            People(name='戊', employee_id='P005', department=None),
        ])

        options = build_field_options(
            People.objects.all(), get_table_definition('people'),
        )

        self.assertEqual(
            options['department'],
            (('研发部', '研发部'), ('运维部', '运维部')),
        )

    def test_distinct_options_fall_back_to_suggestions_above_limit(self):
        People.objects.bulk_create([
            People(
                name=f'人员{number:03d}',
                employee_id=f'P{number:03d}',
                department=f'部门{number:03d}',
            )
            for number in range(101)
        ])

        options, modes = build_field_option_context(
            People.objects.all(), get_table_definition('people'),
        )

        self.assertEqual(len(options['department']), 100)
        self.assertEqual(modes['department'], 'suggest')

    def test_fixed_options_use_registered_values_and_labels(self):
        options, modes = build_field_option_context(
            Server.objects.all(), get_table_definition('servers'),
        )

        self.assertEqual(
            options['server_type'],
            (('linux', 'Linux'), ('windows', 'Windows')),
        )
        self.assertEqual(modes['server_type'], 'fixed')

    def test_secret_fields_can_never_be_option_sources(self):
        secret_sources = {
            'password', 'api_password', 'api_token', 'bind_password',
        }

        for definition in TABLE_DEFINITIONS.values():
            with self.subTest(table=definition.key):
                self.assertFalse(
                    secret_sources & {field.source for field in definition.fields},
                )

    def test_option_metadata_uses_only_supported_modes_and_positive_limits(self):
        supported_modes = {'fixed', 'distinct', 'suggest', 'none'}

        for definition in TABLE_DEFINITIONS.values():
            for field in definition.fields:
                with self.subTest(table=definition.key, field=field.key):
                    self.assertIn(field.option_mode, supported_modes)
                    self.assertGreater(field.option_limit, 0)


class CompactFilterWorkspaceTests(TestCase):
    def setUp(self):
        login_reader(self.client)
        People.objects.bulk_create([
            People(
                name='张三', employee_id='P001', department='研发部',
                email='zhang@example.test', leader='李经理',
            ),
            People(
                name='李四', employee_id='P002', department='运维部',
                email='li@example.test', leader='王经理',
            ),
        ])

    def test_workspace_renders_editable_filters_with_stable_option_selects(self):
        response = self.client.get(reverse('asset_list', args=['people']))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="table-filter-bar"')
        self.assertContains(response, '<summary>更多筛选</summary>', html=True)
        self.assertContains(response, '<summary>自定义表格</summary>', html=True)
        self.assertContains(
            response,
            '<input class="form-control" id="people-name-filter-input" name="filter_name" value="" autocomplete="off">',
            html=True,
        )
        self.assertContains(
            response,
            'data-filter-suggestion-select data-filter-input="people-name-filter-input"',
        )
        self.assertContains(response, '<option value="张三">张三</option>', html=True)
        self.assertContains(response, '<option value="李四">李四</option>', html=True)
        self.assertNotContains(response, 'list="people-name-options"')
        self.assertContains(response, '<option value="研发部">研发部</option>', html=True)
        self.assertContains(response, '<option value="运维部">运维部</option>', html=True)
        self.assertEqual(
            response.context['table_state']['field_option_modes']['department'],
            'distinct',
        )

    def test_selected_text_option_does_not_hide_other_candidates(self):
        response = self.client.get(reverse('asset_list', args=['people']), {
            'filter_name': '张三',
        })

        self.assertContains(response, 'name="filter_name" value="张三"')
        self.assertContains(response, '<option value="张三">张三</option>', html=True)
        self.assertContains(response, '<option value="李四">李四</option>', html=True)

    def test_non_default_filter_still_uses_allowlisted_backend_filter(self):
        definition = get_table_definition('people')
        email = next(field for field in definition.fields if field.key == 'email')
        self.assertTrue(email.filterable)
        self.assertFalse(email.default_filter)

        response = self.client.get(reverse('asset_list', args=['people']), {
            'filter_email': 'li@example.test',
        })

        self.assertEqual(
            [person.employee_id for person in response.context['page_obj']],
            ['P002'],
        )

    def test_unregistered_filters_and_orm_traversal_sort_are_ignored(self):
        response = self.client.get(reverse('asset_list', args=['people']), {
            'filter_password': 'anything',
            'filter_department__contains': '研发',
            'sort': 'analyses__details',
            'order': 'desc',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['table_state']['sort'], 'name')
        self.assertEqual(
            [person.employee_id for person in response.context['page_obj']],
            ['P002', 'P001'],
        )

    def test_invalid_value_for_allowlisted_numeric_choice_does_not_crash(self):
        Server.objects.create(name='应用服务器', ip='192.0.2.30', port=22)

        response = self.client.get(reverse('asset_list', args=['servers']), {
            'filter_port': 'not-a-number',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertNotIn('port', response.context['table_state']['filters'])

    def test_target_scoped_history_builds_options_from_only_that_asset(self):
        first = Network_Device.objects.create(
            device_name='核心交换机', ip='192.0.2.10',
        )
        second = Network_Device.objects.create(
            device_name='接入交换机', ip='192.0.2.11',
        )
        Network_Device_Inspection.objects.create(
            device=first,
            status=RecordStatus.SUCCESS,
            summary='正常',
        )
        Network_Device_Inspection.objects.create(
            device=second,
            status=RecordStatus.SUCCESS,
            summary='正常',
        )

        response = self.client.get(reverse('record_list', args=['networks']), {
            'target': str(first.pk),
        })

        self.assertEqual(
            response.context['table_state']['field_options']['asset'],
            (('核心交换机 (192.0.2.10)', '核心交换机 (192.0.2.10)'),),
        )

    def test_shared_filter_interface_handles_querysets_and_record_lists(self):
        request = RequestFactory().get('/', {'filter_department': '运维部'})
        people, people_state = apply_table_filters(
            request, People.objects.all(), get_table_definition('people'),
        )
        records, record_state = apply_table_filters(
            RequestFactory().get('/', {'filter_category': '网络设备'}),
            [
                {
                    'category': '网络设备', 'asset': '交换机', 'time': None,
                    'ok': True, 'summary': '正常',
                },
                {
                    'category': '服务器', 'asset': '应用服务器', 'time': None,
                    'ok': True, 'summary': '正常',
                },
            ],
            get_table_definition('inspection_records'),
        )

        self.assertEqual([person.employee_id for person in people], ['P002'])
        self.assertEqual([record['category'] for record in records], ['网络设备'])
        self.assertEqual(people_state['filters']['department'], '运维部')
        self.assertEqual(record_state['filters']['category'], '网络设备')

    def test_direct_url_optional_filter_is_visible_and_enabled(self):
        response = self.client.get(reverse('asset_list', args=['people']), {
            'filter_email': 'missing@example.invalid',
        })
        parser = _WorkspaceMarkupParser()
        parser.feed(response.content.decode())

        self.assertNotIn('hidden', parser.filter_fields['email'])
        self.assertIn('data-filter-active', parser.filter_fields['email'])
        self.assertIn('checked', parser.filter_toggles['email'])
        self.assertIn('data-filter-active', parser.filter_toggles['email'])
        self.assertIn('open', parser.more_filters[0])

    def test_stale_distinct_value_stays_editable_and_available_as_a_candidate(self):
        response = self.client.get(reverse('asset_list', args=['people']), {
            'filter_department': '已撤销部门',
        })
        parser = _WorkspaceMarkupParser()
        parser.feed(response.content.decode())

        self.assertNotIn('hidden', parser.filter_fields['department'])
        self.assertContains(response, 'name="filter_department" value="已撤销部门"')
        self.assertContains(
            response,
            '<option value="已撤销部门">已撤销部门（当前筛选）</option>',
            html=True,
        )

    def test_infrastructure_reset_query_preserves_target_scope(self):
        device = Network_Device.objects.create(
            device_name='核心交换机', ip='192.0.2.40',
        )
        response = self.client.get(reverse('record_list', args=['networks']), {
            'target': str(device.pk),
            'filter_summary': '异常',
        })
        parser = _WorkspaceMarkupParser()
        parser.feed(response.content.decode())

        self.assertEqual(len(parser.reset_links), 1)
        reset = urlsplit(parser.reset_links[0])
        self.assertEqual(reset.path, reverse('record_list', args=['networks']))
        self.assertEqual(parse_qs(reset.query), {'target': [str(device.pk)]})

    def test_computer_analysis_statistics_have_no_record_filter_controls(self):
        computer = Computer.objects.create(computer_name='PC-RESET')
        response = self.client.get(reverse('computer_analysis_list'), {
            'target': str(computer.pk),
            'filter_status': 'abnormal',
        })
        parser = _WorkspaceMarkupParser()
        parser.feed(response.content.decode())

        self.assertEqual(len(parser.reset_links), 0)
        self.assertContains(response, '最新任务统计')

    def test_global_and_project_record_workspaces_have_distinct_preference_keys(self):
        cases = (
            (reverse('inspection_records'), 'inspection_records-global'),
            (
                reverse('record_list', args=['networks']),
                'inspection_records-networks',
            ),
            (
                reverse('record_list', args=['servers']),
                'inspection_records-servers',
            ),
            (
                reverse('record_list', args=['monitors']),
                'inspection_records-monitors',
            ),
        )
        observed = []

        for url, expected_key in cases:
            with self.subTest(url=url):
                parser = _WorkspaceMarkupParser()
                parser.feed(self.client.get(url).content.decode())
                self.assertEqual(parser.workspace_keys, [expected_key])
                observed.append(parser.workspace_keys[0])

        self.assertEqual(len(set(observed)), 4)


class _ExportLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'a' and 'data-filtered-export' in attributes:
            self.links.append(attributes.get('href', ''))


class _WorkspaceMarkupParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.filter_fields = {}
        self.filter_toggles = {}
        self.more_filters = []
        self.workspace_keys = []
        self.reset_links = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        filter_key = attributes.get('data-filter-key')
        if filter_key and 'data-filter-field' in attributes:
            self.filter_fields[filter_key] = attributes
        if filter_key and 'data-filter-toggle' in attributes:
            self.filter_toggles[filter_key] = attributes
        if tag == 'details' and 'data-more-filters' in attributes:
            self.more_filters.append(attributes)
        if 'data-table-key' in attributes:
            self.workspace_keys.append(attributes['data-table-key'])
        if tag == 'a' and 'data-query-reset' in attributes:
            self.reset_links.append(attributes.get('href', ''))


class FilteredCsvExportTests(TestCase):
    def setUp(self):
        login_reader(self.client)

    def test_export_uses_all_filtered_rows_and_escapes_formula_prefixes(self):
        People.objects.bulk_create([
            People(
                name='=2+3' if number == 0 else f'运维人员{number:02d}',
                employee_id=f'OPS{number:03d}',
                department='运维部',
            )
            for number in range(25)
        ] + [
            People(
                name=f'研发人员{number:02d}',
                employee_id=f'RD{number:03d}',
                department='研发部',
            )
            for number in range(3)
        ])

        response = self.client.get(reverse('table_export', args=['people']), {
            'filter_department': '运维部',
            'sort': 'employee_id',
            'order': 'asc',
            'page': '2',
            'page_size': '20',
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response_body(response).startswith(b'\xef\xbb\xbf'))
        rows = list(csv.DictReader(StringIO(response_body(response).decode('utf-8-sig'))))
        self.assertEqual(len(rows), 25)
        self.assertEqual({row['部门'] for row in rows}, {'运维部'})
        self.assertEqual(rows[0]['姓名'], "'=2+3")
        self.assertNotIn('password', response_body(response).decode('utf-8-sig').lower())

    def test_target_scope_is_preserved_in_infrastructure_record_export(self):
        first = Network_Device.objects.create(
            device_name='核心交换机', ip='192.0.2.20',
        )
        second = Network_Device.objects.create(
            device_name='接入交换机', ip='192.0.2.21',
        )
        Network_Device_Inspection.objects.create(
            device=first, status=RecordStatus.SUCCESS, summary='核心正常',
        )
        Network_Device_Inspection.objects.create(
            device=second, status=RecordStatus.FAILED, summary='接入异常',
        )

        response = self.client.get(
            reverse(
                'table_export_scoped',
                args=['inspection_records', 'networks'],
            ),
            {'target': str(first.pk)},
        )

        rows = list(csv.DictReader(StringIO(response_body(response).decode('utf-8-sig'))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['设备名称'], '核心交换机 (192.0.2.20)')
        self.assertEqual(rows[0]['摘要'], '核心正常')

    def test_each_table_workspace_has_independent_export_link_with_query(self):
        cases = (
            (reverse('asset_list', args=['people']), reverse('table_export', args=['people'])),
            (reverse('domain_account_list'), reverse('table_export', args=['domain_accounts'])),
            (reverse('inspection_records'), reverse('table_export', args=['inspection_records'])),
            (reverse('error_records'), reverse('table_export', args=['error_records'])),
            (reverse('computer_error_list'), reverse('table_export', args=['computer_errors'])),
            (
                reverse('record_list', args=['networks']),
                reverse('table_export_scoped', args=['inspection_records', 'networks']),
            ),
        )

        for page_url, export_path in cases:
            with self.subTest(page=page_url):
                response = self.client.get(page_url, {
                    'q': '交换机', 'page': '3', 'page_size': '50',
                })
                parser = _ExportLinkParser()
                parser.feed(response.content.decode())
                self.assertEqual(len(parser.links), 1)
                export_url = urlsplit(parser.links[0])
                self.assertEqual(export_url.path, export_path)
                self.assertEqual(parse_qs(export_url.query), {
                    'q': ['交换机'], 'page_size': ['50'],
                })

    def test_unknown_table_and_scope_are_rejected(self):
        self.assertEqual(
            self.client.get(reverse('table_export', args=['passwords'])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse(
                'table_export_scoped', args=['inspection_records', 'unknown'],
            )).status_code,
            404,
        )


class ComputerAnalysisOutcomeTests(TestCase):
    def setUp(self):
        login_reader(self.client)
        computer = Computer.objects.create(computer_name='PC-OUTCOME')
        abnormal_log = create_log_file(
            source_path='abnormal.json',
            modified_at=timezone.now(),
            content_hash='a' * 64,
            import_status='success',
        )
        normal_log = create_log_file(
            source_path='normal.json',
            modified_at=timezone.now(),
            content_hash='b' * 64,
            import_status='success',
        )
        self.abnormal = ComputerAnalysis.objects.create(
            computer=computer,
            log_file=abnormal_log,
            status=RecordStatus.SUCCESS,
            summary='执行成功但发现异常',
        )
        self.normal = ComputerAnalysis.objects.create(
            computer=computer,
            log_file=normal_log,
            status=RecordStatus.SUCCESS,
            summary='未发现异常',
        )
        Error_Computer.objects.create(
            inspection=self.abnormal,
            error_type='磁盘异常',
            error_message='磁盘剩余空间不足',
        )
        profile = ComputerAnalysisProfile.objects.create(name='Outcome details', analysis_items=['resource'])
        self.task = TaskRun.objects.create(task_type='computer_analysis', source='manual', analysis_profile=profile)
        for record in (self.normal, self.abnormal):
            record.task_target = TaskTargetRun.objects.create(task=self.task, target_type='computer_log',
                target_id=str(record.log_file_id), result_type='computer_analysis', result_id=str(record.pk))
            record.save(update_fields=['task_target'])

    def test_anomaly_outcome_is_consistent_in_html_filter_options_and_csv(self):
        abnormal_response = self.client.get(
            reverse('task_detail', args=[self.task.pk]),
            {'filter_status': 'warning'},
        )
        normal_response = self.client.get(
            reverse('task_detail', args=[self.task.pk]),
            {'filter_status': 'normal'},
        )

        self.assertEqual(
            list(abnormal_response.context['page_obj'].object_list),
            [self.abnormal],
        )
        self.assertEqual(
            list(normal_response.context['page_obj'].object_list),
            [self.normal],
        )
        self.assertContains(
            abnormal_response,
            '<option value="warning" selected>警告</option>',
            html=True,
        )
        self.assertContains(abnormal_response, '>警告</span>')
        self.assertEqual(
            abnormal_response.context['table_state']['field_options']['status'],
            (('normal', '正常'), ('info', '提示'), ('warning', '警告'), ('critical', '严重')),
        )

        export_response = self.client.get(
            reverse('table_export', args=['computer_inspections']),
            {'filter_status': 'warning'},
        )
        rows = list(csv.DictReader(StringIO(
            response_body(export_response).decode('utf-8-sig'),
        )))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['分析结果'], '警告')
class PaginationWindowTests(TestCase):
    def test_page_window_keeps_neighbors_between_first_and_last_pages(self):
        from django.core.paginator import Paginator
        from index.templatetags.extras import page_window

        page = Paginator(list(range(200)), 10).page(10)

        self.assertEqual(page_window(page, radius=2), (8, 9, 10, 11, 12))

    def test_page_window_clamps_near_the_start(self):
        from django.core.paginator import Paginator
        from index.templatetags.extras import page_window

        page = Paginator(list(range(200)), 10).page(1)

        self.assertEqual(page_window(page, radius=2), (2, 3))
