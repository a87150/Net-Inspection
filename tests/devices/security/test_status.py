"""Real security response parsing, normalization and persisted selection contracts."""

import json
from unittest.mock import patch

import requests
from django.test import TestCase

from net.models import Error_Monitor, InspectionProfile, SecurityDevice
from net.devices.security.api import collect_security_api
from net.inspections.queue import claim_next_task, enqueue_task, finish_task
from net.inspections.executor import execute_target


NATIVE_XML = '''<DeviceStatus xmlns="urn:device">
  <currentDeviceTime>2026-08-31T12:00:00</currentDeviceTime>
  <deviceUpTime>12345</deviceUpTime>
  <CPUList><CPU><cpuUtilization>17</cpuUtilization></CPU></CPUList>
  <logs><entry><cpuUtilization>PRIVATE LOG</cpuUtilization></entry></logs>
  <channels><channel><online>PRIVATE CHANNEL</online></channel></channels>
</DeviceStatus>'''


def response(body):
    result = requests.Response()
    result.status_code = 200
    result.headers['content-type'] = 'application/xml' if isinstance(body, str) else 'application/json'
    result._content = (body if isinstance(body, str) else json.dumps(body)).encode()
    return result


class SecurityStatusTests(TestCase):
    def setUp(self):
        # Auto collection now falls back to ICMP; never contact a real host in tests.
        ping_patch = patch('net.devices.security.collector.ping_host', return_value=(False, 'No reply'))
        self.ping = ping_patch.start()
        self.addCleanup(ping_patch.stop)
        self.device = SecurityDevice.objects.create(ip='192.0.2.245', vendor='hikvision', api_url='http://192.0.2.245/status')

    def assert_status_roundtrip(self, body, expected, *, expected_abnormal=False):
        with patch('net.infrastructure.http_collectors.requests.get', return_value=response(body)):
            collected = collect_security_api(self.device, selected_items=['status_data'])
            self.assertEqual(collected.status, 'success')
            self.assertEqual(collected.data, {'status_data': expected})
            self.assertEqual(collected.raw, {'status_data': expected})
            self.assertNotIn('PRIVATE', json.dumps(collected.raw))
            # Also preserve native status for legacy callers without a selection.
            legacy = collect_security_api(self.device)
            self.assertEqual(legacy.data['status_data'], expected)
            profile = InspectionProfile.objects.create(name='security', device_type='monitor', selected_items=['status_data'])
            enqueue_task(profile, [self.device.pk], 'manual')
            task = claim_next_task('status-worker', 30)
            target = task.target_runs.get()
            outcome = execute_target(target, worker_id='status-worker')
        finish_task(task.pk, 'status-worker')
        target.refresh_from_db()
        task.refresh_from_db()
        record = self.device.inspections.get()
        self.assertEqual(outcome.status, 'success')
        self.assertEqual(task.status, 'success')
        self.assertEqual({key: value for key, value in record.details.items()
                          if key not in {'issue_findings', 'normal_issue_items'}}, {'status_data': expected})
        if expected_abnormal:
            self.assertEqual([(issue['analysis_item'], issue['severity']) for issue in record.details['issue_findings']],
                             [('status_data', 'warning')])
            self.assertEqual(record.details['normal_issue_items'], ['inspection_collection'])
            self.assertEqual(target.result_snapshot['health_status'], 'abnormal')
        else:
            self.assertEqual(record.details['issue_findings'], [])
            self.assertEqual(record.details['normal_issue_items'], ['inspection_collection', 'status_data'])
            self.assertEqual(target.result_snapshot['health_status'], 'normal')
        self.assertEqual(record.raw_output, collected.raw)
        from net.inspections.result_storage import expanded_result_snapshot
        self.assertEqual(expanded_result_snapshot(target)['details'], record.details)
        self.assertEqual(target.result_id, str(record.pk))
        self.assertEqual(record.task_target_id, target.pk)
        self.assertEqual(Error_Monitor.objects.filter(inspection=record).exists(), expected_abnormal)
        self.assertNotIn('PRIVATE', json.dumps([record.raw_output, record.details, target.result_snapshot]))

    def test_native_xml_status_preserves_cpu_and_ignores_unselected_subtrees(self):
        self.assert_status_roundtrip(NATIVE_XML, {
            'currentDeviceTime': '2026-08-31T12:00:00', 'deviceUpTime': '12345',
            'CPUList': {'CPU': {'cpuUtilization': '17'}},
        })

    def test_native_flat_json_status_maps_before_selection(self):
        self.assert_status_roundtrip({
            'currentDeviceTime': '2026-08-31T12:00:00', 'deviceUpTime': 12345,
            'cpuUtilization': 0, 'channels': ['PRIVATE CHANNEL'], 'logs': ['PRIVATE LOG'],
        }, {'currentDeviceTime': '2026-08-31T12:00:00', 'deviceUpTime': 12345, 'cpuUtilization': 0})

    def test_native_json_device_status_envelope_maps_before_selection(self):
        self.assert_status_roundtrip({'DeviceStatus': {
            'deviceUpTime': 12345, 'CPUList': {'CPU': [
                {'cpuUtilization': 17, 'logs': ['PRIVATE LOG']}, {'cpuUtilization': 23},
            ]}, 'channels': ['PRIVATE CHANNEL'],
        }}, {'deviceUpTime': 12345, 'CPUList': {'CPU': [{'cpuUtilization': 17}, {'cpuUtilization': 23}]}})

    def test_wrapped_json_status_excludes_unselected_nested_items(self):
        self.assert_status_roundtrip({'status': {
            'online': False, 'logs': ['PRIVATE LOG'], 'channels': ['PRIVATE CHANNEL'],
        }, 'logs': ['PRIVATE LOG']}, {'online': False}, expected_abnormal=True)

    def test_canonical_json_status_is_not_an_unrestricted_raw_fallback(self):
        self.assert_status_roundtrip({'status_data': {
            'deviceUpTime': 12345, 'logs': ['PRIVATE LOG'], 'channels': ['PRIVATE CHANNEL'],
        }}, {'deviceUpTime': 12345})

    def test_unmappable_requested_status_is_failed_and_creates_error_record(self):
        profile = InspectionProfile.objects.create(name='missing', device_type='monitor', selected_items=['status_data'])
        for body in ({'unknown': 17, 'logs': ['PRIVATE LOG']},
                     {'status_data': {}}, {'status': {'logs': ['PRIVATE LOG']}},
                     '<DeviceStatus><logs><deviceUpTime>123</deviceUpTime></logs></DeviceStatus>',
                     {'status_data': {'cpuUtilization': None}}, {'status': '   '}):
            with self.subTest(body=body), patch('net.infrastructure.http_collectors.requests.get', return_value=response(body)):
                collected = collect_security_api(self.device, selected_items=['status_data'])
                self.assertEqual(collected.status, 'failed')
                self.assertTrue(collected.reachable)
                self.assertIn('status_data', collected.message)
                self.assertEqual(collected.raw, {})
                self.assertEqual(collected.data, {})
                enqueue_task(profile, [self.device.pk], 'manual')
                task = claim_next_task('status-worker', 30)
                target = task.target_runs.get()
                outcome = execute_target(target, worker_id='status-worker')
                finish_task(task.pk, 'status-worker')
                target.refresh_from_db()
                record = self.device.inspections.get(task_target=target)
                self.assertEqual(outcome.status, 'failed')
                self.assertEqual(record.status, 'failed')
                self.assertEqual(target.status, 'failed')
                self.assertEqual(record.raw_output, {})
                self.assertTrue(Error_Monitor.objects.filter(inspection=record).exists())

    def test_unmappable_status_with_other_selected_data_is_partial(self):
        with patch('net.infrastructure.http_collectors.requests.get', return_value=response({'storage': [{'status': 'normal'}]})):
            collected = collect_security_api(self.device, selected_items=['status_data', 'storage_status'])
            profile = InspectionProfile.objects.create(
                name='partial', device_type='monitor', selected_items=['status_data', 'storage_status'],
            )
            enqueue_task(profile, [self.device.pk], 'manual')
            task = claim_next_task('status-worker', 30)
            target = task.target_runs.get()
            outcome = execute_target(target, worker_id='status-worker')
        finish_task(task.pk, 'status-worker')
        self.assertEqual(collected.status, 'partial')
        self.assertEqual(collected.data, {'storage_status': [{'status': 'normal'}]})
        self.assertEqual(collected.raw, {'storage': [{'status': 'normal'}]})
        self.assertIn('status_data', collected.message)
        self.assertEqual(outcome.status, 'partial')
        record = self.device.inspections.get()
        target.refresh_from_db()
        task.refresh_from_db()
        # A partially collected target retains partial aggregate status even
        # when the batch has no fully successful targets (baseline 3c376f6).
        self.assertEqual(task.status, 'partial')
        self.assertEqual(target.status, 'partial')
        self.assertEqual(record.raw_output, collected.raw)
        self.assertEqual({key: value for key, value in record.details.items()
                          if key not in {'issue_findings', 'normal_issue_items'}}, collected.data)
        self.assertEqual([(issue['rule_key'], issue['severity']) for issue in record.details['issue_findings']],
                         [('missing.status_data', 'info')])
        self.assertEqual(record.details['normal_issue_items'], ['inspection_collection', 'storage_status'])
        self.assertTrue(Error_Monitor.objects.filter(inspection=record).exists())


    def test_worker_persists_snmp_then_api_evidence(self):
        from net.infrastructure.collection import CollectionResult
        calls = []
        def snmp(*args, **kwargs):
            calls.append('snmp')
            return CollectionResult(True, 'success', data={'device_info': {'name': 'Test NVR'}})
        def api(*args, **kwargs):
            calls.append('api')
            return response({'storage': [{'status': 'normal'}]})
        profile = InspectionProfile.objects.create(
            name='fallback', device_type='monitor', selected_items=['device_info', 'storage_status'])
        with patch('net.devices.security.collector._has_snmp_credentials', return_value=True), patch(
            'net.devices.security.collector.collect_network_snmp', side_effect=snmp), patch(
            'net.infrastructure.http_collectors.requests.get', side_effect=api):
            enqueue_task(profile, [self.device.pk], 'manual')
            task = claim_next_task('fallback-worker', 30)
            target = task.target_runs.get()
            outcome = execute_target(target, worker_id='fallback-worker')
            finish_task(task.pk, 'fallback-worker')
        self.assertEqual(calls, ['snmp', 'api'])
        self.ping.assert_not_called()
        record = self.device.inspections.get()
        task.refresh_from_db()
        self.assertEqual(outcome.status, 'success')
        self.assertEqual(task.status, 'success')
        self.assertEqual(record.details['device_info'], {'name': 'Test NVR'})
        self.assertEqual(record.details['storage_status'], [{'status': 'normal'}])
        self.assertNotIn('status_data', record.details)

    def test_worker_retains_partial_status_after_ping_fallback(self):
        from net.infrastructure.collection import CollectionResult
        self.ping.return_value = (True, 'Reply')
        profile = InspectionProfile.objects.create(
            name='fallback', device_type='monitor', selected_items=['status_data', 'storage_status'])
        with patch('net.devices.security.collector._has_snmp_credentials', return_value=True), patch(
            'net.devices.security.collector.collect_network_snmp', return_value=CollectionResult(False, 'failed', 'SNMP timeout')), patch(
            'net.infrastructure.http_collectors.requests.get', return_value=response({'unknown': 1})):
            enqueue_task(profile, [self.device.pk], 'manual')
            task = claim_next_task('fallback-worker', 30)
            target = task.target_runs.get()
            outcome = execute_target(target, worker_id='fallback-worker')
            finish_task(task.pk, 'fallback-worker')
        self.ping.assert_called_once()
        record = self.device.inspections.get()
        target.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(outcome.status, 'partial')
        self.assertEqual(task.status, 'partial')
        self.assertEqual(target.status, 'partial')
        self.assertEqual(record.status, 'partial')
        self.assertEqual(record.details['status_data'], {'online': True, 'source': 'ping'})
        self.assertNotIn('storage_status', record.details)
        self.assertIn('SNMP timeout', record.summary)
        self.assertIn('Ping', record.summary)
