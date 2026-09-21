from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from net.models import (
    Computer,
    Domain_Account,
    Domain_Computer,
    Domain_Group,
    InspectionProfile,
    Network_Device,
    NetworkTopologyInterface,
    NetworkTopologyLink,
    SecurityDevice,
    Server,
    TaskRun,
    TaskTargetRun,
    TopologyDiscoveryBatch,
)
from tests.auth import login_reader


class OperationsSnapshotTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def inspection_task(self, *, status=TaskRun.Status.RUNNING, target_id='overview-device'):
        profile = InspectionProfile.objects.create(
            name=f'overview-{InspectionProfile.objects.count()}',
            device_type=InspectionProfile.DeviceType.NETWORK_DEVICE,
            selected_items=['lldp_neighbors'],
        )
        state = {'status': status, 'progress': 0}
        if status == TaskRun.Status.RUNNING:
            state.update(
                started_at=self.now,
                lease_expires_at=self.now + timedelta(minutes=5),
                worker_id='overview-test',
                progress=35,
            )
        elif status == TaskRun.Status.SUCCESS:
            state.update(
                finished_at=self.now,
                progress=100,
                completed_targets=1,
                successful_targets=1,
            )
        task = TaskRun.objects.create(
            task_type=TaskRun.TaskType.INSPECTION,
            inspection_profile=profile,
            source=TaskRun.Source.MANUAL,
            total_targets=1,
            target_scope_snapshot={},
            selected_items_snapshot=['lldp_neighbors'],
            **state,
        )
        target = TaskTargetRun.objects.create(
            task=task,
            target_type=TaskTargetRun.TargetType.NETWORK_DEVICE,
            target_id=target_id,
            target_snapshot={},
        )
        return task, target

    def test_snapshot_contains_only_network_topology_data(self):
        from index.dashboard.operations import build_operations_snapshot

        snapshot = build_operations_snapshot(now=self.now)

        self.assertEqual(snapshot['schema_version'], 3)
        self.assertEqual(snapshot['generated_at'], self.now.isoformat())
        self.assertEqual(set(snapshot), {'schema_version', 'generated_at', 'topology'})

    def test_network_roles_accept_chinese_english_and_keep_unknown_devices(self):
        from index.dashboard.operations import build_operations_snapshot

        firewall = Network_Device.objects.create(
            device_name='EDGE-FW-01', ip='10.0.0.1', device_type='防火墙',
        )
        core = Network_Device.objects.create(
            device_name='CORE-SW-01', ip='10.0.0.2', device_type='core switch',
        )
        distribution = Network_Device.objects.create(
            device_name='DIST-SW-01', ip='10.0.0.3', device_type='汇聚交换机',
        )
        access = Network_Device.objects.create(
            device_name='ACCESS-SW-01', ip='10.0.0.4', device_type='access switch',
        )
        ac = Network_Device.objects.create(
            device_name='WLAN-AC-01', ip='10.0.0.5', device_type='AC',
        )
        ap = Network_Device.objects.create(
            device_name='AP-01', ip='10.0.0.6', device_type='AP',
        )
        unknown = Network_Device.objects.create(
            device_name='MYSTERY-01', ip='10.0.0.7', device_type='',
        )
        Domain_Account.objects.create(account_name='ignored', login_name='ignored-account')
        Domain_Computer.objects.create(computer_name='ignored-domain-pc')
        Domain_Group.objects.create(
            distinguished_name='CN=ignored,DC=example,DC=test', group_name='ignored-group',
        )

        topology = build_operations_snapshot(now=self.now)['topology']
        nodes = {node['id']: node for node in topology['nodes']}
        roles = {
            node_id: node['role'] for node_id, node in nodes.items()
            if node['kind'] == 'backbone'
        }

        self.assertEqual(roles[f'networks:{firewall.pk}'], 'firewall')
        self.assertEqual(nodes[f'networks:{firewall.pk}']['tier'], 0)
        self.assertEqual(roles[f'networks:{core.pk}'], 'core_switch')
        self.assertEqual(nodes[f'networks:{core.pk}']['tier'], 1)
        self.assertEqual(roles[f'networks:{distribution.pk}'], 'distribution_switch')
        self.assertEqual(nodes[f'networks:{distribution.pk}']['tier'], 2)
        self.assertEqual(roles[f'networks:{access.pk}'], 'access_switch')
        self.assertEqual(nodes[f'networks:{access.pk}']['tier'], 3)
        self.assertEqual(roles[f'networks:{ac.pk}'], 'wireless_controller')
        self.assertEqual(nodes[f'networks:{ac.pk}']['tier'], 3)
        self.assertEqual(roles[f'networks:{unknown.pk}'], 'network_other')
        self.assertEqual(nodes[f'networks:{ap.pk}']['kind'], 'endpoint')
        self.assertEqual(nodes[f'networks:{ap.pk}']['role'], 'access_point')
        self.assertFalse(any(node_id.startswith('domain') for node_id in nodes))

    def test_empty_inventory_has_honest_empty_backbone_payload(self):
        from index.dashboard.operations import build_operations_snapshot

        topology = build_operations_snapshot(now=self.now)['topology']

        self.assertEqual(topology['nodes'], [])
        self.assertEqual(topology['attachment_edges'], [])
        self.assertFalse(topology['has_physical_links'])
        self.assertEqual(topology['mode'], 'empty')

    def test_endpoint_attachment_prefers_access_and_is_stable(self):
        from index.dashboard.operations import build_operations_snapshot

        Network_Device.objects.create(
            device_name='CORE', ip='10.30.0.2', device_type='core switch',
        )
        access = Network_Device.objects.create(
            device_name='ACCESS', ip='10.30.0.3', device_type='access switch',
        )
        pc = Computer.objects.create(
            computer_name='PC-01', ip_addresses='10.30.0.90, 10.31.0.90',
        )

        first = build_operations_snapshot(now=self.now)['topology']
        second = build_operations_snapshot(now=self.now)['topology']
        pc_node = next(node for node in first['nodes'] if node['id'] == f'computers:{pc.pk}')

        self.assertEqual(pc_node['parent_id'], f'networks:{access.pk}')
        self.assertEqual(pc_node['attachment_source'], 'inferred_subnet')
        self.assertEqual(first['attachment_edges'], second['attachment_edges'])
        self.assertEqual(len(first['attachment_edges']), 1)
        access_node = next(node for node in first['nodes'] if node['id'] == f'networks:{access.pk}')
        self.assertEqual(access_node['child_count'], 1)

    def test_invalid_endpoint_address_remains_unattached(self):
        from index.dashboard.operations import build_operations_snapshot

        Network_Device.objects.create(
            device_name='ACCESS', ip='10.30.0.3', device_type='access switch',
        )
        camera = SecurityDevice.objects.create(
            device_name='CAM-01', ip='not-an-ip', device_type='摄像机',
        )

        topology = build_operations_snapshot(now=self.now)['topology']
        camera_node = next(
            node for node in topology['nodes'] if node['id'] == f'monitors:{camera.pk}'
        )

        self.assertIsNone(camera_node['parent_id'])
        self.assertEqual(camera_node['attachment_source'], '')
        self.assertEqual(topology['attachment_edges'], [])

    def test_ap_physical_relationship_beats_subnet_inference_and_stays_physical(self):
        from index.dashboard.operations import build_operations_snapshot

        switch = Network_Device.objects.create(
            device_name='ACCESS', ip='10.40.0.2', device_type='access switch',
        )
        ap = Network_Device.objects.create(
            device_name='AP-01', ip='10.40.0.3', device_type='ap',
        )
        task, target = self.inspection_task(
            status=TaskRun.Status.SUCCESS, target_id=str(switch.pk),
        )
        batch = TopologyDiscoveryBatch.objects.create(
            source_task=task, source_target=target, device=switch,
            protocol=TopologyDiscoveryBatch.Protocol.SNMP_LLDP,
            status=TopologyDiscoveryBatch.Status.SUCCESS,
            started_at=self.now, collected_at=self.now, finished_at=self.now,
        )
        switch_if = NetworkTopologyInterface.objects.create(
            device=switch, stable_key='ifindex:1', name='Gi1/0/1',
            first_seen_at=self.now, last_seen_at=self.now, last_batch=batch,
        )
        ap_if = NetworkTopologyInterface.objects.create(
            device=ap, stable_key='ifindex:1', name='Eth0',
            first_seen_at=self.now, last_seen_at=self.now,
        )
        NetworkTopologyLink.objects.create(
            stable_link_key='a' * 64, local_interface=switch_if,
            remote_device=ap, remote_interface=ap_if,
            protocols=['snmp_lldp'], resolution_status='resolved', status='stale',
            confidence='0.90', first_seen_at=self.now, last_seen_at=self.now,
            last_batch=batch,
        )

        topology = build_operations_snapshot(now=self.now)['topology']
        ap_node = next(node for node in topology['nodes'] if node['id'] == f'networks:{ap.pk}')

        self.assertEqual(ap_node['parent_id'], f'networks:{switch.pk}')
        self.assertEqual(ap_node['attachment_source'], 'physical_discovered')
        self.assertEqual(topology['attachment_edges'][0]['attachment_source'], 'physical_discovered')
        self.assertEqual(topology['physical_edges'][0]['status'], 'stale')

    def test_snapshot_includes_allowlisted_physical_topology(self):
        from index.dashboard.operations import build_operations_snapshot

        device = Network_Device.objects.create(
            device_name='core', ip='192.0.2.80', vendor='cisco',
            password='never-export-password', snmp_community='private-community',
        )
        task, target = self.inspection_task(
            status=TaskRun.Status.SUCCESS, target_id=str(device.pk),
        )
        batch = TopologyDiscoveryBatch.objects.create(
            source_task=task, source_target=target, device=device,
            protocol=TopologyDiscoveryBatch.Protocol.SNMP_LLDP,
            status=TopologyDiscoveryBatch.Status.SUCCESS,
            started_at=self.now, collected_at=self.now, finished_at=self.now,
        )
        interface = NetworkTopologyInterface.objects.create(
            device=device, stable_key='ifindex:1', name='Gi1',
            first_seen_at=self.now, last_seen_at=self.now, last_batch=batch,
        )
        NetworkTopologyLink.objects.create(
            stable_link_key='f' * 64, local_interface=interface,
            remote_system_name='edge-unresolved', protocols=['snmp_lldp'],
            confidence='0.60', first_seen_at=self.now, last_seen_at=self.now,
            last_batch=batch,
        )

        topology = build_operations_snapshot(now=self.now)['topology']

        self.assertEqual(topology['mode'], 'hybrid')
        self.assertEqual(topology['interfaces'][0]['name'], 'Gi1')
        self.assertEqual(topology['physical_edges'][0]['kind'], 'physical_discovered')
        self.assertNotIn('password', str(topology))
        self.assertNotIn('community', str(topology))

    def test_topology_database_failure_is_isolated_and_sanitized(self):
        from index.dashboard.operations import build_operations_snapshot

        with patch(
            'index.dashboard.operations._build_topology',
            side_effect=DatabaseError('secret database coordinates'),
        ):
            snapshot = build_operations_snapshot(now=self.now)

        self.assertEqual(snapshot['topology'], {'error': 'topology_unavailable'})
        self.assertNotIn('secret database coordinates', str(snapshot))

    def test_logical_topology_caps_dense_categories_and_reports_omissions(self):
        from index.dashboard.operations import ASSET_NODE_LIMIT_PER_KIND, build_operations_snapshot

        Computer.objects.bulk_create([
            Computer(computer_name=f'overview-pc-{index:03d}')
            for index in range(ASSET_NODE_LIMIT_PER_KIND + 5)
        ])

        topology = build_operations_snapshot(now=self.now)['topology']
        computer_nodes = [
            node for node in topology['nodes'] if node['id'].startswith('computers:')
        ]

        self.assertEqual(len(computer_nodes), ASSET_NODE_LIMIT_PER_KIND)
        self.assertEqual(topology['asset_truncation']['computers'], 5)

    def test_physical_link_endpoints_survive_the_logical_network_cap(self):
        from index.dashboard.operations import ASSET_NODE_LIMIT_PER_KIND, build_operations_snapshot

        devices = Network_Device.objects.bulk_create([
            Network_Device(device_name=f'network-{index:03d}', ip=f'198.18.{index // 250}.{index % 250 + 1}')
            for index in range(ASSET_NODE_LIMIT_PER_KIND + 1)
        ])
        linked_device = devices[-1]
        task, target = self.inspection_task(
            status=TaskRun.Status.SUCCESS, target_id=str(linked_device.pk),
        )
        batch = TopologyDiscoveryBatch.objects.create(
            source_task=task, source_target=target, device=linked_device,
            protocol=TopologyDiscoveryBatch.Protocol.SNMP_LLDP,
            status=TopologyDiscoveryBatch.Status.SUCCESS,
            started_at=self.now, collected_at=self.now, finished_at=self.now,
        )
        interface = NetworkTopologyInterface.objects.create(
            device=linked_device, stable_key='ifindex:99', name='Gi99',
            first_seen_at=self.now, last_seen_at=self.now, last_batch=batch,
        )
        NetworkTopologyLink.objects.create(
            stable_link_key='e' * 64, local_interface=interface,
            remote_system_name='external-edge', protocols=['snmp_lldp'],
            confidence='0.60', first_seen_at=self.now, last_seen_at=self.now,
            last_batch=batch,
        )

        topology = build_operations_snapshot(now=self.now)['topology']

        self.assertIn(
            f'networks:{linked_device.pk}',
            {node['id'] for node in topology['nodes']},
        )


