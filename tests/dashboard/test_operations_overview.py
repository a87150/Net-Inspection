from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from net.models import (
    AlertChannel,
    AlertDelivery,
    AlertEvent,
    InspectionProfile,
    Network_Device,
    NetworkTopologyInterface,
    NetworkTopologyLink,
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

    def test_snapshot_has_stable_regions_and_safe_task_and_alert_fields(self):
        from index.dashboard.operations import build_operations_snapshot

        task, target = self.inspection_task()
        event = AlertEvent.objects.create(
            task=task,
            target_run=target,
            event_type=AlertEvent.EventType.ABNORMAL,
            status=AlertEvent.Status.PARTIAL,
            severity='critical',
            summary='接口异常',
            findings=[{'key': 'port', 'severity': 'critical', 'title': '端口异常'}],
        )
        channel = AlertChannel.objects.create(
            name='飞书告警', channel_type=AlertChannel.ChannelType.FEISHU,
            settings={'webhook_url': 'https://example.invalid/hook', 'secret': 'never-export'},
        )
        AlertDelivery.objects.create(event=event, channel=channel, status=AlertDelivery.Status.FAILED)

        snapshot = build_operations_snapshot(now=self.now)

        self.assertEqual(snapshot['schema_version'], 1)
        self.assertEqual(snapshot['generated_at'], self.now.isoformat())
        self.assertEqual(
            set(snapshot['summary']),
            {'people', 'computers', 'networks', 'servers', 'monitors', 'domain', 'tasks'},
        )
        self.assertEqual(
            set(snapshot['tasks']['items'][0]),
            {'id', 'type', 'type_label', 'source_label', 'status', 'status_label', 'progress',
             'total_targets', 'completed_targets', 'successful_targets', 'failed_targets',
             'created_at', 'started_at', 'finished_at', 'url'},
        )
        self.assertEqual(
            set(snapshot['alerts']['items'][0]),
            {'id', 'severity', 'status', 'status_label', 'summary', 'target_type',
             'occurred_at', 'url', 'deliveries'},
        )
        self.assertNotIn('settings', str(snapshot))
        self.assertNotIn('never-export', str(snapshot))

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

    def test_region_database_failure_is_isolated_and_sanitized(self):
        from index.dashboard.operations import build_operations_snapshot

        with patch(
            'index.dashboard.operations._build_summary',
            side_effect=DatabaseError('secret database coordinates'),
        ):
            snapshot = build_operations_snapshot(now=self.now)

        self.assertEqual(snapshot['summary'], {'error': 'summary_unavailable'})
        self.assertIn('items', snapshot['tasks'])
        self.assertNotIn('secret database coordinates', str(snapshot))


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
        self.assertEqual(data.json()['schema_version'], 1)
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
