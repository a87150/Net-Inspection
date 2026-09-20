from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from net.models import InspectionProfile, Network_Device, NetworkTopologyInterface, NetworkTopologyLink, NetworkTopologyObservation, TaskRun, TaskTargetRun, TopologyDiscoveryBatch


class TopologyApiTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user(username='reader',password='pw')
        self.device=Network_Device.objects.create(device_name='core',ip='192.0.2.50',vendor='cisco')
        profile=InspectionProfile.objects.create(name='api-topology',device_type='network_device',selected_items=['lldp_neighbors'])
        task=TaskRun.objects.create(task_type='inspection',inspection_profile=profile,source='manual',selected_items_snapshot=['lldp_neighbors'],target_scope_snapshot={},total_targets=1)
        target=TaskTargetRun.objects.create(task=task,target_type='network_device',target_id=str(self.device.pk),target_snapshot={})
        now=timezone.now()
        self.batch=TopologyDiscoveryBatch.objects.create(source_task=task,source_target=target,device=self.device,protocol='snmp_lldp',status='success',started_at=now,collected_at=now,finished_at=now)
        interface=NetworkTopologyInterface.objects.create(device=self.device,stable_key='ifindex:1',name='Gi1',first_seen_at=now,last_seen_at=now,last_batch=self.batch)
        link=NetworkTopologyLink.objects.create(stable_link_key='a'*64,local_interface=interface,remote_system_name='edge',protocols=['snmp_lldp'],confidence='0.60',first_seen_at=now,last_seen_at=now,last_batch=self.batch)
        self.observation=NetworkTopologyObservation.objects.create(batch=self.batch,source_target=target,local_device=self.device,local_interface=interface,link=link,protocol='snmp_lldp',neighbor={'remote_system_name':'edge'},evidence='{"community":"removed","safe":"value"}',evidence_sha256='b'*64,collected_at=now)

    def test_ordinary_topology_api_requires_login_and_excludes_evidence(self):
        url=reverse('topology_data')
        self.assertEqual(self.client.get(url).status_code,302)
        self.client.force_login(self.user)
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        body=response.json()
        self.assertEqual(body['links'][0]['kind'],'physical_discovered')
        self.assertNotIn('evidence',body['links'][0])
        self.assertEqual(self.client.post(url).status_code,403)
        admin=get_user_model().objects.create_user(username='admin',is_staff=True)
        self.client.force_login(admin)
        self.assertEqual(self.client.post(url).status_code,405)

    def test_list_endpoints_paginate_and_filter_stale(self):
        self.client.force_login(self.user)
        response=self.client.get(reverse('topology_links'),{'page_size':1,'include_stale':'0'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['pagination']['count'],1)
        self.assertEqual(len(response.json()['results']),1)

    def test_evidence_requires_explicit_permission_and_is_no_store(self):
        url=reverse('topology_evidence',args=[self.observation.pk])
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code,403)
        permission=Permission.objects.get(codename='view_topology_evidence')
        self.user.user_permissions.add(permission)
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertIn('no-store',response['Cache-Control'])
        self.assertLessEqual(len(response.json()['evidence'].encode()),65536)
        self.assertNotIn('community',response.json())



