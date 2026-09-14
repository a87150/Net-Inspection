import csv

from tests import response_body
import re
from html.parser import HTMLParser
from io import StringIO
from unittest.mock import patch

from django.contrib import admin
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from tests.auth import login_admin, login_reader

from index.common.table_registry import get_table_definition
from net.models import (
    Domain_Account,
    Domain_Computer,
    Network_Device,
    People,
    SecurityDevice,
    Server,
)
from net.data_exchange.inventory_csv import import_csv


class ElementParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def find(self, tag, **attrs):
        return [
            element_attrs
            for element_tag, element_attrs in self.elements
            if element_tag == tag
            and all(element_attrs.get(key) == value for key, value in attrs.items())
        ]


def parse_response_html(response):
    parser = ElementParser()
    parser.feed(response.content.decode(response.charset))
    return parser


def extract_div(document, element_id):
    marker = f'id="{element_id}"'
    marker_index = document.index(marker)
    start = document.rfind('<div', 0, marker_index)
    depth = 0
    for tag in re.finditer(r'</?div\b[^>]*>', document[start:], re.IGNORECASE):
        depth += -1 if tag.group().startswith('</') else 1
        if depth == 0:
            return document[start:start + tag.end()]
    raise AssertionError(f'{element_id} did not close')


class FilteredExportContractTests(TestCase):
    def setUp(self):
        login_reader(self.client)

    def test_each_manual_import_template_has_csv_and_xlsx_sample_data(self):
        login_admin(self.client)
        cases = (
            ('people', People, '工号', 'H10001', 'employee_id', 1),
            ('networks', Network_Device, 'IP地址', '192.0.2.10', 'ip', 4),
            ('servers', Server, 'IP地址', '192.0.2.20', 'ip', 2),
            ('monitors', SecurityDevice, 'IP地址', '192.0.2.30', 'ip', 2),
        )

        for entity, model, column, sample_value, model_field, sample_count in cases:
            with self.subTest(entity=entity):
                csv_response = self.client.get(
                    reverse('download_inventory_template', args=[entity]),
                )
                csv_rows = list(csv.DictReader(StringIO(
                    csv_response.content.decode('utf-8-sig'),
                )))
                self.assertEqual(len(csv_rows), sample_count)
                self.assertEqual(csv_rows[0][column], sample_value)
                if entity == 'networks':
                    sangfor = next(row for row in csv_rows if row['连接方式'] == 'sangfor_api')
                    self.assertEqual(sangfor['设备类型'], 'ac_gateway')
                    self.assertEqual(sangfor['厂商'], 'sangfor')
                if entity == 'networks':
                    hybrid = next(row for row in csv_rows if row['连接方式'] == 'hybrid')
                    self.assertEqual(hybrid['SNMP 版本'], 'v3')
                    self.assertEqual(hybrid['SNMP 端口'], '161')
                    self.assertEqual(hybrid['认证密码'], 'CHANGE-ME')
                    self.assertEqual(hybrid['加密密码'], 'CHANGE-ME')

                xlsx_response = self.client.get(
                    f'/data/{entity}/template/xlsx/',
                )
                self.assertEqual(xlsx_response.status_code, 200)
                self.assertTrue(xlsx_response.content.startswith(b'PK'))
                upload = SimpleUploadedFile(
                    f'{entity}.xlsx',
                    xlsx_response.content,
                    content_type=(
                        'application/vnd.openxmlformats-officedocument.'
                        'spreadsheetml.sheet'
                    ),
                )
                import_response = self.client.post(
                    reverse('import_inventory', args=[entity]),
                    {'file': upload},
                )
                self.assertEqual(import_response.status_code, 302)
                self.assertTrue(model.objects.filter(
                    **{model_field: sample_value},
                ).exists())

    def test_network_lists_and_exports_expose_only_public_connection_settings(self):
        Network_Device.objects.create(
            device_name='secret-network', ip='192.0.2.95',
            connection_type='hybrid', snmp_version='v3', snmp_port=1161,
            password='ssh-password-private', snmp_community='community-private',
            snmp_username='snmp-reader', snmp_security_level='authPriv',
            snmp_auth_protocol='sha256', snmp_auth_password='auth-private',
            snmp_priv_protocol='aes128', snmp_priv_password='priv-private',
        )

        response = self.client.get(reverse('table_export', args=['networks']))
        exported = response_body(response).decode('utf-8-sig')
        headers = next(csv.reader(StringIO(exported)))
        rows = list(csv.DictReader(StringIO(exported)))
        definition = get_table_definition('networks')
        field_keys = {field.key for field in definition.fields}
        field_labels = {field.label for field in definition.fields}
        admin_search_fields = set(
            admin.site._registry[Network_Device].search_fields
        )
        secret_names = {
            'password', 'snmp_community', 'snmp_auth_password',
            'snmp_priv_password',
        }
        secret_labels = {
            'SSH密码', 'SNMP Community', 'SNMP认证密码', 'SNMP加密密码',
            '认证密码', '加密密码',
        }

        self.assertEqual(
            {'connection_type', 'snmp_version', 'snmp_port'} & field_keys,
            {'connection_type', 'snmp_version', 'snmp_port'},
        )
        self.assertTrue(secret_names.isdisjoint(field_keys))
        self.assertTrue(secret_names.isdisjoint(admin_search_fields))
        self.assertTrue(secret_labels.isdisjoint(headers))
        self.assertTrue(secret_labels.isdisjoint(field_labels))
        for header, secret in (
            ('SSH密码', 'ssh-password-private'),
            ('SNMP Community', 'community-private'),
            ('认证密码', 'auth-private'),
            ('加密密码', 'priv-private'),
        ):
            with self.subTest(header=header):
                self.assertNotIn(header, headers)
                self.assertTrue(all(secret not in row.values() for row in rows))
                self.assertNotIn(secret, exported)

    def test_export_uses_filter_and_ignores_page_size(self):
        People.objects.bulk_create([
            People(
                name=f'运维人员{number:02d}',
                employee_id=f'OPS-{number:02d}',
                department='运维部',
            )
            for number in range(25)
        ] + [
            People(name='研发人员', employee_id='DEV-01', department='研发部'),
        ])

        response = self.client.get(reverse('table_export', args=['people']), {
            'filter_department': '运维部',
            'page_size': '20',
            'page': '2',
        })
        rows = list(csv.DictReader(StringIO(response_body(response).decode('utf-8-sig'))))

        self.assertEqual(len(rows), 25)
        self.assertEqual({row['部门'] for row in rows}, {'运维部'})

    def test_importable_asset_modal_contains_import_only_for_its_entity(self):
        login_admin(self.client)
        cases = (
            ('people', 'people'),
            ('networks', 'networks'),
            ('servers', 'servers'),
            ('monitors', 'monitors'),
        )
        for kind, entity in cases:
            with self.subTest(kind=kind):
                response = self.client.get(reverse('asset_list', args=[kind]))
                document = response.content.decode(response.charset)
                parsed = parse_response_html(response)
                modal = extract_div(document, 'importModal')

                self.assertEqual(len(parsed.find(
                    'button', **{'data-bs-target': '#importModal'},
                )), 1)
                button_label = '导入人员' if entity == 'people' else '导入设备'
                self.assertIn(f'>{button_label}</button>', document)
                self.assertIn(
                    f'action="{reverse("import_inventory", args=[entity])}"', modal,
                )
                for file_format in ('csv', 'xlsx'):
                    self.assertIn(
                        reverse(
                            'download_inventory_template_format',
                            args=[entity, file_format],
                        ),
                        modal,
                    )
                self.assertNotIn('/export/', modal)
                self.assertNotIn('导出 CSV', modal)
                self.assertNotIn('数据工具', document)
                self.assertIn(
                    f'href="{reverse("table_export", args=[entity])}', document,
                )

                for other_entity in {'people', 'networks', 'servers', 'monitors'} - {entity}:
                    self.assertNotIn(
                        reverse('import_inventory', args=[other_entity]), modal,
                    )

    def test_automatic_source_pages_have_filtered_export_without_import_modal(self):
        login_admin(self.client)
        pages = (
            (reverse('asset_list', args=['computers']), 'computers'),
            (reverse('domain_computer_list'), 'domain_computers'),
            (reverse('domain_group_list'), 'domain_groups'),
        )
        for url, table_key in pages:
            with self.subTest(url=url):
                response = self.client.get(url)
                document = response.content.decode(response.charset)

                self.assertNotIn('id="importModal"', document)
                self.assertNotIn('data-bs-target="#importModal"', document)
                self.assertNotIn('id="dataToolsModal"', document)
                self.assertNotIn('数据工具', document)
                self.assertNotIn('/import/', document)
                self.assertNotIn('/data/', document)
                self.assertIn(
                    f'href="{reverse("table_export", args=[table_key])}', document,
                )


