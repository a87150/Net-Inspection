"""Contracts for the Phase 3 alert-message senders.

Every transport boundary is injected: these tests must never contact an HTTP
endpoint or an SMTP server.
"""

import base64
import hashlib
import hmac
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, quote_plus, urlsplit

import requests
from django.test import SimpleTestCase

from net.alerts import AlertMessage, build_alert_message, send_alert
from net.alerts.dingtalk import send_dingtalk
from net.alerts.email import send_email
from net.alerts.feishu import send_feishu


class FakeResponse:
    def __init__(self, *, status_code=200, payload=None, text=''):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class AlertSenderTests(SimpleTestCase):
    WEBHOOK_SECRET = 'webhook-signing-secret'
    SMTP_PASSWORD = 'smtp-password-secret'

    def message(self):
        return AlertMessage(
            title='Abnormal target alert',
            text='Target server-1 has a critical connectivity finding.',
            facts={'target': 'server/server-1'},
            detail_url='/alerts/event-1/',
        )

    def channel(self, channel_type, settings, *, enabled=True):
        return SimpleNamespace(
            channel_type=channel_type,
            settings=settings,
            is_enabled=enabled,
            name=f'{channel_type}-channel',
        )

    def test_feishu_posts_signed_text_with_explicit_connect_and_read_timeouts(self):
        channel = self.channel('feishu', {
            'webhook_url': 'https://open.feishu.test/open-apis/bot/v2/hook/private-hook',
            'secret': self.WEBHOOK_SECRET,
        })
        post = Mock(return_value=FakeResponse(payload={'code': 0, 'msg': 'success'}))
        timestamp = 1_700_000_000

        result = send_feishu(channel, self.message(), http_post=post, now=lambda: timestamp)

        expected_sign = base64.b64encode(hmac.new(
            f'{timestamp}\n{self.WEBHOOK_SECRET}'.encode('utf-8'),
            digestmod=hashlib.sha256,
        ).digest()).decode('ascii')
        self.assertTrue(result.success)
        self.assertFalse(result.retryable)
        self.assertEqual(post.call_args.args[0], channel.settings['webhook_url'])
        self.assertEqual(post.call_args.kwargs['json'], {
            'timestamp': str(timestamp),
            'sign': expected_sign,
            'msg_type': 'text',
            'content': {'text': self.message().text},
        })
        timeout = post.call_args.kwargs['timeout']
        self.assertEqual(len(timeout), 2)
        self.assertTrue(all(value > 0 for value in timeout))

    def test_dingtalk_signs_and_url_encodes_timestamp_query_and_parses_success(self):
        channel = self.channel('dingtalk', {
            'webhook_url': 'https://oapi.dingtalk.test/robot/send?access_token=private-token',
            'secret': self.WEBHOOK_SECRET,
        })
        post = Mock(return_value=FakeResponse(payload={'errcode': 0, 'errmsg': 'ok'}))
        timestamp_ms = 1_700_000_000_123

        result = send_dingtalk(channel, self.message(), http_post=post, now=lambda: timestamp_ms / 1000)

        expected_sign = base64.b64encode(hmac.new(
            self.WEBHOOK_SECRET.encode('utf-8'),
            f'{timestamp_ms}\n{self.WEBHOOK_SECRET}'.encode('utf-8'),
            digestmod=hashlib.sha256,
        ).digest()).decode('ascii')
        request_url = post.call_args.args[0]
        query = parse_qs(urlsplit(request_url).query)
        self.assertTrue(result.success)
        self.assertEqual(query['timestamp'], [str(timestamp_ms)])
        self.assertEqual(query['sign'], [expected_sign])
        self.assertIn(f'sign={quote_plus(expected_sign)}', request_url)
        self.assertEqual(post.call_args.kwargs['json'], {
            'msgtype': 'text',
            'text': {'content': self.message().text},
        })

    def test_disabled_channel_never_calls_an_injected_transport(self):
        channel = self.channel('feishu', {
            'webhook_url': 'https://open.feishu.test/open-apis/bot/v2/hook/private-hook',
            'secret': self.WEBHOOK_SECRET,
        }, enabled=False)
        post = Mock()

        result = send_alert(channel, self.message(), http_post=post)

        self.assertFalse(result.success)
        self.assertFalse(result.retryable)
        self.assertIn('disabled', result.response_summary.lower())
        post.assert_not_called()

    def test_webhook_failure_summary_is_bounded_redacted_and_timeout_is_retryable(self):
        channel = self.channel('feishu', {
            'webhook_url': 'https://open.feishu.test/open-apis/bot/v2/hook/private-hook',
            'secret': self.WEBHOOK_SECRET,
        })
        long_secret_bearing_error = (
            f'https://example.test/hook?access_token=private-token&secret={self.WEBHOOK_SECRET} '
            + 'x' * 1_000
        )
        post = Mock(return_value=FakeResponse(payload={
            'code': 19_001,
            'msg': long_secret_bearing_error,
        }))

        failed = send_feishu(channel, self.message(), http_post=post, now=lambda: 1)
        timed_out = send_feishu(
            channel,
            self.message(),
            http_post=Mock(side_effect=requests.Timeout('connect timed out')),
            now=lambda: 1,
        )

        self.assertFalse(failed.success)
        self.assertFalse(failed.retryable)
        self.assertLessEqual(len(failed.response_summary), 500)
        self.assertNotIn(self.WEBHOOK_SECRET, failed.response_summary)
        self.assertNotIn('private-token', failed.response_summary)
        self.assertFalse(timed_out.success)
        self.assertTrue(timed_out.retryable)

    def test_feishu_http_success_without_provider_success_code_is_not_delivered(self):
        channel = self.channel('feishu', {
            'webhook_url': 'https://open.feishu.test/open-apis/bot/v2/hook/private-hook',
        })

        result = send_feishu(
            channel,
            self.message(),
            http_post=Mock(return_value=FakeResponse(status_code=200, payload={'msg': 'missing code'})),
            now=lambda: 1,
        )

        self.assertFalse(result.success)
        self.assertFalse(result.retryable)

    def test_dingtalk_http_success_without_provider_success_code_is_not_delivered(self):
        channel = self.channel('dingtalk', {
            'webhook_url': 'https://oapi.dingtalk.test/robot/send?access_token=private-token',
        })

        result = send_dingtalk(
            channel,
            self.message(),
            http_post=Mock(return_value=FakeResponse(status_code=200, payload={'errmsg': 'missing errcode'})),
            now=lambda: 1,
        )

        self.assertFalse(result.success)
        self.assertFalse(result.retryable)

    def test_email_uses_starttls_and_delivers_to_every_configured_recipient(self):
        channel = self.channel('email', {
            'smtp_host': 'smtp.example.test',
            'smtp_port': 587,
            'use_tls': True,
            'use_ssl': False,
            'username': 'alerts@example.test',
            'password': self.SMTP_PASSWORD,
            'from_email': 'alerts@example.test',
            'recipients': ['ops@example.test', 'security@example.test'],
        })
        smtp = Mock()
        smtp.send_message.return_value = {}
        smtp_factory = Mock(return_value=smtp)

        result = send_email(channel, self.message(), smtp_factory=smtp_factory)

        self.assertTrue(result.success)
        self.assertFalse(result.retryable)
        self.assertEqual(smtp_factory.call_args.args, ('smtp.example.test', 587))
        self.assertGreater(smtp_factory.call_args.kwargs['timeout'], 0)
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with('alerts@example.test', self.SMTP_PASSWORD)
        smtp.send_message.assert_called_once()
        sent_message = smtp.send_message.call_args.args[0]
        self.assertEqual(smtp.send_message.call_args.kwargs['to_addrs'], channel.settings['recipients'])
        self.assertEqual(sent_message['To'], 'ops@example.test, security@example.test')
        self.assertIn(self.message().detail_url, sent_message.get_content())

    def test_email_uses_ssl_without_starttls(self):
        channel = self.channel('email', {
            'smtp_host': 'smtp.example.test',
            'smtp_port': 465,
            'use_tls': False,
            'use_ssl': True,
            'from_email': 'alerts@example.test',
            'recipients': ['ops@example.test'],
        })
        smtp = Mock()
        smtp.send_message.return_value = {}
        smtp_factory = Mock(return_value=smtp)

        result = send_email(channel, self.message(), smtp_factory=smtp_factory)

        self.assertTrue(result.success)
        self.assertIn('context', smtp_factory.call_args.kwargs)
        smtp.starttls.assert_not_called()

    def test_email_rejects_tls_and_ssl_together_without_opening_a_connection(self):
        channel = self.channel('email', {
            'smtp_host': 'smtp.example.test',
            'smtp_port': 465,
            'use_tls': True,
            'use_ssl': True,
            'from_email': 'alerts@example.test',
            'recipients': ['ops@example.test'],
        })
        smtp_factory = Mock()

        result = send_email(channel, self.message(), smtp_factory=smtp_factory)

        self.assertFalse(result.success)
        self.assertFalse(result.retryable)
        smtp_factory.assert_not_called()

    def test_smtp_authentication_failure_is_permanent_and_password_safe(self):
        channel = self.channel('email', {
            'smtp_host': 'smtp.example.test',
            'smtp_port': 587,
            'use_tls': False,
            'use_ssl': False,
            'username': 'alerts@example.test',
            'password': self.SMTP_PASSWORD,
            'from_email': 'alerts@example.test',
            'recipients': ['ops@example.test'],
        })
        smtp = Mock()
        smtp.login.side_effect = __import__('smtplib').SMTPAuthenticationError(
            535, f'bad credentials: {self.SMTP_PASSWORD}',
        )

        result = send_email(channel, self.message(), smtp_factory=Mock(return_value=smtp))

        self.assertFalse(result.success)
        self.assertFalse(result.retryable)
        self.assertNotIn(self.SMTP_PASSWORD, result.response_summary)

    def test_smtp_timeout_is_retryable(self):
        channel = self.channel('email', {
            'smtp_host': 'smtp.example.test',
            'smtp_port': 587,
            'use_tls': False,
            'use_ssl': False,
            'from_email': 'alerts@example.test',
            'recipients': ['ops@example.test'],
        })

        result = send_email(
            channel,
            self.message(),
            smtp_factory=Mock(side_effect=TimeoutError('smtp timeout')),
        )

        self.assertFalse(result.success)
        self.assertTrue(result.retryable)

    def test_smtp_partial_recipient_refusal_is_not_reported_as_full_success(self):
        channel = self.channel('email', {
            'smtp_host': 'smtp.example.test',
            'smtp_port': 587,
            'use_tls': False,
            'use_ssl': False,
            'from_email': 'alerts@example.test',
            'recipients': ['ops@example.test', 'security@example.test'],
        })
        smtp = Mock()
        smtp.send_message.return_value = {'security@example.test': (550, b'mailbox unavailable')}

        result = send_email(channel, self.message(), smtp_factory=Mock(return_value=smtp))

        self.assertFalse(result.success)
        self.assertFalse(result.retryable)
        self.assertIn('security@example.test', result.response_summary)

    def test_normalized_message_includes_event_scope_runtime_findings_and_recovery_finding(self):
        event = SimpleNamespace(
            id='event-1',
            event_type='recovery',
            profile_type='inspection_profile',
            profile_id='project-1',
            target_type='server',
            target_id='server-1',
            occurred_at='2026-08-31T12:00:00+08:00',
            findings=[{
                'key': 'connectivity.unreachable',
                'severity': 'critical',
                'title': 'Target unreachable',
                'detail': 'The target did not answer health checks.',
            }],
            summary='The target is reachable again.',
        )

        message = build_alert_message(event)

        self.assertIn('recovery', message.title.lower())
        self.assertIn('Target unreachable', message.title)
        self.assertEqual(message.detail_url, '/alerts/event-1/')
        self.assertEqual(message.facts['project'], 'inspection_profile/project-1')
        self.assertEqual(message.facts['target'], 'server/server-1')
        for expected in ('recovery', 'project-1', 'server-1', '2026-08-31', 'Target unreachable'):
            self.assertIn(expected, message.text)

    def test_message_boundary_redacts_all_event_display_values_before_delivery(self):
        event = SimpleNamespace(
            id='event-1?secret=detail-path-value',
            event_type='abnormal',
            profile_type='inspection_profile password=project-type-value',
            profile_id='project-1 token=project-id-value',
            target_type={'name': 'server', 'facts': [{'password': 'nested label value'}]},
            target_id='https://target.test/status?access_token=target-url-value&ok=1',
            occurred_at='2026-08-31T12:00:00+08:00 secret=runtime-value',
            findings=[{
                'key': 'connectivity.unreachable',
                'severity': 'critical secret=severity-value',
                'title': 'Target unreachable secret=finding-title-value',
                'detail': {
                    'description': 'Health check failed password=finding-detail-value',
                    'facts': [{'password': 'nested fact value',
                               'note': 'Authorization: Bearer nested-bearer-value'}],
                    'url': 'https://status.test/?access_token=finding-url-value&ok=1',
                },
            }],
            summary='Connection failed password="summary value with spaces"',
        )
        forbidden = (
            'detail-path-value', 'project-type-value', 'project-id-value',
            'nested label value', 'target-url-value', 'runtime-value',
            'severity-value', 'finding-title-value', 'finding-detail-value',
            'nested fact value', 'nested-bearer-value', 'finding-url-value',
            'summary value with spaces', 'event-type-value',
        )
        channel = self.channel('feishu', {'webhook_url': 'https://example.test/fake-hook'})
        for event_type in ('abnormal', 'recovery', 'abnormal secret=event-type-value'):
            with self.subTest(event_type=event_type):
                event.event_type = event_type
                original = deepcopy(vars(event))
                message = build_alert_message(event)
                post = Mock(return_value=FakeResponse(payload={'code': 0}))
                result = send_alert(channel, message, http_post=post, now=lambda: 1)
                self.assertTrue(result.success)
                surfaces = (message.title, message.text, json.dumps(message.facts),
                            message.detail_url, post.call_args.kwargs['json']['content']['text'])
                for surface in surfaces:
                    for secret in forbidden:
                        self.assertNotIn(secret, surface)
                self.assertIn('Target unreachable', message.title)
                self.assertIn('Health check failed', message.text)
                self.assertNotIn('ok=1', message.facts['target'])
                self.assertIn('[REDACTED]', message.text)
                self.assertEqual(vars(event), original)
