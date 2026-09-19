from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from tests.auth import login_admin, login_reader

from net.models import Computer


class AssetDashboardUiTests(TestCase):
    def setUp(self):
        login_admin(self.client)
        self.pc = Computer.objects.create(
            computer_name='PC-DETAIL',
            cpu_model='Intel Core i7',
        )

    def test_home_uses_pc_copy_and_bottom_right_actions(self):
        """A card-layout regression must expose the latest-state copy and aligned actions."""
        response = self.client.get(reverse('index'))

        self.assertContains(response, '>PC<')
        self.assertContains(response, '人员总数')
        self.assertContains(response, '设备总数', count=3)
        self.assertContains(response, '上次分析日期')
        self.assertContains(response, '上次巡检日期')
        self.assertContains(response, 'metric-card__footer metric-card__actions')
        self.assertContains(response, 'metric-card__last-run')
        self.assertContains(response, '>域控管理<')
        self.assertContains(response, '域账号')
        self.assertContains(response, '域计算机')
        self.assertContains(response, '域分组')

    def test_pc_detail_exposes_inventory_but_not_enabled(self):
        """PC details must retain static inventory while hiding collection-only enabled state."""
        login_reader(self.client)
        response = self.client.get(
            reverse('asset_detail', args=['computers', self.pc.pk]),
        )

        self.assertContains(response, 'PC详情')
        self.assertContains(response, 'CPU 型号')
        self.assertNotContains(response, '是否启用')

    def test_pc_list_uses_pc_copy_in_the_empty_state_and_data_note(self):
        """Replacing PC-facing text with ordinary-computer wording must be caught."""
        response = self.client.get(reverse('asset_list', args=['computers']))

        self.assertContains(response, 'PC 资料由终端采集器通过 API 上报日志后自动建立')
        self.assertNotContains(response, '共享目录或 FTP')
class DashboardHierarchyTests(TestCase):
    def setUp(self):
        login_admin(self.client)

    def summaries(self):
        common = {'total': 10, 'normal': 10, 'abnormal': 0, 'unchecked': 0, 'last_run_at': None}
        return [
            {'key': 'people', **common},
            {'key': 'domain_accounts', **common},
            {'key': 'domain_computers', **common},
            {'key': 'domain_groups', **common},
            {'key': 'computers', **common},
            {'key': 'networks', **common, 'normal': 6, 'abnormal': 4},
            {'key': 'servers', **common, 'normal': 9, 'abnormal': 1},
            {'key': 'monitors', **common},
        ]

    def test_cards_keep_the_original_fixed_order_even_when_attention_counts_change(self):
        with patch('index.dashboard.views.build_asset_card_summaries', return_value=self.summaries()):
            response = self.client.get(reverse('index'))

        keys = [item['key'] for item in response.context['items']]
        self.assertEqual(
            keys,
            ['people', 'domain', 'computers', 'networks', 'servers', 'monitors'],
        )

    def test_every_card_has_one_primary_action_and_compact_secondary_actions(self):
        with patch('index.dashboard.views.build_asset_card_summaries', return_value=self.summaries()):
            response = self.client.get(reverse('index'))

        for item in response.context['items']:
            with self.subTest(card=item['key']):
                self.assertTrue(item['primary_action']['url'])
                self.assertIn('secondary_actions', item)
        self.assertContains(response, 'metric-card__primary-action', count=6)
        self.assertContains(response, 'metric-card__secondary-actions')
        self.assertContains(response, 'dashboard-refreshed-at')

    def test_empty_task_region_is_collapsed_but_remains_available(self):
        with patch('index.dashboard.views.build_asset_card_summaries', return_value=self.summaries()):
            response = self.client.get(reverse('index'))

        self.assertContains(response, 'dashboard-task-region--empty')
        self.assertContains(response, '暂无任务，展开查看执行中心')
        self.assertContains(response, 'inspection-taskbar')
