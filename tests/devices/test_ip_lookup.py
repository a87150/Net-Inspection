"""IP lookup over saved ARP/MAC observations."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from net.devices.network.observations import normalize_mac, search
from net.models import Network_Device, Network_Device_Inspection


class IpLookupTests(TestCase):
    def setUp(self):
        self.switch = Network_Device.objects.create(
            device_name="汇聚交换机", ip="192.0.2.1", vendor="huawei",
        )
        Network_Device_Inspection.objects.create(
            device=self.switch, status="success",
            details={
                "arp_table": {"records": [
                    {"ip_address": "10.1.1.20", "mac_address": "aa:bb:cc:dd:ee:ff",
                     "interface": "GigabitEthernet0/0/1"},
                    {"ip_address": "10.1.1.21", "mac_address": "aa-bb-cc-dd-ee-00",
                     "interface": "GigabitEthernet0/0/2"},
                ], "count": 2},
                "mac_table": {"records": [
                    {"mac_address": "aa:bb:cc:dd:ee:ff", "interface": "GE0/0/1", "vlan": "10"},
                ], "count": 1},
            },
        )

    def test_ip_match_returns_interface_and_device(self):
        result = search("10.1.1.20")
        self.assertEqual(len(result["matches"]), 1)
        match = result["matches"][0]
        self.assertEqual(match["device_name"], "汇聚交换机")
        self.assertEqual(match["interface"], "GigabitEthernet0/0/1")
        self.assertEqual(match["source"], "arp")

    def test_mac_match_ignores_separators(self):
        result = search("AABB.CCDD.EEFF")
        self.assertTrue(result["matches"])
        sources = {item["source"] for item in result["matches"]}
        self.assertEqual(sources, {"arp", "mac"})

    def test_dash_separated_mac_is_found(self):
        result = search("aabbccddee00")
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["matches"][0]["ip"], "10.1.1.21")

    def test_unknown_address_returns_nothing_but_reports_scope(self):
        result = search("10.9.9.9")
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["devices"], 1)

    def test_empty_query_is_rejected_without_scanning(self):
        self.assertEqual(search("   ")["matches"], [])

    def test_results_older_than_the_window_are_ignored(self):
        Network_Device_Inspection.objects.filter(device=self.switch).update(
            created_at=timezone.now() - timedelta(days=40),
        )
        self.assertEqual(search("10.1.1.20")["matches"], [])

    def test_only_the_latest_successful_result_per_device_is_used(self):
        Network_Device_Inspection.objects.create(
            device=self.switch, status="success",
            details={"arp_table": {"records": [{"ip_address": "10.1.1.99"}], "count": 1}},
        )
        result = search("10.1.1.99")
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(search("10.1.1.20")["matches"], [])

    def test_normalize_mac(self):
        self.assertEqual(normalize_mac("AA:BB-CC.DD EE"), "aabbccddee")
