import csv

from tests import response_body
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command

from django.test import TestCase
from django.urls import reverse
from tests.auth import login_reader

from index.common.table_registry import get_table_definition
from net.models import Computer, Network_Device, People, Server
from net.devices.pc.snapshot import extract_computer_snapshot, update_computer_snapshot
from net.data_exchange.inventory_csv import export_csv, import_csv, import_file
from net.data_exchange.xlsx import build_xlsx


class AssetInventoryFieldTests(TestCase):
    def test_asset_table_displays_capacity_values_with_gb_units(self):
        login_reader(self.client)
        Computer.objects.create(
            computer_name='PC-CAPACITY', memory_total_gb=32, disk_total_gb=512,
        )
        Computer.objects.create(computer_name='PC-CAPACITY-UNKNOWN')

        response = self.client.get(reverse('asset_list', args=['computers']))

        self.assertContains(
            response,
            '<td data-column-key="memory_total_gb" hidden>32.00 GB</td>',
            html=True,
        )
        self.assertContains(
            response,
            '<td data-column-key="disk_total_gb" hidden>512.00 GB</td>',
            html=True,
        )
        self.assertNotContains(response, '- GB')

    def test_people_and_device_inventory_fields_are_nullable(self):
        person = People.objects.create(employee_id='P-100', hire_date=date(2024, 1, 2))
        pc = Computer.objects.create(computer_name='PC-100', cpu_model='Core i7', memory_total_gb=32)
        network = Network_Device.objects.create(ip='192.0.2.90', port_count=48, vlan_count=12)

        self.assertIsNone(person.departure_date)
        self.assertEqual(pc.memory_total_gb, Decimal('32.00'))
        self.assertEqual(network.port_count, 48)

    def test_pc_table_omits_enabled_and_exposes_static_configuration(self):
        labels = [field.label for field in get_table_definition('computers').fields]

        self.assertNotIn('是否启用', labels)
        self.assertIn('CPU 型号', labels)
        self.assertIn('CPU 物理核心数', labels)
        self.assertIn('CPU 逻辑处理器数', labels)
        self.assertIn('内存总量', labels)
        self.assertIn('磁盘总量', labels)
        self.assertNotIn('磁盘摘要', labels)
        self.assertNotIn('disk_summary', {field.name for field in Computer._meta.get_fields()})

    def test_pc_inventory_distinguishes_physical_cores_and_logical_processors(self):
        """Collapsing both CPU counts into cpu_core_count loses inventory meaning."""
        pc = Computer.objects.create(
            computer_name='PC-CPU-TOPOLOGY',
            cpu_physical_core_count=8,
            cpu_logical_processor_count=16,
        )

        self.assertEqual(pc.cpu_physical_core_count, 8)
        self.assertEqual(pc.cpu_logical_processor_count, 16)
        self.assertNotIn(
            'cpu_core_count',
            {field.name for field in Computer._meta.get_fields()},
        )

    def test_pc_table_contract_uses_pc_title_with_existing_key_and_url(self):
        login_reader(self.client)
        definition = get_table_definition('computers')
        response = self.client.get(reverse('item_list', args=[definition.key]))

        self.assertEqual(definition.key, 'computers')
        self.assertEqual(definition.title, 'PC')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['table_definition'].title, 'PC')


