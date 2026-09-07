from tempfile import TemporaryDirectory
from unittest.mock import patch
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from net.devices.pc.credentials import load_pc_source_secret, store_pc_source_secret
from index.devices.pc.forms import PCLogSourceForm
from .test_source_models import valid_smb_source
from .connector_fakes import memory_connector


@override_settings(PC_LOG_SOURCE_ENCRYPTION_KEY=Fernet.generate_key().decode())
class SourceWorkflowTests(TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.source = valid_smb_source(local_staging_directory=self.folder.name)
        store_pc_source_secret(self.source, 'saved-secret')
        self.admin = get_user_model().objects.create_user('source-admin', is_staff=True)
        self.client.force_login(self.admin)

    def values(self):
        return {**self.source.public_data(), 'password': '••••••••', 'source_type': 'ftp',
                'host': 'new.test', 'port': 21, 'domain': '', 'share_name': ''}

    def test_masked_password_survives_protocol_and_host_change(self):
        display = PCLogSourceForm(instance=self.source)
        self.assertIn('••••••••', display.as_p())
        self.assertNotIn('saved-secret', display.as_p())
        form = PCLogSourceForm(self.values(), instance=self.source)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.host, 'new.test')
        self.assertEqual(load_pc_source_secret(saved), 'saved-secret')

    def test_preview_does_not_download_or_move_files(self):
        connector = memory_connector({'incoming/PC01-20260907.json': b'{}'})
        from django.utils import timezone
        connector.modified_at = timezone.now()
        with patch('index.devices.pc.source.build_connector', return_value=connector):
            response = self.client.post(reverse('pc_log_source_preview'))
        self.assertContains(response, 'PC01-20260907.json')
        self.assertEqual(connector.downloads, [])
        self.assertEqual(connector.moves, [])

    def test_staff_required_and_post_only(self):
        self.assertEqual(self.client.get(reverse('pc_log_source_test')).status_code, 405)
        self.admin.is_staff = False
        self.admin.save()
        values = {key: value for key, value in self.values().items() if value is not None}
        self.assertEqual(self.client.post(reverse('pc_log_source_save'), values).status_code, 403)

    def test_parent_traversal_and_overlapping_archives_rejected(self):
        values = self.values()
        values['remote_processed_directory'] = '../outside'
        self.assertFalse(PCLogSourceForm(values, instance=self.source).is_valid())
        values['remote_processed_directory'] = 'failed/nested'
        self.assertFalse(PCLogSourceForm(values, instance=self.source).is_valid())
