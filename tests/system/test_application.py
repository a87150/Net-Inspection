from tests.devices.pc.helpers import create_log_file
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from io import StringIO
import re
import uuid
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from tests.auth import login_admin, login_reader
from tests.devices.pc.helpers import analysis_task_url

from net.models import (
    Computer,
    ComputerAnalysis,
    ComputerAnalysisProfile,
    ComputerLogFile,
    Domain_Account,
    Domain_Computer,
    Domain_Controller_Config,
    Error_Computer,
    Error_Monitor,
    Error_Network_Device,
    Error_Server,
    SecurityDevice,
    Monitor_Inspection,
    Network_Device,
    Network_Device_Inspection,
    People,
    RecordStatus,
    Server,
    Server_Inspection,
    TaskRun,
)
from net.devices.pc.checks import check_activation, check_system_version
from net.domain.sync import sync_domain
from net.devices.security.api import collect_security_api
from net.devices.server.windows_http import collect_windows_http
from net.infrastructure.collection import CollectionResult
from net.devices.pc.snapshot import extract_computer_snapshot
from index.domain.connection_form import DomainControllerConfigForm


class HookParser(HTMLParser):
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
            and all(
                key in element_attrs if value == '' else element_attrs.get(key) == value
                for key, value in attrs.items()
            )
        ]


def parse_response_html(response):
    parser = HookParser()
    parser.feed(response.content.decode(response.charset))
    return parser


def valid_payload(computer_name='PC-001', current_time=None):
    current_time = current_time or timezone.localtime().replace(microsecond=0)
    return {
        '日志时间': current_time.strftime('%Y-%m-%d %H:%M:%S'),
        '系统信息概览': {
            '计算机名': computer_name,
            '当前登录用户工号': 'H000001',
            '当前登录用户姓名': '测试用户',
            '开机时间': (current_time - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S'),
            '系统主要版本名': '24H2',
            '系统详细版本': '10.0.26100.4770',
            '系统版本类型': 'Professional',
            '系统安装日期': '2025-01-02 03:04:05',
        },
        '网络信息': [{'接口名称': 'Ethernet', 'IP地址': '192.0.2.10', 'MAC地址': '00-00-5E-00-53-01'}],
        '计算机硬件资源情况': {
            '当前CPU温度': '48°C',
            '当前CPU占用率': '10%',
            '当前内存容量': '16GB',
            '当前内存使用率': '61%',
        },
        'Windows激活信息': {'许可证状态': '已授权', '描述': 'RETAIL channel'},
        'KMS服务器连通情况': '正常通讯',
        '已安装软件列表': [{'软件名': 'Microsoft Office', '版本': '1.0'}],
        'BitLocker状态': {'磁盘卷信息': [{'卷': 'C', '转换状态': '完全加密'}]},
        'WindowsDefender状态': {
            '当前病毒库版本': '1.2.3',
            '上次更新时间': current_time.strftime('%Y-%m-%d %H:%M:%S'),
            '扫描信息': {'时间': current_time.strftime('%Y-%m-%d %H:%M:%S')},
        },
        '系统更新历史': [{'日期': current_time.strftime('%Y-%m-%d %H:%M:%S'), '补丁名称': 'KB Test'}],
    }


def create_computer_analysis(
    computer_name=None,
    *,
    computer=None,
    user_name='',
    modified_at=None,
    status=RecordStatus.SUCCESS,
    summary='',
    details=None,
    exceptions=None,
):
    if computer is None:
        computer = Computer.objects.create(
            computer_name=computer_name,
            user_name=user_name,
        )
    log_file = create_log_file(
        source_path=f'test://{computer.computer_name}/{uuid.uuid4()}.json',
        modified_at=modified_at or timezone.now(),
        content_hash=uuid.uuid4().hex * 2,
        import_status='success',
        payload=details or {},
    )
    return ComputerAnalysis.objects.create(
        computer=computer,
        log_file=log_file,
        status=status,
        summary=summary,
        details=details or {},
        exceptions=exceptions or [],
    )


class InspectionRuleTests(TestCase):
    def test_retail_activation_is_not_reported_as_invalid_kms(self):
        issues = []
        check_activation({'Windows激活信息': {'许可证状态': '已授权', '描述': 'RETAIL'}}, issues)
        self.assertEqual(issues, [])

    def test_old_windows_release_is_reported(self):
        issues = []
        check_system_version({'系统信息概览': {'系统主要版本名': '22H2'}}, issues, '23H2')
        self.assertEqual(issues[0]['问题类型'], '系统版本过旧')


class ComputerSnapshotTests(TestCase):
    def test_network_address_snapshot_fields_allow_1024_characters(self):
        self.assertEqual(Computer._meta.get_field('ip_addresses').max_length, 1024)
        self.assertEqual(Computer._meta.get_field('mac_addresses').max_length, 1024)

    def test_extracts_static_snapshot_from_powershell_sections(self):
        snapshot = extract_computer_snapshot(
            {
                '当前登录用户工号': 'DOMAIN\\u001',
                '系统主要版本名': '23H2',
                '系统详细版本': '10.0.22631.5189',
                '系统安装日期': '2025-01-02 03:04:05',
                '开机时间': '2026-08-30 08:00:00',
            },
            [
                {'IP地址': '192.0.2.10', 'MAC地址': 'AA-BB-CC-DD-EE-01'},
                {'IP地址': '192.0.2.11', 'MAC地址': 'AA-BB-CC-DD-EE-02'},
            ],
            {
                '当前CPU温度': '48°C', '当前CPU占用率': '23%',
                '当前内存容量': '16GB', '当前内存使用率': '61%',
            },
        )
        self.assertEqual(snapshot['login_account'], 'DOMAIN\\u001')
        self.assertEqual(snapshot['ip_addresses'], '192.0.2.10, 192.0.2.11')
        self.assertEqual(snapshot['mac_addresses'], 'AA-BB-CC-DD-EE-01, AA-BB-CC-DD-EE-02')
        self.assertEqual(snapshot['os_version'], '23H2')
        self.assertEqual(snapshot['os_build'], '10.0.22631.5189')
        self.assertEqual(snapshot['system_installed_at'], '2025-01-02 03:04:05')
        self.assertFalse({
            'last_boot_at', 'cpu_temperature', 'cpu_usage',
            'memory_total', 'memory_usage',
        } & snapshot.keys())

    def test_backfill_uses_the_latest_inspection_and_is_idempotent(self):
        computer = Computer.objects.create(computer_name='PC-BACKFILL')
        create_computer_analysis(
            computer=computer,
            details={'system_info': {'当前登录用户工号': 'OLD'}},
            modified_at=timezone.now() - timedelta(minutes=1),
        )
        create_computer_analysis(
            computer=computer,
            details={
                'system_info': {'当前登录用户工号': 'NEW'},
                'network_info': [{'IP地址': '192.0.2.20', 'MAC地址': 'AA-BB-CC-DD-EE-20'}],
                'computer_info': {'当前CPU占用率': '44%'},
            },
            modified_at=timezone.now(),
        )

        first_output = StringIO()
        call_command('backfill_computer_snapshots', stdout=first_output)
        computer.refresh_from_db()

        self.assertEqual(computer.login_account, 'NEW')
        self.assertEqual(computer.ip_addresses, '192.0.2.20')
        self.assertEqual(computer.mac_addresses, 'AA-BB-CC-DD-EE-20')
        self.assertIn('updated: 1', first_output.getvalue())

        second_output = StringIO()
        call_command('backfill_computer_snapshots', stdout=second_output)
        self.assertIn('updated: 0', second_output.getvalue())


class DashboardTests(TestCase):
    def setUp(self):
        login_reader(self.client)

    def test_empty_dashboard_and_record_workspace_do_not_crash(self):
        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '后台任务')
        self.assertContains(response, '安防设备')
        self.assertEqual(self.client.get(reverse('computer_analysis_list')).status_code, 200)

    def test_multiple_errors_count_as_one_abnormal_computer(self):
        computer = Computer.objects.create(computer_name='PC-COUNT')
        inspection = create_computer_analysis(
            computer=computer,
            details={'system_info': {'计算机名': computer.computer_name}},
        )
        Error_Computer.objects.create(inspection=inspection, error_type='A', error_message='a')
        Error_Computer.objects.create(inspection=inspection, error_type='B', error_message='b')
        response = self.client.get(reverse('index'))
        computers = next(item for item in response.context['items'] if item['key'] == 'computers')
        self.assertEqual(computers['checked'], 1)
        self.assertEqual(computers['bad'], 1)

    def test_computer_record_workspace_exposes_task_statistics(self):
        analysis = create_computer_analysis('PC-DETAIL-ERROR')
        Error_Computer.objects.create(
            inspection=analysis,
            error_type='磁盘异常',
            error_message='空间不足',
        )

        response = self.client.get(reverse('computer_analysis_list'))

        self.assertEqual(response.context['task_metrics']['task_count'], 0)
        self.assertContains(response, '总体统计')


class PeopleUploadTests(TestCase):
    def test_removed_upload_endpoint_does_not_deactivate_people(self):
        person = People.objects.create(employee_id='H1', is_active=True)
        response = APIClient().post('/api/upload_people/', [], format='json')
        self.assertEqual(response.status_code, 404)
        person.refresh_from_db()
        self.assertTrue(person.is_active)


