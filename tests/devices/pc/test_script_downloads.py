"""Authorization and configuration contracts for collector downloads."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from net.models import ComputerAnalysisProfile, PCLogSourceConfig

class PcScriptDownloadTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='operator', is_staff=True)
        self.profile = ComputerAnalysisProfile.objects.create(
            name='Daily', analysis_items=['resource'])
        self.source = PCLogSourceConfig.objects.create(
            source_type='ftp', host='worker.invalid', port=21, username='worker-secret',
            remote_incoming_directory='incoming', local_staging_directory='stage',
            file_time_mode='recent_days', terminal_windows_path=r'\\files\incoming',
            terminal_macos_path='/Volumes/Logs')
        self.url = reverse('pc_script_download', args=[self.profile.pk, 'windows'])

    def test_anonymous_redirect_and_nonadmin_forbidden(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.user.is_staff = False
        self.user.save()
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_staff_and_superuser_can_download_without_worker_secrets(self):
        for staff, superuser in [(True, False), (False, True)]:
            self.user.is_staff, self.user.is_superuser = staff, superuser
            self.user.save()
            self.client.force_login(self.user)
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertIn('no-store', response['Cache-Control'].split(', '))
            self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
            self.assertIn('attachment;', response['Content-Disposition'])
            self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
            for forbidden in (b'http', b'password', b'/api/', b'worker-secret'):
                self.assertNotIn(forbidden, response.content)

    def test_missing_saved_source_or_platform_path_is_actionable(self):
        self.client.force_login(self.user)
        self.source.terminal_windows_path = ''
        self.source.save()
        self.assertEqual(self.client.get(self.url).status_code, 400)
        self.source.delete()
        self.assertEqual(self.client.get(self.url).status_code, 400)

    def test_disabled_profile_and_invalid_platform(self):
        self.client.force_login(self.user)
        self.profile.is_enabled = False
        self.profile.save()
        self.assertEqual(self.client.get(self.url).status_code, 400)
        self.assertEqual(self.client.get(reverse('pc_script_download',
                         args=[self.profile.pk, 'linux'])).status_code, 404)