class OperationsOverviewViewTests(TestCase):
    def test_named_routes_are_stable(self):
        self.assertEqual(reverse('operations_overview'), '/operations/overview/')
        self.assertEqual(reverse('operations_overview_data'), '/operations/overview/data/')

    def test_reader_can_open_page_and_read_snapshot(self):
        login_reader(self.client)

        page = self.client.get(reverse('operations_overview'))
        data = self.client.get(reverse('operations_overview_data'))

        self.assertEqual(page.status_code, 200)
        self.assertEqual(data.status_code, 200)
        self.assertContains(page, 'id="operations-overview-app"')
        self.assertContains(page, 'id="operations-overview-bootstrap"')
        self.assertContains(page, 'data-snapshot-url="/operations/overview/data/"')
        self.assertEqual(data.json()['schema_version'], 3)
        cache_control = data.headers.get('Cache-Control', '')
        self.assertIn('no-cache', cache_control)
        self.assertIn('no-store', cache_control)

    def test_anonymous_user_is_redirected_to_login(self):
        for name in ('operations_overview', 'operations_overview_data'):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response.url)

    def test_data_endpoint_rejects_post(self):
        user = get_user_model().objects.create_user(
            'overview-admin', is_staff=True, is_active=True,
        )
        self.client.force_login(user)

        response = self.client.post(reverse('operations_overview_data'))

        self.assertEqual(response.status_code, 405)
