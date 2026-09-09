from decimal import Decimal

from django.test import TestCase

from net.devices.inventory import refresh_asset_inventory
from net.inspections.device_issues import evaluate_device_issues
from net.models import Server


class LinuxInventoryRefreshTests(TestCase):
    def test_refresh_extracts_static_linux_evidence_without_persisting_load(self):
        server = Server.objects.create(ip='192.0.2.250', server_type='linux')

        changed = refresh_asset_inventory(server, {
            'system_info': {
                'uname': 'Linux fixture 6.12.0-test.x86_64 #1 SMP x86_64 GNU/Linux',
                'os_release': 'PRETTY_NAME="Rocky Linux 10.2 (Red Quartz)"\nVERSION_ID="10.2"',
            },
            'cpu': {
                'raw': 'Architecture: x86_64\nCPU(s): 2\nModel name: Intel(R) Core(TM) i5-10500 CPU @ 3.10GHz\nCore(s) per socket: 2\nSocket(s): 1\n---LOAD---\n99.00 99.00 99.00 1/157 9171',
            },
            'memory': {'total_bytes': '3810934784', 'used_bytes': '521998336'},
            'storage_status': [
                {'filesystem': '/dev/mapper/rlm-root', 'total_bytes': '46623883264', 'mount': '/'},
                {'filesystem': 'tmpfs', 'total_bytes': '1905467392', 'mount': '/dev/shm'},
                {'filesystem': '/dev/sda2', 'total_bytes': '2080374784', 'mount': '/boot'},
                {'filesystem': '/dev/sda1', 'total_bytes': '627875840', 'mount': '/boot/efi'},
            ],
        })

        server.refresh_from_db()

        self.assertEqual(changed, {
            'os_version', 'os_build', 'architecture', 'cpu_model',
            'cpu_physical_core_count', 'cpu_logical_processor_count',
            'memory_total_gb',
        })
        self.assertEqual(server.os_version, 'Rocky Linux 10.2 (Red Quartz)')
        self.assertEqual(server.os_build, '6.12.0-test.x86_64')
        self.assertEqual(server.architecture, 'x86_64')
        self.assertEqual(server.cpu_model, 'Intel(R) Core(TM) i5-10500 CPU @ 3.10GHz')
        self.assertEqual(server.cpu_physical_core_count, 2)
        self.assertEqual(server.cpu_logical_processor_count, 2)
        self.assertEqual(server.memory_total_gb, Decimal('3.55'))
        self.assertIsNone(server.disk_total_gb)


class LinuxIssueEvaluationTests(TestCase):
    def test_failed_services_and_priority_logs_are_warnings(self):
        issues, normal = evaluate_device_issues(
            'servers', ['services', 'logs'], {
                'system_info': {'uname': 'Linux fixture 6.12.0-test.x86_64'},
                'services': ['vboxadd.service loaded failed failed vboxadd.service'],
                'logs': ['kernel: rcu_preempt detected stalls on CPUs/tasks'],
            }, reachable=True, status='success',
        )

        self.assertEqual(
            [(issue['analysis_item'], issue['severity']) for issue in issues],
            [('services', 'warning'), ('logs', 'warning')],
        )
        self.assertEqual(normal, ['inspection_collection'])
    def test_cpu_evidence_refreshes_even_when_other_linux_sections_are_missing(self):
        server = Server.objects.create(ip='192.0.2.251', server_type='linux')
        changed = refresh_asset_inventory(server, {
            'cpu': {'raw': 'Architecture: x86_64\nCPU(s): 4\nModel name: Fixture CPU\nCore(s) per socket: 2\nSocket(s): 2'},
        })
        server.refresh_from_db()
        self.assertEqual(changed, {'architecture', 'cpu_model', 'cpu_physical_core_count', 'cpu_logical_processor_count'})
        self.assertEqual(server.cpu_physical_core_count, 4)


class LinuxSelectedIssueEvaluationTests(TestCase):
    def test_failed_services_are_found_when_only_services_were_selected(self):
        issues, normal = evaluate_device_issues(
            'servers', ['services'], {'services': ['fixture.service loaded failed failed fixture.service']},
            reachable=True, status='success', server_type='linux',
        )
        self.assertEqual([(issue['analysis_item'], issue['severity']) for issue in issues], [('services', 'warning')])
        self.assertEqual(normal, ['inspection_collection'])


class LinuxDiskEvidenceTests(TestCase):
    def test_only_complete_physical_disk_evidence_updates_total(self):
        import json
        from net.infrastructure.ssh_collectors import _parse_linux
        good = {'name': 'sda', 'type': 'disk', 'size': 10737418240}
        for devices, expected in [([good], 10), (None, None), ([good, good], None),
                                  ([good, {'name': 'sdb', 'type': 'disk', 'size': 'bad'}], None)]:
            with self.subTest(devices=devices):
                result = _parse_linux({'storage': 'Filesystem Size Used Avail Use% Mount\n/dev/sda1 2 1 1 50% /\n---LSBLK---\n' + json.dumps({'blockdevices': devices})})
                self.assertEqual(result['disk_total_gb'], expected)
                self.assertEqual(len(result['storage_status']), 1)

    def test_no_entries_marker_is_not_an_error_log(self):
        from net.infrastructure.ssh_collectors import _parse_linux
        data = _parse_linux({'logs': '-- No entries --'})
        self.assertEqual(data['logs'], [])
        issues, normal = evaluate_device_issues('servers', ['logs'], data,
            reachable=True, status='success', server_type='linux')
        self.assertEqual(issues, [])
        self.assertIn('logs', normal)
