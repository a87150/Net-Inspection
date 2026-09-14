from django.test import TestCase
from django.urls import reverse
from tests.auth import login_admin, login_reader


class ServiceScriptDownloadTests(TestCase):
    def test_download_contains_self_installing_host_and_is_not_cached(self):
        login_admin(self.client)
        response=self.client.get(reverse('windows_server_script_download'))
        self.assertEqual(response.status_code,200)
        self.assertIn('no-store', response['Cache-Control'].split(', '))
        self.assertIn(b'Install-InspectionService',response.content)
        self.assertIn(b'ServiceBase.Run',response.content)
        self.assertIn(b'-RunService',response.content)
        self.assertContains(self.client.get(reverse('asset_list',args=['servers'])), reverse('windows_server_script_download'))

    def test_guests_and_readers_cannot_download(self):
        url=reverse('windows_server_script_download')
        self.assertEqual(self.client.get(url).status_code,302)
        login_reader(self.client)
        self.assertEqual(self.client.get(url).status_code,403)
