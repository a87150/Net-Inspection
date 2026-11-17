"""Batch maintenance windows from the asset list and the maintenance log page."""
import json
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from net.models import MaintenanceWindow, Network_Device, Server


class MaintenanceWindowPageTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            username="ops-admin", password="pw-for-test", is_staff=True,
        )
        self.client.force_login(self.admin)
        self.device = Network_Device.objects.create(
            device_name="核心交换机", ip="192.0.2.10", vendor="huawei",
        )
        self.other = Network_Device.objects.create(
            device_name="汇聚交换机", ip="192.0.2.11", vendor="huawei",
        )

    def _payload(self, start, end, targets=None, note="更换模块"):
        return {
            "target_ids": [str(pk) for pk in (targets or [self.device.pk])],
            "starts_at": start.strftime("%Y-%m-%dT%H:%M"),
            "ends_at": end.strftime("%Y-%m-%dT%H:%M"),
            "note": note,
        }

    def test_list_page_offers_the_batch_button(self):
        response = self.client.get(reverse("asset_list", args=["networks"]))
        self.assertContains(response, "data-maintenance-open")
        self.assertContains(response, "维护窗口")

    def test_one_window_per_selected_asset(self):
        start = timezone.now() + timedelta(hours=1)
        response = self.client.post(
            reverse("asset_maintenance_create", args=["networks"]),
            self._payload(start, start + timedelta(hours=2),
                          targets=[self.device.pk, self.other.pk]),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        windows = MaintenanceWindow.objects.order_by("target_id")
        self.assertEqual(windows.count(), 2)
        self.assertEqual({str(w.target_id) for w in windows},
                         {str(self.device.pk), str(self.other.pk)})
        self.assertEqual({w.target_type for w in windows}, {"network_device"})
        self.assertEqual(windows[0].note, "更换模块")

    def test_servers_get_their_own_target_type(self):
        server = Server.objects.create(name="应用服务器", ip="192.0.2.90")
        start = timezone.now() + timedelta(hours=1)
        self.client.post(
            reverse("asset_maintenance_create", args=["servers"]),
            self._payload(start, start + timedelta(hours=1), targets=[server.pk]),
            follow=True,
        )
        window = MaintenanceWindow.objects.get()
        self.assertEqual(window.target_type, "server")
        self.assertEqual(str(window.target_id), str(server.pk))

    def test_end_before_start_is_rejected(self):
        start = timezone.now() + timedelta(hours=2)
        self.client.post(
            reverse("asset_maintenance_create", args=["networks"]),
            self._payload(start, start - timedelta(minutes=30)), follow=True,
        )
        self.assertFalse(MaintenanceWindow.objects.exists())

    def test_empty_selection_creates_nothing(self):
        start = timezone.now() + timedelta(hours=1)
        payload = self._payload(start, start + timedelta(hours=1))
        payload["target_ids"] = []
        self.client.post(
            reverse("asset_maintenance_create", args=["networks"]), payload, follow=True,
        )
        self.assertFalse(MaintenanceWindow.objects.exists())

    def test_id_of_another_asset_kind_is_refused(self):
        server = Server.objects.create(name="越权服务器", ip="192.0.2.91")
        start = timezone.now() + timedelta(hours=1)
        self.client.post(
            reverse("asset_maintenance_create", args=["networks"]),
            self._payload(start, start + timedelta(hours=1), targets=[server.pk]),
            follow=True,
        )
        self.assertFalse(MaintenanceWindow.objects.exists())

    def _bootstrap(self, response):
        """The page ships its first paint as json_script, so assert the payload."""
        self.assertEqual(response.status_code, 200)
        html = response.content.decode(response.charset)
        match = re.search(
            r'<script id="maintenance-bootstrap" type="application/json">(.*?)</script>',
            html, re.S,
        )
        self.assertIsNotNone(match, "页面缺少注水用的 bootstrap payload")
        return json.loads(match.group(1))

    def test_maintenance_page_lists_window_with_its_phase(self):
        now = timezone.now()
        MaintenanceWindow.objects.create(
            target_id=self.device.pk, starts_at=now - timedelta(minutes=10),
            ends_at=now + timedelta(minutes=50), note="正在变更",
        )
        payload = self._bootstrap(self.client.get(reverse("maintenance_list")))
        self.assertEqual(payload["count"], 1)
        row = payload["rows"][0]
        self.assertEqual(row["asset_name"], "核心交换机")
        self.assertEqual(row["target_type_label"], "网络设备")
        self.assertEqual(row["phase"], "active")
        self.assertEqual(row["phase_label"], "进行中")
        self.assertEqual(row["note"], "正在变更")

    def test_maintenance_page_filters_by_state(self):
        now = timezone.now()
        MaintenanceWindow.objects.create(
            target_id=self.device.pk, starts_at=now - timedelta(minutes=10),
            ends_at=now + timedelta(minutes=50), note="正在进行",
        )
        MaintenanceWindow.objects.create(
            target_id=self.other.pk, starts_at=now - timedelta(days=2),
            ends_at=now - timedelta(days=1), note="早就结束",
        )
        payload = self._bootstrap(
            self.client.get(reverse("maintenance_list"), {"state": "active"}),
        )
        self.assertEqual([row["note"] for row in payload["rows"]], ["正在进行"])
        self.assertEqual(payload["state"], "active")

    def test_admin_deletes_a_window(self):
        window = MaintenanceWindow.objects.create(
            target_id=self.device.pk, starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(hours=1),
        )
        self.client.post(
            reverse("maintenance_window_delete", args=[window.pk]), follow=True,
        )
        self.assertFalse(MaintenanceWindow.objects.exists())

    def test_non_admin_cannot_create(self):
        reader = get_user_model().objects.create_user("reader", password="pw-for-test")
        self.client.force_login(reader)
        start = timezone.now() + timedelta(hours=1)
        self.client.post(
            reverse("asset_maintenance_create", args=["networks"]),
            self._payload(start, start + timedelta(hours=1)),
        )
        self.assertFalse(MaintenanceWindow.objects.exists())
