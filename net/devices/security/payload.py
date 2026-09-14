"""Explicit native status mapping; unknown payloads are never a status fallback."""

from net.inspections.selection import SECURITY_FIELDS, selected_fields


# Scalar leaves and known native container paths only. In particular, never
# search arbitrary descendants: a log/channel may contain the same leaf names.
_STATUS_SCHEMA = {
    'currentDeviceTime': None, 'deviceUpTime': None, 'cpuUtilization': None,
    'online': None, 'state': None, 'health': None,
    'CPUList': {'CPU': {'cpuUtilization': None}},
}


def _status_fragment(value, schema):
    if schema is None:
        if isinstance(value, str):
            return value if value.strip() else None
        return value if isinstance(value, (int, float, bool)) else None
    if isinstance(value, list):
        return [fragment for child in value
                if (fragment := _status_fragment(child, schema)) is not None] or None
    if not isinstance(value, dict):
        return None
    result = {}
    for key, child_schema in schema.items():
        fragment = _status_fragment(value.get(key), child_schema)
        if fragment is not None:
            result[key] = fragment
    return result or None


def normalize_security_payload(payload):
    """Return canonical items plus selected-able raw fragments, not the full body.

    Status raw uses its canonical item key but retains native field names and
    hierarchy. Non-status items retain their existing raw aliases.
    """
    if not isinstance(payload, dict):
        return {}, {}
    if isinstance(payload.get('DeviceStatus'), dict):
        payload = {**payload, **payload['DeviceStatus']}
    data, raw = {}, {}
    for item in ('device_info', 'channel_status', 'storage_status'):
        fragments = selected_fields(payload, [item], SECURITY_FIELDS)
        fragments = {key: value for key, value in fragments.items() if value is not None}
        if not fragments:
            continue
        data[item] = next((fragments[key] for key in SECURITY_FIELDS[item][:2] if key in fragments), fragments)
        if item == 'storage_status' and 'disks' in fragments and not any(
            key in fragments for key in ('storage_status', 'storage')
        ):
            data[item] = fragments['disks']
        raw.update(fragments)

    status = _status_fragment(payload, _STATUS_SCHEMA)
    for wrapper in ('status_data', 'status'):
        value = payload.get(wrapper)
        candidate = _status_fragment(value, _STATUS_SCHEMA if isinstance(value, (dict, list)) else None)
        if candidate is not None:
            status = candidate
            break
    if status is not None:
        data['status_data'] = status
        raw['status_data'] = status
    return data, raw


def collect_native_configuration(device, timeout):
    """Read only Dahua's documented Network section; never export status as config.

    The configured API origin supplies scheme/host/port only. Discard its path,
    query, userinfo, fragment; no redirect or arbitrary name/action is allowed.
    HTTP framing, streaming EOF, a byte limit and a wall-clock deadline are all
    required. The returned body is native text, not a synthesized configuration.
    """
    import asyncio
    from urllib.parse import urlsplit, urlunsplit
    import aiohttp
    from net.devices.security.configuration import security_vendor
    from net.infrastructure.native_http import read_dahua_network

    if not security_vendor(device.vendor):
        return {'status': 'unsupported', 'message': '此厂商未注册可读原生配置接口；二进制备份不支持。'}
    try:
        parts = urlsplit(device.api_url)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            raise ValueError('invalid API origin')
        url = urlunsplit((parts.scheme, parts.netloc, '/cgi-bin/configManager.cgi', '', ''))
        # Workers call this synchronous entry point. A private, joined event
        # loop owns every transport task; no background runner/executor thread.
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(read_dahua_network(device, url, max(1, min(timeout, 120))))
    except (aiohttp.ClientError, OSError, ValueError, UnicodeError):
        return {'status': 'failed', 'message': 'Dahua Network 配置读取失败或响应不完整。'}
