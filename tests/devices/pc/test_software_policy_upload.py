import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from net.models import ComputerAnalysisProfile


class SoftwarePolicyUploadTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('policy-admin', is_staff=True)
        self.client.force_login(self.user)
        self.profile = ComputerAnalysisProfile.objects.create(
            name='PC 软件策略',
            analysis_items=['software'],
            software_policy_path='config/examples/software-policy.ini',
            concurrent_workers=2,
        )
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.settings_override = override_settings(
            PC_SOFTWARE_POLICY_DIR=Path(self.tempdir.name),
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    def _post(self, uploaded_file=None):
        data = {
            'profile_id': str(self.profile.pk),
            'name': self.profile.name,
            'analysis_items': ['software'],
            'concurrent_workers': '2',
        }
        if uploaded_file is not None:
            data['software_policy_file'] = uploaded_file
        return self.client.post(reverse('computer_analysis_profile_configure'), data)

    def test_download_returns_separate_demonstration_policy(self):
        actual_path = Path(settings.BASE_DIR) / 'config/examples/software-policy.ini'
        actual_before = actual_path.read_bytes()

        response = self.client.get(reverse('pc_software_policy_template_download'))

        self.assertEqual(response.status_code, 200)
        self.assertIn('software-policy-demo.ini', response['Content-Disposition'])
        downloaded = b''.join(response.streaming_content).decode('utf-8-sig')
        self.assertIn('[WHITELIST]', downloaded)
        self.assertIn('[SPECIAL_WHITELIST]', downloaded)
        self.assertIn('[BLACKLIST]', downloaded)
        self.assertIn('DEMO-PC-001', downloaded)
        self.assertEqual(actual_path.read_bytes(), actual_before)

    def test_valid_upload_is_saved_as_profile_specific_copy(self):
        actual_path = Path(settings.BASE_DIR) / 'config/examples/software-policy.ini'
        actual_before = actual_path.read_bytes()
        uploaded = SimpleUploadedFile(
            'team-policy.ini',
            '[WHITELIST]\nbase = Browser\n[BLACKLIST]\nkeywords = DemoGame\n'.encode(),
            content_type='text/plain',
        )

        response = self._post(uploaded)

        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        stored_path = Path(self.profile.software_policy_path)
        self.assertEqual(stored_path.parent, Path(self.tempdir.name))
        self.assertEqual(stored_path.name, f'{self.profile.pk}.ini')
        self.assertEqual(stored_path.read_text(encoding='utf-8'),
                         '[WHITELIST]\nbase = Browser\n[BLACKLIST]\nkeywords = DemoGame\n')
        self.assertEqual(actual_path.read_bytes(), actual_before)

    def test_saving_without_upload_keeps_current_policy(self):
        response = self._post()

        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.software_policy_path,
                         'config/examples/software-policy.ini')

    def test_invalid_upload_only_rejects_file_and_keeps_current_profile(self):
        uploaded = SimpleUploadedFile(
            'broken.ini', b'not an ini document', content_type='text/plain',
        )

        response = self._post(uploaded)

        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.software_policy_path,
                         'config/examples/software-policy.ini')
        messages = [str(message) for message in response.wsgi_request._messages]
        self.assertTrue(any('策略文件' in message for message in messages), messages)
        self.assertFalse(any(Path(self.tempdir.name).iterdir()))
