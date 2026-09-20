from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from django.test import SimpleTestCase

from net.topology.discovery import (
    InterfaceCandidate, NeighborCandidate, build_discovery_payload,
    stable_interface_key, stable_link_key,
)


class TopologyDiscoveryDomainTests(SimpleTestCase):
    def setUp(self):
        self.when = datetime(2026, 9, 20, tzinfo=timezone.utc)
        self.local = SimpleNamespace(pk=1, ip='10.0.0.1', device_name='access')

    def test_interface_key_priority(self):
        self.assertEqual(stable_interface_key(InterfaceCandidate(if_index=7, name='GE1', mac_address='00:11:22:33:44:55')), 'ifindex:7')
        self.assertEqual(stable_interface_key(InterfaceCandidate(mac_address='0011.2233.4455')), 'mac:00:11:22:33:44:55')
        self.assertEqual(stable_interface_key(InterfaceCandidate(name=' GigabitEthernet 1/0/1 ')), 'name:gigabitethernet1/0/1')

    def test_resolves_by_chassis_then_management_ip_then_unique_name(self):
        remote = SimpleNamespace(pk=2, ip='10.0.0.2', device_name='core')
        remote_if = SimpleNamespace(device_id=2, stable_key='ifindex:9', mac_address='00:11:22:33:44:55', name='GE1/0/9')
        result = build_discovery_payload(self.local, [{
            'status':'success','complete':True,'protocols':['snmp_lldp'],
            'interfaces':[{'stable_key_hint':'ifindex:1','if_index':1,'name':'GE1/0/1'}],
            'neighbors':[{'local_key_hint':'ifindex:1','remote_chassis_id':'0011.2233.4455','remote_port_id':'GE1/0/9','protocol':'lldp'}],
            'evidence':{},
        }], [remote], [remote_if], self.when)
        observation = result.observations[0]
        self.assertEqual(observation.remote_device_id, 2)
        self.assertEqual(observation.remote_interface_key, 'ifindex:9')
        self.assertEqual(observation.resolution_status, 'resolved')
        self.assertEqual(observation.confidence, Decimal('0.90'))

    def test_duplicate_system_name_remains_conflicted(self):
        devices = [SimpleNamespace(pk=2,ip='10.0.0.2',device_name='edge'), SimpleNamespace(pk=3,ip='10.0.0.3',device_name='edge')]
        result = build_discovery_payload(self.local, [{
            'status':'success','complete':True,'protocols':['ssh_lldp'],
            'interfaces':[{'stable_key_hint':'name:ge1','name':'GE1'}],
            'neighbors':[{'local_key_hint':'name:ge1','local_port_id':'GE1','remote_system_name':'edge','remote_port_id':'GE2','protocol':'lldp'}],
            'evidence':{},
        }], devices, [], self.when)
        observation = result.observations[0]
        self.assertEqual(observation.resolution_status, 'conflict')
        self.assertIsNone(observation.remote_device_id)
        self.assertEqual(observation.confidence, Decimal('0.25'))

    def test_resolved_link_key_is_canonical(self):
        one = SimpleNamespace(local_device_id=1, local_interface_key='ifindex:1', remote_device_id=2, remote_interface_key='ifindex:2', remote_chassis_id='', remote_port_id='', protocol='lldp')
        two = SimpleNamespace(local_device_id=2, local_interface_key='ifindex:2', remote_device_id=1, remote_interface_key='ifindex:1', remote_chassis_id='', remote_port_id='', protocol='lldp')
        self.assertEqual(stable_link_key(one), stable_link_key(two))
