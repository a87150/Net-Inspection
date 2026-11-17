"""IP plan comparison: conflicts, pool overlap and the merged page."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from net.devices.network.ipam import plan_report
from net.models import IpAddress, IpSubnet, Network_Device, Network_Device_Inspection


class PlanReportTests(TestCase):
    def setUp(self):
        self.device = Network_Device.objects.create(
            device_name="汇聚交换机", ip="192.0.2.1", vendor="huawei",
        )
        self.subnet = IpSubnet.objects.create(
            cidr="10.1.1.0/24", gateway="10.1.1.1", vlan="10",
            pool_start="10.1.1.100", pool_end="10.1.1.200",
        )

    def _arp(self, records):
        Network_Device_Inspection.objects.create(
            device=self.device, status="success",
            details={"arp_table": {"records": records, "count": len(records)}},
        )

    def test_subnet_normalizes_host_bits_and_counts_hosts(self):
        subnet = IpSubnet(cidr="10.2.2.5/24", pool_start="10.2.2.10", pool_end="10.2.2.110")
        subnet.clean()
        self.assertEqual(subnet.cidr, "10.2.2.0/24")
        self.assertEqual(subnet.usable_hosts, 254)
        self.assertEqual(subnet.pool_size, 101)
        self.assertTrue(subnet.in_pool("10.2.2.50"))
        self.assertFalse(subnet.in_pool("10.2.2.5"))

    def test_same_ip_with_two_macs_is_a_conflict(self):
        self._arp([
            {"ip_address": "10.1.1.10", "mac_address": "aa:bb:cc:00:00:01"},
            {"ip_address": "10.1.1.10", "mac_address": "aa:bb:cc:00:00:02"},
        ])
        report = plan_report()
        kinds = {issue["kind"] for issue in report["issues"]}
        self.assertIn("conflict", kinds)
        self.assertEqual(report["error_count"], 1)

    def test_static_address_inside_the_pool_is_flagged(self):
        IpAddress.objects.create(subnet=self.subnet, address="10.1.1.150", status="used")
        report = plan_report()
        self.assertIn("pool", {issue["kind"] for issue in report["issues"]})

    def test_static_address_outside_the_pool_is_fine(self):
        IpAddress.objects.create(subnet=self.subnet, address="10.1.1.20", status="used")
        report = plan_report()
        self.assertNotIn("pool", {issue["kind"] for issue in report["issues"]})

    def test_overlapping_subnets_are_reported(self):
        IpSubnet.objects.create(cidr="10.1.0.0/16")
        report = plan_report()
        self.assertIn("overlap", {issue["kind"] for issue in report["issues"]})

    def test_registered_mac_that_drifted_is_reported(self):
        IpAddress.objects.create(
            subnet=self.subnet, address="10.1.1.30", status="used",
            mac="aa:bb:cc:00:00:09",
        )
        self._arp([{"ip_address": "10.1.1.30", "mac_address": "aa:bb:cc:00:00:0a"}])
        report = plan_report()
        self.assertIn("drift", {issue["kind"] for issue in report["issues"]})

    def test_unplanned_addresses_are_counted(self):
        self._arp([{"ip_address": "10.1.1.99", "mac_address": "aa:bb:cc:00:00:03"}])
        report = plan_report()
        self.assertEqual(report["unplanned_count"], 1)
        self.assertIn("10.1.1.99", report["unplanned"])


class IpManagementPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("ops", password="pw-for-test")
        self.client.force_login(self.user)

    def test_page_shows_lookup_and_ledger_together(self):
        IpSubnet.objects.create(cidr="10.1.1.0/24", pool_start="10.1.1.100", pool_end="10.1.1.120")
        response = self.client.get(reverse("ip_management"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "IP 台账")
        self.assertContains(response, "10.1.1.0/24")

    def test_query_renders_matches_inside_the_same_page(self):
        device = Network_Device.objects.create(device_name="接入交换机", ip="192.0.2.2", vendor="huawei")
        Network_Device_Inspection.objects.create(
            device=device, status="success",
            details={"arp_table": {"records": [{"ip_address": "10.1.1.77", "mac_address": "aa:bb:cc:00:00:01"}], "count": 1}},
        )
        response = self.client.get(reverse("ip_management"), {"q": "10.1.1.77"})
        self.assertContains(response, "10.1.1.77")
        self.assertContains(response, "接入交换机")
