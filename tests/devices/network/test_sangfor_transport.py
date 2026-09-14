"""Exercise the requests preparation/adapter boundary without a network connection."""
import io
import json
from hashlib import md5
from types import SimpleNamespace
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch

import requests
from django.test import SimpleTestCase
from net.devices.network.sangfor import collect_sangfor_ac


class FixtureAdapter(requests.adapters.BaseAdapter):
    def __init__(self, payloads):
        self.payloads = payloads
        self.requests = []

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        self.requests.append(request)
        endpoint = urlsplit(request.url).path.rsplit('/', 1)[-1]
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.request = request
        response.headers['Content-Type'] = 'application/json'
        response.raw = io.BytesIO(json.dumps(self.payloads[endpoint]).encode())
        return response

    def close(self):
        pass


class SangforTransportTests(SimpleTestCase):
    def test_post_nonce_is_decimal_and_signs_the_exact_string_sent(self):
        import uuid
        nonces = [uuid.UUID('abcdef12-3456-4789-9234-56789abcdef0'),
                  uuid.UUID('fedcba98-7654-4321-8765-43210fedcba9')]
        sent_nonces = []
        with patch('net.devices.network.sangfor.uuid.uuid4', side_effect=nonces):
            for nonce in nonces:
                result, sent = self.collect({
                    'throughput': {'code': 0, 'data': {'send': 12, 'recv': 24, 'unit': 'bytes'}},
                }, ['throughput'])
                self.assertEqual(result.status, 'success')
                body = json.loads(sent[0].body)
                self.assertIsInstance(body['random'], str)
                self.assertTrue(body['random'].isdecimal())
                self.assertEqual(body['random'], str(nonce.int))
                self.assertEqual(body['md5'], md5(('fixture-secret' + body['random']).encode()).hexdigest())
                sent_nonces.append(body['random'])
        self.assertNotEqual(*sent_nonces)

    def device(self):
        return SimpleNamespace(api_url='https://fixture.invalid:9999',
                               api_shared_secret='fixture-secret', verify_ssl=True)

    def collect(self, payloads, items):
        adapter = FixtureAdapter(payloads)
        with patch('requests.sessions.Session.get_adapter', return_value=adapter):
            result = collect_sangfor_ac(self.device(), selected_items=items)
        return result, adapter.requests

    def test_real_requests_get_and_post_prepare_documented_authentication(self):
        result, sent = self.collect({
            'cpu-usage': {'code': 0, 'data': 92},
            'throughput': {'code': 0, 'data': {'send': 1024, 'recv': 2048, 'unit': 'bytes'}},
        }, ['cpu', 'throughput'])
        self.assertEqual(result.status, 'success', result.message)
        self.assertEqual(len(sent), 2)
        query = parse_qs(urlsplit(sent[0].url).query)
        self.assertEqual(query['md5'][0], md5(('fixture-secret' + query['random'][0]).encode()).hexdigest())
        self.assertEqual(sent[1].method, 'POST')
        self.assertEqual(parse_qs(urlsplit(sent[1].url).query), {'_method': ['GET']})
        body = json.loads(sent[1].body)
        self.assertEqual(body['md5'], md5(('fixture-secret' + body['random']).encode()).hexdigest())
        self.assertNotEqual(body['random'], query['random'][0])
        self.assertEqual(result.data['throughput']['unit'], 'bytes')
        evidence = json.dumps({'raw': result.raw, 'message': result.message})
        self.assertNotIn('fixture-secret', evidence)
        self.assertNotIn(query['md5'][0], evidence)

    def test_null_counts_and_invalid_envelopes_do_not_claim_success(self):
        for payload in ({'code': 0, 'data': None}, {'code': False, 'data': 3},
                        {'code': 0}, {'code': 0, 'data': -2}, {'code': 0, 'data': True}):
            with self.subTest(payload=payload):
                result, _ = self.collect({'online-user': payload}, ['online_users'])
                self.assertEqual(result.status, 'failed')
                self.assertEqual(result.raw, {})

    def test_throughput_keeps_valid_units_and_rejects_unknown_units(self):
        result, _ = self.collect({'throughput': {'code': 0, 'data': {'send': 12, 'recv': 24, 'unit': 'bananas'}}}, ['throughput'])
        self.assertEqual(result.status, 'failed')


    def test_all_documented_status_items_are_parsed(self):
        values = {'version': 'AC 13.0', 'online-user': 3, 'session-num': 25,
                  'insidelib': [{'name': 'fixture', 'is_expired': False}],
                  'log': {'block': 2, 'record': 10}, 'cpu-usage': 12.5,
                  'mem-usage': 34, 'disk-usage': 65, 'sys-time': '2026-09-14 18:25:00',
                  'bandwidth-usage': 45, 'throughput': {'send': 8, 'recv': 16, 'unit': 'bits'}}
        from net.devices.network.sangfor import DEFAULT_ITEMS
        result, sent = self.collect({key: {'code': 0, 'data': value} for key,value in values.items()}, DEFAULT_ITEMS)
        self.assertEqual(result.status, 'success', result.message)
        self.assertEqual(set(result.data), set(DEFAULT_ITEMS))
        self.assertEqual(len(sent), 11)
        self.assertEqual(result.data['log_statistics'], {'block': 2, 'record': 10})

    def test_transport_errors_are_specific_and_do_not_leak_exception_text(self):
        response = requests.Response()
        response.status_code = 403
        for error, expected in ((requests.HTTPError('fixture-secret', response=response), 'HTTP 403'),
                                (requests.Timeout('fixture-secret'), '超时'),
                                (requests.exceptions.SSLError('fixture-secret'), 'TLS'),
                                (requests.ConnectionError('fixture-secret'), '连接失败')):
            with self.subTest(expected=expected):
                with patch('requests.request', side_effect=error):
                    result = collect_sangfor_ac(self.device(), selected_items=['cpu'])
                self.assertEqual(result.status, 'failed')
                self.assertIn(expected, result.message)
                self.assertNotIn('fixture-secret', result.message)

    def test_deadline_does_not_start_remaining_requests(self):
        from unittest.mock import Mock
        clock = Mock(side_effect=[0, 0, 20, 20, 20, 20])
        transport = Mock(return_value={'code': 0, 'data': 30})
        with patch('net.devices.network.sangfor.monotonic', clock):
            result = collect_sangfor_ac(self.device(), timeout=10, selected_items=['cpu','memory'], request=transport)
        self.assertEqual(result.status, 'partial')
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(result.data['cpu']['usage_percent'], 30)
        self.assertIn('超时', result.data['memory']['message'])


    def test_endpoint_credentials_are_rejected_before_snapshotting(self):
        from django.core.exceptions import ValidationError
        from net.models import Network_Device
        from net.inspections.queue import _target_snapshot
        for url in ('https://user:private@fixture.invalid:9999',
                    'https://fixture.invalid:9999?md5=private', 'ftp://fixture.invalid'):
            with self.subTest(url=url):
                device = Network_Device(ip='192.0.2.1', connection_type='sangfor_api',
                                        api_url=url, api_shared_secret='fixture-secret')
                with self.assertRaises(ValidationError):
                    _target_snapshot(device, ('ip','connection_type'))
