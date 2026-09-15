from unittest.mock import patch
from django.test import SimpleTestCase
from net.infrastructure.collection import CollectionResult
from net.devices.security.collector import collect_security
from .test_collector import device


class SecurityFallbackTests(SimpleTestCase):
    def target(self, **settings):
        return device(api_url='https://security.example.test/status', collection_settings={
            'snmp': {'snmp_version': 'v2c', 'snmp_community': 'test-only'}, **settings})

    def test_snmp_success_skips_api_and_ping(self):
        evidence = CollectionResult(True, 'success', data={'device_info': {'name': 'NVR'}})
        with patch('net.devices.security.collector.collect_network_snmp', return_value=evidence) as snmp, patch(
            'net.devices.security.collector.collect_security_api') as api, patch('net.devices.security.collector.ping_host') as ping:
            result = collect_security(self.target(), selected_items=['device_info'])
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.data['device_info']['name'], 'NVR')
        snmp.assert_called_once()
        api.assert_not_called()
        ping.assert_not_called()

    def test_api_only_fills_missing_items_after_snmp(self):
        calls = []
        def snmp(*args, **kwargs):
            calls.append('snmp')
            return CollectionResult(True, 'success', data={'device_info': {'name': 'SNMP name'}})
        def api(*args, **kwargs):
            calls.append('api')
            self.assertEqual(kwargs['selected_items'], ['storage_status'])
            return CollectionResult(True, 'success', data={'storage_status': [{'total': 100}]})
        with patch('net.devices.security.collector.collect_network_snmp', side_effect=snmp), patch(
            'net.devices.security.collector.collect_security_api', side_effect=api), patch('net.devices.security.collector.ping_host') as ping:
            result = collect_security(self.target(), selected_items=['device_info', 'storage_status'])
        self.assertEqual(calls, ['snmp', 'api'])
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.data['device_info']['name'], 'SNMP name')
        ping.assert_not_called()

    def test_both_protocols_fail_then_ping_retains_failures_and_missing_items(self):
        calls = []
        def snmp(*args, **kwargs):
            calls.append('snmp')
            return CollectionResult(False, 'failed', 'SNMP timeout')
        def api(*args, **kwargs):
            calls.append('api')
            return CollectionResult(False, 'failed', 'API denied')
        def ping(*args):
            calls.append('ping')
            return True, 'reply'
        with patch('net.devices.security.collector.collect_network_snmp', side_effect=snmp), patch(
            'net.devices.security.collector.collect_security_api', side_effect=api), patch(
            'net.devices.security.collector.ping_host', side_effect=ping):
            result = collect_security(self.target(), selected_items=['status_data', 'storage_status'])
        self.assertEqual(calls, ['snmp', 'api', 'ping'])
        self.assertEqual(result.status, 'partial')
        self.assertTrue(result.reachable)
        self.assertIn('SNMP timeout', result.message)
        self.assertIn('API denied', result.message)
        self.assertNotIn('storage_status', result.data)
        self.assertEqual(result.data['status_data']['source'], 'ping')

    def test_failed_snmp_marker_is_not_successful_evidence(self):
        with patch('net.devices.security.collector.collect_network_snmp', return_value=CollectionResult(
            True, 'failed', data={'device_info': {'status': 'failed'}})), patch(
            'net.devices.security.collector.collect_security_api', return_value=CollectionResult(
                True, 'success', data={'device_info': {'name': 'API name'}})) as api:
            result = collect_security(self.target(), selected_items=['device_info'])
        api.assert_called_once()
        self.assertEqual(result.data['device_info']['name'], 'API name')

    def test_snmp_failure_without_api_still_uses_ping(self):
        target = self.target()
        target.api_url = ''
        with patch('net.devices.security.collector.collect_network_snmp', return_value=CollectionResult(False, 'failed', 'SNMP timeout')), patch(
            'net.devices.security.collector.ping_host', return_value=(True, 'reply')) as ping:
            result = collect_security(target, selected_items=['status_data'])
        ping.assert_called_once()
        self.assertEqual(result.status, 'partial')

    def test_explicit_snmp_does_not_silently_fall_back(self):
        with patch('net.devices.security.collector.collect_network_snmp', return_value=CollectionResult(False, 'failed')), patch(
            'net.devices.security.collector.collect_security_api') as api, patch('net.devices.security.collector.ping_host') as ping:
            result = collect_security(self.target(protocol='snmp'), selected_items=['status_data'])
        self.assertEqual(result.status, 'failed')
        api.assert_not_called()
        ping.assert_not_called()


    def test_snmp_total_timeout_cancels_pending_collection(self):
        import asyncio
        from net.devices.network.snmp import collect_network_snmp
        cancelled = []
        async def pending(*args):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.append(True)
        with patch('net.devices.network.snmp._collect_snapshot', side_effect=pending):
            result = collect_network_snmp(self.target(), selected_items=['device_info'], total_timeout=0.01)
        self.assertEqual(cancelled, [True])
        self.assertEqual(result.status, 'failed')

    def test_explicit_empty_selection_does_not_contact_devices(self):
        with patch('net.devices.security.collector.collect_network_snmp') as snmp, patch(
            'net.devices.security.collector.collect_security_api') as api, patch('net.devices.security.collector.ping_host') as ping:
            collect_security(self.target(), selected_items=[])
        snmp.assert_not_called()
        api.assert_not_called()
        ping.assert_not_called()

    def test_auto_item_override_keeps_ping_fallback_partial(self):
        with patch('net.devices.security.collector.collect_network_snmp', return_value=CollectionResult(False, 'failed', 'SNMP timeout')), patch(
            'net.devices.security.collector.collect_security_api', return_value=CollectionResult(False, 'failed', 'API denied')), patch(
            'net.devices.security.collector.ping_host', return_value=(True, 'reply')):
            result = collect_security(self.target(item_methods={'status_data': 'auto'}), selected_items=['status_data'])
        self.assertEqual(result.status, 'partial')
        self.assertEqual(result.data['status_data']['source'], 'ping')


    def test_api_business_failure_is_evidence_and_not_replaced_by_ping(self):
        with patch('net.devices.security.collector.collect_network_snmp', return_value=CollectionResult(
            True, 'success', data={'device_info': {'name': 'SNMP name'}})), patch(
            'net.devices.security.collector.collect_security_api', return_value=CollectionResult(
                True, 'success', data={'storage_status': {'status': 'failed'}})), patch(
            'net.devices.security.collector.ping_host') as ping:
            result = collect_security(self.target(), selected_items=['device_info', 'storage_status'])
        self.assertEqual(result.status, 'success')
        self.assertEqual(result.data['storage_status'], {'status': 'failed'})
        ping.assert_not_called()

    def test_failed_configuration_capture_is_still_missing(self):
        with patch('net.devices.security.collector.collect_security_api', return_value=CollectionResult(
            True, 'failed', data={'config_info': {'status': 'failed'}})), patch(
            'net.devices.security.collector.ping_host', return_value=(True, 'Reply')) as ping:
            result = collect_security(self.target(), selected_items=['config_info'])
        self.assertEqual(result.status, 'partial')
        self.assertIn('config_info', result.message)
        ping.assert_called_once()


    def test_api_status_and_config_share_stage_budget(self):
        from net.devices.security.api import collect_security_api
        with patch('net.infrastructure.http_collectors.monotonic', side_effect=[10, 12]), patch(
            'net.infrastructure.http_collectors._request', return_value={'status_data': {'online': True}}) as request, patch(
            'net.infrastructure.http_collectors.collect_native_configuration', return_value={'status': 'success', 'content': 'config'}) as config:
            target = self.target()
            target.verify_ssl = True
            result = collect_security_api(target, 6, ['status_data', 'config_info'])
        self.assertEqual(request.call_args.kwargs['timeout'], 3)
        self.assertEqual(config.call_args.args[1], 4)
        self.assertEqual(result.status, 'success')
