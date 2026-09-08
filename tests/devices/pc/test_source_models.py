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
        'smb_auth_mode': 'credentials',
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
    def test_database_constraints_are_unconditional_for_mysql_compatibility(self):
        """Conditional unique indexes are silently skipped by the production MySQL backend."""
        daily = next(
            constraint for constraint in ComputerLogFile._meta.constraints
            if constraint.name == 'net_pc_imported_log_daily_uniq'
        )
        transfer = next(
            constraint for constraint in ComputerLogTransfer._meta.constraints
            if constraint.name == 'net_pc_transfer_active_identity_uniq'
        )

        self.assertIsNone(daily.condition)
        self.assertIsNone(transfer.condition)
        self.assertTrue(ComputerLogFile._meta.get_field('daily_import_marker').generated)
        self.assertTrue(ComputerLogTransfer._meta.get_field('active_identity_marker').generated)
        self.assertEqual(
            ComputerLogTransfer._meta.get_field('remote_source_path').max_length,
            512,
        )

    def test_imported_log_requires_daily_identity(self):
        """An imported row without a computer and collection date cannot be deduplicated."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ComputerLogFile.objects.create(
                    source_path='remote://missing-identity.json',
                    modified_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
                    content_hash='c' * 64,
                    file_size=42,
                    import_status='imported',
                    platform='windows',
                    source_protocol='smb',
                    remote_source_path='incoming/missing-identity.json',
                )

    def test_database_rejects_an_unknown_source_protocol(self):
        """Choices alone do not protect direct ORM or database writes."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                valid_smb_source(source_type='webdav')

    def test_persisted_connection_errors_redact_the_saved_password(self):
        """Raw protocol exceptions must not expose credentials through admin or public data."""
        source = valid_smb_source()
        store_pc_source_secret(source, 'transport-private')
        source.last_test_error = 'login password=transport-private; echoed transport-private'
        source.save(update_fields=['last_test_error'])
        source.refresh_from_db()

        transfer = ComputerLogTransfer.objects.create(
            source=source,
            remote_source_path='incoming/PC-SOURCE-01-20260904.json',
            observed_mtime=datetime(2026, 9, 4, tzinfo=timezone.utc),
            error_message='connection password=transport-private; echoed transport-private',
        )

        self.assertNotIn('transport-private', source.last_test_error)
        self.assertNotIn('transport-private', str(source.public_data()))
        self.assertNotIn('transport-private', transfer.error_message)
        self.assertNotIn('transport-private', transfer.public_error_message())
        self.assertIn('[REDACTED]', source.last_test_error)
        self.assertIn('[REDACTED]', transfer.error_message)

        with self.assertRaises(ValidationError):
            PCLogSourceConfig.objects.filter(pk=source.pk).update(
                last_test_error='transport-private',
            )
        with self.assertRaises(ValidationError):
            ComputerLogTransfer.objects.filter(pk=transfer.pk).update(
                error_message='transport-private',
            )
        with self.assertRaises(ValidationError):
            ComputerLogTransfer.objects.bulk_update(
                [transfer],
                ['error_message'],
            )

        bulk_transfer = ComputerLogTransfer(
            source=source,
            remote_source_path='incoming/PC-SOURCE-02-20260904.json',
            observed_mtime=datetime(2026, 9, 4, tzinfo=timezone.utc),
            error_message='bulk password=transport-private',
        )
        ComputerLogTransfer.objects.bulk_create([bulk_transfer])
        bulk_transfer.refresh_from_db()
        self.assertNotIn('transport-private', bulk_transfer.error_message)
