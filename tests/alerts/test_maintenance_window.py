"""Maintenance windows hold alert deliveries instead of dropping them."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from net.alerts.service import maintenance_targets
from net.models import MaintenanceWindow, Network_Device


class MaintenanceWindowTests(TestCase):
    def setUp(self):
        self.device = Network_Device.objects.create(
            device_name="接入交换机", ip="192.0.2.77", vendor="h3c",
        )
        self.now = timezone.now()

    def _window(self, *, start_minutes=-10, end_minutes=10, enabled=True):
        return MaintenanceWindow.objects.create(
            device_id=self.device.pk,
            starts_at=self.now + timedelta(minutes=start_minutes),
            ends_at=self.now + timedelta(minutes=end_minutes),
            note="夜间割接", is_enabled=enabled,
        )

    def test_active_uses_a_half_open_interval(self):
        window = self._window(start_minutes=0, end_minutes=30)
        self.assertTrue(window.is_active(self.now))
        self.assertFalse(window.is_active(self.now + timedelta(minutes=30)))
        self.assertFalse(window.is_active(self.now - timedelta(seconds=1)))

    def test_disabled_window_is_never_active(self):
        window = self._window(enabled=False)
        self.assertFalse(window.is_active(self.now))

    def test_active_windows_are_reported_for_delivery_filtering(self):
        self._window()
        self.assertEqual(maintenance_targets(self.now), {str(self.device.pk)})

    def test_expired_and_future_windows_are_not_reported(self):
        self._window(start_minutes=-60, end_minutes=-30)
        self._window(start_minutes=30, end_minutes=60)
        self.assertEqual(maintenance_targets(self.now), set())

    def test_end_must_be_after_start(self):
        window = MaintenanceWindow(
            device_id=self.device.pk,
            starts_at=self.now, ends_at=self.now - timedelta(minutes=5),
        )
        with self.assertRaises(Exception):
            window.full_clean()
