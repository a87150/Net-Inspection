"""Shared alert-delivery values and secret-safe response handling."""

from dataclasses import dataclass
import json
from typing import Mapping

from net.infrastructure.sanitization import REDACTED, sanitize


HTTP_TIMEOUT = (3.05, 10)
SMTP_TIMEOUT = 10
MAX_RESPONSE_SUMMARY_LENGTH = 500


@dataclass(frozen=True)
class DeliveryResult:
    success: bool
    response_summary: str
    retryable: bool


@dataclass(frozen=True)
class AlertMessage:
    title: str
    text: str
    facts: Mapping[str, str]
    detail_url: str


def redact_secret(value, *secrets):
    """Return a display-safe value without retaining endpoint credentials."""
    if isinstance(value, str):
        for secret in sorted((item for item in secrets if isinstance(item, str) and item), key=len, reverse=True):
            value = value.replace(secret, REDACTED)
    return sanitize(value, secrets=secrets)


def summarize(value, *secrets):
    """Produce a bounded result suitable for a future delivery record."""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            text = str(value)
    safe_text = str(redact_secret(text, *secrets)).replace('\r', ' ').replace('\n', ' ')
    return safe_text[:MAX_RESPONSE_SUMMARY_LENGTH]


def response_payload(response):
    """Return JSON when the response exposes it, otherwise its text."""
    try:
        return response.json()
    except (AttributeError, TypeError, ValueError):
        return getattr(response, 'text', '')


def response_summary(response, *secrets):
    return summarize(response_payload(response), *secrets)


def disabled_result():
    return DeliveryResult(False, 'Alert channel is disabled.', False)