class AssetInventoryImportExportTests(TestCase):
    def test_people_import_reports_all_blank_employee_id_rows_without_writes(self):
        rows = (
            ('姓名', '工号', '邮箱'),
            ('空工号甲', '', 'first@example.test'),
            ('有效人员', 'P-VALID', 'valid@example.test'),
            ('空工号乙', '   ', 'second@example.test'),
        )
        uploads = (
            SimpleUploadedFile(
                'people.csv',
                ('姓名,工号,邮箱\n'
                 '空工号甲,,first@example.test\n'
                 '有效人员,P-VALID,valid@example.test\n'
                 '空工号乙,   ,second@example.test\n').encode('utf-8-sig'),
                content_type='text/csv',
            ),
            SimpleUploadedFile(
                'people.xlsx',
                build_xlsx(rows, sheet_name='人员'),
                content_type=(
                    'application/vnd.openxmlformats-officedocument.'
                    'spreadsheetml.sheet'
                ),
            ),
        )

        for upload in uploads:
            with self.subTest(filename=upload.name):
                with self.assertRaisesRegex(
                    ValueError,
                    '发现 2 条人员记录的“工号”为空（第 2、4 行）',
                ):
                    import_file('people', upload)
                self.assertEqual(People.objects.count(), 0)

    def test_pc_export_has_static_configuration_but_not_enabled_state(self):
        Computer.objects.create(
            computer_name='PC-EXPORT', login_account='EXAMPLE\\alice',
            cpu_model='Core i7', memory_total_gb=32, disk_total_gb=512,
            cpu_physical_core_count=8, cpu_logical_processor_count=16,
            is_active=False,
        )

        headers, row = list(csv.reader(StringIO(export_csv('computers').lstrip('\ufeff'))))

        self.assertIn('CPU 型号', headers)
        self.assertIn('CPU 物理核心数', headers)
        self.assertIn('CPU 逻辑处理器数', headers)
        self.assertIn('内存总量', headers)
        self.assertIn('磁盘总量', headers)
        self.assertNotIn('磁盘摘要', headers)
        self.assertNotIn('是否启用', headers)
        self.assertNotIn('登录账户', headers)
        self.assertNotIn('EXAMPLE\\alice', row)
        self.assertEqual(row[headers.index('CPU 型号')], 'Core i7')
        self.assertEqual(row[headers.index('CPU 物理核心数')], '8')
        self.assertEqual(row[headers.index('CPU 逻辑处理器数')], '16')

    def test_network_inventory_omits_dynamic_active_port_count(self):
        labels = [field.label for field in get_table_definition('networks').fields]
        headers = next(csv.reader(StringIO(export_csv('networks').lstrip('\ufeff'))))

        self.assertNotIn('活跃端口数', labels)
        self.assertNotIn('活跃端口数', headers)
        self.assertNotIn('active_port_count', {
            field.name for field in Network_Device._meta.get_fields()
        })

    def test_network_import_accepts_complete_hybrid_snmp_v3_settings(self):
        import_csv('networks', SimpleUploadedFile(
            'hybrid-network.csv',
            (
                '设备名称,IP地址,连接方式,SSH端口,SSH账号,SSH密码,'
                'SNMP 版本,SNMP 端口,SNMP Community,SNMPv3 用户名,安全级别,'
                '认证协议,认证密码,加密协议,加密密码,上下文,重试次数\n'
                '混合巡检交换机,192.0.2.94,hybrid,2222,ssh-reader,ssh-secret,'
                'v3,1161,unused-community,snmp-reader,authPriv,sha256,'
                'auth-secret,aes128,priv-secret,tenant-a,3\n'
            ).encode('utf-8-sig'),
            content_type='text/csv',
        ))

        device = Network_Device.objects.get(ip='192.0.2.94')
        self.assertEqual(device.connection_type, 'hybrid')
        self.assertEqual(device.port, 2222)
        self.assertEqual(device.username, 'ssh-reader')
        self.assertEqual(device.password, 'ssh-secret')
        self.assertEqual(device.snmp_version, 'v3')
        self.assertEqual(device.snmp_port, 1161)
        self.assertEqual(device.snmp_community, 'unused-community')
        self.assertEqual(device.snmp_username, 'snmp-reader')
        self.assertEqual(device.snmp_security_level, 'authPriv')
        self.assertEqual(device.snmp_auth_protocol, 'sha256')
        self.assertEqual(device.snmp_auth_password, 'auth-secret')
        self.assertEqual(device.snmp_priv_protocol, 'aes128')
        self.assertEqual(device.snmp_priv_password, 'priv-secret')
        self.assertEqual(device.snmp_context_name, 'tenant-a')
        self.assertEqual(device.snmp_retries, 3)

    def test_server_inventory_matches_pc_static_configuration_detail(self):
        server = Server.objects.create(
            name='SRV-INVENTORY', ip='192.0.2.120', os='Windows Server 2025',
            os_version='24H2', os_build='26100', system_installed_at='2026-01-02',
            manufacturer='Dell', model='PowerEdge R760', serial_number='SRV-SN-01',
            architecture='x86_64', cpu_model='Xeon Gold',
            cpu_physical_core_count=24, cpu_logical_processor_count=48,
            memory_total_gb=128, disk_total_gb=4096,
        )
        labels = [field.label for field in get_table_definition('servers').fields]
        rows = list(csv.DictReader(StringIO(export_csv('servers').lstrip('\ufeff'))))

        for label in (
            '系统版本', '系统构建号', '系统安装时间', '制造商', '型号', '序列号',
            '系统架构', 'CPU 型号', 'CPU 物理核心数', 'CPU 逻辑处理器数',
            '内存总量', '磁盘总量',
        ):
            self.assertIn(label, labels)
            self.assertIn(label, rows[0])
        self.assertEqual(rows[0]['序列号'], server.serial_number)

    def test_filtered_pc_export_omits_login_account_header_and_data(self):
        login_reader(self.client)
        Computer.objects.create(
            computer_name='PC-FILTERED', login_account='EXAMPLE\\filtered-user',
        )
        Computer.objects.create(
            computer_name='PC-OTHER', login_account='EXAMPLE\\other-user',
        )

        response = self.client.get(reverse('table_export', args=['computers']), {
            'filter_computer_name': 'PC-FILTERED',
        })
        rows = list(csv.DictReader(StringIO(response_body(response).decode('utf-8-sig'))))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('登录账户', rows[0])
        self.assertNotIn('login_account', rows[0])
        self.assertEqual([row['PC 名称'] for row in rows], ['PC-FILTERED'])
        self.assertNotIn('EXAMPLE\\filtered-user', response_body(response).decode('utf-8-sig'))

    def test_import_accepts_blank_inventory_values_and_rejects_negative_counts_atomically(self):
        import_csv('networks', SimpleUploadedFile(
            'network.csv',
            'IP地址,CPU 型号,内存总量,磁盘总量,端口总数,VLAN 数量\n'
            '192.0.2.91,Switch CPU,,,48,12\n'.encode('utf-8-sig'),
            content_type='text/csv',
        ))
        network = Network_Device.objects.get(ip='192.0.2.91')
        self.assertEqual(network.cpu_model, 'Switch CPU')
        self.assertIsNone(network.memory_total_gb)
        self.assertIsNone(network.disk_total_gb)
        self.assertEqual((network.port_count, network.vlan_count), (48, 12))

        with self.assertRaisesRegex(ValueError, '端口总数'):
            import_csv('networks', SimpleUploadedFile(
                'invalid-network.csv',
                'IP地址,端口总数\n192.0.2.92,-1\n'.encode('utf-8-sig'),
                content_type='text/csv',
            ))
        self.assertFalse(Network_Device.objects.filter(ip='192.0.2.92').exists())

    def test_people_import_parses_dates_and_rejects_invalid_dates_atomically(self):
        import_csv('people', SimpleUploadedFile(
            'people.csv',
            '工号,入职日期,离职日期\nP-101,2024-01-02,\n'.encode('utf-8-sig'),
            content_type='text/csv',
        ))
        person = People.objects.get(employee_id='P-101')
        self.assertEqual(person.hire_date, date(2024, 1, 2))
        self.assertIsNone(person.departure_date)

        with self.assertRaisesRegex(ValueError, '入职日期'):
            import_csv('people', SimpleUploadedFile(
                'invalid-people.csv',
                '工号,入职日期\nP-102,2024/01/02\n'.encode('utf-8-sig'),
                content_type='text/csv',
            ))
        self.assertFalse(People.objects.filter(employee_id='P-102').exists())


