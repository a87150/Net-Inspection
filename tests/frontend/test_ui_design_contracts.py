from pathlib import Path

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
        self.assertContains(response, 'Operations Console')
        self.assertContains(response, 'navbar-collapse')
        self.assertContains(response, 'admin-navbar-user')
        self.assertContains(response, '返回运维总览')
        self.assertContains(response, '修改密码')
        self.assertContains(response, '注销')
        self.assertContains(response, 'id="content-main"')
        self.assertContains(response, 'id="content-related"')
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
        self.assertContains(response, 'Operations Console')
        self.assertContains(response, 'navbar-collapse')
        self.assertContains(response, 'admin-navbar-user')
        self.assertContains(response, '返回运维总览')
        self.assertContains(response, '修改密码')
        self.assertContains(response, '注销')
        self.assertContains(response, 'id="content-main"')
        self.assertContains(response, 'id="content-related"')
        self.assertContains(response, 'app-brand__copy')
    def test_admin_theme_is_loaded_after_django_page_and_responsive_styles(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('admin:index'))
        html = response.content.decode(response.charset)

        self.assertLess(html.index('admin/css/dashboard.css'), html.index('app/css/admin.css'))
        self.assertLess(html.index('admin/css/responsive.css'), html.index('app/css/admin.css'))

    def test_admin_list_and_change_pages_keep_the_shared_shell(self):
        self.client.force_login(self.admin_user)
        urls = (
            reverse('admin:auth_user_changelist'),
            reverse('admin:auth_user_change', args=[self.admin_user.pk]),
        )

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'app-navbar app-header')
                self.assertContains(response, 'app/css/admin.css')
                self.assertContains(response, '返回运维总览')

class ModalVisualContractTests(TestCase):
    def test_shared_shell_loads_modal_feedback_controller(self):
        response = self.client.get(reverse('asset_list', args=['networks']))

        self.assertContains(response, 'app/js/common/modal_feedback.js')

    def test_domain_redirect_marks_reopened_dialog_for_inline_feedback(self):
        response = self.client.get(reverse('domain_controller_settings') + '?modal=1')

        self.assertContains(response, 'id="domainConfigModal"')
        self.assertContains(response, 'data-auto-open="true"')

    def test_invalid_domain_account_import_reopens_the_import_dialog(self):
        response = self.client.post(reverse('domain_account_import'), {})

        self.assertRedirects(
            response,
            reverse('domain_account_list') + '?domain_modal=account_import',
            fetch_redirect_response=False,
        )
        page = self.client.get(response.url)
        self.assertContains(page, 'id="domainAccountImportModal"')
        self.assertContains(page, 'data-auto-open="true"')

    def test_invalid_domain_operation_reopens_the_operation_dialog(self):
        response = self.client.post(reverse('domain_operation_create'), {
            'object_type': 'account',
            'action': 'disable',
        })

        self.assertRedirects(
            response,
            reverse('domain_account_list') + '?domain_modal=operation',
            fetch_redirect_response=False,
        )
        page = self.client.get(response.url)
        self.assertContains(page, 'id="domainOperationModal"')
        self.assertContains(page, 'data-auto-open="true"')

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.admin_user = get_user_model().objects.create_superuser(
            username='modal-theme-test',
            email='modal-theme@example.test',
            password='unused',
        )
        self.client.force_login(self.admin_user)

    def test_major_modal_pages_use_the_shared_glass_surface(self):
        urls = (
            reverse('asset_list', args=['people']),
            reverse('asset_list', args=['computers']),
            reverse('asset_list', args=['networks']),
            reverse('domain_controller_settings'),
            reverse('domain_account_list'),
            reverse('alert_list'),
        )

        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'modal-surface')
                self.assertContains(response, 'modal-body--scroll')

    def test_domain_connection_modal_has_grouped_configuration_sections(self):
        response = self.client.get(reverse('domain_controller_settings'))

        self.assertContains(response, 'domain-config-modal')
        self.assertContains(response, 'data-domain-config-section="connection"')
        self.assertContains(response, 'data-domain-config-section="authentication"')
        self.assertContains(response, 'data-domain-config-section="directory"')
        self.assertContains(response, 'data-domain-config-section="filters"')
        self.assertContains(response, 'modal-footer--sticky')

    def test_import_and_operation_modals_use_section_cards_and_sticky_actions(self):
        people = self.client.get(reverse('asset_list', args=['people']))
        accounts = self.client.get(reverse('domain_account_list'))

        self.assertContains(people, 'import-config-modal')
        self.assertContains(people, 'import-config-section')
        self.assertContains(accounts, 'domain-account-import-modal')
        self.assertContains(accounts, 'domain-operation-modal')
        self.assertContains(accounts, 'modal-footer--sticky', count=2)

class PcListActionContractTests(TestCase):
    def test_pc_list_header_does_not_offer_script_downloads(self):
        template_path = Path(__file__).resolve().parents[2] / 'index' / 'templates' / 'devices' / 'list.html'
        html = template_path.read_text(encoding='utf-8')
        actions_start = html.index('<div class="page-heading__actions">')
        actions_end = html.index('</header>', actions_start)
        header_actions = html[actions_start:actions_end]

        self.assertNotIn('下载 Windows 采集脚本', header_actions)
        self.assertNotIn('下载 macOS 采集脚本', header_actions)
