from pathlib import Path

from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from net.models import PCLogSourceConfig
from net.devices.pc.credentials import load_pc_source_secret, store_pc_source_secret
from net.devices.pc.connectors.base import RemoteLogEntry, select_entries
from django.utils import timezone
from .test_source_models import valid_smb_source


@override_settings(PC_LOG_SOURCE_ENCRYPTION_KEY=Fernet.generate_key().decode())
class SimpleSourceFormTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user('simple-admin', is_staff=True))

    def submit(self, **values):
        return self.client.post(reverse('pc_log_source_save'), {
            'smb_auth_mode': 'credentials',
            'source_type': 'smb', 'shared_path': r'\\files.test\PCLogs\incoming',
            'username': r'CORP\reader', 'password': 'test-only-secret', **values,
        })

    def test_three_smb_inputs_save_complete_connection(self):
        response = self.submit()
        self.assertEqual(response.status_code, 302)
        source = PCLogSourceConfig.load()
        self.assertEqual((source.host, source.share_name, source.port), ('files.test', 'PCLogs', 445))
        self.assertEqual(source.remote_incoming_directory, 'incoming')
        self.assertEqual(source.remote_processed_directory, 'incoming/_processed')
        self.assertEqual(source.remote_failed_directory, 'incoming/_failed')
        self.assertEqual(source.terminal_windows_path, r'\\files.test\PCLogs\incoming')
        self.assertTrue(Path(source.local_staging_directory).is_absolute())
        self.assertEqual((source.file_time_mode, source.recent_days), ('recent_days', 7))
        self.assertEqual(load_pc_source_secret(source), 'test-only-secret')
        page = self.client.get(response['Location'])
        self.assertTrue(page.context['profile_modal_auto_open'])

    def test_share_root_is_supported_without_collecting_archives(self):
        response = self.submit(shared_path=r'\\files.test\PCLogs')
        self.assertEqual(response.status_code, 302)
        source = PCLogSourceConfig.load()
        self.assertEqual(source.remote_incoming_directory, '.')
        now = timezone.now()
        entries = [RemoteLogEntry(name, 2, now) for name in ('pc.json', '_processed/old.json', '_failed/bad.json')]
        self.assertEqual([row.path for row in select_entries(source, entries)], ['pc.json'])

    def test_ftp_does_not_require_a_windows_script_path(self):
        response = self.submit(source_type='ftp', shared_path='', host='ftp.test',
                               ftp_directory='/logs/incoming', username='reader')
        self.assertEqual(response.status_code, 302)
        source = PCLogSourceConfig.load()
        self.assertEqual((source.host, source.port), ('ftp.test', 21))
        self.assertEqual((source.remote_root_directory, source.remote_incoming_directory),
                         ('/', 'logs/incoming'))
        self.assertEqual(source.terminal_windows_path, '')
        self.assertTrue(source.ftp_passive)

    def test_edit_preserves_custom_paths_root_and_password(self):
        source = valid_smb_source(
            host='files.test', share_name='PCLogs', remote_root_directory='department',
            local_staging_directory=str(settings.BASE_DIR / 'custom-staging'),
            terminal_windows_path=r'\\alias.test\PCLogs\incoming',
        )
        store_pc_source_secret(source, 'existing-secret')
        response = self.submit(shared_path=r'\\files.test\PCLogs\department\incoming',
                               password='', recent_days=14)
        self.assertEqual(response.status_code, 302)
        source.refresh_from_db()
        self.assertEqual(source.remote_root_directory, 'department')
        self.assertEqual(source.remote_incoming_directory, 'incoming')
        self.assertEqual(source.remote_processed_directory, 'processed')
        self.assertEqual(source.local_staging_directory, str(settings.BASE_DIR / 'custom-staging'))
        self.assertEqual(source.terminal_windows_path, r'\\alias.test\PCLogs\incoming')
        self.assertEqual(load_pc_source_secret(source), 'existing-secret')
        self.assertEqual(source.recent_days, 14)

    def test_invalid_shared_path_shows_editable_field_and_does_not_save(self):
        for value in (r'C:\logs', r'\\host', r'\\host\share\..\private', r'\\?\C:\logs'):
            with self.subTest(value=value):
                response = self.submit(shared_path=value)
                self.assertEqual(response.status_code, 400)
                self.assertContains(response, 'name="shared_path"', status_code=400)
                self.assertFalse(PCLogSourceConfig.objects.exists())

    def test_page_and_validation_failure_keep_advanced_fields_collapsed(self):
        response = self.client.get(reverse('computer_analysis_list'))
        self.assertContains(response, 'name="shared_path"')
        self.assertContains(response, '<details data-source-advanced')
        self.assertNotContains(response, '<details data-source-advanced open')
        response = self.submit(username='')
        self.assertContains(response, 'data-pc-source-form', status_code=400)
        self.assertContains(response, 'name="shared_path"', status_code=400)

    def test_real_form_blanks_use_defaults_and_unchecked_flags_stay_off(self):
        response = self.submit(
            simple_source_form='1', port='', local_staging_directory='',
            remote_processed_directory='', remote_failed_directory='',
            terminal_windows_path='', file_time_mode='date_range',
            range_start_date='2026-09-01', range_end_date='2026-09-07',
        )
        self.assertEqual(response.status_code, 302)
        source = PCLogSourceConfig.load()
        self.assertEqual(source.remote_processed_directory, 'incoming/_processed')
        self.assertEqual(source.terminal_windows_path, r'\\files.test\PCLogs\incoming')
        self.assertIsNone(source.recent_days)
        self.assertEqual(str(source.range_start_date), '2026-09-01')
        self.assertFalse(source.recursive)
        self.assertFalse(source.ftp_passive)

    def test_invalid_protocol_and_malicious_derived_field_are_rejected_not_crashed(self):
        response = self.submit(source_type='unsupported', remote_incoming_directory='../outside')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PCLogSourceConfig.objects.exists())
