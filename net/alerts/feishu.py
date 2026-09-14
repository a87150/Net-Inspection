"""Feishu custom-bot delivery adapter."""

import base64
import hashlib
import hmac
import time

import requests

from .base import HTTP_TIMEOUT, DeliveryResult, disabled_result, response_payload, response_summary, summarize


def _feishu_signature(timestamp, secret):
    string_to_sign = f'{timestamp}\n{secret}'.encode('utf-8')
    return base64.b64encode(
        hmac.new(string_to_sign, digestmod=hashlib.sha256).digest(),
    ).decode('ascii')


def _http_failure(response, *secrets):
    status_code = getattr(response, 'status_code', 200)
    try:
        status_code = int(status_code)
    except (TypeError, ValueError):
        status_code = 200
    return DeliveryResult(
        False,
        response_summary(response, *secrets),
        status_code == 408 or status_code == 429 or status_code >= 500,
    )


def send_feishu(channel, message, *, http_post=None, now=None):
    """Send a signed Feishu text message through an injected or default client."""
    if not getattr(channel, 'is_enabled', False):
        return disabled_result()

    settings = channel.settings
    webhook_url = settings.get('webhook_url', '')
    secret = settings.get('secret', '')
    timestamp = str(int((now or time.time)()))
    payload = {
        'timestamp': timestamp,
        'msg_type': 'text',
        'content': {'text': message.text},
    }
    if secret:
        payload['sign'] = _feishu_signature(timestamp, secret)

    try:
        response = (http_post or requests.post)(
            webhook_url,
            json=payload,
            timeout=HTTP_TIMEOUT,
        )
    except (requests.Timeout, TimeoutError) as exc:
        return DeliveryResult(False, summarize(f'Feishu timeout: {exc}', webhook_url, secret), True)
    except requests.RequestException as exc:
        return DeliveryResult(False, summarize(f'Feishu request failed: {exc}', webhook_url, secret), True)

    if not 200 <= getattr(response, 'status_code', 200) < 300:
        return _http_failure(response, webhook_url, secret)
    payload_result = response_payload(response)
    if not isinstance(payload_result, dict) or payload_result.get('code') != 0:
        return DeliveryResult(False, response_summary(response, webhook_url, secret), False)
    return DeliveryResult(True, response_summary(response, webhook_url, secret), False)