class PersonnelApiImportContractTests(TestCase):
    def setUp(self):
        login_admin(self.client)

    def test_people_import_modal_offers_fixed_settings_for_both_providers(self):
        response = self.client.get(reverse('asset_list', args=['people']))
        document = response.content.decode(response.charset)
        modal = extract_div(document, 'importModal')

        self.assertIn('飞书 API 设置', modal)
        self.assertIn('钉钉 API 设置', modal)
        self.assertIn(
            f'action="{reverse("people_provider_save", args=["feishu"])}"', modal,
        )
        self.assertIn(
            f'action="{reverse("people_provider_save", args=["dingtalk"])}"', modal,
        )
        self.assertNotIn('新建来源', modal)
        self.assertNotIn('来源名称', modal)
        self.assertNotIn('稳定来源标识', modal)
        self.assertNotIn('name="source_id"', modal)

    def test_provider_query_reopens_people_import_without_a_legacy_post_bridge(self):
        response = self.client.get(
            f'{reverse("asset_list", args=["people"])}?import=people&provider=feishu',
        )
        document = response.content.decode(response.charset)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '飞书 API 设置')
        self.assertEqual(People.objects.count(), 0)
        self.assertIn('id="importModal"', document)
        self.assertIn('data-auto-open="true"', extract_div(document, 'importModal'))

    def test_known_secret_is_never_rendered_or_stored_in_session(self):
        response = self.client.post(reverse('people_provider_save', args=['feishu']), {
            'app_id': 'cli-test', 'app_secret': 'super-secret',
            'root_department_ids': '0', 'is_enabled': 'on',
        }, follow=True)
        self.assertNotIn(b'super-secret', response.content)
        self.assertNotIn('super-secret', str(dict(self.client.session)))


