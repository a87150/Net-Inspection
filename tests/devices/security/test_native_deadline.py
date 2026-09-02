"""Real aiohttp/Digest/framing on an in-memory wire: no sockets or real waits."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp.client_proto import ResponseHandler
from django.test import SimpleTestCase

from tests.devices.network.test_configuration_export import DAHUA_TEXT, VirtualLoop
from net.devices.security.payload import collect_native_configuration


CHALLENGE = (b'HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n'
             b'WWW-Authenticate: Digest realm="camera", nonce="challenge", qop="auth"\r\n\r\n')
SUCCESS = (b'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: '
           + str(len(DAHUA_TEXT.encode())).encode() + b'\r\n\r\n' + DAHUA_TEXT.encode())


class Wire(asyncio.Transport):
    def __init__(self, protocol, events):
        self.protocol, self.events = protocol, events
        self.closed = False
        self.handles, self.requests = [], []

    def write(self, data):
        self.requests.append(bytes(data))
        for delay, packet in self.events:
            callback = self.close if packet is None else lambda packet=packet: self.protocol.data_received(packet)
            self.handles.append(asyncio.get_running_loop().call_later(delay, callback))

    def is_closing(self):
        return self.closed

    def close(self):
        if not self.closed:
            self.closed = True
            for handle in self.handles:
                handle.cancel()
            self.protocol.connection_lost(None)

    abort = close

    def get_extra_info(self, name, default=None):
        return default


class NativeDeadlineTests(SimpleTestCase):
    def setUp(self):
        self.device = SimpleNamespace(vendor='Dahua', api_url='http://192.0.2.20/status',
                                      api_username='digest-user', api_password='digest-secret', verify_ssl=True)
        self.loop = VirtualLoop()
        self.wires, self.connects = [], []
        self.enterContext(patch('asyncio.SelectorEventLoop', return_value=self.loop))
        self.enterContext(patch('threading.Thread.start', side_effect=AssertionError('background thread forbidden')))
        self.enterContext(patch('requests.get', side_effect=AssertionError('blocking HTTP forbidden')))
        self.enterContext(patch('aiohappyeyeballs.start_connection', side_effect=AssertionError('network forbidden')))

    def wire_connections(self, plans):
        plans = iter(plans)

        async def connect(connector, request, traces, timeout):
            delay, events = next(plans)
            self.connects.append(self.loop.time())
            await asyncio.sleep(delay)
            protocol = ResponseHandler(loop=self.loop)
            wire = Wire(protocol, events)
            protocol.connection_made(wire)
            self.wires.append(wire)
            return protocol

        self.enterContext(patch('aiohttp.TCPConnector._create_connection', connect))

    def capture(self, budget=1):
        item = collect_native_configuration(self.device, budget)
        self.assertTrue(self.loop.is_closed())
        self.assertFalse(asyncio.all_tasks(self.loop))
        self.assertTrue(all(wire.closed for wire in self.wires))
        self.assertNotIn('digest-secret', str(item))
        return item

    def assert_timeout(self, budget=1):
        item = self.capture(budget)
        self.assertEqual(item['status'], 'failed')
        self.assertNotIn('content', item)
        self.assertEqual(self.loop.now, float(budget))

    def test_real_digest_challenge_and_native_body_complete_within_one_budget(self):
        self.wire_connections([(0, [(.2, CHALLENGE)]), (0, [(.2, SUCCESS)])])
        item = self.capture()
        self.assertEqual(item['status'], 'success')
        self.assertEqual(item['content'], DAHUA_TEXT)
        request = b''.join(self.wires[-1].requests)
        self.assertIn(b'Authorization: Digest username="digest-user"', request)
        self.assertIn(b'GET /cgi-bin/configManager.cgi?action=getConfig&name=Network ', request)
        self.assertNotIn(b'digest-secret', request)

    def test_connect_is_cancelled_and_joined_at_total_deadline(self):
        self.wire_connections([(180, [(0, SUCCESS)])])
        self.assert_timeout()

    def test_digest_retry_connection_does_not_restart_total_budget(self):
        self.wire_connections([(.3, [(.3, CHALLENGE)]), (.6, [(0, SUCCESS)])])
        self.assert_timeout()
        self.assertEqual(len(self.connects), 2)

    def test_digest_retry_headers_do_not_restart_total_budget(self):
        self.wire_connections([(0, [(.6, CHALLENGE)]), (0, [(.6, SUCCESS)])])
        self.assert_timeout()
        self.assertEqual(len(self.wires), 2)

    def test_real_stream_slow_progress_is_cancelled(self):
        header, _, body = SUCCESS.partition(b'\r\n\r\n')
        events = [(0, header + b'\r\n\r\n')]
        events += [((index + 1) * .25, bytes([value])) for index, value in enumerate(body)]
        self.wire_connections([(0, events)])
        self.assert_timeout()

    def test_real_stream_late_eof_is_cancelled(self):
        # EOF-delimited HTTP body: valid bytes alone must not imply completion.
        packet = b'HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\n\r\n' + DAHUA_TEXT.encode()
        self.wire_connections([(0, [(.5, packet), (180, None)])])
        self.assert_timeout()

    def test_long_budget_is_not_rounded_up_by_transport(self):
        self.loop.now = .25
        self.wire_connections([(180, [(0, SUCCESS)])])
        self.assertEqual(self.capture(6)['status'], 'failed')
        self.assertEqual(self.loop.now, 6.25)

    def test_dns_io_cancels_in_caller_task_without_executor_or_abandoned_work(self):
        self.device.api_url = 'http://camera.example.invalid/status'
        cancelled = []

        async def resolve_name(resolver, host, **kwargs):
            try:
                await asyncio.sleep(180)
            finally:
                cancelled.append(host)

        with patch('dns.asyncresolver.Resolver.resolve_name', resolve_name):
            self.assert_timeout()
        self.assertEqual(cancelled, ['camera.example.invalid'])
