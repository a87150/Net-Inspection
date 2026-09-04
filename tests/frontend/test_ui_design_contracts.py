from django.test import TestCase
from django.urls import reverse


class SharedInterfaceContractTests(TestCase):
    def test_core_pages_use_shared_application_chrome(self):
        urls = (
            reverse('index'),
            reverse('asset_list', args=['people']),
            reverse('people_statistics'),
            reverse('asset_list', args=['computers']),
            reverse('computer_analysis_list'),
            reverse('asset_list', args=['networks']),
            reverse('domain_controller_settings'),
            reverse('task_list'),
            reverse('inspection_records'),
            reverse('alert_list'),
        )

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'app-header')
                self.assertContains(response, 'app-content')
                self.assertContains(response, 'id="main-content"')

    def test_dashboard_exposes_scan_friendly_overview_and_task_table(self):
        response = self.client.get(reverse('index'))

        self.assertContains(response, 'dashboard-grid')
        self.assertContains(response, 'metric-card__status-row')
        self.assertContains(response, 'data-table')
        self.assertContains(response, '人员')
        self.assertContains(response, 'PC')
        self.assertContains(response, '网络设备')
        self.assertContains(response, '服务器')
        self.assertContains(response, '安防设备')
        self.assertContains(response, '域控管理')
        self.assertContains(response, '巡检任务栏')

    def test_asset_table_marks_scroll_context_and_action_column(self):
        response = self.client.get(reverse('asset_list', args=['networks']))

        self.assertContains(response, 'table-scroll-shell')
        self.assertContains(response, 'table-scroll-hint')
        self.assertContains(response, 'class="table data-table')
        self.assertContains(response, 'table-actions-column')
        self.assertContains(response, 'table-actions-cell')

    def test_long_configuration_modal_has_shared_scroll_shell_and_sections(self):
        response = self.client.get(reverse('asset_list', args=['computers']))

        self.assertContains(response, 'id="profileConfigModal"')
        self.assertContains(response, 'modal-dialog-scrollable modal-shell')
        self.assertContains(response, 'modal-section')

    def test_domain_and_import_modals_use_shared_modal_shell(self):
        domain = self.client.get(reverse('domain_controller_settings'))
        people = self.client.get(reverse('asset_list', args=['people']))

        self.assertContains(domain, 'modal-dialog-scrollable modal-shell')
        self.assertContains(people, 'class="modal-dialog modal-lg modal-dialog-scrollable modal-shell')
