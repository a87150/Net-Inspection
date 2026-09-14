import csv

from tests import response_body
import uuid
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Q
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from tests.auth import login_admin

from net.models import (
    Computer,
    ComputerAnalysis,
    ComputerLogFile,
    Domain_Account,
    Domain_Computer,
    Domain_Controller_Config,
    Domain_Group,
    Error_Computer,
    Error_Monitor,
    Error_Network_Device,
    Error_Server,
    SecurityDevice,
    Monitor_Inspection,
    Network_Device,
    Network_Device_Inspection,
    People,
    PeopleSyncSource,
    Server,
    Server_Inspection,
)


BUSINESS_MODELS = (
    People,
    Computer,
    ComputerLogFile,
    ComputerAnalysis,
    Network_Device,
    Network_Device_Inspection,
    Server,
    Server_Inspection,
    SecurityDevice,
    Monitor_Inspection,
    Domain_Account,
    Domain_Computer,
    Domain_Controller_Config,
    Domain_Group,
    Error_Computer,
    Error_Network_Device,
    Error_Server,
    Error_Monitor,
)


class DashboardActionTests(TestCase):
    def setUp(self):
        login_admin(self.client)

    def test_dashboard_separates_list_and_dynamic_actions(self):
        call_command('seed_demo_data', reset=True, stdout=StringIO())

        response = self.client.get(reverse('index'))
        items = {item['key']: item for item in response.context['items']}

        self.assertContains(response, 'PC列表')
        self.assertContains(response, '日志分析记录')
        self.assertContains(response, '手动执行巡检', count=3)
        self.assertEqual(
            items['computers']['list_url'],
            reverse('asset_list', args=['computers']),
        )
        self.assertEqual(
            items['computers']['record_url'], reverse('computer_analysis_list'),
        )
        for kind in ('networks', 'servers', 'monitors'):
            with self.subTest(kind=kind):
                self.assertEqual(
                    items[kind]['manual_action_label'], '手动执行巡检',
                )
                self.assertTrue(items[kind]['list_url'])
                self.assertTrue(items[kind]['record_url'])


