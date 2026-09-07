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

    def test_brand_uses_text_identity_without_letter_mark(self):
        response = self.client.get(reverse('index'))

        self.assertContains(response, 'app-brand__copy')
        self.assertNotContains(response, 'app-brand__mark')

    def test_operations_console_context_links_to_django_admin(self):
        response = self.client.get(reverse('index'))

        self.assertContains(response, f'href="{reverse("admin:index")}"')
        self.assertContains(response, '运维管理台')
        self.assertContains(response, 'admin-user-tools__links')
        self.assertContains(response, 'admin-top-action')
        self.assertContains(response, 'app-brand__copy')

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

    def test_execution_and_department_surfaces_use_the_shared_glass_workspace(self):
        dashboard = self.client.get(reverse('index'))
        statistics = self.client.get(reverse('people_statistics'))
        task_list = self.client.get(reverse('task_list'))
        records = self.client.get(reverse('inspection_records'))
        analyses = self.client.get(reverse('computer_analysis_list'))

        self.assertContains(dashboard, 'execution-workspace')
        self.assertContains(dashboard, 'execution-workspace__summary')
        self.assertContains(statistics, 'department-workspace')
        self.assertContains(statistics, 'department-workspace__summary')
        self.assertContains(task_list, 'execution-list-workspace')
        self.assertContains(records, 'execution-list-workspace')
        self.assertContains(analyses, 'execution-list-workspace')

    def test_dashboard_cards_use_glass_surface_without_status_side_rail(self):
        response = self.client.get(reverse('index'))

        self.assertContains(response, 'glass-card')
        self.assertContains(response, 'data-dashboard-card-state')
        self.assertNotContains(response, 'metric-card--danger')
        self.assertNotContains(response, 'metric-card--success')

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
        self.assertContains(response, 'inspection-config-modal')
        self.assertContains(response, 'task-run-modal')
        self.assertContains(response, 'modal-dialog-scrollable modal-shell')
        self.assertContains(response, 'modal-section')

    def test_inspection_profile_modal_uses_flat_ordered_configuration_blocks(self):
        response = self.client.get(reverse('asset_list', args=['networks']))
        html = response.content.decode(response.charset)

        expected_steps = ('profile', 'parameters', 'items', 'targets', 'schedule')
        positions = [html.index(f'data-config-step="{step}"') for step in expected_steps]
        self.assertEqual(positions, sorted(positions))
        self.assertContains(response, 'inspection-config-layout')

    def test_scheduled_target_rules_use_a_dedicated_glass_section(self):
        response = self.client.get(reverse('asset_list', args=['networks']))

        self.assertContains(response, 'inspection-config-section--targets')
        self.assertContains(response, 'target-device-picker')
        self.assertContains(response, 'data-target-device-vendor')
        self.assertContains(response, 'data-target-device-type')
        self.assertNotContains(response, '定时目标范围')

    def test_domain_and_import_modals_use_shared_modal_shell(self):
        domain = self.client.get(reverse('domain_controller_settings'))
        people = self.client.get(reverse('asset_list', args=['people']))

        self.assertContains(domain, 'modal-dialog-scrollable modal-shell')
        self.assertContains(people, 'class="modal-dialog modal-lg modal-dialog-scrollable modal-shell')

    def test_alert_configuration_modals_use_glass_sections_and_persistent_actions(self):
        response = self.client.get(reverse('alert_list'))

        self.assertContains(response, 'alert-policy-modal')
        self.assertContains(response, 'alert-channel-modal')
        self.assertContains(response, 'modal-section')
        self.assertContains(response, 'modal-footer')

    def test_alert_channel_management_is_embedded_in_the_configuration_modal(self):
        response = self.client.get(reverse('alert_list'))

        self.assertContains(response, 'alert-channel-management')
        self.assertContains(response, 'alert-channel-editor')
        self.assertNotContains(response, '渠道管理：</span>')


class AdminVisualContractTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.admin_user = get_user_model().objects.create_superuser(
            username='admin-theme-test',
            email='admin-theme@example.test',
            password='unused',
        )

    def test_admin_login_uses_the_operations_console_theme(self):
        response = self.client.get(reverse('admin:login'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'admin-login-shell')
        self.assertContains(response, 'app-shell')
        self.assertContains(response, 'app-navbar app-header')
        self.assertContains(response, 'app-navbar__inner')
        self.assertContains(response, 'app-main')
        self.assertContains(response, 'app/css/style.css')
        self.assertContains(response, 'app/css/admin.css')
        self.assertContains(response, '网络巡检中心')

    def test_admin_index_uses_the_operations_console_theme(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('admin:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'admin-shell')
        self.assertContains(response, 'app-shell')
        self.assertContains(response, 'app-navbar app-header')
        self.assertContains(response, 'app-navbar__inner')
        self.assertContains(response, 'app-main')
        self.assertContains(response, 'app/css/style.css')
        self.assertContains(response, 'app/css/admin.css')
        self.assertContains(response, '运维管理台')
        self.assertContains(response, 'admin-user-tools__links')
        self.assertContains(response, 'admin-top-action')
        self.assertContains(response, 'app-brand__copy')
