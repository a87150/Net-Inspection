"""REST contract for the IP page's subnet modal."""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from net.models import IpSubnet

URL = "/api/v1/ip-subnets/"


class IpSubnetApiTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            "subnet-admin", password="pw-for-test", is_staff=True,
        )
        self.reader = get_user_model().objects.create_user(
            "subnet-reader", password="pw-for-test",
        )

    def _post(self, payload):
        return self.client.post(
            URL, data=json.dumps(payload), content_type="application/json",
        )

    def test_anonymous_is_refused(self):
        response = self._post({"cidr": "10.1.1.0/24"})
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(IpSubnet.objects.count(), 0)

    def test_reader_cannot_create(self):
        self.client.force_login(self.reader)
        response = self._post({"cidr": "10.1.1.0/24"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(IpSubnet.objects.count(), 0)

    def test_admin_creates_subnet_and_cidr_is_normalized(self):
        self.client.force_login(self.admin)
        response = self._post({
            "cidr": "10.1.1.20/24",
            "purpose": "office",
            "gateway": "10.1.1.1",
            "vlan": "11",
            "pool_start": "10.1.1.100",
            "pool_end": "10.1.1.200",
            "note": "一层办公",
        })
        self.assertEqual(response.status_code, 201)
        subnet = IpSubnet.objects.get()
        self.assertEqual(subnet.cidr, "10.1.1.0/24")
        self.assertEqual(subnet.gateway, "10.1.1.1")
        self.assertEqual(subnet.pool_size, 101)
        self.assertEqual(subnet.note, "一层办公")

    def test_optional_fields_can_be_omitted(self):
        self.client.force_login(self.admin)
        response = self._post({"cidr": "10.2.0.0/16"})
        self.assertEqual(response.status_code, 201)
        subnet = IpSubnet.objects.get()
        self.assertIsNone(subnet.gateway)
        self.assertEqual(subnet.purpose, IpSubnet.Purpose.OFFICE)

    def test_invalid_cidr_reports_field_error(self):
        self.client.force_login(self.admin)
        response = self._post({"cidr": "10.300.0.0/24"})
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertIn("cidr", json.dumps(body, ensure_ascii=False))
        self.assertEqual(IpSubnet.objects.count(), 0)

    def test_pool_bounds_are_validated(self):
        self.client.force_login(self.admin)
        response = self._post({
            "cidr": "10.3.0.0/24",
            "pool_start": "10.3.0.200",
            "pool_end": "10.3.0.10",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(IpSubnet.objects.count(), 0)

    def test_duplicate_cidr_is_rejected(self):
        IpSubnet.objects.create(cidr="10.4.0.0/24")
        self.client.force_login(self.admin)
        response = self._post({"cidr": "10.4.0.0/24"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(IpSubnet.objects.count(), 1)