class DeterministicDemoSeedTests(TestCase):
    def _create_previous_computer_fixture(self):
        now = timezone.now()
        fixture = []
        for index, spec in enumerate((
            {
                'computer_name': 'DEMO-PC-01',
                'os': 'Windows 11',
                'user_name': '演示-张工',
                'content_hash': '03ce1696b1f9d7cf58632e13a534acba94ae49da32689ebf03cc5b93583baafc',
                'status': 'success',
                'summary': '分析正常',
                'cpu': 15,
                'exceptions': [],
            },
            {
                'computer_name': 'DEMO-PC-02',
                'os': 'Windows 10',
                'user_name': '演示-李工',
                'content_hash': 'a3b4fbf4c1fb082e7aca3f54099c27e92a4e7b31df6b76a00a5d872e4fb6cc8a',
                'status': 'failed',
                'summary': 'CPU 使用率过高',
                'cpu': 70,
                'exceptions': [{
                    '问题类型': 'CPU 使用率过高',
                    '详细问题': '演示数据：CPU 使用率达到 70%。',
                }],
            },
        )):
            computer = Computer.objects.create(
                computer_name=spec['computer_name'],
                os=spec['os'],
                user_name=spec['user_name'],
                last_report_at=now - timedelta(hours=index * 2),
                is_active=True,
            )
            log_file = ComputerLogFile.objects.create(
                source_path=f"demo://{spec['computer_name']}.json",
                modified_at=now - timedelta(hours=index),
                content_hash=spec['content_hash'],
                import_status='success',
                payload={
                    '系统信息概览': {'计算机名': spec['computer_name']},
                },
            )
            analysis = ComputerAnalysis.objects.create(
                computer=computer,
                log_file=log_file,
                status=spec['status'],
                summary=spec['summary'],
                details={
                    'system_info': {'操作系统': spec['os']},
                    'computer_info': {'CPU使用率': spec['cpu']},
                },
                exceptions=spec['exceptions'],
            )
            error = None
            if spec['status'] == 'failed':
                error = Error_Computer.objects.create(
                    inspection=analysis,
                    error_type='CPU 使用率过高',
                    error_message='演示数据：CPU 使用率达到 70%。',
                )
            fixture.append((computer, log_file, analysis, error))
        return fixture

    def _create_previous_server_monitor_fixture(self):
        legacy_servers = []
        for ip, name, server_type, operating_system, status, summary in (
            ('192.0.2.21', '演示-Linux应用服务器', 'linux', 'Ubuntu 24.04', 'success', '巡检成功'),
            ('192.0.2.22', '演示-Windows文件服务器', 'windows', 'Windows Server 2022', 'success', '巡检成功'),
            ('192.0.2.23', '演示-数据库服务器', 'linux', 'Rocky Linux 9', 'failed', '磁盘空间不足（演示）'),
        ):
            server = Server.objects.create(
                name=name,
                ip=ip,
                server_type=server_type,
                os=operating_system,
            )
            inspection = Server_Inspection.objects.create(
                server=server,
                status=status,
                summary=summary,
                is_reachable=True,
                details={
                    'cpu': {'usage_percent': 35},
                    'memory': {'usage_percent': 52},
                    'storage_status': [{
                        'mount': 'C:' if server_type == 'windows' else '/',
                        'usage_percent': 62,
                    }],
                },
            )
            if status == 'failed':
                Error_Server.objects.create(
                    inspection=inspection,
                    error_message={'演示异常': summary},
                )
            legacy_servers.append((server, inspection))

        legacy_monitors = []
        for ip, name, device_type, vendor, status, reachable, summary in (
            ('192.0.2.31', '演示-大厅摄像机', '摄像机', 'Hikvision', 'success', True, '巡检成功'),
            ('192.0.2.32', '演示-NVR录像机', 'NVR', 'Dahua', 'success', True, '巡检成功'),
            ('192.0.2.33', '演示-仓库摄像机', '摄像机', 'Hikvision', 'failed', False, '摄像机离线（演示）'),
        ):
            monitor = SecurityDevice.objects.create(
                device_name=name,
                ip=ip,
                device_type=device_type,
                vendor=vendor,
            )
            inspection = Monitor_Inspection.objects.create(
                monitor=monitor,
                status=status,
                summary=summary,
                is_reachable=reachable,
                details={
                    'device_info': {'型号': 'DEMO'},
                    'channel_status': [{'channel': 1, 'online': reachable}],
                    'storage_status': [{'status': 'normal'}],
                },
            )
            if status == 'failed':
                Error_Monitor.objects.create(
                    inspection=inspection,
                    error_message={'演示异常': summary},
                )
            legacy_monitors.append((monitor, inspection))
        return legacy_servers, legacy_monitors

    def _snapshot(self):
        return {
            'counts': {
                model._meta.label: model.objects.count()
                for model in BUSINESS_MODELS
            },
            'asset_ids': {
                'people': tuple(
                    People.objects.order_by('employee_id').values_list(
                        'employee_id', 'pk',
                    )
                ),
                'computers': tuple(
                    Computer.objects.order_by('computer_name').values_list(
                        'computer_name', 'pk',
                    )
                ),
                'networks': tuple(
                    Network_Device.objects.order_by('ip').values_list(
                        'ip', 'pk', 'connection_type', 'snmp_version',
                        'snmp_port', 'snmp_community', 'snmp_security_level',
                        'snmp_username', 'snmp_auth_protocol',
                        'snmp_auth_password', 'snmp_priv_protocol',
                        'snmp_priv_password', 'snmp_context_name',
                        'snmp_retries',
                    )
                ),
                'servers': tuple(
                    Server.objects.order_by('ip').values_list('ip', 'pk')
                ),
                'monitors': tuple(
                    SecurityDevice.objects.order_by('ip').values_list('ip', 'pk')
                ),
            },
            'record_ids': {
                'analyses': tuple(
                    ComputerAnalysis.objects.order_by('pk').values_list(
                        'pk', flat=True,
                    )
                ),
                'network': tuple(
                    Network_Device_Inspection.objects.order_by('pk').values_list(
                        'pk', flat=True,
                    )
                ),
                'server': tuple(
                    Server_Inspection.objects.order_by('pk').values_list(
                        'pk', flat=True,
                    )
                ),
                'monitor': tuple(
                    Monitor_Inspection.objects.order_by('pk').values_list(
                        'pk', flat=True,
                    )
                ),
            },
        }

    @patch('requests.sessions.Session.request')
    def test_demo_seed_reset_is_repeatable_and_offline(self, request_mock):
        call_command('seed_demo_data', reset=True, stdout=StringIO())
        first = self._snapshot()

        call_command('seed_demo_data', reset=True, stdout=StringIO())

        self.assertEqual(self._snapshot(), first)
        request_mock.assert_not_called()

    def test_demo_networks_cover_ssh_hybrid_and_auto_without_routable_targets(self):
        call_command('seed_demo_data', reset=True, stdout=StringIO())

        devices = list(Network_Device.objects.order_by('ip'))
        self.assertEqual(
            {device.connection_type for device in devices},
            {'ssh', 'hybrid', 'auto'},
        )
        self.assertEqual(
            [device.ip for device in devices],
            ['192.0.2.11', '192.0.2.12', '192.0.2.13'],
        )
        self.assertTrue(all(device.snmp_port == 161 for device in devices))
        snmp_devices = [
            device for device in devices if device.connection_type != 'ssh'
        ]
        self.assertEqual(
            {device.snmp_version for device in snmp_devices}, {'v2c', 'v3'},
        )
        self.assertTrue(all(
            not value or value == 'DEMO-ONLY-NOT-A-SECRET'
            for device in snmp_devices
            for value in (
                device.snmp_community, device.snmp_auth_password,
                device.snmp_priv_password,
            )
        ))

    def test_reset_preserves_rows_outside_the_named_demo_scope(self):
        person = People.objects.create(name='真实人员', employee_id='REAL-001')
        device = Network_Device.objects.create(
            device_name='真实交换机', ip='10.10.10.10',
        )
        config = Domain_Controller_Config.objects.create(
            name='生产域控', host='dc.corp.internal', bind_username='svc-reader',
        )

        call_command('seed_demo_data', reset=True, stdout=StringIO())

        self.assertTrue(People.objects.filter(pk=person.pk).exists())
        self.assertTrue(Network_Device.objects.filter(pk=device.pk).exists())
        config.refresh_from_db()
        self.assertEqual(config.host, 'dc.corp.internal')

    def test_reset_preserves_non_demo_dependents_on_demo_assets_and_logs(self):
        call_command('seed_demo_data', reset=True, stdout=StringIO())
        demo_device = Network_Device.objects.get(ip='192.0.2.11')
        demo_computer = Computer.objects.get(computer_name='DEMO-PC-OPS-01')
        user_inspection = Network_Device_Inspection.objects.create(
            device=demo_device,
            status='success',
            summary='用户创建的巡检记录',
        )
        owned_inspection = Network_Device_Inspection.objects.get(
            device=demo_device,
            status='partial',
        )
        user_error = Error_Network_Device.objects.create(
            inspection=owned_inspection,
            error_message={'用户标记': '必须保留'},
        )
        owned_log = ComputerLogFile.objects.get(
            source_path='demo://powershell/DEMO-PC-OPS-01-1.json',
        )
        shared_log_analysis = ComputerAnalysis.objects.create(
            computer=demo_computer,
            log_file=owned_log,
            summary='共享演示日志的用户分析',
        )
        user_log = ComputerLogFile.objects.create(
            source_path='demo://user-upload.json',
            modified_at=timezone.now(),
            content_hash='a' * 64,
            import_status='success',
        )
        user_log_analysis = ComputerAnalysis.objects.create(
            computer=demo_computer,
            log_file=user_log,
            summary='demo 协议下的用户分析',
        )

        call_command('seed_demo_data', reset=True, stdout=StringIO())

        self.assertTrue(
            Network_Device.objects.filter(pk=demo_device.pk).exists()
        )
        self.assertTrue(
            Network_Device_Inspection.objects.filter(pk=user_inspection.pk).exists()
        )
        self.assertTrue(Error_Network_Device.objects.filter(pk=user_error.pk).exists())
        self.assertTrue(
            ComputerAnalysis.objects.filter(pk=shared_log_analysis.pk).exists()
        )
        self.assertTrue(ComputerLogFile.objects.filter(pk=owned_log.pk).exists())
        self.assertTrue(ComputerLogFile.objects.filter(pk=user_log.pk).exists())
        self.assertTrue(
            ComputerAnalysis.objects.filter(pk=user_log_analysis.pk).exists()
        )

    def test_asset_uuid_collision_is_controlled_and_rolls_back_every_write(self):
        conflict = Network_Device.objects.create(
            id=uuid.UUID('92f5508e-a97a-5a1a-8762-22b735e2826f'),
            device_name='用户设备',
            ip='10.20.30.41',
        )

        with self.assertRaisesMessage(CommandError, '演示数据 UUID 冲突'):
            call_command('seed_demo_data', reset=True, stdout=StringIO())

        conflict.refresh_from_db()
        self.assertEqual(conflict.ip, '10.20.30.41')
        self.assertFalse(People.objects.filter(employee_id='DEMO-P001').exists())
        self.assertFalse(Network_Device.objects.filter(ip='192.0.2.11').exists())

    def test_record_uuid_collision_is_not_deleted_or_overwritten(self):
        device = Network_Device.objects.create(
            device_name='用户交换机', ip='10.20.30.42',
        )
        conflict = Network_Device_Inspection.objects.create(
            id=uuid.UUID('d69ccd6f-def8-5c0f-b9d8-23b3c50c9ecc'),
            device=device,
            status='failed',
            summary='用户固定 UUID 巡检',
        )

        with self.assertRaisesMessage(CommandError, '演示数据 UUID 冲突'):
            call_command('seed_demo_data', reset=True, stdout=StringIO())

        conflict.refresh_from_db()
        self.assertEqual(conflict.device_id, device.pk)
        self.assertEqual(conflict.summary, '用户固定 UUID 巡检')
        self.assertFalse(People.objects.filter(employee_id='DEMO-P001').exists())

    def test_error_uuid_collision_is_not_deleted_or_overwritten(self):
        device = Network_Device.objects.create(
            device_name='用户交换机', ip='10.20.30.43',
        )
        inspection = Network_Device_Inspection.objects.create(
            device=device,
            status='failed',
            summary='用户巡检',
        )
        conflict = Error_Network_Device.objects.create(
            id=uuid.UUID('7e56bfd0-4a48-5497-a339-d8275db97799'),
            inspection=inspection,
            error_message={'用户异常': '不可覆盖'},
        )

        with self.assertRaisesMessage(CommandError, '演示数据 UUID 冲突'):
            call_command('seed_demo_data', reset=True, stdout=StringIO())

        conflict.refresh_from_db()
        self.assertEqual(conflict.inspection_id, inspection.pk)
        self.assertEqual(conflict.error_message, {'用户异常': '不可覆盖'})
        self.assertFalse(People.objects.filter(employee_id='DEMO-P001').exists())

    def test_reserved_log_hash_with_different_path_rolls_back_without_mutation(self):
        computer = Computer.objects.create(
            computer_name='USER-PC-LOG-01',
            os='Windows 11 Pro',
            user_name='用户-林工',
        )
        original_modified_at = timezone.now()
        conflict = ComputerLogFile.objects.create(
            source_path='C:/user/current-hash.json',
            modified_at=original_modified_at,
            content_hash='5a6d844f48ecb09223016b93d251e8863f3767c6c9e756efcfc655ac7f9b76e5',
            import_status='failed',
            archived_path='C:/user/archive/current-hash.json',
            parse_error='用户解析标记',
            payload={'owner': 'user', 'value': 17},
        )
        analysis = ComputerAnalysis.objects.create(
            computer=computer,
            log_file=conflict,
            status='failed',
            summary='用户日志分析',
            details={'owner': 'user'},
        )

        with self.assertRaisesMessage(CommandError, '演示日志身份冲突'):
            call_command('seed_demo_data', reset=True, stdout=StringIO())

        conflict.refresh_from_db()
        analysis.refresh_from_db()
        self.assertEqual(conflict.source_path, 'C:/user/current-hash.json')
        self.assertEqual(conflict.modified_at, original_modified_at)
        self.assertEqual(conflict.import_status, 'failed')
        self.assertEqual(
            conflict.archived_path, 'C:/user/archive/current-hash.json',
        )
        self.assertEqual(conflict.parse_error, '用户解析标记')
        self.assertEqual(conflict.payload, {'owner': 'user', 'value': 17})
        self.assertEqual(analysis.summary, '用户日志分析')
        self.assertEqual(analysis.details, {'owner': 'user'})
        self.assertFalse(People.objects.filter(employee_id='DEMO-P001').exists())
        self.assertFalse(
            Network_Device.objects.filter(ip='192.0.2.11').exists()
        )

    def test_reset_removes_the_complete_previous_server_and_monitor_fixture(self):
        legacy_servers, legacy_monitors = (
            self._create_previous_server_monitor_fixture()
        )
        legacy_asset_ids = [
            *(asset.pk for asset, _ in legacy_servers),
            *(asset.pk for asset, _ in legacy_monitors),
        ]
        legacy_record_ids = [
            *(record.pk for _, record in legacy_servers),
            *(record.pk for _, record in legacy_monitors),
        ]

        call_command('seed_demo_data', reset=True, stdout=StringIO())

        self.assertFalse(Server.objects.filter(pk__in=legacy_asset_ids).exists())
        self.assertFalse(SecurityDevice.objects.filter(pk__in=legacy_asset_ids).exists())
        self.assertFalse(
            Server_Inspection.objects.filter(pk__in=legacy_record_ids).exists()
        )
        self.assertFalse(
            Monitor_Inspection.objects.filter(pk__in=legacy_record_ids).exists()
        )
        self.assertEqual(Server.objects.count(), 3)
        self.assertEqual(SecurityDevice.objects.count(), 5)
        self.assertEqual(Error_Server.objects.count(), 2)
        self.assertEqual(Error_Monitor.objects.count(), 2)

    def test_reset_preserves_non_demo_record_on_a_previous_demo_asset(self):
        legacy_servers, _ = self._create_previous_server_monitor_fixture()
        legacy_server, legacy_demo_record = legacy_servers[0]
        user_record = Server_Inspection.objects.create(
            server=legacy_server,
            status='success',
            summary='用户后来创建的巡检',
        )

        call_command('seed_demo_data', reset=True, stdout=StringIO())

        self.assertTrue(Server.objects.filter(pk=legacy_server.pk).exists())
        self.assertTrue(Server_Inspection.objects.filter(pk=user_record.pk).exists())
        self.assertFalse(
            Server_Inspection.objects.filter(pk=legacy_demo_record.pk).exists()
        )

    def test_reset_preserves_non_owned_rows_using_a_legacy_demo_namespace(self):
        legacy_computer = Computer.objects.create(
            computer_name='DEMO-PC-01', os='Windows 11',
        )
        legacy_log = ComputerLogFile.objects.create(
            source_path='demo://DEMO-PC-01.json',
            modified_at=timezone.now(),
            content_hash='f' * 64,
            import_status='success',
        )
        ComputerAnalysis.objects.create(
            computer=legacy_computer,
            log_file=legacy_log,
            summary='旧版演示记录',
        )

        call_command('seed_demo_data', reset=True, stdout=StringIO())

        self.assertTrue(
            Computer.objects.filter(computer_name='DEMO-PC-01').exists()
        )
        self.assertTrue(
            ComputerLogFile.objects.filter(source_path='demo://DEMO-PC-01.json').exists()
        )
        self.assertTrue(ComputerAnalysis.objects.filter(summary='旧版演示记录').exists())

    def test_reset_deletes_only_exact_orphan_legacy_computer_and_log_identity(self):
        legacy_computer = Computer.objects.create(
            computer_name='DEMO-PC-01',
            os='Windows 11',
            user_name='演示-张工',
        )
        exact_log = ComputerLogFile.objects.create(
            source_path='demo://DEMO-PC-01.json',
            modified_at=timezone.now(),
            content_hash='03ce1696b1f9d7cf58632e13a534acba94ae49da32689ebf03cc5b93583baafc',
            import_status='success',
        )
        same_path_other_hash = ComputerLogFile.objects.create(
            source_path='demo://DEMO-PC-02.json',
            modified_at=timezone.now(),
            content_hash='b' * 64,
            import_status='success',
        )

        call_command('seed_demo_data', reset=True, stdout=StringIO())

        self.assertFalse(Computer.objects.filter(pk=legacy_computer.pk).exists())
        self.assertFalse(ComputerLogFile.objects.filter(pk=exact_log.pk).exists())
        self.assertTrue(
            ComputerLogFile.objects.filter(pk=same_path_other_hash.pk).exists()
        )

    def test_reset_removes_complete_previous_computer_fixture_graph(self):
        fixture = self._create_previous_computer_fixture()
        computer_ids = [computer.pk for computer, _, _, _ in fixture]
        log_ids = [log_file.pk for _, log_file, _, _ in fixture]
        analysis_ids = [analysis.pk for _, _, analysis, _ in fixture]
        error_ids = [error.pk for _, _, _, error in fixture if error is not None]

        call_command('seed_demo_data', reset=True, stdout=StringIO())

        self.assertFalse(Computer.objects.filter(pk__in=computer_ids).exists())
        self.assertFalse(ComputerLogFile.objects.filter(pk__in=log_ids).exists())
        self.assertFalse(
            ComputerAnalysis.objects.filter(pk__in=analysis_ids).exists()
        )
        self.assertFalse(Error_Computer.objects.filter(pk__in=error_ids).exists())
        self.assertEqual(Computer.objects.count(), 3)
        self.assertEqual(ComputerLogFile.objects.count(), 4)
        self.assertEqual(ComputerAnalysis.objects.count(), 4)
        self.assertEqual(Error_Computer.objects.count(), 2)

    def test_reset_preserves_non_demo_analyses_and_errors_on_previous_fixture(self):
        first, second = self._create_previous_computer_fixture()
        first_computer, first_log, first_owned_analysis, _ = first
        second_computer, second_log, second_owned_analysis, second_owned_error = second
        user_analysis = ComputerAnalysis.objects.create(
            computer=first_computer,
            log_file=first_log,
            status='success',
            summary='用户后来创建的分析',
            details={'owner': 'user'},
        )
        user_error = Error_Computer.objects.create(
            inspection=second_owned_analysis,
            error_type='用户备注',
            error_message='必须保留',
        )

        call_command('seed_demo_data', reset=True, stdout=StringIO())

        self.assertFalse(
            ComputerAnalysis.objects.filter(pk=first_owned_analysis.pk).exists()
        )
        self.assertFalse(Error_Computer.objects.filter(pk=second_owned_error.pk).exists())
        self.assertTrue(ComputerAnalysis.objects.filter(pk=user_analysis.pk).exists())
        self.assertTrue(Error_Computer.objects.filter(pk=user_error.pk).exists())
        self.assertTrue(
            ComputerAnalysis.objects.filter(pk=second_owned_analysis.pk).exists()
        )
        self.assertTrue(Computer.objects.filter(pk=first_computer.pk).exists())
        self.assertTrue(Computer.objects.filter(pk=second_computer.pk).exists())
        self.assertTrue(ComputerLogFile.objects.filter(pk=first_log.pk).exists())
        self.assertTrue(ComputerLogFile.objects.filter(pk=second_log.pk).exists())

    def test_seed_covers_assets_sources_records_domains_and_outcomes(self):
        call_command('seed_demo_data', reset=True, stdout=StringIO())

        self.assertEqual(
            set(People.objects.values_list('source', flat=True)),
            {'manual', 'csv', 'feishu', 'dingtalk'},
        )
        self.assertEqual(
            set(PeopleSyncSource.objects.values_list('source_key', flat=True)),
            {'people-provider-feishu', 'people-provider-dingtalk'},
        )
        self.assertEqual(
            set(PeopleSyncSource.objects.values_list('name', flat=True)),
            {'飞书', '钉钉'},
        )
        self.assertEqual(Computer.objects.count(), 3)
        self.assertTrue(
            all(computer.analyses.exists() for computer in Computer.objects.all())
        )
        self.assertEqual(ComputerAnalysis.objects.count(), 4)
        self.assertEqual(ComputerLogFile.objects.count(), 4)
        self.assertTrue(Domain_Account.objects.filter(is_active=True).exists())
        self.assertTrue(Domain_Account.objects.filter(is_active=False).exists())
        self.assertTrue(Domain_Computer.objects.filter(is_active=True).exists())
        self.assertTrue(Domain_Computer.objects.filter(is_active=False).exists())
        self.assertTrue(Domain_Group.objects.filter(group_category='security').exists())
        self.assertTrue(Domain_Group.objects.filter(group_category='distribution').exists())

        for asset_model, record_model in (
            (Network_Device, Network_Device_Inspection),
            (Server, Server_Inspection),
            (SecurityDevice, Monitor_Inspection),
        ):
            with self.subTest(model=asset_model._meta.label):
                self.assertEqual(
                    asset_model.objects.count(),
                    5 if asset_model is SecurityDevice else 3,
                )
                self.assertEqual(record_model.objects.count(), 5 if asset_model is Network_Device else 4)
                self.assertEqual(
                    set(record_model.objects.values_list('status', flat=True)),
                    {'success', 'partial', 'failed'},
                )
                for record in record_model.objects.prefetch_related('errors'):
                    self.assertEqual(
                        record.errors.exists(), record.status != 'success',
                    )

        infrastructure_records = [
            *Network_Device_Inspection.objects.all(),
            *Server_Inspection.objects.all(),
            *Monitor_Inspection.objects.all(),
        ]
        self.assertEqual(
            {record.is_reachable for record in infrastructure_records},
            {True, False},
        )
        normal_analyses = ComputerAnalysis.objects.filter(
            status='success', errors__isnull=True,
        ).distinct()
        abnormal_analyses = ComputerAnalysis.objects.filter(
            Q(errors__isnull=False) | ~Q(status='success'),
        ).distinct()
        self.assertEqual(
            set(normal_analyses.values_list('summary', flat=True)),
            {'分析正常'},
        )
        self.assertEqual(normal_analyses.count(), 2)
        self.assertEqual(
            set(abnormal_analyses.values_list('summary', flat=True)),
            {'发现异常：系统盘空间不足', '发现异常：日志超过预期上报时间'},
        )
        self.assertEqual(abnormal_analyses.count(), 2)
        self.assertEqual(Error_Computer.objects.count(), 2)
        for analysis in abnormal_analyses.prefetch_related('errors'):
            self.assertEqual(analysis.errors.count(), 1)

        self.assertTrue(Error_Computer.objects.exists())
        self.assertTrue(Error_Network_Device.objects.exists())
        self.assertTrue(Error_Server.objects.exists())
        self.assertTrue(Error_Monitor.objects.exists())
        self.assertTrue(Server.objects.filter(server_type='linux').exists())
        self.assertTrue(Server.objects.filter(server_type='windows').exists())
        self.assertTrue(
            all(
                not value or value.endswith('.invalid/api/v1/health')
                for value in Server.objects.values_list('api_url', flat=True)
            )
        )
        self.assertTrue(
            all(
                not value or value == 'DEMO-ONLY-NOT-A-SECRET'
                for value in (
                    list(Network_Device.objects.values_list('password', flat=True))
                    + list(Server.objects.values_list('password', flat=True))
                    + list(Server.objects.values_list('api_token', flat=True))
                    + list(SecurityDevice.objects.values_list('api_password', flat=True))
                    + list(SecurityDevice.objects.values_list('api_token', flat=True))
                )
            )
        )

    def test_seeded_data_smokes_lists_details_filters_exports_and_import_ui(self):
        login_admin(self.client)
        call_command('seed_demo_data', reset=True, stdout=StringIO())

        homepage = self.client.get(reverse('index'))
        self.assertContains(homepage, '巡检任务栏')
        self.assertContains(homepage, '演示网络巡检')
        self.assertContains(homepage, reverse('task_list'))
        for kind, model, lookup, identity, detail_value in (
            ('people', People, {'employee_id': 'DEMO-P002'}, '演示-李然', 'DEMO-P002'),
            ('computers', Computer, {'computer_name': 'DEMO-PC-DEV-02'}, 'DEMO-PC-DEV-02', '02-00-00-00-01-02'),
            ('networks', Network_Device, {'ip': '192.0.2.11'}, '演示-核心交换机', 'CloudEngine S5735-L'),
            ('servers', Server, {'ip': '198.51.100.22'}, '演示-Windows文件服务器', 'Windows Server 2022'),
            ('monitors', SecurityDevice, {'ip': '203.0.113.32'}, '演示-NVR录像机', 'NVR5216-4KS2'),
        ):
            with self.subTest(kind=kind):
                asset = model.objects.get(**lookup)
                listing = self.client.get(reverse('asset_list', args=[kind]))
                detail_url = reverse('asset_detail', args=[kind, asset.pk])
                self.assertContains(listing, identity)
                self.assertContains(listing, detail_url)
                detail = self.client.get(detail_url)
                self.assertContains(detail, identity)
                self.assertContains(detail, detail_value)
                if kind == 'computers':
                    self.assertContains(
                        detail,
                        f'{reverse("computer_analysis_list")}?target={asset.pk}',
                    )
                elif kind in {'networks', 'servers', 'monitors'}:
                    self.assertContains(
                        detail,
                        f'{reverse("record_list", args=[kind])}?target={asset.pk}',
                    )
                if kind == 'computers':
                    self.assertNotContains(listing, 'id="importModal"')
                else:
                    self.assertContains(listing, 'id="importModal"')

        for kind, model, summary in (
            ('networks', Network_Device_Inspection, '发现 2 个接入端口未连接'),
            ('servers', Server_Inspection, 'Windows 巡检 API 返回部分指标'),
            ('monitors', Monitor_Inspection, '录像保留天数低于策略要求'),
        ):
            with self.subTest(records=kind):
                record = model.objects.get(summary=summary)
                detail_url = reverse('record_detail', args=[kind, record.pk])
                listing = self.client.get(reverse('record_list', args=[kind]))
                if kind in {'networks', 'servers'}:
                    # These records predate the newest seeded task for the project.
                    self.assertNotContains(listing, summary)
                else:
                    self.assertContains(listing, summary)
                    self.assertContains(listing, detail_url)
                detail = self.client.get(detail_url)
                self.assertContains(detail, summary)
                self.assertContains(detail, '设备在线，但数据采集未完整成功')

        analysis = ComputerAnalysis.objects.get(
            summary='发现异常：系统盘空间不足',
        )
        analysis_url = reverse('computer_analysis_detail', args=[analysis.pk])
        analysis_list = self.client.get(reverse('computer_analysis_list'))
        # The project landing page shows task statistics; standalone historical
        # analyses remain reachable in the global records list and detail page.
        self.assertContains(analysis_list, '最新任务统计')
        historical_list = self.client.get(reverse('inspection_records'), {'q': 'DEMO-PC-DEV-02'})
        self.assertContains(historical_list, 'DEMO-PC-DEV-02')
        self.assertContains(historical_list, analysis_url)
        analysis_detail = self.client.get(analysis_url)
        self.assertContains(analysis_detail, '磁盘空间不足')
        self.assertContains(analysis_detail, '系统盘剩余空间低于 10%')

        consolidated = self.client.get(reverse('inspection_records'))
        self.assertContains(consolidated, 'DEMO-PC-DEV-02')
        self.assertContains(consolidated, 'Windows 巡检 API 返回部分指标')
        errors = self.client.get(reverse('error_records'))
        self.assertContains(errors, '磁盘空间不足')
        self.assertContains(errors, '摄像机离线（演示）')

        domain_page = self.client.get(reverse('domain_controller_settings'))
        self.assertContains(domain_page, 'dc.demo.invalid')
        for list_route, detail_route, model, lookup, identity, detail_value in (
            ('domain_account_list', 'domain_account_detail', Domain_Account, {'login_name': 'demo.zhang@demo.invalid'}, 'demo.zhang@demo.invalid', 'OU=Operations'),
            ('domain_computer_list', 'domain_computer_detail', Domain_Computer, {'computer_name': 'DEMO-DOMAIN-PC-01'}, 'DEMO-DOMAIN-PC-01', 'Windows 11 Enterprise'),
        ):
            domain_object = model.objects.get(**lookup)
            detail_url = reverse(detail_route, args=[domain_object.pk])
            listing = self.client.get(reverse(list_route))
            self.assertContains(listing, identity)
            self.assertContains(listing, detail_url)
            detail = self.client.get(detail_url)
            self.assertContains(detail, identity)
            self.assertContains(detail, detail_value)

        response = self.client.get(reverse('table_export', args=['people']), {
            'filter_department': '网络运维部',
            'sort': 'employee_id',
            'order': 'asc',
        })
        rows = list(csv.DictReader(StringIO(response_body(response).decode('utf-8-sig'))))
        self.assertEqual([row['工号'] for row in rows], ['DEMO-P002', 'DEMO-P004'])
