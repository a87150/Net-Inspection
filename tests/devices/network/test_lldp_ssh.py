from pathlib import Path
from django.test import SimpleTestCase

from net.devices.network.topology_protocols import normalize_ssh_neighbors

FIXTURES = Path(__file__).with_name('fixtures')


class LldpSshNormalizerTests(SimpleTestCase):
    def test_normalizes_supported_vendor_outputs(self):
        for vendor in ('huawei', 'h3c', 'ruijie', 'cisco'):
            with self.subTest(vendor=vendor):
                raw = (FIXTURES / f'lldp_{vendor}.txt').read_text(encoding='utf-8-sig')
                result = normalize_ssh_neighbors([], {'show lldp neighbors': raw}, vendor)
                self.assertTrue(result['complete'])
                self.assertEqual(result['neighbors'][0]['protocol'], 'lldp')
                self.assertTrue(result['neighbors'][0]['local_port_id'])
                self.assertTrue(result['neighbors'][0]['remote_system_name'] or result['neighbors'][0]['remote_chassis_id'])

    def test_cisco_cdp_is_tagged_separately(self):
        raw = (FIXTURES / 'cdp_cisco.txt').read_text(encoding='utf-8-sig')
        result = normalize_ssh_neighbors([], {'show cdp neighbors': raw}, 'cisco')
        self.assertEqual(result['neighbors'][0]['protocol'], 'cdp')
        self.assertEqual(result['protocols'], ['ssh_cdp'])

    def test_records_are_mapped_without_losing_display_names(self):
        result = normalize_ssh_neighbors([
            {'local_interface': 'GigabitEthernet1/0/1', 'neighbor': 'edge',
             'neighbor_interface': 'GigabitEthernet1/0/2', 'chassis_id': '0011.2233.4455'}
        ], {}, 'cisco')
        self.assertEqual(result['neighbors'][0]['local_port_id'], 'GigabitEthernet1/0/1')
        self.assertEqual(result['neighbors'][0]['remote_chassis_id'], '00:11:22:33:44:55')

    def test_empty_marker_is_complete_but_unmatched_output_is_not(self):
        empty = normalize_ssh_neighbors([], {'display lldp neighbor': 'Total number of neighbors: 0'}, 'huawei')
        self.assertTrue(empty['complete'])
        self.assertEqual(empty['neighbors'], [])
        unmatched = normalize_ssh_neighbors([], {'display lldp neighbor': 'unexpected output'}, 'huawei')
        self.assertFalse(unmatched['complete'])
        self.assertEqual(unmatched['status'], 'failed')