class CsvImportIsolationTests(TestCase):
    def setUp(self):
        login_admin(self.client)

    def _post_server_csv(self, content):
        upload = SimpleUploadedFile(
            'servers.csv', content.encode('utf-8-sig'), content_type='text/csv',
        )
        return self.client.post(
            reverse('import_inventory', args=['servers']),
            {'file': upload},
            follow=True,
        )

    def test_oversize_upload_is_rejected_before_import_and_reopens_modal(self):
        upload = SimpleUploadedFile(
            'servers.csv',
            b'IP\xe5\x9c\xb0\xe5\x9d\x80\n' + (b'x' * (2 * 1024 * 1024)),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('import_inventory', args=['servers']),
            {'file': upload},
            follow=True,
        )

        self.assertContains(response, '导入失败：文件不能超过 2 MB')
        self.assertIn(
            'data-auto-open="true"',
            extract_div(response.content.decode(response.charset), 'importModal'),
        )
        self.assertEqual(Server.objects.count(), 0)

    def test_over_limit_field_returns_controlled_feedback_without_writes(self):
        response = self._post_server_csv(
            '服务器名称,IP地址,服务器类型\n'
            f'{"超" * 20000},192.0.2.50,Linux\n'
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '导入失败：')
        self.assertContains(response, '单元格内容不能超过 10000 个字符')
        self.assertEqual(Server.objects.count(), 0)

    def test_malformed_csv_returns_controlled_feedback_without_writes(self):
        response = self._post_server_csv(
            '服务器名称,IP地址,服务器类型\n'
            '"未闭合服务器,192.0.2.51,Linux\n'
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '导入失败：CSV 格式错误')
        self.assertEqual(Server.objects.count(), 0)

    def test_row_limit_rejects_whole_file(self):
        rows = ''.join(
            f'服务器{number},192.0.2.{number % 250 + 1},Linux\n'
            for number in range(5001)
        )
        response = self._post_server_csv(
            '服务器名称,IP地址,服务器类型\n' + rows,
        )

        self.assertContains(response, '导入失败：CSV 最多允许 5000 行数据')
        self.assertEqual(Server.objects.count(), 0)

    def test_upload_read_failure_becomes_import_validation_error(self):
        class BrokenUpload:
            size = 100

            def read(self, *_args, **_kwargs):
                raise OSError('disk read failed')

        with self.assertRaisesRegex(ValueError, '读取上传文件失败'):
            import_csv('servers', BrokenUpload())

    def test_wrong_entity_headers_reject_whole_file_instead_of_partial_mapping(self):
        upload = SimpleUploadedFile(
            'network-devices.csv',
            (
                '设备名称,IP地址,设备类型,厂商,连接方式,SSH端口\n'
                '核心交换机,192.0.2.10,交换机,华为,ssh,22\n'
            ).encode('utf-8-sig'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('import_inventory', args=['servers']),
            {'file': upload},
            follow=True,
        )

        self.assertContains(response, '导入失败：')
        self.assertContains(response, '不属于服务器导入模板')
        self.assertEqual(Server.objects.count(), 0)
        self.assertEqual(Network_Device.objects.count(), 0)

    def test_extra_cells_reject_whole_file_instead_of_being_ignored(self):
        upload = SimpleUploadedFile(
            'servers.csv',
            (
                '服务器名称,IP地址,服务器类型\n'
                '服务器一,192.0.2.30,Linux,不应被忽略\n'
            ).encode('utf-8-sig'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('import_inventory', args=['servers']),
            {'file': upload},
            follow=True,
        )

        self.assertContains(response, '导入失败：')
        self.assertContains(response, '列数超过表头')
        self.assertEqual(Server.objects.count(), 0)

    def test_duplicate_keys_reject_whole_file_instead_of_last_row_winning(self):
        upload = SimpleUploadedFile(
            'servers.csv',
            (
                '服务器名称,IP地址,服务器类型\n'
                '服务器一,192.0.2.40,Linux\n'
                '服务器二,192.0.2.40,Windows\n'
            ).encode('utf-8-sig'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('import_inventory', args=['servers']),
            {'file': upload},
            follow=True,
        )

        self.assertContains(response, '导入失败：')
        self.assertContains(response, '主键重复')
        self.assertEqual(Server.objects.count(), 0)

    def test_one_invalid_row_rolls_back_all_rows(self):
        upload = SimpleUploadedFile(
            'servers.csv',
            (
                '服务器名称,IP地址,服务器类型\n'
                '有效服务器,192.0.2.20,Linux\n'
                '错误服务器,not-an-ip,Linux\n'
            ).encode('utf-8-sig'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('import_inventory', args=['servers']),
            {'file': upload},
            follow=True,
        )

        self.assertContains(response, '导入失败：')
        self.assertEqual(Server.objects.count(), 0)

    def test_domain_objects_cannot_be_manually_imported(self):
        for entity, model in (
            ('accounts', Domain_Account),
            ('domain_computers', Domain_Computer),
        ):
            with self.subTest(entity=entity):
                upload = SimpleUploadedFile(
                    f'{entity}.csv', b'ignored', content_type='text/csv',
                )
                response = self.client.post(
                    reverse('import_inventory', args=[entity]), {'file': upload},
                    follow=True,
                )

                self.assertContains(response, '不支持手动导入')
                self.assertEqual(model.objects.count(), 0)

    def test_unknown_entity_returns_404_without_processing_or_mutation(self):
        self.client.raise_request_exception = False
        upload = SimpleUploadedFile(
            'unknown.csv', b'anything', content_type='text/csv',
        )

        response = self.client.post(
            reverse('import_inventory', args=['not-a-real-entity']),
            {'file': upload},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Server.objects.count(), 0)


class CsvModelValidationTests(TestCase):
    def setUp(self):
        login_admin(self.client)

    def _post(self, body):
        upload = SimpleUploadedFile(
            'servers.csv', body.encode('utf-8-sig'), content_type='text/csv',
        )
        return self.client.post(
            reverse('import_inventory', args=['servers']),
            {'file': upload},
            follow=True,
        )

    def test_create_rejects_invalid_url_with_row_number(self):
        response = self._post(
            '服务器名称,IP地址,服务器类型,Windows API地址\n'
            '新服务器,192.0.2.60,Windows,not-a-url\n'
        )

        self.assertContains(response, '导入失败：第 2 行')
        self.assertContains(response, 'Windows API地址')
        self.assertEqual(Server.objects.count(), 0)

    def test_create_rejects_over_length_value_with_row_number(self):
        response = self._post(
            '服务器名称,IP地址,服务器类型\n'
            f'{"长" * 256},192.0.2.61,Linux\n'
        )

        self.assertContains(response, '导入失败：第 2 行')
        self.assertContains(response, '服务器名称')
        self.assertEqual(Server.objects.count(), 0)

    def test_update_rejects_invalid_url_without_changing_existing_row(self):
        server = Server.objects.create(
            name='原服务器', ip='192.0.2.62', server_type='windows',
            api_url='http://192.0.2.62/inspection',
        )

        response = self._post(
            '服务器名称,IP地址,服务器类型,Windows API地址\n'
            '新名称,192.0.2.62,Windows,not-a-url\n'
        )

        self.assertContains(response, '导入失败：第 2 行')
        server.refresh_from_db()
        self.assertEqual(server.name, '原服务器')
        self.assertEqual(server.api_url, 'http://192.0.2.62/inspection')

    def test_update_rejects_over_length_value_without_changing_existing_row(self):
        server = Server.objects.create(
            name='原服务器', ip='192.0.2.63', server_type='linux',
        )

        response = self._post(
            '服务器名称,IP地址,服务器类型\n'
            f'{"长" * 256},192.0.2.63,Linux\n'
        )

        self.assertContains(response, '导入失败：第 2 行')
        server.refresh_from_db()
        self.assertEqual(server.name, '原服务器')

    def test_late_row_validation_failure_writes_nothing(self):
        response = self._post(
            '服务器名称,IP地址,服务器类型,Windows API地址\n'
            '有效服务器,192.0.2.64,Linux,\n'
            '错误服务器,192.0.2.65,Windows,not-a-url\n'
        )

        self.assertContains(response, '导入失败：第 3 行')
        self.assertEqual(Server.objects.count(), 0)

    @patch('net.models.devices.Server.save', side_effect=IntegrityError('race'))
    def test_database_error_rolls_back_and_returns_controlled_feedback(self, _save):
        self.client.raise_request_exception = False
        response = self._post(
            '服务器名称,IP地址,服务器类型\n'
            '新服务器,192.0.2.66,Linux\n'
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '导入失败：数据库写入失败')
        self.assertEqual(Server.objects.count(), 0)


class CanonicalIpImportTests(TestCase):
    def setUp(self):
        login_admin(self.client)

    def _post(self, body):
        upload = SimpleUploadedFile(
            'servers.csv', body.encode('utf-8-sig'), content_type='text/csv',
        )
        return self.client.post(
            reverse('import_inventory', args=['servers']),
            {'file': upload},
            follow=True,
        )

    def test_equivalent_ipv6_rows_are_rejected_as_duplicate_business_keys(self):
        response = self._post(
            '服务器名称,IP地址,服务器类型\n'
            '服务器一,2001:db8::1,Linux\n'
            '服务器二,2001:0db8:0:0:0:0:0:1,Linux\n'
        )

        self.assertContains(response, '导入失败：')
        self.assertContains(response, '主键重复')
        self.assertEqual(Server.objects.count(), 0)

    def test_equivalent_existing_ipv6_is_updated_and_canonicalized(self):
        server = Server.objects.create(
            name='原服务器', ip='2001:0db8:0:0:0:0:0:2', server_type='linux',
        )

        response = self._post(
            '服务器名称,IP地址,服务器类型\n'
            '更新服务器,2001:db8::2,Linux\n'
        )

        self.assertContains(response, '导入完成：新增 0 条，更新 1 条')
        self.assertEqual(Server.objects.count(), 1)
        server.refresh_from_db()
        self.assertEqual(server.name, '更新服务器')
        self.assertEqual(server.ip, '2001:db8::2')