class ComputerSnapshotInventoryTests(TestCase):
    def test_snapshot_keeps_existing_static_text_when_new_payload_is_blank(self):
        computer = Computer.objects.create(computer_name='PC-SNAPSHOT', cpu_model='Existing CPU')
        analysis = type('Analysis', (), {'details': {
            'system_info': {'制造商': 'Dell', '型号': 'Latitude 7450', '序列号': 'SN-001', '系统架构': 'x64'},
            'network_info': [],
            'computer_info': {
                'CPU型号': '', 'CPU核心数': 16, '当前内存容量': '32 GB', '磁盘总量': '1 TB',
            },
        }})()

        update_computer_snapshot(computer, analysis)
        computer.refresh_from_db()

        self.assertEqual(computer.cpu_model, 'Existing CPU')
        self.assertEqual(computer.manufacturer, 'Dell')
        self.assertEqual(computer.cpu_physical_core_count, 16)
        self.assertEqual(computer.memory_total_gb, Decimal('32.00'))
        self.assertEqual(computer.disk_total_gb, Decimal('1024.00'))

    def test_snapshot_extracts_only_static_inventory_not_utilization(self):
        snapshot = extract_computer_snapshot(
            {}, [], {'CPU型号': 'Ryzen 7', '当前内存容量': '16GB', 'CPU使用率': '91%'},
        )

        self.assertEqual(snapshot['cpu_model'], 'Ryzen 7')
        self.assertEqual(snapshot['memory_total_gb'], Decimal('16.00'))
        self.assertNotIn('cpu_usage_percent', snapshot)
        self.assertNotIn('manufacturer', extract_computer_snapshot({}, [], {}))

    def test_snapshot_extracts_distinct_cpu_counts_without_dynamic_disk_summary(self):
        """Static topology belongs in inventory while disk layout stays in analyses."""
        snapshot = extract_computer_snapshot({}, [], {
            'CPU物理核心数': 8,
            'CPU逻辑处理器数': 16,
            '磁盘摘要': 'C: 476 GB NVMe; D: 931 GB SSD',
        })

        self.assertEqual(snapshot['cpu_physical_core_count'], 8)
        self.assertEqual(snapshot['cpu_logical_processor_count'], 16)
        self.assertNotIn('disk_summary', snapshot)


