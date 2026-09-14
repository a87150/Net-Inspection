from uuid import UUID

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from index.urls import urlpatterns
from net.models import People, Server, TaskRun


class AccessPolicyTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.reader = users.objects.create_user('reader', password='reader-pass')
        self.admin = users.objects.create_user('operator', password='admin-pass', is_staff=True)
        self.person = People.objects.create(name='Contact', employee_id='PRIVATE-001', email='private@example.invalid')
        self.server = Server.objects.create(name='Private server', ip='192.0.2.40', server_type='linux')

    def test_every_application_mutation_requires_administration(self):
        samples = {'kind': 'servers', 'entity': 'people', 'provider': 'feishu',
                   'table_key': 'people', 'scope': 'servers', 'platform': 'windows',
                   'file_format': 'csv', 'item': 'people', 'category': 'servers'}
        for actor in (None, self.reader):
            self.client.logout()
            if actor:
                self.client.force_login(actor)
            for pattern in urlpatterns:
                if pattern.name in {'login', 'logout'}:
                    continue
                kwargs = {key: samples.get(key, 1 if converter.__class__.__name__ == 'IntConverter'
                                           else UUID(int=1))
                          for key, converter in pattern.pattern.converters.items()}
                url = reverse(pattern.name, kwargs=kwargs)
                with self.subTest(actor=bool(actor), url=url):
                    response = self.client.post(url, {})
                    self.assertEqual(response.status_code, 403 if actor else 302)
            self.assertEqual(People.objects.count(), 1)
            self.assertEqual(Server.objects.count(), 1)
            self.assertEqual(TaskRun.objects.count(), 0)

    def test_guest_cannot_read_private_details_or_export(self):
        urls = [reverse('asset_detail', args=['people', self.person.pk]),
                reverse('asset_detail', args=['servers', self.server.pk]),
                reverse('table_export', args=['people']), '/computers/logs/',
                '/tasks/', '/records/servers/', '/domain/accounts/',
                reverse('configuration_zip', args=['networks']),
                reverse('windows_server_script_download')]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.url.startswith('/login/?next='))
                self.assertNotIn(b'private@example.invalid', response.content)
                self.assertIn('no-store', response.headers['Cache-Control'])

    def test_reader_can_read_contacts_and_export_but_not_configuration_files(self):
        self.client.force_login(self.reader)
        response = self.client.get(reverse('table_export', args=['people']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'private@example.invalid')
        self.assertIn('no-store', response.headers['Cache-Control'])
        for url in ('/settings/domain-controller/', '/servers/scripts/windows/',
                    '/assets/networks/configurations.zip', '/integrations/people/preview/',
                    '/data/people/template/'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_admin_can_download_import_template(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/data/people/template/').status_code, 200)

    def test_staff_admin_needs_no_legacy_domain_permission(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/settings/domain-controller/').status_code, 200)
        self.assertEqual(self.client.get('/domain/accounts/import/template/csv/').status_code, 200)

    def test_only_admin_can_add_device_without_starting_inspection(self):
        payload = {'name': 'New server', 'ip': '192.0.2.50', 'server_type': 'linux'}
        self.client.force_login(self.reader)
        self.assertEqual(self.client.post('/assets/servers/add/', payload).status_code, 403)
        self.assertFalse(Server.objects.filter(ip='192.0.2.50').exists())
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post('/assets/servers/add/', payload).status_code, 302)
        self.assertEqual(Server.objects.get(ip='192.0.2.50').name, 'New server')
        self.assertFalse(TaskRun.objects.exists())

    def test_reader_cannot_request_credentials_as_export_columns(self):
        self.server.password = 'ssh-private-canary'
        self.server.api_token = 'api-private-canary'
        self.server.api_url = 'http://user:embedded-private@192.0.2.40/?token=query-private'
        self.server.save()
        self.client.force_login(self.reader)
        response = self.client.get(reverse('table_export', args=['servers']), {
            'columns': 'password,api_token,api_url', 'fields': 'password,api_token',
        })
        self.assertEqual(response.status_code, 200)
        for secret in ('ssh-private-canary', 'api-private-canary', 'embedded-private', 'query-private'):
            self.assertNotContains(response, secret)

    def test_inactive_privileged_user_is_not_administrator(self):
        from index.common.access import is_admin
        self.assertTrue(is_admin(self.admin))
        self.admin.is_active = False
        self.assertFalse(is_admin(self.admin))
        self.reader.is_superuser = True
        self.assertTrue(is_admin(self.reader))

    def test_ordinary_login_and_logout_validate_redirects(self):
        response = self.client.post('/login/?next=https://untrusted.invalid/',
                                    {'username': 'reader', 'password': 'reader-pass'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/')
        self.assertEqual(self.client.session['_auth_user_id'], str(self.reader.pk))
        self.assertEqual(self.client.get('/logout/').status_code, 405)
        self.assertEqual(self.client.post('/logout/').status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_login_does_not_bypass_csrf_or_accept_inactive_account(self):
        protected = Client(enforce_csrf_checks=True)
        self.assertEqual(protected.post('/login/', {'username': 'reader', 'password': 'reader-pass'}).status_code, 403)
        self.reader.is_active = False
        self.reader.save(update_fields=['is_active'])
        response = self.client.post('/login/', {'username': 'reader', 'password': 'reader-pass'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
