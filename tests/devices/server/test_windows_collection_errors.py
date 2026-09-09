import json
from types import SimpleNamespace
from unittest.mock import patch

import requests
from django.test import SimpleTestCase
from net.infrastructure.http_collectors import collect_windows_http
from net.inspections.device_issues import evaluate_device_issues
from net.inspections.executor import _selected_details
from net.infrastructure.collection import CollectionResult


class WindowsCollectionErrorsTests(SimpleTestCase):
    def collect(self, payload, selected, code=200):
        response = requests.Response()
        response.status_code = code
        response._content = json.dumps(payload).encode()
        response.headers['Content-Type'] = 'application/json'
        server = SimpleNamespace(ip='192.0.2.1', api_url='', api_token='fixture-secret', verify_ssl=True)
        with patch('net.infrastructure.http_collectors.requests.get', return_value=response):
            return collect_windows_http(server, selected_items=selected)

    def test_service_failure_keeps_cpu_and_records_sanitized_error(self):
        result = self.collect({'cpu': {'usage_percent': 12}, 'services': [],
            'collection_errors': {'services': ['PermissionDenied fixture-secret']}}, ['cpu', 'services'])
        self.assertEqual(result.status, 'partial')
        self.assertEqual(result.data, {'cpu': {'usage_percent': 12}})
        self.assertIn('PermissionDenied', result.message)
        self.assertNotIn('fixture-secret', result.message + str(result.raw))
        self.assertIn('services', result.raw['collection_errors'])

    def test_empty_services_with_error_is_failed_not_healthy(self):
        result = self.collect({'services': [], 'collection_errors': {'services': 'PermissionDenied'}}, ['services'])
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.data, {})

    def test_unselected_failure_does_not_affect_cpu(self):
        result = self.collect({'cpu': {'usage_percent': 12},
            'collection_errors': {'services': ['PermissionDenied']}}, ['cpu'])
        self.assertEqual(result.status, 'success')
        self.assertNotIn('collection_errors', result.raw)

    def test_old_agent_500_explains_error_without_token(self):
        result = self.collect({'error': 'PermissionDenied fixture-secret'}, ['services'], 500)
        self.assertEqual(result.status, 'failed')
        self.assertIn('PermissionDenied', result.message)
        self.assertNotIn('fixture-secret', result.message)

    def test_empty_services_without_errors_are_valid(self):
        self.assertEqual(self.collect({'services': []}, ['services']).status, 'success')

    def test_partial_service_payload_keeps_collected_rows_and_marks_collection_partial(self):
        services = [
            {'Name': name, 'DisplayName': name + ' service', 'Status': 1, 'StartType': 2}
            for name in ('edgeupdate', 'wuauserv', 'bits', 'spooler', 'eventlog', 'winrm', 'termservice')
        ]
        result = self.collect({
            'services': services,
            'collection_errors': {'services': [
                'PermissionDenied CDPUserSvc_1 fixture-secret',
                'PermissionDenied CDPUserSvc_2 fixture-secret',
                'PermissionDenied CDPUserSvc_3 fixture-secret',
                'PermissionDenied CDPUserSvc_4 fixture-secret',
            ]},
        }, ['services'])

        self.assertEqual(result.status, 'partial')
        self.assertEqual(result.data['services'], services)
        self.assertEqual(len(result.raw['collection_errors']['services']), 4)
        self.assertNotIn('fixture-secret', result.message + str(result.raw))

    def test_cpu_with_collection_error_is_not_kept_as_valid_evidence(self):
        result = self.collect({
            'cpu': {'usage_percent': 12},
            'collection_errors': {'cpu': ['PermissionDenied fixture-secret']},
        }, ['cpu'])

        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.data, {})
        self.assertNotIn('fixture-secret', result.message + str(result.raw))

    def test_selected_details_keep_selected_collection_limitations(self):
        task = SimpleNamespace(selected_items_snapshot=['services'])
        collection = CollectionResult(True, 'partial', data={'services': []}, raw={
            'collection_errors': {'services': ['PermissionDenied CDPUserSvc_1']},
        })

        self.assertEqual(_selected_details(task, collection), {
            'services': [],
            'collection_errors': {'services': ['PermissionDenied CDPUserSvc_1']},
        })

    def test_lowercase_stopped_automatic_windows_service_is_an_attention_item(self):
        services = [{'name': 'edgeupdate', 'display_name': 'Microsoft Edge Update', 'status': 'stopped', 'start_type': 'auto'}]
        issues, normal = evaluate_device_issues(
            'servers', ['services'], {'services': services}, reachable=True, status='success', server_type='windows',
        )

        self.assertEqual([(issue['analysis_item'], issue['severity']) for issue in issues], [('services', 'info')])
        self.assertNotIn('services', normal)

    def test_explicitly_failed_windows_service_remains_an_abnormal_service(self):
        issues, normal = evaluate_device_issues(
            'servers', ['services'], {'services': [{'name': 'fixture', 'status': 'failed'}]},
            reachable=True, status='success', server_type='windows',
        )

        self.assertEqual([(issue['analysis_item'], issue['severity']) for issue in issues], [('services', 'warning')])
        self.assertNotIn('services', normal)

    def test_stopped_automatic_windows_services_are_attention_items_not_failures(self):
        services = [{'Name': 'edgeupdate', 'DisplayName': 'Microsoft Edge Update', 'Status': 1, 'StartType': 2}]
        issues, normal = evaluate_device_issues(
            'servers', ['services'], {'services': services}, reachable=True, status='success', server_type='windows',
        )

        self.assertEqual([(issue['analysis_item'], issue['severity']) for issue in issues], [('services', 'info')])
        self.assertIn('触发器', issues[0]['详细问题'])
        self.assertNotIn('services', normal)
