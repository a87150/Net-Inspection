"""IP 管理页把「增加 IP 段」的表单注水到本页，不再跳后台。"""
import json
import re

from django.contrib.auth import get_user_model
from django.test import TestCase

from net.models import IpSubnet

URL = "/network/ip/"


def bootstrap(response):
    html = response.content.decode()
    match = re.search(
        r'id="ip-subnet-bootstrap"[^>]*>(.*?)</script>', html, re.S,
    )
    return json.loads(match.group(1)) if match else None


class IpManagementPageTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            "ip-admin", password="pw-for-test", is_staff=True,
        )
        self.reader = get_user_model().objects.create_user(
            "ip-reader", password="pw-for-test",
        )

    def test_page_mounts_the_modal_and_hydrates_its_options(self):
        self.client.force_login(self.admin)
        response = self.client.get(URL, SERVER_NAME="127.0.0.1")
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-vue-page="ip-subnet-form"', html)
        self.assertIn("app/vue/boot.mjs", html)
        self.assertIn("csrfmiddlewaretoken", html)

        payload = bootstrap(response)
        self.assertEqual(payload["endpoint"], "/api/v1/ip-subnets/")
        self.assertTrue(payload["can_administer"])
        self.assertEqual(
            [item["value"] for item in payload["purposes"]],
            [value for value, _ in IpSubnet.Purpose.choices],
        )

    def test_reader_sees_the_page_without_the_create_button(self):
        self.client.force_login(self.reader)
        response = self.client.get(URL, SERVER_NAME="127.0.0.1")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(bootstrap(response)["can_administer"])

    def test_ledger_lists_created_subnets(self):
        IpSubnet.objects.create(cidr="10.9.0.0/24", vlan="90", note="测试段")
        self.client.force_login(self.admin)
        html = self.client.get(URL, SERVER_NAME="127.0.0.1").content.decode()
        self.assertIn("10.9.0.0/24", html)
        self.assertIn("测试段", html)
