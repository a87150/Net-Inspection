from django.test import TestCase
from django.urls import reverse

from net.models import Computer


class AssetDashboardUiTests(TestCase):
    def setUp(self):
        self.pc = Computer.objects.create(
            computer_name='PC-DETAIL',
            cpu_model='Intel Core i7',
        )

    def test_home_uses_pc_copy_and_bottom_right_actions(self):
        """A card-layout regression must expose the latest-state copy and aligned actions."""
        response = self.client.get(reverse('index'))

        self.assertContains(response, '>PC<')
        self.assertContains(response, '人员总数')
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
        response = self.client.get(
            reverse('asset_detail', args=['computers', self.pc.pk]),
        )

        self.assertContains(response, 'PC详情')
        self.assertContains(response, 'CPU 型号')
        self.assertNotContains(response, '是否启用')

    def test_pc_list_uses_pc_copy_in_the_empty_state_and_data_note(self):
        """Replacing PC-facing text with ordinary-computer wording must be caught."""
        response = self.client.get(reverse('asset_list', args=['computers']))

        self.assertContains(response, 'PC 数据由 PowerShell 自动采集上报。')
        self.assertNotContains(response, '计算机数据由 PowerShell 自动采集上报。')
