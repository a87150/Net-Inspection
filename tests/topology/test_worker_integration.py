from unittest.mock import patch
from django.test import TestCase

from net.infrastructure.collection import CollectionResult
from net.inspections.executor import execute_target
from net.inspections.queue import claim_next_task, enqueue_task
from net.models import DeviceCollectionBinding, InspectionProfile, Network_Device, NetworkTopologyObservation, TaskRun, TopologyDiscoveryBatch


class TopologyWorkerIntegrationTests(TestCase):
    def test_selected_topology_item_creates_batch_and_observation(self):
        device=Network_Device.objects.create(device_name='access',ip='192.0.2.31',vendor='cisco',connection_type='snmp',snmp_community='public-test')
        Network_Device.objects.create(device_name='core',ip='192.0.2.32',vendor='cisco')
        DeviceCollectionBinding.objects.create(kind='networks',target_id=device.pk,overrides={'selected_items':['lldp_neighbors'],'item_methods':{'lldp_neighbors':'snmp'}})
        profile=InspectionProfile.objects.create(name='topology-worker',device_type='network_device',selected_items=['lldp_neighbors'])
        enqueue_task(profile,[device.pk],TaskRun.Source.MANUAL)
        task=claim_next_task('topology-worker',30)
        target=task.target_runs.get()
        self.assertIn('lldp_neighbors', task.selected_items_snapshot)
        self.assertIn('lldp_neighbors', __import__('net.inspections.executor', fromlist=['_device_task'])._device_task(target, task).selected_items_snapshot)
        result=CollectionResult(True,'success',data={'lldp_neighbors':{
            'status':'success','complete':True,'protocols':['snmp_lldp'],
            'interfaces':[{'if_index':1,'name':'Gi1/0/1'}],
            'neighbors':[{'local_key_hint':'ifindex:1','remote_system_name':'core','remote_port_id':'Gi1/0/2','protocol':'lldp'}],
            'evidence':{'table_states':{'neighbors':'success'}},
        }})
        with patch('net.inspections.executor.collect_network',return_value=result):
            outcome=execute_target(target,worker_id='topology-worker')
        self.assertFalse(outcome.stale)
        batch=TopologyDiscoveryBatch.objects.get()
        self.assertEqual(batch.status,'success')
        self.assertEqual(batch.observation_count,1)
        self.assertEqual(NetworkTopologyObservation.objects.count(),1)