class NetworkCheckCommandTests(TestCase):
    def setUp(self):
        login_admin(self.client)

    def test_command_enqueues_all_asset_types_without_collecting(self):
        Network_Device.objects.create(device_name='SW-1', ip='192.0.2.1')
        Server.objects.create(ip='192.0.2.2')
        SecurityDevice.objects.create(device_name='CAM-1', ip='192.0.2.3')

        output = StringIO()
        call_command('run_network_checks', stdout=output)

        self.assertEqual(TaskRun.objects.filter(status=TaskRun.Status.QUEUED).count(), 3)
        self.assertEqual(Network_Device_Inspection.objects.count(), 0)
        self.assertEqual(Server_Inspection.objects.count(), 0)
        self.assertEqual(Monitor_Inspection.objects.count(), 0)
        self.assertIn('已创建 3 个巡检任务', output.getvalue())
        response = self.client.get(reverse('index'))
        server_card = next(item for item in response.context['items'] if item['key'] == 'servers')
        self.assertEqual(server_card['checked'], 0)
        self.assertEqual(server_card['bad'], 0)
        self.assertEqual(self.client.get(reverse('inspection_records')).status_code, 200)
        self.assertEqual(self.client.get(reverse('error_records')).status_code, 200)

    def test_single_asset_page_action_only_enqueues(self):
        device = Network_Device.objects.create(device_name='SW-WEB', ip='192.0.2.20')
        response = self.client.post(reverse('run_infrastructure_inspection'), {
            'asset_type': 'networks',
            'asset_id': str(device.pk),
        })
        self.assertRedirects(response, reverse('item_list', args=['networks']))
        self.assertEqual(Network_Device_Inspection.objects.filter(device=device).count(), 0)
        self.assertEqual(TaskRun.objects.filter(status=TaskRun.Status.QUEUED).count(), 1)
        list_response = self.client.get(reverse('item_list', args=['networks']))
        self.assertNotContains(list_response, '最新状态')

    def test_command_snapshot_excludes_network_credentials(self):
        device = Network_Device.objects.create(
            device_name='SW-SSH', ip='192.0.2.30', username='reader', password='secret', vendor='Cisco',
        )
        call_command('run_network_checks', asset_type='networks', asset_id=str(device.pk), stdout=StringIO())
        target = TaskRun.objects.get().target_runs.get()
        self.assertEqual(target.target_snapshot['ip'], '192.0.2.30')
        self.assertNotIn('username', target.target_snapshot)
        self.assertNotIn('password', target.target_snapshot)


class ProtocolCollectorTests(TestCase):
    @patch('net.infrastructure.http_collectors.requests.get')
    def test_windows_http_collector_maps_agent_payload(self, get_mock):
        response = Mock()
        response.headers = {'content-type': 'application/json'}
        response.json.return_value = {
            'computer_name': 'WIN-SRV-01', 'cpu': {'usage_percent': 11},
            'memory': {'used_percent': 40}, 'storage_status': [{'device': 'C:'}],
        }
        response.raise_for_status.return_value = None
        get_mock.return_value = response
        server = Server(ip='192.0.2.40', server_type='windows', api_token='token')
        result = collect_windows_http(server, selected_items=['computer_name', 'cpu', 'memory', 'storage_status'])
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.data['computer_name'], 'WIN-SRV-01')
        called_url = get_mock.call_args.args[0]
        self.assertEqual(called_url, 'http://192.0.2.40:9180/inspection')

    @patch('net.infrastructure.http_collectors.requests.get')
    def test_security_api_collector_supports_vendor_json(self, get_mock):
        response = Mock()
        response.headers = {'content-type': 'application/json'}
        response.json.return_value = {
            'device_info': {'model': 'NVR'}, 'channels': [{'id': 1, 'online': True}],
            'storage': [{'name': 'HDD1', 'status': 'normal'}],
        }
        response.raise_for_status.return_value = None
        get_mock.return_value = response
        device = SecurityDevice(ip='192.0.2.50', vendor='generic', api_url='http://192.0.2.50/api/status')
        result = collect_security_api(device)
        self.assertEqual(result.status, 'success')
        self.assertTrue(result.data['channel_status'][0]['online'])


class InventoryImportExportTests(TestCase):
    def setUp(self):
        login_admin(self.client)

    def test_people_csv_template_import_and_filtered_export(self):
        template_response = self.client.get(reverse('download_inventory_template', args=['people']))
        self.assertEqual(template_response.status_code, 200)
        self.assertTrue(template_response.content.startswith(b'\xef\xbb\xbf'))
        self.assertIn('姓名,工号', template_response.content.decode('utf-8-sig'))

        upload = SimpleUploadedFile(
            'people.csv',
            '姓名,工号,邮箱,部门,上级,是否在职\n张三,H10001,zhang@example.com,信息部,李经理,是\n'.encode('utf-8-sig'),
            content_type='text/csv',
        )
        response = self.client.post(reverse('import_inventory', args=['people']), {'file': upload})
        self.assertRedirects(response, reverse('item_list', args=['people']))
        self.assertTrue(People.objects.filter(employee_id='H10001', name='张三').exists())

        export_response = self.client.get(reverse('table_export', args=['people']))
        self.assertContains(export_response, 'H10001')

    def test_invalid_ip_rejects_whole_import(self):
        upload = SimpleUploadedFile(
            'servers.csv',
            'IP地址,操作系统\nnot-an-ip,Linux\n'.encode('utf-8-sig'),
            content_type='text/csv',
        )
        self.client.post(reverse('import_inventory', args=['servers']), {'file': upload})
        self.assertEqual(Server.objects.count(), 0)

    def test_protocol_settings_import_but_secrets_do_not_export(self):
        upload = SimpleUploadedFile(
            'servers.csv',
            '服务器名称,IP地址,服务器类型,管理端口,SSH账号,SSH密码,校验HTTPS证书\n'
            'LINUX-01,192.0.2.60,Linux,22,inspector,secret,是\n'.encode('utf-8-sig'),
            content_type='text/csv',
        )
        response = self.client.post(reverse('import_inventory', args=['servers']), {'file': upload})
        self.assertRedirects(response, reverse('item_list', args=['servers']))
        server = Server.objects.get(ip='192.0.2.60')
        self.assertEqual(server.username, 'inspector')
        self.assertEqual(server.password, 'secret')
        from tests import response_body
        exported = response_body(self.client.get(
            reverse('table_export', args=['servers']),
        )).decode('utf-8-sig')
        self.assertNotIn('secret', exported)

    def test_reported_computers_do_not_offer_or_accept_manual_import(self):
        list_response = self.client.get(reverse('item_list', args=['computers']))
        self.assertNotContains(list_response, '开始导入')
        self.assertContains(list_response, '导出筛选结果')

        upload = SimpleUploadedFile(
            'computers.csv',
            '计算机名,操作系统\nMANUAL-PC,Windows 11\n'.encode('utf-8-sig'),
            content_type='text/csv',
        )
        response = self.client.post(reverse('import_inventory', args=['computers']), {'file': upload})
        self.assertRedirects(response, reverse('item_list', args=['computers']))
        self.assertFalse(Computer.objects.filter(computer_name='MANUAL-PC').exists())


class ImportModalTests(TestCase):
    def setUp(self):
        login_admin(self.client)

    def _modal_markup(self, response):
        document = response.content.decode(response.charset)
        self.assertIn('id="importModal"', document)
        modal_start = document.rfind('<div', 0, document.index('id="importModal"'))
        depth = 0
        for tag in re.finditer(r'</?div\b[^>]*>', document[modal_start:], re.IGNORECASE):
            depth += -1 if tag.group().startswith('</') else 1
            if depth == 0:
                return document[modal_start:modal_start + tag.end()]
        self.fail('import modal did not close')

    def test_importable_asset_puts_only_template_and_upload_in_import_modal(self):
        response = self.client.get(reverse('item_list', args=['people']))
        document = parse_response_html(response)
        modal_markup = self._modal_markup(response)

        self.assertEqual(len(document.find('button', **{'data-bs-target': '#importModal'})), 1)
        self.assertIn(
            reverse('download_inventory_template_format', args=['people', 'csv']),
            modal_markup,
        )
        self.assertIn(
            reverse('download_inventory_template_format', args=['people', 'xlsx']),
            modal_markup,
        )
        self.assertIn(reverse('import_inventory', args=['people']), modal_markup)
        self.assertIn('enctype="multipart/form-data"', modal_markup)
        self.assertIn('name="file"', modal_markup)
        self.assertNotIn('/export/', modal_markup)
        self.assertNotIn('导出 CSV', modal_markup)
        self.assertContains(response, '导出筛选结果')

    def test_invalid_csv_import_reopens_import_modal_once_with_message(self):
        upload = SimpleUploadedFile(
            'servers.csv',
            'IP地址,操作系统\nnot-an-ip,Linux\n'.encode('utf-8-sig'),
            content_type='text/csv',
        )

        response = self.client.post(
            reverse('import_inventory', args=['servers']),
            {'file': upload},
            follow=True,
        )
        document = parse_response_html(response)

        self.assertContains(response, '导入失败：')
        self.assertTrue(response.context['open_import_modal'])
        self.assertTrue(document.find(
            'div', id='importModal', **{'data-auto-open': 'true'},
        ))

        next_response = self.client.get(reverse('item_list', args=['servers']))
        next_document = parse_response_html(next_response)
        self.assertFalse(next_response.context['open_import_modal'])
        self.assertTrue(next_document.find(
            'div', id='importModal', **{'data-auto-open': 'false'},
        ))

    def test_computers_explain_backend_log_source_without_import_modal(self):
        response = self.client.get(reverse('item_list', args=['computers']))
        document = response.content.decode(response.charset)

        self.assertFalse(response.context['import_enabled'])
        self.assertEqual(response.context['data_source_note'], 'PC 资料由终端采集器通过 API 上报日志后自动建立，无需导入设备清单。')
        self.assertNotIn('id="importModal"', document)
        self.assertContains(response, 'PC 资料由终端采集器通过 API 上报日志后自动建立，无需导入设备清单。')
        self.assertContains(response, '导出筛选结果')

    def test_domain_child_pages_offer_filtered_export_and_only_accounts_allow_import(self):
        operator = get_user_model().objects.create_user(
            username='domain-list-operator', password='test-password', is_staff=True,
        )
        operator.user_permissions.add(
            Permission.objects.get(
                content_type__app_label='net', codename='manage_domain_operations',
            ),
        )
        self.client.force_login(operator)

        for route_name, table_key in (
            ('domain_account_list', 'domain_accounts'),
            ('domain_computer_list', 'domain_computers'),
            ('domain_group_list', 'domain_groups'),
        ):
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name))
                document = response.content.decode(response.charset)

                self.assertIn(reverse('table_export', args=[table_key]), document)
                self.assertEqual('id="domainAccountImportModal"' in document,
                                 route_name == 'domain_account_list')
                self.assertNotIn('download_inventory_template', document)


