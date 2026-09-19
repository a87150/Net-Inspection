"""Reader pages retain data but never render administrator configuration."""
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse

from index.alerts.views import alert_modal_context
from index.inspections.tasks import task_modal_context
from index.people.integrations import people_modal_context
from index.common.access import access_context
from net.models import ComputerAnalysisProfile, Domain_Controller_Config, PCUploadConfig, People, Server, TaskRun


class Forms(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == 'form':
            self.forms.append(dict(attrs))


class ReaderControlsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.reader = users.objects.create_user('ui-reader')
        cls.staff = users.objects.create_user('ui-staff', is_staff=True)
        cls.superuser = users.objects.create_user('ui-super', is_superuser=True)
        cls.person = People.objects.create(name='Readable Person', employee_id='UI-001')
        cls.server = Server.objects.create(name='Readable Server', ip='192.0.2.15', server_type='linux')
        cls.canary = 'reader-secret-config-91da.invalid'
        PCUploadConfig.objects.create(pk=1, endpoint_url=f'https://{cls.canary}/api/pc/logs/')
        cls.profile = ComputerAnalysisProfile.objects.create(name='secret-profile-91da', analysis_items=['resource'])
        cls.task = TaskRun.objects.create(task_type='computer_analysis', source='manual', analysis_profile=cls.profile)

    def test_reader_lists_keep_tables_and_exports_without_operation_forms(self):
        self.client.force_login(self.reader)
        urls = [reverse('asset_list', args=[kind]) for kind in
                ('people', 'computers', 'networks', 'servers', 'monitors')]
        urls += [reverse('alert_list'), reverse('computer_log_list'),
                 reverse('computer_analysis_list'), reverse('domain_account_list'),
                 reverse('task_list')]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                if url == reverse('computer_analysis_list'):
                    self.assertContains(response, 'id="latest-analysis-title"')
                    self.assertContains(response, reverse('task_detail', args=[self.task.pk]))
                else:
                    self.assertContains(response, 'data-table-workspace')
                    self.assertContains(response, 'data-table-query-form')
                for control in ('id="importModal"', 'id="profileConfigModal"',
                                'id="runTaskModal"', 'id="alertPolicyModal"',
                                'id="alertChannelModal"', 'id="addDeviceModal"',
                                'id="domainOperationModal"', 'configurations.zip',
                                'name="password"', 'name="api_key"'):
                    self.assertNotIn(control, response.content.decode(), msg=url)
                post_forms = [form for form in Forms(response.content.decode()).forms
                              if form.get('method', '').lower() == 'post']
                self.assertEqual([form.get('action') for form in post_forms], [reverse('logout')])
                self.assertContains(response, reverse('logout'))
                self.assertNotContains(response, reverse('admin:index'))
                self.assertNotContains(response, reverse('domain_controller_settings'))
        self.assertContains(self.client.get(reverse('asset_list', args=['people'])), 'Readable Person')
        self.assertContains(self.client.get(reverse('table_export', args=['people'])), 'Readable Person')

    def test_administrators_keep_asset_controls(self):
        for user in (self.staff, self.superuser):
            self.client.force_login(user)
            response = self.client.get(reverse('asset_list', args=['servers']))
            for control in ('id="importModal"', 'id="profileConfigModal"',
                            'id="runTaskModal"', 'id="addDeviceModal"'):
                self.assertContains(response, control)

    def test_reader_context_builders_do_not_load_configuration(self):
        request = RequestFactory().get('/?task_modal=profile&import=api&alert_modal=policy')
        request.user = self.reader
        request.session = SessionStore()
        with self.assertNumQueries(0):
            task_context = task_modal_context(request, 'computers')
            alerts = alert_modal_context(request)
            people = people_modal_context(request)
        self.assertIsNone(task_context.get('pc_log_source_form'))
        self.assertIsNone(task_context.get('task_default_profile'))
        self.assertFalse(alerts)
        self.assertFalse(people)

    def test_sensitive_modal_canaries_never_render_for_reader(self):
        canary = 'READER-MUST-NOT-SEE-CONFIG-91da'
        for template in ('inspections/profile_modal.html', 'devices/pc/config_modal.html',
                         'alerts/channel_modal.html', 'common/import_modal.html',
                         'inspections/issue_settings.html'):
            with self.subTest(template=template):
                html = render_to_string(template, {
                    'can_administer': False, 'item_key': 'people',
                    'task_project_kind': 'servers',
                    'task_default_profile': {'name': canary},
                    'pc_log_source_form': canary, 'alert_channel_form': canary,
                    'people_import_error': canary,
                })
                self.assertNotIn(canary, html)
                self.assertNotIn('<form', html)

    def test_saved_configuration_is_absent_from_reader_responses(self):
        url = reverse('asset_list', args=['computers']) + '?task_modal=profile'
        self.client.force_login(self.reader)
        response = self.client.get(url)
        for value in (self.canary, 'secret-user-91da', 'secret-profile-91da'):
            self.assertNotContains(response, value)
        self.assertIsNone(response.context.get('pc_log_source_form'))
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(url), self.canary)

    def test_task_and_asset_details_remain_readable_without_actions(self):
        for user in (self.reader, self.staff, self.superuser):
            self.client.force_login(user)
            response = self.client.get(reverse('task_detail', args=[self.task.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(reverse('task_cancel', args=[self.task.pk]) in response.content.decode(),
                             user != self.reader)
            response = self.client.get(reverse('asset_detail', args=['servers', self.server.pk]))
            self.assertContains(response, 'Readable Server')
            self.assertEqual('?task_modal=run' in response.content.decode(), user != self.reader)

    def test_navbar_login_logout_and_admin_settings(self):
        for user in (AnonymousUser(), self.reader, self.staff, self.superuser):
            request = RequestFactory().get('/')
            request.user = user
            html = render_to_string('common/base.html', access_context(request), request=request)
            self.assertEqual(reverse('login') in html, not user.is_authenticated)
            self.assertEqual(reverse('domain_controller_settings') in html,
                             user in (self.staff, self.superuser))
            if user.is_authenticated:
                self.assertIn('name="csrfmiddlewaretoken"', html)
                self.assertEqual(Forms(html).forms, [{'method': 'post', 'action': reverse('logout')}])

    def test_inactive_staff_cannot_build_admin_context(self):
        self.staff.is_active = False
        request = RequestFactory().get('/')
        request.user = self.staff
        request.session = SessionStore()
        with self.assertNumQueries(0):
            self.assertEqual(task_modal_context(request, 'computers'), {'task_default_profile': None})
            self.assertEqual(alert_modal_context(request), {})
            self.assertEqual(people_modal_context(request), {})

    def test_staff_can_cancel_domain_tasks_without_legacy_permissions(self):
        from net.domain.sync_tasks import enqueue_domain_sync
        Domain_Controller_Config.objects.create(
            host='dc.invalid', base_dn='DC=invalid', bind_username='fixture@invalid')
        task = enqueue_domain_sync()
        self.client.force_login(self.reader)
        response = self.client.get(reverse('task_detail', args=[task.pk]))
        self.assertNotContains(response, reverse('domain_controller_settings'))
        self.client.force_login(self.staff)
        response = self.client.post(reverse('task_cancel', args=[task.pk]))
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, 'cancelled')

    def test_staff_domain_settings_accept_validation_without_legacy_permissions(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse('domain_controller_settings'), {'action': 'save'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors)

    def test_administrator_people_preview_preserves_phone_text(self):
        phone = '+86 00123-456 ext 09'
        html = render_to_string('integrations/preview_modal.html', {
            'can_administer': True,
            'people_preview': {'creates': [{'name': 'New Person', 'phone': phone}],
                               'updates': [{'name': 'Updated Person', 'phone': '0012345'}]},
        })
        self.assertIn('<th>手机号</th>', html)
        self.assertIn(phone, html)
        self.assertIn('0012345', html)
