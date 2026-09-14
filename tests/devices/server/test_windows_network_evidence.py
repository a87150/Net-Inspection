"""N01: Windows Agent interface-array evidence, with only HTTP I/O mocked."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from net.models import InspectionProfile, Server, Server_Inspection
from net.infrastructure.http_collectors import collect_windows_http
from net.inspections.executor import execute_target
from net.inspections.queue import claim_next_task, enqueue_task


class WindowsNetworkEvidenceTests(TestCase):
    def setUp(self):
        self.server = Server.objects.create(name='N01 agent', ip='192.0.2.40', server_type='windows')
        # InspectionHttpService.ps1 Get-InspectionPayload: @() at both levels.
        self.interfaces = [{'interface': 'Ethernet', 'ipv4': ['192.0.2.40'],
                            'gateway': ['192.0.2.1'], 'dns': ['192.0.2.53', '2001:db8::53']}]
        self.cpu = {'usage_percent': 10, 'logical_processors': 8}

    def response(self, payload):
        return SimpleNamespace(headers={'content-type': 'application/json'},
                               json=lambda: deepcopy(payload), raise_for_status=lambda: None)

    def collect(self, payload, selected_items):
        with patch('requests.get', return_value=self.response(payload)):
            return collect_windows_http(self.server, selected_items=selected_items)

    def test_network_only_preserves_shipped_agent_interface_array(self):
        result = self.collect({'network_info': self.interfaces}, ['network_info'])
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.data, {'network_info': self.interfaces})
        self.assertEqual(result.raw, {'network_info': self.interfaces})

    def test_cpu_and_network_preserve_both_selected_items_through_worker(self):
        profile = InspectionProfile.objects.create(name='N01 combined', device_type='server',
                                                   selected_items=['cpu', 'network_info'])
        task = enqueue_task(profile, [self.server.pk], 'manual')
        claim_next_task('n01-worker', 60)
        payload = {'cpu': self.cpu, 'network_info': self.interfaces}
        with patch('requests.get', return_value=self.response(payload)):
            execute_target(task.target_runs.get(), worker_id='n01-worker')
        record = Server_Inspection.objects.get()
        self.assertEqual(record.status, 'success')
        self.assertEqual({key: value for key, value in record.details.items()
                          if key not in {'issue_findings', 'normal_issue_items'}}, payload)
        self.assertEqual(record.details['issue_findings'], [])
        self.assertEqual(record.details['normal_issue_items'], ['inspection_collection', 'cpu', 'network_info'])
        self.assertEqual(record.raw_output, payload)
        from net.inspections.result_storage import expanded_result_snapshot
        self.assertEqual(expanded_result_snapshot(task.target_runs.get())['details'], record.details)
        self.assertFalse(task.alert_events.exists())

    def test_explicit_empty_interface_array_is_valid_selected_evidence(self):
        for selected in (['network_info'], ['cpu', 'network_info']):
            with self.subTest(selected=selected):
                result = self.collect({'network_info': [], 'cpu': self.cpu}, selected)
                self.assertEqual(result.status, 'success')
                self.assertEqual(result.data['network_info'], [])

    def test_interface_without_addresses_retains_powershell_empty_or_null_arrays(self):
        # @($null) serializes as [null]; a missing gateway is not malformed evidence.
        for addresses in ([], [None]):
            interfaces = [{'interface': 'Ethernet', 'ipv4': addresses, 'gateway': addresses, 'dns': addresses}]
            with self.subTest(addresses=addresses):
                result = self.collect({'network_info': interfaces}, ['network_info'])
                self.assertEqual(result.status, 'success')
                self.assertEqual(result.data['network_info'], interfaces)

    def test_missing_network_evidence_is_not_a_valid_empty_result(self):
        result = self.collect({}, ['network_info'])
        self.assertEqual((result.status, result.data), ('failed', {}))
        result = self.collect({'cpu': self.cpu}, ['cpu', 'network_info'])
        self.assertEqual((result.status, result.data), ('partial', {'cpu': self.cpu}))

    def test_malformed_network_never_becomes_success_or_discards_valid_cpu(self):
        row = self.interfaces[0]
        invalid = [None, True, 'online', {}, {'unknown': 'truthy'}, [None], ['Ethernet'], [{}],
                   [{**row, 'interface': ' '}], [{**row, 'interface': 7}],
                   [{key: value for key, value in row.items() if key != 'dns'}],
                   [{**row, 'ipv4': '192.0.2.40'}], [{**row, 'gateway': None}],
                   [{**row, 'dns': [42]}], [{**row, 'dns': ['not-an-address']}],
                   [{**row, 'ipv4': ['2001:db8::40']}], [row, {'unknown': 'truthy'}]]
        for network in invalid:
            with self.subTest(network=network):
                result = self.collect({'network_info': network}, ['network_info'])
                self.assertEqual((result.status, result.data), ('failed', {}))
                result = self.collect({'cpu': self.cpu, 'network_info': network}, ['cpu', 'network_info'])
                self.assertEqual((result.status, result.data), ('partial', {'cpu': self.cpu}))

    def test_existing_network_alias_uses_the_same_schema(self):
        result = self.collect({'network': self.interfaces}, ['network_info'])
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.data, {'network_info': self.interfaces})
        self.assertEqual(result.raw, {'network': self.interfaces})

    def test_valid_network_does_not_mask_missing_cpu(self):
        result = self.collect({'network_info': self.interfaces}, ['cpu', 'network_info'])
        self.assertEqual((result.status, result.data), ('partial', {'network_info': self.interfaces}))
