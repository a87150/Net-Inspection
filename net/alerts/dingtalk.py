"""DingTalk custom-robot delivery adapter."""

import base64
import hashlib
import hmac
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from .base import HTTP_TIMEOUT, DeliveryResult, disabled_result, response_payload, response_summary, summarize


def _dingtalk_signature(timestamp, secret):
    string_to_sign = f'{timestamp}\n{secret}'.encode('utf-8')
    return base64.b64encode(
        hmac.new(secret.encode('utf-8'), string_to_sign, digestmod=hashlib.sha256).digest(),
    ).decode('ascii')


def _signed_webhook_url(webhook_url, timestamp, secret):
    if not secret:
        return webhook_url
    parts = urlsplit(webhook_url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.extend((('timestamp', timestamp), ('sign', _dingtalk_signature(timestamp, secret))))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def send_dingtalk(channel, message, *, http_post=None, now=None):
    """Send a signed DingTalk text message through an injected or default client."""
    if not getattr(channel, 'is_enabled', False):
        return disabled_result()

    settings = channel.settings
    webhook_url = settings.get('webhook_url', '')
    secret = settings.get('secret', '')
    timestamp = str(int(round((now or time.time)() * 1000)))
    request_url = _signed_webhook_url(webhook_url, timestamp, secret)
    payload = {'msgtype': 'text', 'text': {'content': message.text}}

    try:
        response = (http_post or requests.post)(request_url, json=payload, timeout=HTTP_TIMEOUT)
    except (requests.Timeout, TimeoutError) as exc:
        return DeliveryResult(False, summarize(f'DingTalk timeout: {exc}', webhook_url, request_url, secret), True)
    except requests.RequestException as exc:
        return DeliveryResult(False, summarize(f'DingTalk request failed: {exc}', webhook_url, request_url, secret), True)

    status_code = getattr(response, 'status_code', 200)
    if not 200 <= status_code < 300:
        return DeliveryResult(
            False,
            response_summary(response, webhook_url, request_url, secret),
            status_code == 408 or status_code == 429 or status_code >= 500,
        )
    payload_result = response_payload(response)
    if not isinstance(payload_result, dict) or payload_result.get('errcode') != 0:
        return DeliveryResult(False, response_summary(response, webhook_url, request_url, secret), False)
    return DeliveryResult(True, response_summary(response, webhook_url, request_url, secret), False)
