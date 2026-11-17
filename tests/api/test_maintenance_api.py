"""REST contract for maintenance windows: auth, batch create, filter, delete."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from net.models import MaintenanceWindow, Network_Device, Server

LIST_URL = "/api/v1/maintenance-windows/"


class MaintenanceWindowApiTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            "api-admin", password="pw-for-test", is_staff=True,
        )
        self.reader = get_user_model().objects.create_user(
            "api-reader", password="pw-for-test",
        )
        self.device = Network_Device.objects.create(
            device_name="核心交换机", ip="192.0.2.10", vendor="huawei",
        )
        self.server = Server.objects.create(name="应用服务器", ip="192.0.2.20")
        self.start = timezone.now() + timedelta(hours=1)
        self.end = self.start + timedelta(hours=2)

    def _payload(self, target_type="network_device", targets=None):
        return {
            "target_type": target_type,
            "target_ids": [str(pk) for pk in (targets or [self.device.pk])],
            "starts_at": self.start.isoformat(),
            "ends_at": self.end.isoformat(),
            "note": "更换模块",
        }

    def test_anonymous_is_refused(self):
        response = self.client.get(LIST_URL)
        self.assertIn(response.status_code, (401, 403))

    def test_reader_can_list(self):
        self.client.force_login(self.reader)
        response = self.client.get(LIST_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])

    def test_reader_cannot_create(self):
        self.client.force_login(self.reader)
        response = self.client.post(LIST_URL, self._payload(), content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(MaintenanceWindow.objects.exists())

    def test_admin_creates_one_window_per_asset(self):
        self.client.force_login(self.admin)
        payload = self._payload(targets=[self.device.pk])
        response = self.client.post(LIST_URL, payload, content_type="application/json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["created"], 1)
        window = MaintenanceWindow.objects.get()
        self.assertEqual(window.target_type, "network_device")
        self.assertEqual(str(window.target_id), str(self.device.pk))
        self.assertEqual(window.note, "更换模块")

    def test_list_exposes_the_asset_name_and_phase(self):
        self.client.force_login(self.admin)
        MaintenanceWindow.objects.create(
            target_type="server", target_id=self.server.pk,
            starts_at=timezone.now() - timedelta(minutes=5),
            ends_at=timezone.now() + timedelta(minutes=55),
            note="在线扩容",
        )
        body = self.client.get(LIST_URL).json()
        self.assertEqual(body["count"], 1)
        row = body["results"][0]
        self.assertEqual(row["asset_name"], "应用服务器")
        self.assertEqual(row["target_type_label"], "服务器")
        self.assertEqual(row["phase"], "active")

    def test_state_filter(self):
        self.client.force_login(self.admin)
        now = timezone.now()
        MaintenanceWindow.objects.create(
            target_id=self.device.pk, starts_at=now - timedelta(minutes=5),
            ends_at=now + timedelta(minutes=5), note="进行中",
        )
        MaintenanceWindow.objects.create(
            target_id=self.device.pk, starts_at=now - timedelta(days=2),
            ends_at=now - timedelta(days=1), note="已结束",
        )
        body = self.client.get(LIST_URL, {"state": "active"}).json()
        self.assertEqual([row["note"] for row in body["results"]], ["进行中"])

    def test_end_before_start_is_rejected_with_a_message(self):
        self.client.force_login(self.admin)
        payload = self._payload()
        payload["ends_at"] = (self.start - timedelta(minutes=30)).isoformat()
        response = self.client.post(LIST_URL, payload, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.json()["message"])
        self.assertFalse(MaintenanceWindow.objects.exists())

    def test_asset_of_another_type_is_rejected(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            LIST_URL, self._payload(targets=[self.server.pk]), content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(MaintenanceWindow.objects.exists())

    def test_delete_removes_one_window(self):
        self.client.force_login(self.admin)
        window = MaintenanceWindow.objects.create(
            target_id=self.device.pk, starts_at=self.start, ends_at=self.end,
        )
        response = self.client.delete(f"{LIST_URL}{window.pk}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(MaintenanceWindow.objects.exists())

    def test_meta_lists_states_and_kinds(self):
        self.client.force_login(self.admin)
        body = self.client.get(f"{LIST_URL}meta/").json()
        self.assertEqual(
            [item["value"] for item in body["states"]],
            ["", "active", "pending", "ended"],
        )
        self.assertIn({"kind": "servers", "target_type": "server"}, body["kinds"])
