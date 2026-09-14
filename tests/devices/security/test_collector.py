from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from net.infrastructure.collection import CollectionResult


def device(**settings):
    defaults = {
        'ip': '192.0.2.45', 'vendor': 'generic', 'api_url': '',
        'api_username': '', 'api_password': '', 'api_token': '',
        'collection_settings': {},
    }
    defaults.update(settings)
    return SimpleNamespace(**defaults)


class SecurityCollectorTests(SimpleTestCase):
    def test_auto_api_failure_keeps_failure_and_adds_ping_evidence(self):
        from net.devices.security.collector import collect_security

        failed_api = CollectionResult(False, 'failed', '安防设备 API 采集失败：403')
        target = device(api_url='https://192.0.2.45/status')
        with patch('net.devices.security.collector.collect_security_api', return_value=failed_api), patch(
            'net.devices.security.collector.ping_host', return_value=(True, 'ICMP reply')
        ):
            result = collect_security(target, selected_items=['status_data'])

        self.assertTrue(result.reachable)
        self.assertEqual(result.status, 'partial')
        self.assertEqual(result.data['status_data']['source'], 'ping')
        self.assertIn('API 采集失败', result.message)
    def test_auto_prefers_configured_api(self):
        from net.devices.security.collector import collect_security

        api_result = CollectionResult(True, 'success', data={'status_data': {'online': True}})
        with patch('net.devices.security.collector.collect_security_api', return_value=api_result) as api:
            result = collect_security(device(api_url='https://192.0.2.45/status'), selected_items=['status_data'])

        self.assertIs(result, api_result)
        api.assert_called_once()

    def test_snmp_converts_actual_standard_metrics_into_status_data(self):
        from net.devices.security.collector import collect_security

        snmp_result = CollectionResult(True, 'success', data={
            'device_info': {'name': 'NVR'}, 'cpu': {'usage_percent': 33.0},
            'memory': {'usage_percent': 44.0},
        }, raw={'sysName': 'NVR'})
        target = device(collection_settings={
            'protocol': 'snmp',
            'snmp': {'snmp_version': 'v2c', 'snmp_community': 'test-community'},
        })
        with patch('net.devices.security.collector.collect_network_snmp', return_value=snmp_result) as snmp:
            result = collect_security(target, selected_items=['device_info', 'status_data'])

        self.assertEqual(result.status, 'success')
        self.assertEqual(result.data['device_info'], {'name': 'NVR'})
        self.assertEqual(result.data['status_data'], {
            'source': 'snmp', 'cpu_usage_percent': 33.0, 'memory_usage_percent': 44.0,
        })
        self.assertEqual(target.snmp_community, 'test-community')
        snmp.assert_called_once_with(target, 12, selected_items=['device_info', 'cpu', 'memory', 'temperature'])

    def test_ping_only_completes_status_without_claiming_storage(self):
        from net.devices.security.collector import collect_security

        with patch('net.devices.security.collector.ping_host', return_value=(True, 'ICMP reply')):
            result = collect_security(device(), selected_items=['status_data', 'storage_status'])

        self.assertTrue(result.reachable)
        self.assertEqual(result.status, 'partial')
        self.assertEqual(result.data, {'status_data': {'online': True, 'source': 'ping'}})
        self.assertIn('storage_status', result.message)

    def test_ping_failure_does_not_claim_power_loss(self):
        from net.devices.security.collector import collect_security

        with patch('net.devices.security.collector.ping_host', return_value=(False, 'ICMP timed out')):
            result = collect_security(device(), selected_items=['status_data'])

        self.assertFalse(result.reachable)
        self.assertEqual(result.status, 'failed')
        self.assertIn('不能据此判定断电', result.message)
