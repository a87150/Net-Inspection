import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from net.models import (
    InspectionProfile,
    Network_Device,
    NetworkTopologyInterface,
    NetworkTopologyLink,
    NetworkTopologyObservation,
    TaskRun,
    TaskTargetRun,
    TopologyDiscoveryBatch,
)


class TopologyModelTests(TestCase):
    def setUp(self):
        self.device = Network_Device.objects.create(
            device_name='core-1', ip='192.0.2.1', vendor='cisco',
        )
        profile = InspectionProfile.objects.create(
            name='topology', device_type='network_device',
            selected_items=['lldp_neighbors'],
        )
        self.task = TaskRun.objects.create(
            task_type='inspection', inspection_profile=profile,
            source='manual', selected_items_snapshot=['lldp_neighbors'],
            target_scope_snapshot={'target_ids': [str(self.device.pk)]},
            total_targets=1,
        )
        self.target = TaskTargetRun.objects.create(
            task=self.task, target_type='network_device',
            target_id=str(self.device.pk),
            target_snapshot={'ip': self.device.ip},
        )

    def batch(self, **overrides):
        values = {
            'source_task': self.task,
            'source_target': self.target,
            'device': self.device,
            'protocol': 'snmp_lldp',
            'status': 'success',
            'started_at': timezone.now(),
            'collected_at': timezone.now(),
            'finished_at': timezone.now(),
        }
        values.update(overrides)
        return TopologyDiscoveryBatch.objects.create(**values)

    def interface(self, **overrides):
        values = {
            'device': self.device,
            'stable_key': 'ifindex:1',
            'name': 'Gi0/1',
            'vlan_ids': [10, 20],
            'first_seen_at': timezone.now(),
            'last_seen_at': timezone.now(),
        }
        values.update(overrides)
        return NetworkTopologyInterface.objects.create(**values)

    def test_topology_models_use_uuid_primary_keys_and_safe_defaults(self):
        batch = self.batch()
        interface = self.interface(last_batch=batch)
        link = NetworkTopologyLink.objects.create(
            stable_link_key='a' * 64,
            local_interface=interface,
            remote_management_addresses=[],
            protocols=['snmp_lldp'],
            vlan_ids=[],
            confidence=Decimal('0.60'),
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
            last_batch=batch,
        )
        observation = NetworkTopologyObservation.objects.create(
            batch=batch,
            source_target=self.target,
            local_device=self.device,
            local_interface=interface,
            link=link,
            protocol='snmp_lldp',
            neighbor={'remote_system_name': 'edge-1'},
            evidence='{}',
            evidence_sha256='b' * 64,
            collected_at=timezone.now(),
        )

        for row in (batch, interface, link, observation):
            self.assertIsInstance(row.pk, uuid.UUID)
        self.assertEqual(batch.schema_version, 1)
        self.assertEqual(batch.interface_count, 0)
        self.assertEqual(interface.admin_status, 'unknown')
        self.assertEqual(interface.oper_status, 'unknown')
        self.assertFalse(interface.is_stale)
        self.assertEqual(link.evidence_direction, 'unilateral')
        self.assertEqual(link.resolution_status, 'unresolved')
        self.assertEqual(link.status, 'current')
        self.assertEqual(link.missing_complete_batches, 0)

    def test_source_target_and_stable_keys_are_unique(self):
        batch = self.batch()
        self.interface()
        NetworkTopologyLink.objects.create(
            stable_link_key='c' * 64,
            local_interface=NetworkTopologyInterface.objects.get(),
            confidence=Decimal('0.60'),
            first_seen_at=timezone.now(),
            last_seen_at=timezone.now(),
        )
        NetworkTopologyObservation.objects.create(
            batch=batch, source_target=self.target, local_device=self.device,
            protocol='snmp_lldp', neighbor={}, evidence='{}',
            evidence_sha256='d' * 64, collected_at=timezone.now(),
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.batch()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.interface()
        with self.assertRaises(IntegrityError), transaction.atomic():
            NetworkTopologyLink.objects.create(
                stable_link_key='c' * 64,
                local_interface=NetworkTopologyInterface.objects.get(),
                confidence=Decimal('0.60'),
                first_seen_at=timezone.now(),
                last_seen_at=timezone.now(),
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            NetworkTopologyObservation.objects.create(
                batch=batch, source_target=self.target, local_device=self.device,
                protocol='snmp_lldp', neighbor={}, evidence='different',
                evidence_sha256='d' * 64, collected_at=timezone.now(),
            )

    def test_validation_rejects_invalid_json_states_confidence_and_time_order(self):
        now = timezone.now()
        batch = TopologyDiscoveryBatch(
            source_task=self.task, source_target=self.target, device=self.device,
            protocol='invalid', status='success', started_at=now,
            collected_at=now, finished_at=now,
        )
        with self.assertRaises(ValidationError):
            batch.full_clean()

        interface = NetworkTopologyInterface(
            device=self.device, stable_key='ifindex:2', vlan_ids={'bad': True},
            first_seen_at=now, last_seen_at=now,
        )
        with self.assertRaises(ValidationError):
            interface.full_clean()

        saved_interface = self.interface(stable_key='ifindex:3')
        link = NetworkTopologyLink(
            stable_link_key='e' * 64, local_interface=saved_interface,
            confidence=Decimal('1.01'), first_seen_at=now,
            last_seen_at=now, protocols='snmp_lldp',
        )
        with self.assertRaises(ValidationError):
            link.full_clean()

    def test_evidence_permission_and_query_indexes_are_declared(self):
        permissions = dict(NetworkTopologyObservation._meta.permissions)
        self.assertIn('view_topology_evidence', permissions)
        interface_indexes = {
            tuple(index.fields) for index in NetworkTopologyInterface._meta.indexes
        }
        link_indexes = {
            tuple(index.fields) for index in NetworkTopologyLink._meta.indexes
        }
        self.assertIn(('device', 'last_seen_at'), interface_indexes)
        self.assertIn(('status', 'last_seen_at'), link_indexes)