class ComputerInventoryDemoSeedTests(TestCase):
    def test_demo_seed_populates_completed_pc_inventory(self):
        """Leaving new PC inventory blank would make the demo hide the completed schema."""
        call_command('seed_demo_data', reset=True, stdout=StringIO())

        pc = Computer.objects.get(computer_name='DEMO-PC-OPS-01')
        self.assertEqual(pc.cpu_physical_core_count, 12)
        self.assertEqual(pc.cpu_logical_processor_count, 14)


class AssetInventoryRefreshTests(TestCase):
    def test_refresh_updates_only_allowlisted_static_values(self):
        from net.devices.inventory import refresh_asset_inventory

        network = Network_Device.objects.create(ip='192.0.2.93')
        changed = refresh_asset_inventory(network, {
            'cpu_model': 'Switch ASIC',
            'memory_total_gb': 8,
            'disk_total_gb': 64,
            'port_count': 48,
            'active_port_count': 40,
            'vlan_count': 12,
            'cpu_usage_percent': 99,
        })
        network.refresh_from_db()

        self.assertEqual(changed, {
            'cpu_model', 'memory_total_gb', 'disk_total_gb',
            'port_count', 'vlan_count',
        })
        self.assertEqual(network.cpu_model, 'Switch ASIC')
        self.assertFalse(hasattr(network, 'active_port_count'))
        self.assertFalse(hasattr(network, 'cpu_usage_percent'))


class PeopleInventoryDateSyncTests(TestCase):
    def test_directory_sync_updates_dates_only_when_the_provider_supplies_them(self):
        from net.people.directory import DirectoryPerson
        from net.people.directory.base import directory_source_configuration_identity
        from net.people.directory.sync import apply_people_sync, preview_people_sync
        from net.models import PeopleSyncSource

        source = PeopleSyncSource.objects.create(
            source_type='feishu', name='总部目录', source_key='dates-hq',
            credentials={'app_id': 'fixture-app', 'app_secret': 'fixture-secret'},
            root_department_ids=['root'],
        )

        class Adapter:
            source_key = source.source_key
            fetch_configuration_identity = directory_source_configuration_identity(source)
            last_snapshot_complete = False

            def iter_people(self):
                yield DirectoryPerson(
                    employee_id='P-103', external_user_id='ou-103', hire_date='2024-01-02',
                )
                self.last_snapshot_complete = True

        apply_people_sync(source, preview_people_sync(source, Adapter()))
        person = People.objects.get(employee_id='P-103')
        self.assertEqual(person.hire_date, date(2024, 1, 2))
        self.assertIsNone(person.departure_date)
