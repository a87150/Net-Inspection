"""Configuration diff view: access, comparison and the single-version case."""
import hashlib
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from cryptography.fernet import Fernet
from django.test import RequestFactory, TestCase, override_settings

from index.devices.backups import configuration_backup_diff
from net.models import DeviceConfigurationBackup, Network_Device


class ConfigurationDiffViewTests(TestCase):
    key = Fernet.generate_key().decode()

    def setUp(self):
        self.admin = SimpleNamespace(
            is_authenticated=True, is_active=True, is_staff=True, is_superuser=True,
        )
        self.device = Network_Device.objects.create(
            device_name="核心交换机", ip="192.0.2.10", vendor="cisco",
        )

    def _backup(self, text, day, captured):
        raw = text.encode("utf-8")
        return DeviceConfigurationBackup.objects.create(
            device_type="network_device", device_id=self.device.pk,
            backup_date=day, captured_at=captured,
            filename=f"sw-{day.isoformat()}.cfg", media_type="text/plain",
            scope="running", vendor="cisco",
            sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw),
            ciphertext=Fernet(self.key).encrypt(raw),
        )

    def _request(self, backup_id, **params):
        request = RequestFactory().get("/diff/", params)
        request.user = self.admin
        return request

    def test_diff_compares_against_the_previous_version_by_default(self):
        self._backup("hostname sw1\n!\nntp server 10.0.0.1\n",
                     date(2026, 11, 15), datetime(2026, 11, 15, 2, tzinfo=timezone.utc))
        newer = self._backup("hostname sw1\n!\nntp server 10.0.0.2\n",
                             date(2026, 11, 16), datetime(2026, 11, 16, 2, tzinfo=timezone.utc))
        with override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=self.key):
            response = configuration_backup_diff(
                self._request(newer.pk), "networks", self.device.pk, newer.pk,
            )
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertIn("新增 1 行", page)
        self.assertIn("删除 1 行", page)
        self.assertIn("ntp server 10.0.0.2", page)

    def test_explicit_against_parameter_selects_the_other_side(self):
        oldest = self._backup("a\n", date(2026, 11, 14),
                              datetime(2026, 11, 14, 2, tzinfo=timezone.utc))
        self._backup("b\n", date(2026, 11, 15),
                     datetime(2026, 11, 15, 2, tzinfo=timezone.utc))
        newest = self._backup("c\n", date(2026, 11, 16),
                              datetime(2026, 11, 16, 2, tzinfo=timezone.utc))
        with override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=self.key):
            response = configuration_backup_diff(
                self._request(newest.pk, against=str(oldest.pk)),
                "networks", self.device.pk, newest.pk,
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("新增 1 行", response.content.decode())

    def test_single_version_explains_there_is_nothing_to_compare(self):
        only = self._backup("hostname sw1\n", date(2026, 11, 16),
                            datetime(2026, 11, 16, 2, tzinfo=timezone.utc))
        response = configuration_backup_diff(
            self._request(only.pk), "networks", self.device.pk, only.pk,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("没有更早的版本", response.content.decode())

    def test_unknown_backup_id_is_not_found(self):
        self._backup("hostname sw1\n", date(2026, 11, 16),
                     datetime(2026, 11, 16, 2, tzinfo=timezone.utc))
        response = configuration_backup_diff(
            self._request(uuid4()), "networks", self.device.pk, uuid4(),
        )
        self.assertEqual(response.status_code, 404)

    def test_decryption_failure_reports_service_unavailable(self):
        older = self._backup("a\n", date(2026, 11, 15),
                             datetime(2026, 11, 15, 2, tzinfo=timezone.utc))
        newer = self._backup("b\n", date(2026, 11, 16),
                             datetime(2026, 11, 16, 2, tzinfo=timezone.utc))
        other_key = Fernet.generate_key().decode()
        with override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=other_key):
            response = configuration_backup_diff(
                self._request(newer.pk), "networks", self.device.pk, newer.pk,
            )
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(str(older.pk), response.content.decode())
