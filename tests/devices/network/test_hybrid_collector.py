from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from net.infrastructure.collection import CollectionResult
from net.devices.network.collector import (
    _merge_network_results,
    collect_network,
)


class FakeCollector:
    def __init__(self, result):
        self.result = result
        self.calls = []

    @property
    def selected_items(self):
        return self.calls[-1] if self.calls else None

    def __call__(self, _device, _timeout=12, selected_items=None):
        self.calls.append(list(selected_items or []))
        return self.result


class HybridNetworkCollectorTests(SimpleTestCase):
    def setUp(self):
        self.device = SimpleNamespace(connection_type='hybrid')
        self.snmp = FakeCollector(CollectionResult(
            True,
            'success',
            data={
                'cpu': {'usage_percent': 10},
                'interface_status': [{'name': 'Gi0/1', 'status': 'up'}],
            },
        ))
        self.ssh = FakeCollector(CollectionResult(
            True,
            'success',
            data={
                'logs': ['login accepted'],
                'config_info': {'status': 'success', 'content': 'hostname edge'},
            },
        ))

    def collect(self, selected_items):
        return collect_network(
            self.device,
            selected_items=selected_items,
            snmp_collector=self.snmp,
            ssh_collector=self.ssh,
        )

    def test_ssh_routes_every_requested_item_only_to_ssh(self):
        self.device.connection_type = 'ssh'
        self.ssh.result = CollectionResult(
            True, 'success', data={'cpu': {'usage_percent': 8}, 'logs': []},
        )

        result = self.collect(['cpu', 'logs'])

        self.assertEqual(self.snmp.calls, [])
        self.assertEqual(self.ssh.selected_items, ['cpu', 'logs'])
        self.assertEqual(result.status, 'success')

    def test_ssh_failed_config_object_is_retained_but_not_completed(self):
        self.device.connection_type = 'ssh'
        failed_config = {'status': 'failed', 'message': 'sanitized failure'}
        self.ssh.result = CollectionResult(
            True, 'failed', data={'config_info': failed_config},
        )

        result = self.collect(['config_info'])

        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.data['config_info'], failed_config)
        self.assertIn('config_info', result.message)

    def test_snmp_leaves_unsupported_ssh_only_item_explicitly_missing(self):
        self.device.connection_type = 'snmp'
        self.snmp.result = CollectionResult(
            True, 'success', data={'cpu': {'usage_percent': 10}},
        )

        result = self.collect(['cpu', 'logs'])

        self.assertEqual(self.snmp.selected_items, ['cpu'])
        self.assertEqual(self.ssh.calls, [])
        self.assertEqual(result.status, 'partial')
        self.assertIn('logs', result.message)
        self.assertNotIn('logs', result.data)

    def test_hybrid_routes_metrics_to_snmp_and_log_config_to_ssh(self):
        result = self.collect(['cpu', 'interface_status', 'logs', 'config_info'])

        self.assertEqual(self.snmp.selected_items, ['cpu', 'interface_status'])
        self.assertEqual(self.ssh.selected_items, ['logs', 'config_info'])
        self.assertEqual(result.status, 'success')

    def test_hybrid_failed_config_object_makes_successful_metrics_partial(self):
        failed_config = {'status': 'unsupported', 'message': 'not supported'}
        self.ssh.result = CollectionResult(
            True, 'failed', data={'config_info': failed_config},
        )

        result = self.collect(['cpu', 'config_info'])

        self.assertEqual(result.status, 'partial')
        self.assertEqual(result.data['cpu'], {'usage_percent': 10})
        self.assertEqual(result.data['config_info'], failed_config)

    def test_auto_falls_back_to_ssh_only_for_missing_snmp_items(self):
        self.device.connection_type = 'auto'
        self.snmp.result = CollectionResult(
            True, 'partial', data={'cpu': {'usage_percent': 10}},
        )
        self.ssh.result = CollectionResult(
            True, 'success', data={'memory': {'usage_percent': 20}},
        )

        result = self.collect(['cpu', 'memory'])

        self.assertEqual(self.snmp.selected_items, ['cpu', 'memory'])
        self.assertEqual(self.ssh.selected_items, ['memory'])
        self.assertEqual(result.status, 'success')

    def test_one_protocol_success_preserves_data_as_partial(self):
        self.snmp.result = CollectionResult(
            True, 'success', data={'cpu': {'usage_percent': 10}}, duration_ms=4,
        )
        self.ssh.result = CollectionResult(
            False, 'failed', 'SSH unavailable', duration_ms=6,
        )

        result = self.collect(['cpu', 'logs'])

        self.assertEqual(result.data, {'cpu': {'usage_percent': 10}})
        self.assertEqual(result.status, 'partial')
        self.assertTrue(result.reachable)
        self.assertEqual(result.duration_ms, 10)

    def test_both_protocols_failed_without_data_is_failed(self):
        self.snmp.result = CollectionResult(False, 'failed', 'SNMP unavailable')
        self.ssh.result = CollectionResult(False, 'failed', 'SSH unavailable')

        result = self.collect(['cpu', 'logs'])

        self.assertEqual(result.status, 'failed')
        self.assertFalse(result.reachable)
        self.assertEqual(result.data, {})

    def test_merge_keeps_colliding_raw_evidence_with_protocol_prefixes(self):
        result = _merge_network_results(
            ['cpu', 'logs'],
            [
                ('snmp', CollectionResult(
                    True, 'failed', data={'cpu': {'usage_percent': 10}},
                    raw={'shared': 'snmp evidence'}, duration_ms=3,
                )),
                ('ssh', CollectionResult(
                    False, 'failed', data={'logs': []},
                    raw={'shared': 'ssh evidence'}, duration_ms=7,
                )),
            ],
        )

        self.assertEqual(result.status, 'success')
        self.assertEqual(result.raw, {
            'snmp:shared': 'snmp evidence',
            'ssh:shared': 'ssh evidence',
        })
        self.assertEqual(result.duration_ms, 10)
        self.assertTrue(result.reachable)

    def test_merge_allocates_suffixes_when_input_already_uses_protocol_prefixes(self):
        result = _merge_network_results(
            ['cpu', 'logs'],
            [
                ('snmp', CollectionResult(
                    True, 'success', data={'cpu': {'usage_percent': 10}},
                    raw={'shared': 'snmp shared'},
                )),
                ('ssh', CollectionResult(
                    True, 'success', data={'logs': ['ok']},
                    raw={
                        'shared': 'ssh shared',
                        'snmp:shared': 'ssh pre-prefixed',
                        'ssh:shared': 'ssh second pre-prefixed',
                    },
                )),
            ],
        )

        self.assertEqual(list(result.raw.values()), [
            'snmp shared',
            'ssh shared',
            'ssh pre-prefixed',
            'ssh second pre-prefixed',
        ])
        self.assertEqual(len(result.raw), 4)

    @patch('net.devices.network.collector.collect_network_ssh')
    def test_default_ssh_collector_reports_missing_credentials_without_transport(self, ssh):
        self.device.connection_type = 'ssh'
        ssh.return_value = CollectionResult(
            True, 'success', data={'cpu': {'usage_percent': 1}},
        )

        result = collect_network(self.device, selected_items=['cpu'])

        ssh.assert_not_called()
        self.assertEqual(result.status, 'failed')
        self.assertIn('未配置网络设备 SSH 账号和密码', result.message)

    @patch('net.devices.network.collector.collect_network_snmp')
    def test_default_snmp_collector_reports_missing_credentials_without_transport(self, snmp):
        self.device.connection_type = 'snmp'
        self.device.snmp_version = 'v2c'
        self.device.snmp_community = ''
        snmp.return_value = CollectionResult(
            True, 'success', data={'cpu': {'usage_percent': 1}},
        )

        result = collect_network(self.device, selected_items=['cpu'])

        snmp.assert_not_called()
        self.assertEqual(result.status, 'failed')
        self.assertIn('未配置网络设备 SNMP 凭据', result.message)
