"""Maintenance windows created and removed from the device detail page."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from net.models import MaintenanceWindow, Network_Device


class MaintenanceWindowPageTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username="ops-admin", password="pw-for-test", is_staff=True,
        )
        self.client.force_login(self.admin)
        self.device = Network_Device.objects.create(
            device_name="核心交换机", ip="192.0.2.10", vendor="huawei",
        )

    def _payload(self, start, end, note="更换模块"):
        return {
            "starts_at": start.strftime("%Y-%m-%dT%H:%M"),
            "ends_at": end.strftime("%Y-%m-%dT%H:%M"),
            "note": note,
        }

    def test_detail_page_offers_the_window_block(self):
        response = self.client.get(reverse("asset_detail", args=["networks", self.device.pk]))
        self.assertContains(response, "维护窗口")
        self.assertContains(response, "新建窗口")

    def test_admin_creates_a_window(self):
        start = timezone.now() + timedelta(hours=1)
        response = self.client.post(
            reverse("asset_maintenance_create", args=["networks", self.device.pk]),
            self._payload(start, start + timedelta(hours=2)), follow=True,
        )
        self.assertEqual(response.status_code, 200)
        window = MaintenanceWindow.objects.get()
        self.assertEqual(str(window.device_id), str(self.device.pk))
        self.assertEqual(window.note, "更换模块")

    def test_end_before_start_is_rejected(self):
        start = timezone.now() + timedelta(hours=2)
        self.client.post(
            reverse("asset_maintenance_create", args=["networks", self.device.pk]),
            self._payload(start, start - timedelta(minutes=30)), follow=True,
        )
        self.assertFalse(MaintenanceWindow.objects.exists())

    def test_window_is_listed_with_its_phase(self):
        now = timezone.now()
        MaintenanceWindow.objects.create(
            device_id=self.device.pk, starts_at=now - timedelta(minutes=10),
            ends_at=now + timedelta(minutes=50), note="正在变更",
        )
        response = self.client.get(reverse("asset_detail", args=["networks", self.device.pk]))
        self.assertContains(response, "进行中")
        self.assertContains(response, "正在变更")

    def test_admin_deletes_a_window(self):
        window = MaintenanceWindow.objects.create(
            device_id=self.device.pk, starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(hours=1),
        )
        self.client.post(reverse(
            "asset_maintenance_delete", args=["networks", self.device.pk, window.pk],
        ), follow=True)
        self.assertFalse(MaintenanceWindow.objects.exists())

    def test_non_admin_cannot_create(self):
        reader = get_user_model().objects.create_user("reader", password="pw-for-test")
        self.client.force_login(reader)
        start = timezone.now() + timedelta(hours=1)
        self.client.post(
            reverse("asset_maintenance_create", args=["networks", self.device.pk]),
            self._payload(start, start + timedelta(hours=1)),
        )
        self.assertFalse(MaintenanceWindow.objects.exists())
