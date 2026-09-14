from unittest.mock import patch
from decimal import Decimal
from django.test import TestCase
from net.models import Server, InspectionProfile, Server_Inspection
from net.inspections.queue import enqueue_task, claim_next_task
from net.inspections.executor import execute_target
from net.infrastructure.http_collectors import collect_windows_http
from net.devices.inventory import refresh_asset_inventory


class WindowsInventoryTests(TestCase):
    def setUp(self):
        self.server = Server.objects.create(ip='192.0.2.205', server_type='windows', disk_total_gb=99)
        self.payload = {
            'cpu': {'usage_percent': 12, 'model': 'Fixture CPU', 'physical_cores': 4, 'logical_processors': 8},
            'memory': {'used_percent': 25, 'total_bytes': 8 * 1024**3},
            'storage_status': [{'device': 'C:', 'used_percent': 10, 'total_bytes': 10 * 1024**3}],
            'physical_disks': [{'device': 'disk0', 'total_bytes': 20 * 1024**3}, {'device': 'disk1', 'total_bytes': 30 * 1024**3}],
        }

    def test_http_worker_updates_only_target_inventory(self):
        other = Server.objects.create(ip='192.0.2.206', server_type='windows')
        profile = InspectionProfile.objects.create(name='Windows', device_type='server', selected_items=['cpu', 'memory', 'storage_status'])
        task = enqueue_task(profile, [self.server.pk], 'manual')
        claim_next_task('inventory-test', 60)
        with patch('net.infrastructure.http_collectors._request', return_value=self.payload):
            outcome = execute_target(task.target_runs.get(), worker_id='inventory-test')
        self.assertEqual(outcome.status, 'success')
        self.server.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.server.cpu_model, 'Fixture CPU')
        self.assertEqual(self.server.cpu_physical_core_count, 4)
        self.assertEqual(self.server.cpu_logical_processor_count, 8)
        self.assertEqual(self.server.memory_total_gb, Decimal('8'))
        self.assertEqual(self.server.disk_total_gb, Decimal('50'))
        self.assertIsNone(other.memory_total_gb)
        self.assertEqual(Server_Inspection.objects.get().details['cpu']['usage_percent'], 12)
        self.assertEqual(Server_Inspection.objects.get().raw_output['physical_disks'], self.payload['physical_disks'])

    def test_unselected_inventory_and_incomplete_disks_do_not_overwrite(self):
        with patch('net.infrastructure.http_collectors._request', return_value=self.payload):
            result = collect_windows_http(self.server, selected_items=['cpu'])
        refresh_asset_inventory(self.server, result.data)
        self.server.refresh_from_db()
        self.assertIsNone(self.server.memory_total_gb)
        self.assertEqual(self.server.disk_total_gb, 99)
        for disks in ([], None, [self.payload['physical_disks'][0]] * 2,
                      [{'device': 'disk0', 'total_bytes': 'NaN'}],
                      [self.payload['physical_disks'][0], {'device': 'disk1', 'total_bytes': None}]):
            refresh_asset_inventory(self.server, {'physical_disks': disks})
            self.server.refresh_from_db()
            self.assertEqual(self.server.disk_total_gb, 99)

    def test_old_agent_memory_and_cpu_count_are_still_usable(self):
        refresh_asset_inventory(self.server, {'cpu': {'usage_percent': 90, 'logical_processors': 8},
                                             'memory': {'total_bytes': 8 * 1024**3, 'used_percent': 99},
                                             'storage_status': self.payload['storage_status']})
        self.server.refresh_from_db()
        self.assertEqual(self.server.memory_total_gb, 8)
        self.assertEqual(self.server.cpu_logical_processor_count, 8)
        self.assertEqual(self.server.disk_total_gb, 99)
