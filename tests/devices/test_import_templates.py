import csv
from io import BytesIO, StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from tests.auth import login_admin

from net.data_exchange.inventory_csv import export_csv, export_xlsx_template, import_file
from net.data_exchange.xlsx import read_xlsx_rows
from net.models import Network_Device, SecurityDevice, Server


EXPECTED_TEMPLATE_HEADERS = {
    'networks': [
        '设备名称', 'IP地址', '设备类型', '厂商', '连接方式', '深信服 API地址',
        '深信服共享密钥', '校验HTTPS证书', 'SSH端口',
        'SSH账号', 'SSH密码', 'SNMP 版本', 'SNMP 端口', 'SNMP Community',
        'SNMPv3 用户名', '安全级别', '认证协议', '认证密码', '加密协议', '加密密码',
        '上下文', '重试次数', '系统版本',
    ],
    'servers': [
        '服务器名称', 'IP地址', '服务器类型', '管理端口', 'SSH账号', 'SSH密码',
        'Windows API地址', 'API令牌', '校验HTTPS证书', '系统版本',
    ],
    'monitors': [
        '设备名称', 'IP地址', '设备类型', '厂商', 'API地址', 'API账号',
        'API密码', 'API令牌', '校验HTTPS证书',
    ],
}


class DeviceImportTemplateTests(TestCase):
    def _csv_rows(self, entity):
        return list(csv.reader(StringIO(export_csv(entity, template_only=True).lstrip('\ufeff'))))

    def test_templates_only_request_fields_used_to_configure_devices(self):
        for entity, expected_headers in EXPECTED_TEMPLATE_HEADERS.items():
            with self.subTest(entity=entity):
                self.assertEqual(self._csv_rows(entity)[0], expected_headers)
                self.assertNotIn('CPU 型号', expected_headers)
                self.assertNotIn('内存总量', expected_headers)
                self.assertNotIn('磁盘总量', expected_headers)

    def test_network_template_scenarios_are_directly_importable(self):
        rows = self._csv_rows('networks')
        self.assertEqual({row[4] for row in rows[1:]}, {'ssh', 'snmp', 'hybrid', 'sangfor_api'})

        created, updated = import_file('networks', SimpleUploadedFile(
            'network-template.csv', export_csv('networks', template_only=True).encode('utf-8'),
            content_type='text/csv',
        ))

        self.assertEqual((created, updated), (4, 0))
        self.assertEqual(Network_Device.objects.count(), 4)
        sangfor = Network_Device.objects.get(connection_type='sangfor_api')
        self.assertEqual(sangfor.device_type, 'ac_gateway')
        self.assertTrue(sangfor.api_url and sangfor.api_shared_secret)
        self.assertEqual(Network_Device.objects.get(connection_type='snmp').snmp_version, 'v2c')
        self.assertEqual(Network_Device.objects.get(connection_type='hybrid').snmp_security_level,
                         'authPriv')

    def test_network_normal_export_never_includes_sangfor_shared_secret(self):
        Network_Device.objects.create(
            ip='192.0.2.14', connection_type='sangfor_api',
            api_url='https://ac.example.invalid', api_shared_secret='api-export-secret',
        )
        exported = export_csv('networks', template_only=False)
        self.assertNotIn('深信服共享密钥', exported)
        self.assertNotIn('api-export-secret', exported)

    def test_server_template_covers_linux_ssh_and_windows_api(self):
        rows = self._csv_rows('servers')
        self.assertEqual({row[2].lower() for row in rows[1:]}, {'linux', 'windows'})

        created, updated = import_file('servers', SimpleUploadedFile(
            'server-template.csv', export_csv('servers', template_only=True).encode('utf-8'),
            content_type='text/csv',
        ))

        self.assertEqual((created, updated), (2, 0))
        linux = Server.objects.get(server_type='linux')
        windows = Server.objects.get(server_type='windows')
        self.assertTrue(linux.username and linux.password)
        self.assertTrue(windows.api_url and windows.api_token)

    def test_security_template_covers_password_and_token_authentication(self):
        rows = self._csv_rows('monitors')
        self.assertEqual(len(rows), 3)

        created, updated = import_file('monitors', SimpleUploadedFile(
            'security-template.csv', export_csv('monitors', template_only=True).encode('utf-8'),
            content_type='text/csv',
        ))

        self.assertEqual((created, updated), (2, 0))
        self.assertTrue(SecurityDevice.objects.exclude(api_password='').exists())
        self.assertTrue(SecurityDevice.objects.exclude(api_token='').exists())

    def test_excel_and_csv_templates_expose_the_same_scenarios(self):
        for entity in EXPECTED_TEMPLATE_HEADERS:
            with self.subTest(entity=entity):
                csv_rows = self._csv_rows(entity)
                xlsx_rows = read_xlsx_rows(
                    BytesIO(export_xlsx_template(entity)),
                    max_rows=10,
                    max_columns=64,
                    max_cell_chars=4096,
                )
                self.assertEqual(xlsx_rows, csv_rows)
    def test_import_dialog_warns_that_demonstration_rows_must_be_replaced(self):
        login_admin(self.client)
        response = self.client.get(reverse('asset_list', args=['networks']))

        self.assertContains(response, '请删除或替换模板中的演示数据行')
