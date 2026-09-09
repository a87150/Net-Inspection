"""Offline contracts for encrypted, restorable configuration versions."""
from datetime import datetime, timedelta, timezone as dt_timezone
from importlib import import_module, util
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from net.data_exchange.adapters import UnsupportedConfiguration
from net.models import Network_Device, SecurityDevice


RAW = ('version 15.2\r\nhostname edge\r\nusername admin password 0 test-password\r\n'
       'snmp-server community test-key rw\r\nend\r\n')


def item(content=RAW, **changes):
    result = dict(status='success', vendor='cisco', format='text', content=content,
                  scope='running-config', complete=True)
    result.update(changes)
    return result


@override_settings(TIME_ZONE='Asia/Shanghai')
class ConfigurationBackupTests(TestCase):
    def setUp(self):
        self.assertIsNotNone(util.find_spec('net.devices.configuration_backups'),
                             'The backup storage service must exist')
        self.api = import_module('net.devices.configuration_backups')
        self.enterContext(override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=Fernet.generate_key()))
        self.asset = Network_Device.objects.create(ip='192.0.2.10', vendor='cisco')
        self.at = datetime(2026, 9, 1, 15, 59, tzinfo=dt_timezone.utc)

    def store(self, payload=None, at=None, asset=None):
        return self.api.store_configuration_backup(
            asset or self.asset, payload if payload is not None else item(),
            captured_at=at or self.at)

    def test_unredacted_roundtrip_and_metadata_only_listing(self):
        backup = self.store()
        backup.refresh_from_db()
        self.assertEqual(self.api.read_configuration_backup(backup), RAW.encode())
        self.assertNotIn(b'test-password', bytes(backup.ciphertext))
        self.assertNotIn(b'test-key', bytes(backup.ciphertext))
        self.assertEqual(backup.byte_size, len(RAW.encode()))
        self.assertEqual(backup.scope, 'running-config')
        self.assertEqual(backup.media_type, 'text/plain')
        self.assertIn('ciphertext', self.api.list_configuration_backups(self.asset).first().get_deferred_fields())
        self.assertEqual(self.api.latest_configuration_backup(self.asset).pk, backup.pk)

    def test_bytes_encoding_and_line_endings_are_preserved(self):
        raw = RAW.replace('hostname edge', 'hostname 核心').encode('gb18030')
        backup = self.store(item(raw, encoding='gb18030'))
        self.assertEqual(self.api.read_configuration_backup(backup), raw)

    def test_h3c_native_configuration(self):
        raw = 'sysname core\r\nlocal-user admin\r\n password simple test-password\r\nreturn\r\n'
        backup = self.store(item(raw, vendor='h3c', scope='current-configuration'))
        self.assertEqual(self.api.read_configuration_backup(backup), raw.encode())

    def test_first_success_wins_day_and_unchanged_next_day_is_retained(self):
        self.assertTrue(self.api.backup_due(self.asset, at=self.at))
        first = self.store()
        second = self.store(item(RAW.replace('edge', 'changed')))
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self.api.read_configuration_backup(second), RAW.encode())
        self.assertFalse(self.api.backup_due(self.asset, at=self.at))
        tomorrow = self.at + timedelta(minutes=2)
        with timezone.override('America/New_York'):
            self.assertTrue(self.api.backup_due(self.asset, at=tomorrow))
            third = self.store(at=tomorrow)
        self.assertNotEqual(first.pk, third.pk)
        self.assertEqual(str(third.backup_date), '2026-09-02')
        self.assertEqual(self.api.list_configuration_backups(self.asset).count(), 2)

    def test_beijing_day_survives_default_timezone_change(self):
        first = self.store()
        midnight = datetime(2026, 9, 1, 16, 0, tzinfo=dt_timezone.utc)
        with override_settings(TIME_ZONE='UTC'), timezone.override('America/New_York'):
            self.assertFalse(self.api.backup_due(self.asset, at=self.at))
            self.assertTrue(self.api.backup_due(self.asset, at=midnight))
            second = self.store(at=midnight)
            self.assertNotEqual(first.pk, second.pk)
            self.assertEqual(str(second.backup_date), '2026-09-02')
            with patch('net.devices.configuration_backups.timezone.now', return_value=midnight):
                self.assertFalse(self.api.backup_due(self.asset))
            self.assertEqual(self.store(at=midnight + timedelta(hours=8)).pk, second.pk)

    @override_settings(TIME_ZONE='UTC')
    def test_naive_capture_time_is_interpreted_as_beijing_time(self):
        backup = self.store(at=datetime(2026, 9, 2, 0, 0))
        backup.refresh_from_db()
        self.assertEqual(backup.captured_at, datetime(2026, 9, 1, 16, 0, tzinfo=dt_timezone.utc))
        self.assertFalse(self.api.backup_due(
            self.asset, at=datetime(2026, 9, 1, 16, 0, tzinfo=dt_timezone.utc)))

    def test_retains_latest_ten_dates_per_asset(self):
        other = Network_Device.objects.create(ip='192.0.2.11')
        untouched = self.store(asset=other)
        versions = [self.store(at=self.at + timedelta(days=day)) for day in range(11)]
        self.assertEqual(list(self.api.list_configuration_backups(self.asset).values_list('pk', flat=True)),
                         [version.pk for version in reversed(versions[1:])])
        self.assertEqual(self.api.latest_configuration_backup(other).pk, untouched.pk)

    def test_failure_does_not_reserve_day_or_remove_existing(self):
        first = self.store()
        tomorrow = self.at + timedelta(days=1)
        for bad in (item(complete=False), item(status='failed'), item('hostname truncated'),
                    item('hostname edge\n--More--\nend\n')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store(bad, at=tomorrow)
        self.assertTrue(self.api.backup_due(self.asset, at=tomorrow))
        self.assertEqual(self.api.latest_configuration_backup(self.asset).pk, first.pk)
        self.store(at=tomorrow)

    def test_missing_or_invalid_key_never_persists(self):
        for key in (None, '', 'invalid'):
            with self.subTest(key=key), override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=key):
                with self.assertRaises(ImproperlyConfigured):
                    self.store()
        self.assertIsNone(self.api.latest_configuration_backup(self.asset))
        self.store()

    def test_corrupt_ciphertext_wrong_key_and_checksum_are_rejected(self):
        backup = self.store()
        with override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=Fernet.generate_key()):
            with self.assertRaises(ValueError):
                self.api.read_configuration_backup(backup)
        backup.sha256 = '0' * 64
        with self.assertRaises(ValueError):
            self.api.read_configuration_backup(backup)
        backup.refresh_from_db()
        backup.ciphertext = b'broken'
        with self.assertRaises(ValueError):
            self.api.read_configuration_backup(backup)

    def test_partial_security_configuration_is_not_a_backup(self):
        camera = SecurityDevice.objects.create(ip='192.0.2.20')
        with self.assertRaisesRegex(UnsupportedConfiguration, '(?i)partial|section'):
            self.store(item('table.Network.Hostname=camera', vendor='dahua', scope='Network'), asset=camera)
        self.assertIsNone(self.api.latest_configuration_backup(camera))

    def test_trim_failure_rolls_back_save_and_preserves_existing(self):
        for day in range(10):
            self.store(at=self.at + timedelta(days=day))
        before = list(self.api.list_configuration_backups(self.asset).values_list('pk', flat=True))
        with patch('django.db.models.query.QuerySet.delete', side_effect=RuntimeError('trim failed')):
            with self.assertRaises(RuntimeError):
                self.store(at=self.at + timedelta(days=10))
        self.assertEqual(list(self.api.list_configuration_backups(self.asset).values_list('pk', flat=True)), before)

    def test_database_enforces_daily_uniqueness(self):
        backup = self.store()
        backup.pk = None
        with self.assertRaises(IntegrityError), transaction.atomic():
            backup.save(force_insert=True)
