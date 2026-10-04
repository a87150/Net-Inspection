from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from net.admin.assets import NetworkDeviceAdmin
from net.models import (
    InspectionProfile,
    NetworkTopologyInterface,
    NetworkTopologyLink,
    NetworkTopologyObservation,
    Network_Device,
    TaskRun,
    TaskTargetRun,
    TopologyDiscoveryBatch,
)


class BulkDeviceDeleteTests(TestCase):
    """The admin bulk-delete action has to clear PROTECTed topology rows first."""

    def setUp(self):
        self.now = timezone.now()
        self.device = Network_Device.objects.create(device_name='核心交换机', ip='192.0.2.1')
        self.other = Network_Device.objects.create(device_name='接入交换机', ip='192.0.2.2')
        profile = InspectionProfile.objects.create(
            name='bulk-delete-profile',
            device_type=InspectionProfile.DeviceType.NETWORK_DEVICE,
            selected_items=['lldp_neighbors'],
        )
        task = TaskRun.objects.create(
            task_type=TaskRun.TaskType.INSPECTION,
            inspection_profile=profile,
            source=TaskRun.Source.MANUAL,
            status=TaskRun.Status.SUCCESS,
            total_targets=1, target_scope_snapshot={},
            selected_items_snapshot=['lldp_neighbors'],
            finished_at=self.now, progress=100,
            completed_targets=1, successful_targets=1,
        )
        target = TaskTargetRun.objects.create(
            task=task, target_type=TaskTargetRun.TargetType.NETWORK_DEVICE,
            result_id=str(self.device.pk), status='success',
        )
        self.batch = TopologyDiscoveryBatch.objects.create(
            source_task=task, source_target=target, device=self.device,
            protocol=TopologyDiscoveryBatch.Protocol.SNMP_LLDP,
            status=TopologyDiscoveryBatch.Status.SUCCESS,
            started_at=self.now, collected_at=self.now, finished_at=self.now,
        )
        self.interface = NetworkTopologyInterface.objects.create(
            device=self.device, stable_key='ifindex:1', if_index=1,
            first_seen_at=self.now, last_seen_at=self.now, last_batch=self.batch,
        )
        self.peer = NetworkTopologyInterface.objects.create(
            device=self.other, stable_key='ifindex:1', if_index=1,
            first_seen_at=self.now, last_seen_at=self.now,
        )
        self.link = NetworkTopologyLink.objects.create(
            stable_link_key='a' * 64, local_interface=self.interface,
            remote_device=self.other, remote_interface=self.peer,
            resolution_status='resolved',
            first_seen_at=self.now, last_seen_at=self.now, last_batch=self.batch,
        )
        self.observation = NetworkTopologyObservation.objects.create(
            batch=self.batch, source_target=target, local_device=self.device,
            local_interface=self.interface, link=self.link,
            protocol=TopologyDiscoveryBatch.Protocol.SNMP_LLDP,
            evidence='raw', evidence_sha256='b' * 64, collected_at=self.now,
        )

    def test_direct_delete_is_blocked_by_protected_topology_rows(self):
        # Guards the premise: without the admin cleanup these rows raise.
        with self.assertRaises(ProtectedError):
            self.device.delete()

    def test_bulk_delete_removes_the_device_and_its_topology(self):
        admin = NetworkDeviceAdmin(Network_Device, None)
        admin.delete_queryset(None, Network_Device.objects.filter(pk=self.device.pk))
        self.assertFalse(Network_Device.objects.filter(pk=self.device.pk).exists())
        self.assertFalse(
            NetworkTopologyInterface.objects.filter(pk=self.interface.pk).exists())
        self.assertFalse(NetworkTopologyLink.objects.filter(pk=self.link.pk).exists())
        self.assertFalse(NetworkTopologyObservation.objects.filter(
            pk=self.observation.pk).exists())
        self.assertFalse(
            TopologyDiscoveryBatch.objects.filter(pk=self.batch.pk).exists())

    def test_bulk_delete_leaves_other_devices_topology_alone(self):
        admin = NetworkDeviceAdmin(Network_Device, None)
        admin.delete_queryset(None, Network_Device.objects.filter(pk=self.device.pk))
        self.assertTrue(Network_Device.objects.filter(pk=self.other.pk).exists())
        self.assertTrue(
            NetworkTopologyInterface.objects.filter(pk=self.peer.pk).exists())

