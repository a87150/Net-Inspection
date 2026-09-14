from pathlib import Path
import re

from django.contrib.staticfiles import finders

from django.test import TestCase
from django.template.loader import render_to_string
from django.urls import reverse
from tests.auth import login_admin


class SharedInterfaceContractTests(TestCase):
    def setUp(self):
        login_admin(self.client)

    def test_table_query_actions_share_vertical_centering_contract(self):
        response = self.client.get(reverse('asset_list', args=['networks']))
        html = response.content.decode(response.charset)

        actions = (
            ('data-filter-submit', '筛选'),
            ('data-query-reset', '重置查询'),
            ('data-filtered-export', '导出筛选结果'),
        )
        for marker, label in actions:
            with self.subTest(marker=marker):
                match = re.search(
                    rf'<(?:button|a)[^>]*{marker}[^>]*>{label}</(?:button|a)>',
                    html,
                )
                self.assertIsNotNone(match)
                self.assertIn(
                    'd-inline-flex align-items-center justify-content-center',
                    match.group(),
                )

    def test_shared_brand_partial_renders_application_identity_and_destination(self):
        html = render_to_string('common/brand.html', {'brand_url': '/destination/'})

        self.assertIn('href="/destination/"', html)
        self.assertIn('网络巡检中心', html)
        self.assertIn('Operations Console', html)
    def test_shared_shell_links_ordered_local_design_layers_without_css_imports(self):
        template_root = Path(__file__).resolve().parents[2] / 'index' / 'templates'
        base = (template_root / 'common' / 'base.html').read_text(encoding='utf-8')
        entrypoint = Path(finders.find('app/css/style.css'))
        source = entrypoint.read_text(encoding='utf-8')
        layers = ('tokens.css', 'foundation.css', 'operations.css', 'modal-workflows.css')

        self.assertNotIn('@import', source)
        positions = [base.index(f"app/css/{name}") for name in layers]
        self.assertEqual(positions, sorted(positions))
        combined = ''.join((entrypoint.parent / name).read_text(encoding='utf-8') for name in layers)
        for selector in (':root', '.app-navbar', '.data-table', '.glass-card', '.modal-surface'):
            with self.subTest(selector=selector):
                self.assertIn(selector, combined)

    def test_page_specific_workflows_are_not_loaded_by_the_shared_shell(self):
        template_root = Path(__file__).resolve().parents[2] / 'index' / 'templates'
        base = (template_root / 'common' / 'base.html').read_text(encoding='utf-8')

        self.assertIn('{% block page_scripts %}', base)
        self.assertNotIn('common/table_tools.js', base)
        self.assertNotIn('inspections/task_ui.js', base)
        self.assertNotIn('people/import_tasks.js', base)
        self.assertNotIn('people/modal.js', base)

        device_list = (template_root / 'devices' / 'list.html').read_text(encoding='utf-8')
        self.assertIn("common/scripts/table_workspace.html", device_list)
        self.assertIn("common/scripts/modal_workflows.html", device_list)
        self.assertIn("common/scripts/inspection_workflows.html", device_list)
        self.assertIn("common/scripts/people_workflows.html", device_list)
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
        self.assertContains(response, '运维管理台')
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
        self.assertContains(analyses, '最新任务统计')
        self.assertNotContains(analyses, 'execution-list-workspace')

    def test_dashboard_cards_use_glass_surface_without_status_side_rail(self):
        response = self.client.get(reverse('index'))

        self.assertContains(response, 'glass-card')
        self.assertContains(response, 'data-dashboard-card-state')
        self.assertNotContains(response, 'metric-card--danger')
        self.assertNotContains(response, 'metric-card--success')

    def test_asset_table_marks_scroll_context_and_action_column(self):
        response = self.client.get(reverse('asset_list', args=['networks']))

        self.assertContains(response, 'table-scroll-shell')
        self.assertContains(response, '<caption class="visually-hidden">网络设备数据表</caption>', html=True)
        self.assertContains(response, 'data-active-filter-list')
        self.assertContains(response, 'scope="col"')
        self.assertContains(response, 'for="networks-keyword-filter"')
        self.assertContains(response, 'id="networks-keyword-filter"')
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

        expected_steps = ('profile', 'parameters', 'targets', 'items', 'schedule')
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

    def test_collection_template_workflow_uses_glass_list_and_scroll_shell(self):
        template_root = Path(__file__).resolve().parents[2] / 'index' / 'templates'
        body = (template_root / 'devices' / 'collection_settings_body.html').read_text(encoding='utf-8')
        modal = (template_root / 'devices' / 'collection_template_modal.html').read_text(encoding='utf-8')

        self.assertIn('collection-template-workspace', body)
        self.assertIn('collection-template-picker', body)
        self.assertIn('name="edit"', body)
        self.assertIn('class="form-select"', body)
        self.assertIn('打开模板', body)
        self.assertNotIn('class="interactive-list"', body)
        self.assertIn('modal-dialog modal-xl modal-dialog-scrollable modal-shell', modal)
        self.assertIn('modal-footer modal-footer--sticky', modal)
        self.assertIn('id="collectionSettingsForm"', body)
        self.assertIn('form="collectionSettingsForm"', modal)

    def test_plain_history_and_task_links_use_the_shared_interactive_list(self):
        template_root = Path(__file__).resolve().parents[2] / 'index' / 'templates'
        paths = (
            'devices/pc/log_detail.html',
            'common/import_modal.html',
            'domain/sync_task_list.html',
        )

        for relative_path in paths:
            with self.subTest(template=relative_path):
                source = (template_root / relative_path).read_text(encoding='utf-8')
                self.assertIn('interactive-list', source)
                self.assertIn('interactive-list__item', source)

    def test_legacy_error_cards_use_shared_status_surfaces(self):
        template_root = Path(__file__).resolve().parents[2] / 'index' / 'templates'
        paths = (
            'devices/pc/inspection_detail.html',
            'inspections/detail.html',
            'inspections/issue_findings.html',
            'inspections/record_detail.html',
        )

        for relative_path in paths:
            with self.subTest(template=relative_path):
                source = (template_root / relative_path).read_text(encoding='utf-8')
                self.assertNotIn('card border-danger', source)
                self.assertIn('surface-card', source)

    def test_shared_interactive_list_has_responsive_accessible_states(self):
        css = Path(finders.find('app/css/foundation.css')).read_text(encoding='utf-8')

        for selector in (
            '.interactive-list',
            '.interactive-list__item',
            '.interactive-list__item:hover',
            '.interactive-list__item:focus-visible',
            '.interactive-list__meta',
            '.interactive-list__empty',
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, css)

    def test_shared_buttons_center_text_for_links_and_native_buttons(self):
        css = Path(finders.find('app/css/foundation.css')).read_text(encoding='utf-8')
        rule = re.search(r'(?m)^\.btn\s*\{(?P<body>[^}]*)\}', css)

        self.assertIsNotNone(rule)
        self.assertIn('display: inline-flex', rule.group('body'))
        self.assertIn('align-items: center', rule.group('body'))
        self.assertIn('justify-content: center', rule.group('body'))
        self.assertIn('line-height: 1.2', rule.group('body'))

    def test_remaining_standalone_content_cards_use_the_shared_surface(self):
        template_root = Path(__file__).resolve().parents[2] / 'index' / 'templates'
        paths = (
            'registration/login.html',
            'common/table_filter.html',
            'public/list.html',
            'public/summary.html',
            'inspections/traffic_table.html',
        )

        for relative_path in paths:
            with self.subTest(template=relative_path):
                source = (template_root / relative_path).read_text(encoding='utf-8')
                self.assertIn('surface-card', source)


class AdminVisualContractTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        self.admin_user = get_user_model().objects.create_superuser(
            username='admin-theme-test',
            email='admin-theme@example.test',
            password='unused',
        )

    def test_admin_login_only_renders_the_login_card_and_password_reset(self):
        response = self.client.get(reverse('admin:login'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'admin-login-shell')
        self.assertContains(response, 'admin-login-card')
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'name="password"')
        self.assertContains(response, 'href="/admin/password_reset/"')
        self.assertContains(response, 'app/css/style.css')
        self.assertContains(response, 'app/css/admin.css')
        self.assertNotContains(response, '<header id="header"')
        self.assertNotContains(response, '安全访问')
        self.assertNotContains(response, '使用管理员账号管理资产、策略与后台数据。')
        self.assertNotContains(response, '<footer id="footer"')

    def test_admin_password_reset_form_is_available(self):
        response = self.client.get('/admin/password_reset/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="email"')
        self.assertContains(response, 'type="submit"')

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
        self.assertContains(response, '返回运维总览')
        self.assertContains(response, '修改密码')
        self.assertContains(response, '退出登录')
        self.assertNotContains(response, '>注销<')
        self.assertNotContains(response, '欢迎')
        self.assertNotContains(response, 'admin/js/theme.js')
        self.assertNotContains(response, '查看站点')
        self.assertContains(response, 'app/js/common/form_accessibility.js')
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
        self.assertGreaterEqual(
            accounts.content.decode(accounts.charset).count('modal-footer--sticky'),
            2,
        )

    def test_issue_settings_loads_in_the_current_glass_modal_without_iframe(self):
        records = self.client.get(reverse('computer_analysis_list'))
        settings = self.client.get(reverse('issue_severity_settings') + '?project=computers')

        self.assertContains(records, 'data-modal-load="issueSeverityModal"')
        self.assertNotContains(records, '<iframe')
        for response in (records, settings):
            self.assertContains(response, 'id="issueSeverityModal"')
            self.assertContains(response, 'modal-dialog-scrollable modal-shell')
            self.assertContains(response, 'modal-content modal-surface')
            self.assertContains(response, 'modal-footer modal-footer--sticky')

    def test_remaining_workflow_modals_use_the_shared_shell(self):
        template_root = Path(__file__).resolve().parents[2] / 'index' / 'templates'
        paths = (
            'devices/add_modal.html',
            'devices/pc/bulk_analysis_modal.html',
            'domain/bitlocker_modal.html',
            'access/source_modal.html',
        )
        for relative_path in paths:
            with self.subTest(template=relative_path):
                source = (template_root / relative_path).read_text(encoding='utf-8')
                self.assertIn('modal-shell', source)
                self.assertIn('modal-surface', source)
                self.assertIn('modal-footer--sticky', source)

    def test_templates_do_not_use_inline_native_confirmation(self):
        template_root = Path(__file__).resolve().parents[2] / 'index' / 'templates'
        sources = '\n'.join(path.read_text(encoding='utf-8') for path in template_root.rglob('*.html'))

        self.assertNotIn('onsubmit="return confirm(', sources)
        self.assertIn('data-confirm-message=', sources)

class PcListActionContractTests(TestCase):
    def test_pc_list_header_does_not_offer_script_downloads(self):
        template_path = Path(__file__).resolve().parents[2] / 'index' / 'templates' / 'devices' / 'list.html'
        html = template_path.read_text(encoding='utf-8')
        actions_start = html.index('<div class="page-heading__actions">')
        actions_end = html.index('</header>', actions_start)
        header_actions = html[actions_start:actions_end]

        self.assertNotIn('下载 Windows 采集脚本', header_actions)
        self.assertNotIn('下载 macOS 采集脚本', header_actions)
