from datetime import date, datetime, timezone

from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from net.devices.pc.credentials import load_pc_source_secret, store_pc_source_secret
from net.models import Computer, ComputerLogFile, ComputerLogTransfer, PCLogSourceConfig


def valid_smb_source(**overrides):
    fields = {
        'source_type': PCLogSourceConfig.SourceType.SMB,
        'host': 'files.test',
        'port': 445,
        'username': 'svc-pc-logs',
        'domain': 'EXAMPLE',
        'share_name': 'logs',
        'remote_root_directory': 'pc',
        'remote_incoming_directory': 'incoming',
        'local_staging_directory': 'C:/pc-stage',
        'terminal_windows_path': r'\\files.test\logs\incoming',
        'file_time_mode': 'recent_days',
    }
    fields.update(overrides)
    return PCLogSourceConfig.objects.create(**fields)


@override_settings(PC_LOG_SOURCE_ENCRYPTION_KEY=Fernet.generate_key().decode('ascii'))
class PCLogSourceModelTests(TestCase):
    def setUp(self):
        self.pc = Computer.objects.create(computer_name='PC-SOURCE-01')

    def test_singleton_rejects_a_second_source(self):
        """Allowing a second source would make the Worker pick an ambiguous origin."""
        valid_smb_source(pk=1)

        with self.assertRaises(ValidationError):
            PCLogSourceConfig(pk=2, source_type='ftp', host='ftp.test').full_clean()

    def test_load_returns_the_singleton_source(self):
        """A missing or non-singleton source must not become an implicit Worker choice."""
        self.assertIsNone(PCLogSourceConfig.load())

        source = valid_smb_source()

        self.assertEqual(PCLogSourceConfig.load(), source)

    def test_encrypted_password_round_trips_without_appearing_on_source(self):
        """Persisting plaintext on the source object would expose a connection credential."""
        source = valid_smb_source()

        store_pc_source_secret(source, 'not-plaintext')

        self.assertEqual(load_pc_source_secret(source), 'not-plaintext')
        self.assertNotIn('not-plaintext', str(source.public_data()))
        self.assertNotIn('not-plaintext', bytes(source.credential.encrypted_payload).decode('latin1'))

    def test_one_computer_has_only_one_imported_log_per_day(self):
        """A second imported daily log would produce duplicate default analysis work."""
        log_fields = {
            'source_path': 'legacy://PC-SOURCE-01.json',
            'modified_at': datetime(2026, 9, 4, tzinfo=timezone.utc),
            'file_size': 42,
            'platform': 'windows',
            'source_protocol': 'smb',
            'remote_source_path': 'incoming/PC-SOURCE-01-20260904.json',
        }
        ComputerLogFile.objects.create(
            computer=self.pc,
            collected_date=date(2026, 9, 4),
            content_hash='a' * 64,
            import_status='imported',
            **log_fields,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ComputerLogFile.objects.create(
                    computer=self.pc,
                    collected_date=date(2026, 9, 4),
                    content_hash='b' * 64,
                    import_status='imported',
                    **log_fields,
                )

    def test_source_reuses_one_active_transfer_for_remote_identity(self):
        """Two active rows for one remote version would race to import and archive it."""
        source = valid_smb_source()
        observed_at = datetime(2026, 9, 4, tzinfo=timezone.utc)
        transfer_fields = {
            'source': source,
            'remote_source_path': 'incoming/PC-SOURCE-01-20260904.json',
            'observed_mtime': observed_at,
            'local_staging_path': 'C:/pc-stage/PC-SOURCE-01.part',
            'remote_size': 42,
        }
        ComputerLogTransfer.objects.create(**transfer_fields)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ComputerLogTransfer.objects.create(**transfer_fields)
