from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class WindowsServerScriptDownloadTests(TestCase):
    url = '/servers/scripts/windows/'

    def test_admin_can_download_agent_from_server_inspection_page(self):
        self.client.force_login(get_user_model().objects.create_superuser('script-admin', password='test'))
        page = self.client.get(reverse('record_list', args=['servers']))
        self.assertContains(page, f'href="{self.url}"')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Disposition'], 'attachment; filename="InspectionHttpService.ps1"')
        self.assertEqual(response.content, (Path(settings.BASE_DIR) / 'agents/server/windows/InspectionHttpService.ps1').read_bytes())
        self.assertEqual(self.client.post(self.url).status_code, 405)

    def test_anonymous_must_login_to_download(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('next=', response['Location'])

    def test_non_admin_cannot_download_or_see_button(self):
        self.client.force_login(get_user_model().objects.create_user('script-reader', password='test'))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertNotContains(self.client.get(reverse('record_list', args=['servers'])), f'href="{self.url}"')
