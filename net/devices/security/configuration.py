"""Dahua HTTP API v1.40 §5.2.1: named Network configuration only.

Native CGI key/value text is retained verbatim. Structured snapshots may retain
the same native table.Network keys as JSON; device/status payloads are rejected.
Opaque/encrypted backup formats and unregistered vendor/scope pairs are refused.
"""
import json
import re

from net.data_exchange.adapters import MAX_CONFIG_BYTES, UnsupportedConfiguration, readable_text


def security_vendor(value):
    value = str(value or '').strip().casefold()
    return 'dahua' if re.search(r'(?<![a-z0-9])dahua(?![a-z0-9])|大华', value) else ''


def validate_network(content, fmt):
    if fmt == 'text':
        if not readable_text(content):
            raise ValueError('unreadable configuration')
        lines = [line for line in content.splitlines() if line.strip()]
        if not lines or any(not re.fullmatch(r'table\.Network\.[\w.\[\]-]+=[^\r\n]*', line) for line in lines):
            raise ValueError('not native Network configuration')
    elif fmt == 'json':
        if (not isinstance(content, dict) or not content
                or any(not re.fullmatch(r'table\.Network\.[\w.\[\]-]+', key)
                       or not isinstance(value, (str, int, float, bool)) for key, value in content.items())
                or len(json.dumps(content, allow_nan=False).encode()) > MAX_CONFIG_BYTES):
            raise ValueError('not native Network configuration')
    else:
        raise UnsupportedConfiguration('binary/unknown configuration')
    return content


def adapt(item):
    if security_vendor(item.get('vendor')) != 'dahua' or item.get('scope') != 'Network':
        raise UnsupportedConfiguration('unregistered vendor configuration section')
    if item.get('complete') is not True:
        raise ValueError('incomplete named section')
    fmt = item.get('format')
    content = validate_network(item.get('content'), fmt)
    return content, 'application/json' if fmt == 'json' else 'text/plain', 'json' if fmt == 'json' else 'txt', 'Network'
