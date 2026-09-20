from django.test import SimpleTestCase

from net.devices.network.topology_protocols import parse_lldp_snapshot


class LldpSnmpParserTests(SimpleTestCase):
    def test_parses_local_port_neighbor_and_ipv4_management_address(self):
        snapshot = {'tables': {
            'local_port_id': [('5', b'GE1/0/5')],
            'local_port_description': [('5', 'uplink')],
            'remote_chassis_id': [('100.5.1', bytes.fromhex('001122334455'))],
            'remote_port_id': [('100.5.1', b'GE1/0/1')],
            'remote_port_description': [('100.5.1', 'downlink')],
            'remote_system_name': [('100.5.1', 'core-01')],
            'remote_management_address': [('100.5.1.1.4.10.0.0.2', 2)],
        }}
        states = {'local_ports': 'success', 'neighbors': 'success', 'management': 'success'}
        result = parse_lldp_snapshot(snapshot, states)
        self.assertEqual(result['status'], 'success')
        self.assertTrue(result['complete'])
        self.assertEqual(result['interfaces'][0]['name'], 'GE1/0/5')
        self.assertEqual(result['neighbors'][0]['remote_chassis_id'], '00:11:22:33:44:55')
        self.assertEqual(result['neighbors'][0]['remote_management_addresses'], ['10.0.0.2'])

    def test_complete_empty_is_distinct_from_timeout(self):
        empty = parse_lldp_snapshot(
            {'tables': {}},
            {'local_ports': 'success', 'neighbors': 'success', 'management': 'success'},
        )
        self.assertEqual(empty['status'], 'success')
        self.assertTrue(empty['complete'])
        self.assertEqual(empty['neighbors'], [])
        timeout = parse_lldp_snapshot(
            {'tables': {}},
            {'local_ports': 'success', 'neighbors': 'timeout', 'management': 'not_started'},
        )
        self.assertEqual(timeout['status'], 'failed')
        self.assertFalse(timeout['complete'])

    def test_partial_or_malformed_rows_are_not_reported_complete(self):
        partial = parse_lldp_snapshot(
            {'tables': {'remote_system_name': [('bad-index', 'edge')]}},
            {'local_ports': 'success', 'neighbors': 'partial', 'management': 'not_started'},
        )
        self.assertEqual(partial['status'], 'partial')
        self.assertFalse(partial['complete'])
        self.assertEqual(partial['neighbors'], [])