class DomainControllerSettingsTests(TestCase):
    def setUp(self):
        self.operator = get_user_model().objects.create_user(
            username='domain-settings-operator', password='test-password', is_staff=True,
        )
        self.operator.user_permissions.add(
            Permission.objects.get(
                content_type__app_label='net', codename='manage_domain_operations',
            ),
        )
        self.client.force_login(self.operator)

    def test_ldap_single_value_lists_are_normalized(self):
        from net.domain.sync import _entry_attributes
        attrs = _entry_attributes({'attributes': {
            'userAccountControl': ['512'], 'displayName': [], 'memberOf': ['A', 'B'],
        }})
        self.assertEqual(attrs['userAccountControl'], '512')
        self.assertIsNone(attrs['displayName'])
        self.assertEqual(attrs['memberOf'], ['A', 'B'])

    def test_domain_form_rejects_implicit_ldaps_on_ldap_port(self):
        form = DomainControllerConfigForm(data={
            'name': '公司域控', 'host': 'dc.example.com', 'port': 389, 'use_ssl': 'on',
            'base_dn': 'DC=example,DC=com', 'bind_username': 'sync', 'bind_password': 'password',
            'user_filter': '(&(objectCategory=person)(objectClass=user))',
            'computer_filter': '(objectCategory=computer)',
            'group_filter': '(objectCategory=group)',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('port', form.errors)

    @patch('ldap3.Connection')
    @patch('ldap3.Server')
    def test_bare_ad_username_is_converted_to_upn(self, server_mock, connection_mock):
        config = Domain_Controller_Config(
            host='dc.example.com', port=636, use_ssl=True,
            base_dn='DC=example,DC=com', bind_username='sync', bind_password='password',
        )
        from net.domain.sync import _connect
        _connect(config)
        self.assertEqual(connection_mock.call_args.kwargs['user'], 'sync@example.com')

    def test_domain_settings_page_renders(self):
        response = self.client.get(reverse('domain_controller_settings'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '测试连接')
        self.assertContains(response, '域账号')
        self.assertContains(response, '域计算机')
        self.assertNotContains(response, '开始导入')

    @patch('index.domain.views.test_domain_connection', return_value='连接成功')
    def test_domain_config_can_be_saved_and_tested(self, connection_mock):
        response = self.client.post(reverse('domain_controller_settings'), {
            'name': '公司域控', 'host': 'dc.example.com', 'port': 389,
            'base_dn': 'DC=example,DC=com', 'bind_username': 'EXAMPLE\\sync',
            'bind_password': 'password', 'user_filter': '(&(objectCategory=person)(objectClass=user))',
            'computer_filter': '(objectCategory=computer)',
            'group_filter': '(objectCategory=group)', 'action': 'test',
        })
        self.assertRedirects(response, reverse('domain_controller_settings') + '?modal=1')
        self.assertTrue(self.client.get(response.url).context['open_domain_modal'])
        self.assertTrue(Domain_Controller_Config.objects.filter(host='dc.example.com').exists())
        connection_mock.assert_called_once()

    @patch('net.domain.sync._connect', side_effect=AssertionError('HTTP must only queue synchronization'))
    def test_domain_sync_action_queues_task(self, connect_mock):
        response = self.client.post(reverse('domain_controller_settings'), {
            'name': '公司域控', 'host': 'dc.example.com', 'port': 389,
            'base_dn': 'DC=example,DC=com', 'bind_username': 'EXAMPLE\\sync',
            'bind_password': 'password', 'user_filter': '(&(objectCategory=person)(objectClass=user))',
            'computer_filter': '(objectCategory=computer)',
            'group_filter': '(objectCategory=group)', 'action': 'sync',
        })
        self.assertRedirects(response, reverse('domain_controller_settings') + '?modal=1')
        task = TaskRun.objects.get(task_type='domain_sync')
        self.assertEqual(task.status, 'queued')
        self.assertEqual(task.source, 'manual')
        target = task.target_runs.get()
        self.assertEqual(target.target_type, 'domain_config')
        self.assertEqual(target.target_id, '1')
        connect_mock.assert_not_called()

    @patch('net.domain.sync._connect')
    def test_domain_entries_are_mapped_to_accounts_and_computers(self, connect_mock):
        connection = connect_mock.return_value
        connection.extend.standard.paged_search.side_effect = [
            iter([{'type': 'searchResEntry', 'attributes': {
                'displayName': ['域用户'], 'sAMAccountName': ['domain.user'],
                'userAccountControl': ['512'], 'distinguishedName': ['CN=域用户,OU=Users,DC=example,DC=com'],
                'userWorkstations': ['PC-AD'], 'lastLogonTimestamp': ['0'],
            }}]),
            iter([{'type': 'searchResEntry', 'attributes': {
                'name': ['PC-AD'], 'operatingSystem': ['Windows 11'],
                'userAccountControl': ['4096'], 'distinguishedName': ['CN=PC-AD,OU=Computers,DC=example,DC=com'],
                'lastLogonTimestamp': ['0'],
            }}]),
            iter([]),
            iter([]),  # organizational units, including empty OUs
        ]
        config = Domain_Controller_Config(
            host='dc.example.com', base_dn='DC=example,DC=com',
            bind_username='EXAMPLE\\sync', bind_password='password',
        )
        counts = sync_domain(config)
        self.assertEqual(counts, (1, 1, 0))
        self.assertTrue(Domain_Account.objects.filter(login_name='domain.user', ou__contains='OU=Users').exists())
        self.assertTrue(Domain_Computer.objects.filter(computer_name='PC-AD', os='Windows 11').exists())
        connection.unbind.assert_called_once()


class DomainWorkspaceTests(TestCase):
    def setUp(self):
        login_reader(self.client)
        self.account = Domain_Account.objects.create(
            account_name='域账号', login_name='domain.user', is_active=True,
        )
        self.domain_computer = Domain_Computer.objects.create(
            computer_name='DOMAIN-PC', os='Windows 11', is_active=True,
        )

    def test_domain_overview_links_to_separate_tables(self):
        login_admin(self.client)
        response = self.client.get(reverse('domain_controller_settings'))

        self.assertNotContains(response, '<table', html=False)
        self.assertContains(response, reverse('domain_account_list'))
        self.assertContains(response, reverse('domain_computer_list'))
        self.assertContains(response, reverse('domain_group_list'))
        self.assertContains(response, 'id="domainConfigModal"')

    def test_domain_account_and_computer_routes_are_separate(self):
        account_response = self.client.get(reverse('domain_account_list'))
        computer_response = self.client.get(reverse('domain_computer_list'))

        self.assertContains(account_response, self.account.login_name)
        self.assertNotContains(account_response, self.domain_computer.computer_name)
        self.assertContains(computer_response, self.domain_computer.computer_name)
        self.assertNotContains(computer_response, self.account.login_name)

    def test_domain_account_export_link_returns_csv(self):
        account_response = self.client.get(reverse('domain_account_list'))
        document = parse_response_html(account_response)
        export_links = [
            attributes['href']
            for attributes in document.find('a')
            if 'data-filtered-export' in attributes
        ]
        self.assertEqual(len(export_links), 1)

        self.client.raise_request_exception = False
        export_response = self.client.get(export_links[0])

        self.assertEqual(export_response.status_code, 200)
        self.assertTrue(export_response['Content-Type'].startswith('text/csv'))

    def test_domain_account_route_uses_registered_filtering_and_pagination(self):
        inactive = Domain_Account.objects.create(
            account_name='停用账号', login_name='inactive.user', is_active=False,
        )
        Domain_Account.objects.bulk_create([
            Domain_Account(account_name=f'账号{number}', login_name=f'user{number:03d}')
            for number in range(20)
        ])

        response = self.client.get(reverse('domain_account_list'), {
            'filter_is_active': 'false', 'page_size': '20',
        })

        self.assertEqual(list(response.context['page_obj'].object_list), [inactive])
        self.assertEqual(response.context['table_definition'].key, 'domain_accounts')

    def test_invalid_domain_settings_submission_reopens_modal_with_errors(self):
        response = self.client.post(reverse('domain_controller_settings'), {'name': ''})

        self.assertEqual(response.status_code, 403)

    def test_unauthorized_overview_sync_does_not_queue_task(self):
        response = self.client.post(reverse('domain_controller_settings'), {'action': 'sync'})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(TaskRun.objects.exists())


class TableFilteringAndSortingTests(TestCase):
    def setUp(self):
        login_reader(self.client)

    def test_asset_list_filters_and_sorts_people(self):
        People.objects.create(name='张三', employee_id='H200', department='技术部', is_active=True)
        People.objects.create(name='李四', employee_id='H100', department='财务部', is_active=False)
        People.objects.create(name='王五', employee_id='H300', department='技术部', is_active=True)

        response = self.client.get(reverse('item_list', args=['people']), {
            'q': '技术部', 'filter_is_active': 'true',
            'sort': 'employee_id', 'order': 'desc',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual([obj.employee_id for obj in response.context['page_obj'].object_list], ['H300', 'H200'])
        self.assertContains(response, 'value="技术部"')

    def test_invalid_asset_sort_field_is_ignored(self):
        People.objects.create(name='安全测试', employee_id='H1')
        response = self.client.get(reverse('item_list', args=['people']), {'sort': 'password'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('table_state', response.context)
        self.assertEqual(response.context['table_state']['sort'], 'name')

    def test_infrastructure_asset_lists_ignore_dynamic_record_query_keys(self):
        cases = (
            (
                'networks', Network_Device, Network_Device_Inspection, 'device',
                'device_name', '192.0.2.71',
            ),
            (
                'servers', Server, Server_Inspection, 'server',
                'name', '192.0.2.81',
            ),
            (
                'monitors', SecurityDevice, Monitor_Inspection, 'monitor',
                'device_name', '192.0.2.91',
            ),
        )
        for item, model, inspection_model, foreign_key, name_field, ip_prefix in cases:
            with self.subTest(item=item):
                normal = model.objects.create(
                    **{name_field: f'{item}-正常', 'ip': ip_prefix},
                )
                abnormal = model.objects.create(
                    **{name_field: f'{item}-异常', 'ip': ip_prefix[:-1] + '2'},
                )
                unchecked = model.objects.create(
                    **{name_field: f'{item}-未巡检', 'ip': ip_prefix[:-1] + '3'},
                )
                inspection_model.objects.create(
                    **{foreign_key: normal},
                    is_reachable=True,
                    status='success',
                )
                inspection_model.objects.create(
                    **{foreign_key: abnormal},
                    is_reachable=True,
                    status='failed',
                )

                filtered_response = self.client.get(
                    reverse('item_list', args=[item]),
                    {'filter_latest_status': 'abnormal'},
                )
                sorted_response = self.client.get(
                    reverse('item_list', args=[item]),
                    {'sort': 'latest_status', 'order': 'asc'},
                )

                self.assertEqual(
                    set(filtered_response.context['page_obj'].object_list),
                    {normal, abnormal, unchecked},
                )
                self.assertNotIn(
                    'latest_status', filtered_response.context['table_state']['filters'],
                )
                self.assertEqual(
                    sorted_response.context['table_state']['sort'],
                    'device_name' if item != 'servers' else 'name',
                )
                self.assertIn(unchecked, sorted_response.context['page_obj'].object_list)

    def test_domain_tables_have_independent_filters_and_sorting(self):
        Domain_Account.objects.create(account_name='测试甲', login_name='user.b', is_active=True, ou='OU=技术部')
        Domain_Account.objects.create(account_name='测试乙', login_name='user.a', is_active=False, ou='OU=财务部')
        Domain_Computer.objects.create(computer_name='PC-Z', os='Windows 11', is_active=True)
        Domain_Computer.objects.create(computer_name='PC-A', os='Windows 10', is_active=False)

        account_response = self.client.get(reverse('domain_account_list'), {
            'q': '测试', 'filter_is_active': 'false',
        })
        computer_response = self.client.get(reverse('domain_computer_list'), {
            'q': 'PC', 'sort': 'computer_name', 'order': 'desc',
        })
        self.assertEqual([obj.login_name for obj in account_response.context['page_obj'].object_list], ['user.a'])
        self.assertEqual(
            [obj.computer_name for obj in computer_response.context['page_obj'].object_list],
            ['PC-Z', 'PC-A'],
        )

    def test_inspection_records_filter_and_sort_all_device_records(self):
        older = create_computer_analysis('PC-OLD', user_name='甲')
        newer = create_computer_analysis('PC-NEW', user_name='乙')
        ComputerAnalysis.objects.filter(pk=older.pk).update(
            created_at=timezone.now() - timedelta(days=1),
        )
        response = self.client.get(reverse('inspection_records'), {
            'q': 'PC', 'category': 'PC 分析', 'sort': 'asset', 'order': 'asc',
        })
        assets = [record['asset'] for record in response.context['page_obj'].object_list]
        self.assertEqual(assets, ['PC-NEW', 'PC-OLD'])

    def test_inspection_records_keeps_legacy_status_sort_key(self):
        normal = create_computer_analysis('PC-NORMAL')
        abnormal = create_computer_analysis('PC-ABNORMAL')
        Error_Computer.objects.create(
            inspection=abnormal, error_type='测试异常', error_message='异常',
        )

        response = self.client.get(reverse('inspection_records'), {
            'sort': 'status', 'order': 'asc',
        })

        self.assertEqual(
            [record['asset'] for record in response.context['page_obj'].object_list],
            ['PC-ABNORMAL', 'PC-NORMAL'],
        )


class TableRegistryTests(TestCase):
    def setUp(self):
        login_reader(self.client)

    def test_computer_registry_defaults_show_key_snapshot_fields(self):
        response = self.client.get(reverse('item_list', args=['computers']))

        visible_fields = [
            field.key
            for field in response.context['table_state']['default_visible_fields']
        ]
        self.assertEqual(visible_fields, [
            'computer_name', 'ip_addresses', 'os', 'user_name',
            'last_report_at',
        ])

    def test_computer_registry_exposes_snapshot_fields_without_credentials(self):
        response = self.client.get(reverse('item_list', args=['computers']))

        fields = {
            field.key
            for field in response.context['table_state']['field_definitions']
        }
        self.assertTrue({
            'ip_addresses', 'mac_addresses', 'os_version',
            'os_build', 'system_installed_at',
        }.issubset(fields))
        self.assertFalse({
            'last_boot_at', 'cpu_temperature', 'cpu_usage',
            'memory_total', 'memory_usage',
        } & fields)
        self.assertNotIn('password', fields)
        self.assertNotIn('api_token', fields)
        self.assertNotIn('login_account', fields)

    def test_infrastructure_registry_contains_only_static_asset_fields(self):
        for item in ('networks', 'servers', 'monitors'):
            with self.subTest(item=item):
                response = self.client.get(reverse('item_list', args=[item]))
                document = parse_response_html(response)
                fields = {
                    field.key: field
                    for field in response.context['table_state']['field_definitions']
                }

                self.assertFalse({'latest_status', 'latest_inspected_at'} & fields.keys())
                self.assertFalse(document.find(
                    'th', **{'data-column-key': 'latest_status'},
                ))
                self.assertFalse(document.find(
                    'input',
                    **{'data-column-toggle': '', 'data-column-key': 'latest_status'},
                ))
                self.assertFalse(document.find('select', name='filter_latest_status'))
                self.assertFalse(document.find('select', name='status'))


class VisualStructureTests(TestCase):
    def setUp(self):
        login_admin(self.client)

    @staticmethod
    def _elements_with_class(document, tag, class_name):
        return [
            attrs
            for element_tag, attrs in document.elements
            if element_tag == tag
            and class_name in attrs.get('class', '').split()
        ]

    def test_workspace_pages_share_landmarks_and_page_heading_structure(self):
        responses = [
            self.client.get(reverse('index')),
            self.client.get(reverse('item_list', args=['people'])),
            self.client.get(reverse('computer_analysis_list')),
            self.client.get(reverse('domain_controller_settings')),
            self.client.get(reverse('domain_account_list')),
            self.client.get(reverse('inspection_records')),
            self.client.get(reverse('error_records')),
            self.client.get(reverse('computer_analysis_list')),
            self.client.get(reverse('computer_error_list')),
        ]

        for response in responses:
            with self.subTest(path=response.request['PATH_INFO']):
                document = parse_response_html(response)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    len(document.find('nav', **{'aria-label': '主导航'})), 1,
                )
                self.assertEqual(
                    len(document.find('main', id='main-content')), 1,
                )
                self.assertEqual(
                    len(self._elements_with_class(document, 'header', 'page-heading')), 1,
                )
                self.assertEqual(
                    len(self._elements_with_class(document, 'h1', 'page-heading__title')), 1,
                )

    def test_workspace_components_expose_shared_visual_classes(self):
        Network_Device.objects.create(device_name='SW-VISUAL', ip='192.0.2.88')

        dashboard = parse_response_html(self.client.get(reverse('index')))
        asset_page = parse_response_html(
            self.client.get(reverse('item_list', args=['networks'])),
        )
        empty_page = parse_response_html(
            self.client.get(reverse('item_list', args=['people'])),
        )

        self.assertTrue(self._elements_with_class(dashboard, 'section', 'metric-card'))
        self.assertTrue(self._elements_with_class(dashboard, 'section', 'surface-card'))
        self.assertTrue(self._elements_with_class(asset_page, 'div', 'table-toolbar'))
        self.assertTrue(asset_page.find('a', href=reverse(
            'asset_detail', args=['networks', Network_Device.objects.get().pk],
        )))
        self.assertTrue(self._elements_with_class(empty_page, 'div', 'empty-state'))

    def test_dedicated_computer_record_pages_use_shared_table_surfaces(self):
        for url in (analysis_task_url(), reverse('computer_error_list')):
            with self.subTest(url=url):
                document = parse_response_html(self.client.get(url))
                self.assertTrue(
                    self._elements_with_class(document, 'div', 'table-toolbar'),
                )
                self.assertTrue(
                    self._elements_with_class(document, 'div', 'surface-card'),
                )
                sort_links = [
                    attrs
                    for tag, attrs in document.elements
                    if tag == 'a' and 'data-sort-key' in attrs
                ]
                self.assertTrue(sort_links)
                self.assertTrue(all(link.get('aria-label') for link in sort_links))

    def test_navigation_and_modals_have_accessible_names(self):
        asset_document = parse_response_html(
            self.client.get(reverse('item_list', args=['people'])),
        )
        domain_document = parse_response_html(
            self.client.get(reverse('domain_controller_settings')),
        )

        self.assertEqual(len(asset_document.find(
            'button',
            **{
                'data-bs-target': '#mainNav',
                'aria-controls': 'mainNav',
                'aria-expanded': 'false',
                'aria-label': '切换主导航',
            },
        )), 1)
        self.assertEqual(len(asset_document.find(
            'div',
            id='importModal',
            role='dialog',
            **{
                'aria-modal': 'true',
                'aria-labelledby': 'importModalLabel',
                'aria-describedby': 'importModalDescription',
            },
        )), 1)
        self.assertEqual(len(domain_document.find(
            'div',
            id='domainConfigModal',
            role='dialog',
            **{
                'aria-modal': 'true',
                'aria-labelledby': 'domainConfigModalLabel',
                'aria-describedby': 'domainConfigModalDescription',
            },
        )), 1)

    def test_sortable_links_announce_direction_and_active_sort_state(self):
        response = self.client.get(reverse('item_list', args=['people']), {
            'sort': 'name', 'order': 'asc',
        })
        document = parse_response_html(response)

        self.assertEqual(len(document.find(
            'th', **{'data-column-key': 'name', 'aria-sort': 'ascending'},
        )), 1)
        self.assertEqual(len(document.find(
            'a',
            **{
                'data-sort-key': 'employee_id',
                'aria-label': '按工号升序排列',
            },
        )), 1)

    def test_action_urls_and_homepage_overview_tables_are_preserved(self):
        run_url = reverse('run_infrastructure_inspection')
        manual_task_url = reverse('manual_task_create')
        dashboard = self.client.get(reverse('index'))
        network_page = self.client.get(reverse('item_list', args=['networks']))
        people_page = self.client.get(reverse('item_list', args=['people']))
        dashboard_document = parse_response_html(dashboard)
        network_document = parse_response_html(network_page)
        people_document = parse_response_html(people_page)

        self.assertFalse(dashboard_document.find('form', method='post', action=run_url))
        self.assertTrue(dashboard_document.find('a', href='/assets/networks/?task_modal=run'))
        self.assertTrue(network_document.find(
            'form', id='manualTaskForm', method='post', action=manual_task_url,
        ))
        self.assertTrue(people_document.find(
            'form',
            method='post',
            action=reverse('import_inventory', args=['people']),
        ))
        self.assertTrue(people_document.find(
            'a', href=reverse('table_export', args=['people']),
        ))
        self.assertFalse(dashboard_document.find('div', **{'data-table-workspace': ''}))
        self.assertFalse(dashboard_document.find('input', **{'data-column-toggle': ''}))


class TableWorkspaceTemplateTests(TestCase):
    def setUp(self):
        login_admin(self.client)

    def test_asset_page_renders_configurable_workspace(self):
        People.objects.create(
            name='张三', employee_id='H100', department='技术部', leader='李经理',
        )

        response = self.client.get(reverse('item_list', args=['people']))
        document = parse_response_html(response)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(document.find('div', **{'data-table-workspace': ''}))
        self.assertTrue(document.find('div', **{'data-table-key': 'people'}))
        self.assertContains(response, '自定义表格')
        self.assertTrue(document.find('button', **{'data-table-reset': ''}))
        self.assertTrue(document.find('select', **{'data-page-size': ''}))
        self.assertTrue(document.find('div', **{'data-filter-key': 'department'}))

        fields = response.context['table_state']['field_definitions']
        for field in fields:
            selectors = document.find(
                'input',
                **{'data-column-toggle': '', 'data-column-key': field.key},
            )
            self.assertEqual(len(selectors), 1, field.key)
            self.assertEqual('checked' in selectors[0], field.default_visible, field.key)
            cells = document.find('th', **{'data-column-key': field.key})
            self.assertEqual(len(cells), 1, field.key)
            self.assertEqual('hidden' in cells[0], not field.default_visible, field.key)

    def test_only_registry_default_columns_are_initially_visible(self):
        Computer.objects.create(computer_name='PC-001', mac_addresses='00-00-5E-00-53-01')

        response = self.client.get(reverse('item_list', args=['computers']))
        document = parse_response_html(response)

        default_headers = document.find('th', **{'data-column-key': 'computer_name'})
        optional_headers = document.find('th', **{'data-column-key': 'mac_addresses'})
        default_toggles = document.find(
            'input', **{'data-column-toggle': '', 'data-column-key': 'computer_name'},
        )
        optional_toggles = document.find(
            'input', **{'data-column-toggle': '', 'data-column-key': 'mac_addresses'},
        )

        self.assertTrue(default_headers)
        self.assertTrue(optional_headers)
        self.assertTrue(default_toggles)
        self.assertTrue(optional_toggles)
        default_header = default_headers[0]
        optional_header = optional_headers[0]
        default_toggle = default_toggles[0]
        optional_toggle = optional_toggles[0]

        self.assertNotIn('hidden', default_header)
        self.assertIn('checked', default_toggle)
        self.assertIn('hidden', optional_header)
        self.assertNotIn('checked', optional_toggle)
        self.assertIn('hidden', document.find('td', **{'data-column-key': 'mac_addresses'})[0])

    def test_pc_datetime_field_is_formatted_without_enabled_state(self):
        Computer.objects.create(
            computer_name='PC-001',
            is_active=False,
            last_report_at=timezone.make_aware(datetime(2026, 8, 30, 9, 5, 7)),
        )

        response = self.client.get(reverse('item_list', args=['computers']))

        self.assertNotContains(response, 'data-column-key="is_active"')
        self.assertContains(
            response,
            '<td data-column-key="last_report_at">2026-08-30 09:05:07</td>',
            html=True,
        )

    def test_registered_verify_ssl_field_is_rendered_in_server_workspace(self):
        Server.objects.create(name='安全服务器', ip='192.0.2.201', verify_ssl=True)

        response = self.client.get(reverse('item_list', args=['servers']))
        document = parse_response_html(response)

        self.assertTrue(document.find('th', **{'data-column-key': 'verify_ssl'}))
        self.assertTrue(document.find(
            'input', **{'data-column-toggle': '', 'data-column-key': 'verify_ssl'},
        ))
        self.assertTrue(document.find('div', **{'data-filter-key': 'verify_ssl'}))

    def test_active_filter_uses_registry_without_legacy_status_options(self):
        People.objects.create(name='在职人员', employee_id='H100', is_active=True)
        inactive = People.objects.create(name='离职人员', employee_id='H200', is_active=False)

        response = self.client.get(reverse('item_list', args=['people']), {
            'filter_is_active': 'false',
        })
        document = parse_response_html(response)

        self.assertNotIn('status_options', response.context)
        self.assertFalse(document.find('select', name='status'))
        self.assertEqual(len(document.find('select', name='filter_is_active')), 1)
        self.assertContains(response, '<option value="false" selected>否</option>', html=True)
        self.assertEqual(list(response.context['page_obj'].object_list), [inactive])

    def test_sortable_headers_preserve_active_query_parameters(self):
        People.objects.create(name='张三', employee_id='H100', department='技术部')

        response = self.client.get(reverse('item_list', args=['people']), {
            'q': '张',
            'filter_department': '技术部',
            'page_size': '50',
            'sort': 'name',
            'order': 'asc',
        })
        document = parse_response_html(response)
        sort_links = document.find('a', **{'data-sort-key': 'employee_id'})

        self.assertTrue(sort_links)
        sort_link = sort_links[0]
        query = parse_qs(urlsplit(sort_link['href']).query)

        self.assertEqual(query, {
            'q': ['张'],
            'filter_department': ['技术部'],
            'page_size': ['50'],
            'sort': ['employee_id'],
            'order': ['asc'],
        })

    def test_pagination_links_include_effective_page_size(self):
        People.objects.bulk_create([
            People(name=f'人员{number}', employee_id=f'P{number:03d}')
            for number in range(21)
        ])

        response = self.client.get(reverse('item_list', args=['people']))
        document = parse_response_html(response)
        next_links = document.find('a', **{'data-page': 'next'})

        self.assertTrue(next_links)
        next_link = next_links[0]
        self.assertEqual(parse_qs(urlsplit(next_link['href']).query), {
            'page': ['2'],
            'page_size': ['20'],
        })
        self.assertContains(
            response,
            '<span class="page-link" aria-current="page">1</span>',
            html=True,
        )

    def test_workspace_reports_total_and_current_result_range(self):
        People.objects.bulk_create([
            People(name=f'人员{number}', employee_id=f'P{number:03d}')
            for number in range(21)
        ])

        response = self.client.get(
            reverse('item_list', args=['people']),
            {'page': '2'},
        )

        self.assertContains(response, '共 21 条，当前显示第 21–21 条')
        document = parse_response_html(response)
        self.assertEqual(len(document.find('div', **{'data-table-result-bar': ''})), 1)
        self.assertEqual(len(document.find('select', **{'data-page-size': ''})), 1)

    def test_domain_list_omits_obsolete_read_only_sync_message(self):
        response = self.client.get(reverse('domain_account_list'))

        self.assertNotContains(response, '由域控同步维护，只读查看。')

    def test_workspace_reports_explicit_zero_results(self):
        response = self.client.get(reverse('item_list', args=['people']))

        self.assertContains(response, '共 0 条，当前无结果')


class DynamicTableQueryTests(TestCase):
    def setUp(self):
        login_reader(self.client)
        self.ops_user = People.objects.create(
            name='运维人员', employee_id='H200', department='运维', is_active=True,
        )
        People.objects.create(
            name='运维离职人员', employee_id='H300', department='运维', is_active=False,
        )
        People.objects.create(
            name='运维中心人员', employee_id='H100', department='运维中心', is_active=True,
        )

    def test_dynamic_field_filters_apply_to_whole_queryset(self):
        response = self.client.get(reverse('item_list', args=['people']), {
            'filter_department': '运维',
            'filter_is_active': 'true',
            'sort': 'employee_id',
            'order': 'desc',
            'page_size': '50',
        })

        self.assertEqual(list(response.context['page_obj'].object_list), [self.ops_user])
        self.assertEqual(response.context['table_state']['page_size'], 50)

    def test_text_filter_uses_registered_field_source(self):
        response = self.client.get(reverse('item_list', args=['people']), {
            'filter_name': '中心',
        })

        self.assertEqual(
            [person.employee_id for person in response.context['page_obj'].object_list],
            ['H100'],
        )

    def test_date_range_filter_uses_registered_date_field(self):
        Domain_Account.objects.create(
            account_name='旧账号', login_name='old.user', last_login_date=date(2026, 1, 1),
        )
        Domain_Account.objects.create(
            account_name='新账号', login_name='new.user', last_login_date=date(2026, 1, 15),
        )

        response = self.client.get(reverse('domain_account_list'), {
            'filter_last_login_date_from': '2026-01-10',
            'filter_last_login_date_to': '2026-01-20',
        })

        self.assertEqual(
            [account.login_name for account in response.context['page_obj'].object_list],
            ['new.user'],
        )

    def test_datetime_date_range_includes_the_requested_end_date(self):
        Computer.objects.create(
            computer_name='PC-IN-RANGE',
            last_report_at=timezone.make_aware(datetime(2026, 1, 15, 12, 0)),
        )
        Computer.objects.create(
            computer_name='PC-OUT-RANGE',
            last_report_at=timezone.make_aware(datetime(2026, 1, 16, 0, 0)),
        )

        response = self.client.get(reverse('item_list', args=['computers']), {
            'filter_last_report_at_from': '2026-01-15',
            'filter_last_report_at_to': '2026-01-15',
        })

        self.assertEqual(
            [computer.computer_name for computer in response.context['page_obj'].object_list],
            ['PC-IN-RANGE'],
        )

    def test_impossible_date_filter_is_ignored_without_server_error(self):
        Domain_Account.objects.create(
            account_name='日期账号', login_name='date.user',
            last_login_date=date(2026, 2, 1),
        )
        self.client.raise_request_exception = False

        response = self.client.get(reverse('domain_account_list'), {
            'filter_last_login_date_from': '2026-02-31',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [account.login_name for account in response.context['page_obj'].object_list],
            ['date.user'],
        )
        self.assertNotIn('last_login_date', response.context['table_state']['filters'])

    def test_valid_date_bound_remains_active_when_other_bound_is_impossible(self):
        Domain_Account.objects.create(
            account_name='较早账号', login_name='older.user',
            last_login_date=date(2026, 1, 5),
        )
        Domain_Account.objects.create(
            account_name='较新账号', login_name='newer.user',
            last_login_date=date(2026, 1, 20),
        )
        cases = (
            (
                {
                    'filter_last_login_date_from': '2026-01-10',
                    'filter_last_login_date_to': '2026-02-31',
                },
                ['newer.user'],
            ),
            (
                {
                    'filter_last_login_date_from': '2026-02-31',
                    'filter_last_login_date_to': '2026-01-10',
                },
                ['older.user'],
            ),
        )

        for query, expected_logins in cases:
            with self.subTest(query=query):
                response = self.client.get(reverse('domain_account_list'), query)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    [
                        account.login_name
                        for account in response.context['page_obj'].object_list
                    ],
                    expected_logins,
                )

    def test_unregistered_filter_and_sort_are_ignored(self):
        response = self.client.get(reverse('item_list', args=['people']), {
            'filter_password': 'secret', 'sort': 'password', 'page_size': '9999',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['table_state']['sort'], 'name')
        self.assertEqual(response.context['table_state']['page_size'], 500)

    def test_server_verify_ssl_is_visible_and_filters_as_boolean(self):
        secure = Server.objects.create(name='安全服务器', ip='192.0.2.201', verify_ssl=True)
        Server.objects.create(name='非安全服务器', ip='192.0.2.202', verify_ssl=False)

        response = self.client.get(reverse('item_list', args=['servers']), {
            'filter_verify_ssl': 'true',
        })

        fields = [
            field for field in response.context['table_state']['field_definitions']
            if field.key == 'verify_ssl'
        ]
        self.assertEqual(len(fields), 1)
        if not fields:
            return
        field = fields[0]
        self.assertEqual(list(response.context['page_obj'].object_list), [secure])
        self.assertEqual(
            (field.kind, field.default_visible, field.default_filter, field.sortable),
            ('boolean', True, True, True),
        )

    def test_monitor_verify_ssl_is_visible_and_filters_as_boolean(self):
        secure = SecurityDevice.objects.create(device_name='安全监控', ip='192.0.2.203', verify_ssl=True)
        SecurityDevice.objects.create(device_name='非安全监控', ip='192.0.2.204', verify_ssl=False)

        response = self.client.get(reverse('item_list', args=['monitors']), {
            'filter_verify_ssl': 'true',
        })

        fields = [
            field for field in response.context['table_state']['field_definitions']
            if field.key == 'verify_ssl'
        ]
        self.assertEqual(len(fields), 1)
        if not fields:
            return
        field = fields[0]
        self.assertEqual(list(response.context['page_obj'].object_list), [secure])
        self.assertEqual(
            (field.kind, field.default_visible, field.default_filter, field.sortable),
            ('boolean', True, True, True),
        )


class TablePaginationTests(TestCase):
    def setUp(self):
        login_reader(self.client)

    def test_table_page_size_supports_large_operational_views(self):
        People.objects.bulk_create([
            People(name=f'人员{number}', employee_id=f'P-LARGE-{number:03d}')
            for number in range(21)
        ])

        response = self.client.get(
            reverse('item_list', args=['people']), {'page_size': '500'},
        )

        self.assertEqual(response.context['table_state']['page_size'], 500)
        self.assertContains(response, '<option value="500" selected>500</option>', html=True)

    def test_asset_page_size_controls_page_object_count(self):
        People.objects.bulk_create([
            People(name=f'人员{number}', employee_id=f'P{number:03d}')
            for number in range(51)
        ])

        response = self.client.get(reverse('item_list', args=['people']), {'page_size': '50'})

        self.assertEqual(len(response.context['page_obj'].object_list), 50)

    def test_domain_page_sizes_control_their_own_page_objects(self):
        Domain_Account.objects.bulk_create([
            Domain_Account(account_name=f'账户{number}', login_name=f'account{number:03d}')
            for number in range(51)
        ])
        Domain_Computer.objects.bulk_create([
            Domain_Computer(computer_name=f'DOMAIN-PC-{number:03d}')
            for number in range(101)
        ])

        account_response = self.client.get(reverse('domain_account_list'), {'page_size': '50'})
        computer_response = self.client.get(reverse('domain_computer_list'), {'page_size': '100'})

        self.assertEqual(len(account_response.context['page_obj'].object_list), 50)
        self.assertEqual(len(computer_response.context['page_obj'].object_list), 100)

    def test_inspection_record_page_size_controls_page_object_count(self):
        [
            create_computer_analysis(f'RECORD-PC-{number:03d}')
            for number in range(100)
        ]

        response = self.client.get(reverse('inspection_records'), {'page_size': '100'})

        self.assertEqual(len(response.context['page_obj'].object_list), 100)

    def test_error_record_page_size_controls_page_object_count(self):
        inspections = [
            create_computer_analysis(f'ERROR-PC-{number:03d}')
            for number in range(51)
        ]
        Error_Computer.objects.bulk_create([
            Error_Computer(inspection=inspection, error_type='测试异常', error_message='异常')
            for inspection in inspections
        ])

        response = self.client.get(reverse('error_records'), {'page_size': '50'})

        self.assertEqual(len(response.context['page_obj'].object_list), 50)

    def test_aggregate_inspection_search_includes_record_older_than_100_boundary(self):
        target = create_computer_analysis('BOUNDARY-INSPECTION')
        [
            create_computer_analysis(f'NEW-PC-{number:03d}')
            for number in range(100)
        ]
        ComputerAnalysis.objects.filter(pk=target.pk).update(
            created_at=timezone.now() - timedelta(days=30),
        )

        response = self.client.get(reverse('inspection_records'), {
            'q': 'BOUNDARY-INSPECTION',
        })

        self.assertEqual(
            [record['asset'] for record in response.context['page_obj'].object_list],
            ['BOUNDARY-INSPECTION'],
        )

    def test_aggregate_error_search_includes_record_older_than_100_boundary(self):
        target = create_computer_analysis('BOUNDARY-ERROR')
        Error_Computer.objects.create(
            inspection=target,
            error_type='边界异常',
            error_message='目标历史异常',
        )
        newer = [
            create_computer_analysis(f'NEW-ERROR-PC-{number:03d}')
            for number in range(100)
        ]
        Error_Computer.objects.bulk_create([
            Error_Computer(
                inspection=inspection,
                error_type='测试异常',
                error_message='较新异常',
            )
            for inspection in newer
        ])
        ComputerAnalysis.objects.filter(pk=target.pk).update(
            created_at=timezone.now() - timedelta(days=30),
        )

        response = self.client.get(reverse('error_records'), {
            'q': 'BOUNDARY-ERROR',
        })

        self.assertEqual(
            [record['asset'] for record in response.context['page_obj'].object_list],
            ['BOUNDARY-ERROR'],
        )


class RecordTableDefinitionTests(TestCase):
    def setUp(self):
        login_reader(self.client)

    @patch('index.inspections.records._inspection_records', return_value=[])
    def test_empty_inspection_list_keeps_its_explicit_definition(self, inspection_records_mock):
        response = self.client.get(reverse('inspection_records'))

        fields = {
            field.key for field in response.context['table_state']['field_definitions']
        }
        self.assertIn('status', fields)
        self.assertNotIn('type', fields)


class RecordWorkspaceTests(TestCase):
    def setUp(self):
        login_reader(self.client)

    def test_computer_record_list_does_not_duplicate_analysis_with_multiple_errors(self):
        analysis = create_computer_analysis('PC-MULTI-ERROR')
        Error_Computer.objects.create(
            inspection=analysis,
            error_type='磁盘异常',
            error_message='空间不足',
        )
        Error_Computer.objects.create(
            inspection=analysis,
            error_type='网络异常',
            error_message='连接超时',
        )

        response = self.client.get(analysis_task_url(analysis))

        self.assertEqual(list(response.context['page_obj'].object_list), [analysis])

    def test_inspection_dictionary_filters_use_registered_comparisons(self):
        records = [
            {
                'category': '计算机', 'asset': '目标-B', 'ok': False,
                'summary': '发现异常',
                'time': timezone.make_aware(datetime(2026, 8, 29, 10, 0)),
                'url': '/inspection/b/',
            },
            {
                'category': '计算机', 'asset': '目标-A', 'ok': False,
                'summary': '发现异常',
                'time': timezone.make_aware(datetime(2026, 8, 29, 9, 0)),
                'url': '/inspection/a/',
            },
            {
                'category': '服务器', 'asset': '目标-C', 'ok': True,
                'summary': '巡检正常',
                'time': timezone.make_aware(datetime(2026, 8, 28, 9, 0)),
                'url': '/inspection/c/',
            },
        ]
        with patch('index.inspections.records._inspection_records', return_value=records):
            response = self.client.get(reverse('inspection_records'), {
                'filter_category': '计算机',
                'filter_asset': '目标',
                'filter_status': 'abnormal',
                'filter_time_from': '2026-08-29',
                'filter_time_to': '2026-08-29',
                'sort': 'asset',
                'order': 'asc',
            })

        self.assertEqual(
            [record['asset'] for record in response.context['page_obj'].object_list],
            ['目标-A', '目标-B'],
        )

    def test_error_dictionary_filters_use_registered_exact_and_date_comparisons(self):
        records = [
            {
                'category': '计算机', 'asset': 'PC-B', 'type': '磁盘异常',
                'message': '空间不足',
                'time': timezone.make_aware(datetime(2026, 8, 29, 10, 0)),
                'url': '/errors/b/',
            },
            {
                'category': '计算机', 'asset': 'PC-A', 'type': '磁盘异常',
                'message': '磁盘离线',
                'time': timezone.make_aware(datetime(2026, 8, 29, 9, 0)),
                'url': '/errors/a/',
            },
            {
                'category': '服务器', 'asset': 'SRV-A', 'type': '采集异常',
                'message': '连接超时',
                'time': timezone.make_aware(datetime(2026, 8, 28, 9, 0)),
                'url': '/errors/c/',
            },
        ]
        with patch('index.inspections.records._error_records', return_value=records):
            response = self.client.get(reverse('error_records'), {
                'filter_category': '计算机',
                'filter_asset': 'PC',
                'filter_type': '磁盘异常',
                'filter_time_from': '2026-08-29',
                'filter_time_to': '2026-08-29',
                'sort': 'asset',
                'order': 'asc',
            })

        self.assertEqual(
            [record['asset'] for record in response.context['page_obj'].object_list],
            ['PC-A', 'PC-B'],
        )

    def test_dictionary_record_queries_ignore_unregistered_filter_and_sort_keys(self):
        records = [{
            'category': '计算机', 'asset': 'PC-SAFE', 'ok': True,
            'summary': '巡检正常', 'time': timezone.now(),
            'url': '/inspection/safe/', 'secret': 'do-not-filter',
        }]
        with patch('index.inspections.records._inspection_records', return_value=records):
            response = self.client.get(reverse('inspection_records'), {
                'filter_secret': 'missing', 'sort': 'secret',
            })

        self.assertEqual(
            [record['asset'] for record in response.context['page_obj'].object_list],
            ['PC-SAFE'],
        )
        self.assertEqual(response.context['table_state']['sort'], 'time')

    def test_dictionary_record_filter_ignores_unsupported_registered_choice(self):
        records = [
            {
                'category': '计算机', 'asset': 'PC-A', 'ok': True,
                'summary': '巡检正常', 'time': timezone.now(),
                'url': '/inspection/a/',
            },
            {
                'category': '服务器', 'asset': 'SERVER-B', 'ok': False,
                'summary': '采集异常', 'time': timezone.now(),
                'url': '/inspection/b/',
            },
        ]
        with patch('index.inspections.records._inspection_records', return_value=records):
            response = self.client.get(reverse('inspection_records'), {
                'filter_status': 'not-a-supported-status',
            })

        self.assertEqual(
            [record['asset'] for record in response.context['page_obj'].object_list],
            ['PC-A', 'SERVER-B'],
        )
        self.assertNotIn('status', response.context['table_state']['filters'])

    def test_aggregate_record_pages_render_distinct_configurable_workspaces(self):
        inspection = {
            'category': '计算机', 'asset': 'PC-INSPECTION', 'ok': False,
            'summary': '发现异常', 'time': timezone.now(),
            'url': '/inspection/detail/',
        }
        error = {
            'category': '计算机', 'asset': 'PC-ERROR', 'type': '磁盘异常',
            'message': '空间不足', 'time': timezone.now(),
            'url': '/error/detail/',
        }
        with patch('index.inspections.records._inspection_records', return_value=[inspection]):
            inspection_response = self.client.get(reverse('inspection_records'))
        with patch('index.inspections.records._error_records', return_value=[error]):
            error_response = self.client.get(reverse('error_records'))

        inspection_document = parse_response_html(inspection_response)
        error_document = parse_response_html(error_response)
        self.assertTrue(inspection_document.find(
            'div', **{
                'data-table-workspace': '',
                'data-table-key': 'inspection_records-global',
            },
        ))
        self.assertTrue(error_document.find(
            'div', **{'data-table-workspace': '', 'data-table-key': 'error_records'},
        ))
        self.assertTrue(inspection_document.find(
            'input', **{'data-column-toggle': '', 'data-column-key': 'status'},
        ))
        self.assertTrue(error_document.find(
            'input', **{'data-column-toggle': '', 'data-column-key': 'type'},
        ))
        self.assertContains(inspection_response, 'href="/inspection/detail/"')
        self.assertTrue(inspection_document.find('span', **{'data-status': 'abnormal'}))
        self.assertContains(error_response, 'href="/error/detail/"')
        self.assertContains(error_response, '<span class="badge status-badge text-bg-danger" data-status="abnormal">磁盘异常</span>', html=True)

    def test_dedicated_computer_record_pages_have_unique_registered_workspaces(self):
        inspection = create_computer_analysis('PC-DEDICATED', user_name='测试用户')
        error = Error_Computer.objects.create(
            inspection=inspection, error_type='磁盘异常', error_message='空间不足',
        )

        inspection_response = self.client.get(analysis_task_url(inspection))
        error_response = self.client.get(reverse('computer_error_list'))
        inspection_document = parse_response_html(inspection_response)
        error_document = parse_response_html(error_response)

        self.assertTrue(inspection_document.find(
            'div', **{'data-table-workspace': '', 'data-table-key': 'computer_inspections'},
        ))
        self.assertTrue(error_document.find(
            'div', **{'data-table-workspace': '', 'data-table-key': 'computer_errors'},
        ))
        detail_url = reverse('computer_analysis_detail', args=[inspection.pk])
        self.assertContains(inspection_response, f'href="{detail_url}"')
        self.assertContains(inspection_response, '<span class="badge status-badge text-bg-warning" data-status="warning">警告</span>', html=True)
        self.assertContains(error_response, f'href="{detail_url}"')
        self.assertContains(error_response, f'<span class="badge status-badge text-bg-danger" data-status="abnormal">{error.error_type}</span>', html=True)

    def test_dedicated_computer_record_pages_use_registered_page_size(self):
        inspections = [
            create_computer_analysis(f'PC-{number:03d}')
            for number in range(51)
        ]
        Error_Computer.objects.bulk_create([
            Error_Computer(
                inspection=inspection, error_type='测试异常', error_message='异常',
            )
            for inspection in inspections
        ])

        inspection_response = self.client.get(
            analysis_task_url(*inspections), {'page_size': '50'},
        )
        error_response = self.client.get(
            reverse('computer_error_list'), {'page_size': '50'},
        )

        self.assertEqual(len(inspection_response.context['page_obj'].object_list), 50)
        self.assertEqual(len(error_response.context['page_obj'].object_list), 50)
        self.assertEqual(inspection_response.context['table_state']['page_size'], 50)
        self.assertEqual(error_response.context['table_state']['page_size'], 50)

    def test_computer_inspection_workspace_filters_registered_status_and_date_fields(self):
        abnormal = create_computer_analysis('PC-ABNORMAL', status=RecordStatus.FAILED)
        normal = create_computer_analysis('PC-NORMAL')
        Error_Computer.objects.create(
            inspection=abnormal, error_type='测试异常', error_message='异常',
        )

        response = self.client.get(analysis_task_url(abnormal, normal), {
            'filter_computer_name': 'ABNORMAL',
            'filter_status': 'failed',
            'filter_created_at_from': timezone.localdate().isoformat(),
            'sort': 'computer_name',
            'order': 'asc',
        })

        self.assertEqual(list(response.context['page_obj'].object_list), [abnormal])
        self.assertNotIn(normal, response.context['page_obj'].object_list)

    def test_computer_error_workspace_searches_registered_field_sources(self):
        matching = create_computer_analysis('PC-SEARCH-MATCH')
        other = create_computer_analysis('PC-SEARCH-OTHER')
        matching_error = Error_Computer.objects.create(
            inspection=matching, error_type='磁盘异常', error_message='空间不足',
        )
        Error_Computer.objects.create(
            inspection=other, error_type='网络异常', error_message='连接超时',
        )

        self.client.raise_request_exception = False
        response = self.client.get(reverse('computer_error_list'), {'q': 'MATCH'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['page_obj'].object_list), [matching_error])

    def test_aggregate_inspection_workspace_uses_only_registered_status_filter(self):
        normal = create_computer_analysis('PC-NORMAL')
        abnormal = create_computer_analysis('PC-ABNORMAL', status=RecordStatus.FAILED)
        Error_Computer.objects.create(
            inspection=abnormal, error_type='测试异常', error_message='异常',
        )

        response = self.client.get(reverse('inspection_records'), {
            'filter_status': 'abnormal',
        })
        document = parse_response_html(response)

        self.assertEqual(
            [record['asset'] for record in response.context['page_obj'].object_list],
            [abnormal.computer.computer_name],
        )
        self.assertFalse(document.find('select', name='status'))
        self.assertEqual(len(document.find('select', name='filter_status')), 1)
        self.assertContains(
            response, '<option value="abnormal" selected>异常</option>', html=True,
        )

    def test_computer_inspection_workspace_status_filter_uses_anomaly_outcome(self):
        normal = create_computer_analysis('PC-NORMAL')
        abnormal = create_computer_analysis('PC-ABNORMAL')
        Error_Computer.objects.create(
            inspection=abnormal, error_type='测试异常', error_message='异常',
        )

        task_url = analysis_task_url(normal, abnormal)
        normal_response = self.client.get(task_url, {
            'filter_status': 'normal',
        })
        abnormal_response = self.client.get(task_url, {
            'filter_status': 'abnormal',
        })
        document = parse_response_html(normal_response)

        self.assertEqual(list(normal_response.context['page_obj'].object_list), [normal])
        self.assertEqual(list(abnormal_response.context['page_obj'].object_list), [abnormal])
        self.assertFalse(document.find('select', name='status'))
        self.assertEqual(len(document.find('select', name='filter_status')), 1)
        self.assertContains(
            normal_response, '<option value="normal" selected>正常</option>', html=True,
        )

    def test_computer_severity_filters_include_findings_without_error_rows(self):
        normal = create_computer_analysis('PC-NORMAL')
        info = create_computer_analysis('PC-INFO', exceptions=[{'severity': 'info'}])
        warning = create_computer_analysis('PC-WARNING', exceptions=[{'severity': 'warning'}])
        critical = create_computer_analysis('PC-CRITICAL', exceptions=[{'severity': 'critical'}])
        failed = create_computer_analysis('PC-FAILED', status=RecordStatus.FAILED)
        task_url = analysis_task_url(normal, info, warning, critical, failed)
        for value, expected in (
            ('normal', [normal]), ('info', [info]), ('warning', [warning]),
            ('critical', [critical, failed]), ('abnormal', [warning, critical, failed]),
        ):
            with self.subTest(status=value):
                response = self.client.get(task_url, {'filter_status': value})
                self.assertCountEqual(response.context['page_obj'].object_list, expected)
                self.assertEqual(response.context['table_state']['filters']['status'], value)

    def test_computer_error_workspace_filters_registered_type_and_date_fields(self):
        matching = create_computer_analysis('PC-MATCH')
        other = create_computer_analysis('PC-OTHER')
        matching_error = Error_Computer.objects.create(
            inspection=matching, error_type='磁盘异常', error_message='空间不足',
        )
        Error_Computer.objects.create(
            inspection=other, error_type='网络异常', error_message='连接超时',
        )

        response = self.client.get(reverse('computer_error_list'), {
            'filter_computer_name': 'MATCH',
            'filter_type': '磁盘异常',
            'filter_time_from': timezone.localdate().isoformat(),
            'sort': 'computer_name',
            'order': 'asc',
        })

        self.assertEqual(list(response.context['page_obj'].object_list), [matching_error])


class DemoDataCommandTests(TestCase):
    def test_seed_demo_data_only_fills_empty_asset_types_and_is_idempotent(self):
        existing = People.objects.create(name='真实人员', employee_id='REAL-1')
        call_command('seed_demo_data', stdout=StringIO())
        first_counts = {
            'people': People.objects.count(), 'computers': Computer.objects.count(),
            'networks': Network_Device.objects.count(), 'servers': Server.objects.count(),
            'monitors': SecurityDevice.objects.count(),
        }
        call_command('seed_demo_data', stdout=StringIO())
        second_counts = {
            'people': People.objects.count(), 'computers': Computer.objects.count(),
            'networks': Network_Device.objects.count(), 'servers': Server.objects.count(),
            'monitors': SecurityDevice.objects.count(),
        }
        self.assertEqual(first_counts, second_counts)
        self.assertEqual(People.objects.get(pk=existing.pk).name, '真实人员')
        self.assertGreater(first_counts['computers'], 0)
        self.assertGreater(ComputerAnalysis.objects.count(), 0)
        self.assertGreater(Error_Computer.objects.count(), 0)
        self.assertGreater(Error_Network_Device.objects.count(), 0)
        self.assertGreater(Error_Server.objects.count(), 0)
        self.assertGreater(Error_Monitor.objects.count(), 0)
