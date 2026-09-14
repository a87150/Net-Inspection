from datetime import timedelta
import hashlib
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase
from django.urls import reverse
from tests.auth import login_admin
from django.utils import timezone

from net.models import (
    Computer,
    ComputerAnalysis,
    ComputerLogFile,
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
)
from net.dashboard.assets import build_asset_card_summaries
from tests.devices.pc.helpers import create_log_file


class DashboardSummaryTests(TestCase):
    def setUp(self):
        login_admin(self.client)

    def _analysis(self, computer, name, *, status=RecordStatus.SUCCESS, created_at):
        log_file = create_log_file(
            computer=computer,
            source_path=f'C:/logs/{name}.json',
            modified_at=created_at,
            content_hash=hashlib.sha256(name.encode()).hexdigest(),
            import_status='imported',
            payload={'computer_name': computer.computer_name},
        )
        analysis = ComputerAnalysis.objects.create(
            computer=computer,
            log_file=log_file,
            status=status,
            summary=f'{name} analysis',
        )
        ComputerAnalysis.objects.filter(pk=analysis.pk).update(created_at=created_at)
        analysis.refresh_from_db()
        return analysis

    def _network_inspection(
        self, device, *, status=RecordStatus.SUCCESS, is_reachable=True,
    ):
        return Network_Device_Inspection.objects.create(
            device=device,
            status=status,
            is_reachable=is_reachable,
            summary='network inspection',
        )

    def test_pc_status_uses_each_devices_latest_analysis_not_today(self):
        """Changing the newest analysis must change only that PC's card status."""
        yesterday = timezone.now() - timedelta(days=1)
        old_normal = Computer.objects.create(computer_name='PC-OLD-NORMAL')
        abnormal = Computer.objects.create(computer_name='PC-ABNORMAL')
        Computer.objects.create(computer_name='PC-UNCHECKED')
        self._analysis(old_normal, 'old-normal', created_at=yesterday)
        self._analysis(abnormal, 'older-normal', created_at=yesterday)
        self.abnormal = self._analysis(
            abnormal,
            'latest-abnormal',
            status=RecordStatus.FAILED,
            created_at=timezone.now(),
        )

        summary = next(
            item for item in build_asset_card_summaries()
            if item['key'] == 'computers'
        )

        self.assertEqual(summary['normal'], 1)
        self.assertEqual(summary['abnormal'], 1)
        self.assertEqual(summary['unchecked'], 1)
        self.assertEqual(summary['last_run_at'], self.abnormal.created_at)

    def test_pc_analysis_with_errors_is_abnormal(self):
        """Removing the error check would incorrectly classify this PC as normal."""
        computer = Computer.objects.create(computer_name='PC-WITH-ERROR')
        analysis = self._analysis(
            computer, 'analysis-error', created_at=timezone.now(),
        )
        Error_Computer.objects.create(
            inspection=analysis,
            error_type='software',
            error_message='Unsupported software found',
        )

        summary = next(
            item for item in build_asset_card_summaries()
            if item['key'] == 'computers'
        )

        self.assertEqual(summary['normal'], 0)
        self.assertEqual(summary['abnormal'], 1)
        self.assertEqual(summary['unchecked'], 0)

    def test_infrastructure_partial_or_unreachable_is_abnormal(self):
        """Dropping status or reachability from the condition would hide an outage."""
        normal = Network_Device.objects.create(device_name='SW-NORMAL', ip='192.0.2.11')
        partial = Network_Device.objects.create(device_name='SW-PARTIAL', ip='192.0.2.12')
        unreachable = Network_Device.objects.create(
            device_name='SW-UNREACHABLE', ip='192.0.2.13',
        )
        self._network_inspection(normal)
        self._network_inspection(partial, status=RecordStatus.PARTIAL)
        self._network_inspection(unreachable, is_reachable=False)

        summary = next(
            item for item in build_asset_card_summaries()
            if item['key'] == 'networks'
        )

        self.assertEqual(summary['normal'], 1)
        self.assertEqual(summary['abnormal'], 2)

    def test_infrastructure_multiple_inspections_use_latest_and_last_run_at(self):
        """Characterize latest-row selection and category last-run aggregation."""
        device = Network_Device.objects.create(
            device_name='SW-MULTI-INSPECTION', ip='192.0.2.17',
        )
        older_time = timezone.now() - timedelta(days=2)
        latest_time = timezone.now() - timedelta(hours=1)
        older = self._network_inspection(device, status=RecordStatus.FAILED)
        Network_Device_Inspection.objects.filter(pk=older.pk).update(
            created_at=older_time,
        )
        latest = self._network_inspection(device, status=RecordStatus.SUCCESS)
        Network_Device_Inspection.objects.filter(pk=latest.pk).update(
            created_at=latest_time,
        )

        summary = next(
            item for item in build_asset_card_summaries()
            if item['key'] == 'networks'
        )

        self.assertEqual(summary['normal'], 1)
        self.assertEqual(summary['abnormal'], 0)
        self.assertEqual(summary['last_run_at'], latest_time)

    def test_network_success_with_linked_error_is_abnormal(self):
        """A successful reachable inspection with errors is not normal health."""
        device = Network_Device.objects.create(device_name='SW-ERROR', ip='192.0.2.14')
        inspection = self._network_inspection(device)
        Error_Network_Device.objects.create(
            inspection=inspection,
            error_message={'interface': 'down'},
        )

        summary = next(
            item for item in build_asset_card_summaries()
            if item['key'] == 'networks'
        )

        self.assertEqual(summary['normal'], 0)
        self.assertEqual(summary['abnormal'], 1)

    def test_server_success_with_linked_error_is_abnormal(self):
        """Server health must include linked inspection errors."""
        server = Server.objects.create(name='SERVER-ERROR', ip='192.0.2.15')
        inspection = Server_Inspection.objects.create(
            server=server,
            status=RecordStatus.SUCCESS,
            is_reachable=True,
        )
        Error_Server.objects.create(
            inspection=inspection,
            error_message={'disk': 'low'},
        )

        summary = next(
            item for item in build_asset_card_summaries()
            if item['key'] == 'servers'
        )

        self.assertEqual(summary['normal'], 0)
        self.assertEqual(summary['abnormal'], 1)

    def test_monitor_success_with_linked_error_is_abnormal(self):
        """Security device health must include linked inspection errors."""
        monitor = SecurityDevice.objects.create(device_name='CAMERA-ERROR', ip='192.0.2.16')
        inspection = Monitor_Inspection.objects.create(
            monitor=monitor,
            status=RecordStatus.SUCCESS,
            is_reachable=True,
        )
        Error_Monitor.objects.create(
            inspection=inspection,
            error_message={'storage': 'degraded'},
        )

        summary = next(
            item for item in build_asset_card_summaries()
            if item['key'] == 'monitors'
        )

        self.assertEqual(summary['normal'], 0)
        self.assertEqual(summary['abnormal'], 1)

    def test_dashboard_shows_partial_unchecked_inventory_counts(self):
        """One completed result must not hide other assets awaiting their first run."""
        analyzed = Computer.objects.create(computer_name='PC-ANALYZED')
        Computer.objects.create(computer_name='PC-WAITING')
        self._analysis(analyzed, 'analyzed', created_at=timezone.now())

        network = Network_Device.objects.create(device_name='SW-CHECKED', ip='192.0.2.21')
        Network_Device.objects.create(device_name='SW-WAITING', ip='192.0.2.22')
        self._network_inspection(network)

        server = Server.objects.create(name='SERVER-CHECKED', ip='192.0.2.23')
        Server.objects.create(name='SERVER-WAITING', ip='192.0.2.24')
        Server_Inspection.objects.create(server=server, status=RecordStatus.SUCCESS)

        monitor = SecurityDevice.objects.create(device_name='CAMERA-CHECKED', ip='192.0.2.25')
        SecurityDevice.objects.create(device_name='CAMERA-WAITING', ip='192.0.2.26')
        Monitor_Inspection.objects.create(monitor=monitor, status=RecordStatus.SUCCESS)

        response = self.client.get(reverse('index'))
        items = {item['key']: item for item in response.context['items']}

        self.assertEqual(items['computers']['note'], '等待首次分析 1 台')
        for key in ('networks', 'servers', 'monitors'):
            self.assertEqual(items[key]['note'], '等待首次巡检 1 台')
        self.assertContains(response, '等待首次分析 1 台', count=1)
        self.assertContains(response, '等待首次巡检 1 台', count=3)

    def test_dashboard_isolates_one_category_database_error(self):
        """A failed category query must render an error without zeroing or losing peers."""
        People.objects.create(name='仍可展示的人员', employee_id='P-DB-ERROR')

        with patch(
            'net.dashboard.assets._computer_summary',
            side_effect=DatabaseError('computer table unavailable'),
        ), self.assertLogs('net.dashboard.assets', level='ERROR'):
            response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        items = {item['key']: item for item in response.context['items']}
        self.assertEqual(items['people']['total'], 1)
        self.assertIn('error', items['computers'])
        self.assertNotIn('total', items['computers'])
        self.assertNotIn('normal', items['computers'])
        self.assertContains(response, 'PC 统计数据加载失败')
        self.assertContains(response, 'data-dashboard-card-state="error"')

    def test_summary_query_count_does_not_grow_with_asset_count(self):
        """Per-asset queries would make dashboard loading slow as inventory grows."""
        for number in range(20):
            Computer.objects.create(computer_name=f'PC-QUERY-{number}')
            Network_Device.objects.create(
                device_name=f'SW-QUERY-{number}', ip=f'192.0.2.{number + 20}',
            )

        # 人员、域账号、域计算机、域分组及四类设备各使用一条固定汇总查询。
        with self.assertNumQueries(8):
            summaries = build_asset_card_summaries()

        self.assertEqual(
            {summary['key'] for summary in summaries},
            {
                'people', 'domain_accounts', 'domain_computers', 'domain_groups', 'computers',
                'networks', 'servers', 'monitors',
            },
        )

    def test_dashboard_exposes_checked_compatibility_alongside_latest_states(self):
        """The old card field remains an analyzed count until Task 4 renders normal."""
        computer = Computer.objects.create(computer_name='PC-COMPATIBILITY')
        analysis = self._analysis(
            computer, 'compatibility-error', created_at=timezone.now(),
        )
        Error_Computer.objects.create(
            inspection=analysis,
            error_type='software',
            error_message='Unsupported software found',
        )

        response = self.client.get(reverse('index'))
        item = next(
            item for item in response.context['items']
            if item['key'] == 'computers'
        )

        self.assertEqual(item['normal'], 0)
        self.assertEqual(item['abnormal'], 1)
        self.assertEqual(item['unchecked'], 0)
        self.assertEqual(item['checked'], 1)
        self.assertEqual(item['bad'], 1)

    def test_dashboard_groups_ad_objects_in_a_domain_management_card(self):
        """Splitting AD objects into ordinary asset cards would hide their shared management context."""
        response = self.client.get(reverse('index'))
        items = {item['key']: item for item in response.context['items']}

        self.assertEqual(items['computers']['name'], 'PC')
        self.assertEqual(items['computers']['total_label'], 'PC 总数')
        self.assertContains(response, '<h2 class="h5 mb-0">PC</h2>')
        self.assertNotContains(response, 'PC 计算机')
        self.assertIn('domain', items)
        domain = items['domain']
        self.assertEqual(domain['name'], '域控管理')
        self.assertEqual(domain['account_total'], 0)
        self.assertEqual(domain['computer_total'], 0)
        self.assertEqual(domain['group_total'], 0)

    def test_dashboard_cards_render_compact_labels_and_aligned_footer_dates(self):
        response = self.client.get(reverse('index'))
        items = {item['key']: item for item in response.context['items']}

        self.assertEqual(items['computers']['normal_label'], '正常')
        self.assertEqual(items['computers']['abnormal_label'], '异常')
        self.assertContains(response, 'class="metric-card__domain-status"', count=6)
        self.assertContains(
            response,
            'class="metric-card__domain-breakdown flex-column align-items-center"',
            count=3,
        )
        self.assertContains(
            response,
            '<strong class="text-success">0</strong>',
            count=2,
            html=True,
        )
        self.assertContains(
            response,
            '<strong class="text-danger">0</strong>',
            count=2,
            html=True,
        )
        self.assertContains(
            response,
            'class="metric-card__last-run text-secondary mb-0 text-end"',
            count=4,
        )
        self.assertContains(
            response,
            'class="card-footer metric-card__footer metric-card__actions px-4 pb-4 d-flex gap-2 flex-wrap align-items-center justify-content-end"',
            count=6,
        )
