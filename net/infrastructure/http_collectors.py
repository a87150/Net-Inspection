import xml.etree.ElementTree as ET
from ipaddress import ip_address
from time import monotonic
from urllib.parse import unquote_plus, urlsplit, urlunsplit

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from net.devices.security.payload import normalize_security_payload, collect_native_configuration
from net.infrastructure.collection import CollectionResult, Timer
from net.infrastructure.sanitization import sanitize
from net.inspections.selection import SECURITY_FIELDS, WINDOWS_FIELDS, selected_fields


def _xml_fields(node):
    """Retain parent paths and repeated elements so logs cannot impersonate status."""
    if len(node) == 0:
        return node.text
    grouped = {}
    for child in node:
        grouped.setdefault(child.tag.split('}')[-1], []).append(_xml_fields(child))
    return {key: values[0] if len(values) == 1 else values for key, values in grouped.items()}


def _response_data(response, *, structured_xml=False):
    content_type = response.headers.get('content-type', '').lower()
    if 'json' in content_type:
        return response.json()
    text = response.text.strip()
    try:
        return response.json()
    except ValueError:
        pass
    if text.startswith('<'):
        root = ET.fromstring(text)
        if structured_xml:
            return _xml_fields(root)
        return {node.tag.split('}')[-1]: node.text for node in root.iter() if len(node) == 0}
    return {'response': text}


def _request(url, token='', username='', password='', verify_ssl=True, timeout=12, digest=False, selected_items=None, structured_xml=False, error_body=False):
    headers = {'Accept': 'application/json, application/xml, text/xml'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    auth = None
    if username:
        auth_class = HTTPDigestAuth if digest else HTTPBasicAuth
        auth = auth_class(username, password or '')
    options = {} if selected_items is None else {'params': {'fields': ','.join(selected_items)}}
    if selected_items is not None:
        parts = urlsplit(url)
        query = '&'.join(part for part in parts.query.split('&')
                         if unquote_plus(part.partition('=')[0]).lower() != 'fields')
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
    response = requests.get(url, headers=headers, auth=auth, timeout=timeout, verify=verify_ssl, **options)
    try:
        response.raise_for_status()
    except requests.HTTPError:
        if error_body:
            try:
                detail = _response_data(response, structured_xml=structured_xml).get('error')
            except (ValueError, ET.ParseError, AttributeError):
                detail = None
            if isinstance(detail, str) and detail.strip():
                raise requests.HTTPError('Windows agent error: ' + detail.strip(), response=response) from None
        raise
    return _response_data(response, structured_xml=structured_xml)


def _valid_windows_network(value):
    """Validate the shipped Agent's interface array without normalizing evidence."""
    if not isinstance(value, list):
        return False
    for interface in value:
        if (not isinstance(interface, dict)
                or not isinstance(interface.get('interface'), str)
                or not interface['interface'].strip()):
            return False
        for field in ('ipv4', 'gateway', 'dns'):
            addresses = interface.get(field)
            if not isinstance(addresses, list):
                return False
            for address in addresses:
                # PowerShell @($null) emits [null] for an absent address/gateway.
                if address is None:
                    continue
                if not isinstance(address, str):
                    return False
                try:
                    parsed = ip_address(address)
                except ValueError:
                    return False
                if field != 'dns' and parsed.version != 4:
                    return False
    # [] explicitly reports no interfaces; missing/null never reaches this path.
    return True


def _windows_collection_errors(payload, selected_items, token):
    errors = payload.get('collection_errors') if isinstance(payload, dict) else None
    requested = WINDOWS_FIELDS if selected_items is None else set(selected_items)
    if not isinstance(errors, dict):
        return {}
    result = {}
    for item in requested:
        messages = errors.get(item)
        if isinstance(messages, str):
            messages = [messages]
        if isinstance(messages, list):
            safe = [sanitize(message, secrets=(token,)) for message in messages if isinstance(message, str) and message.strip()]
            if safe:
                result[item] = safe
    return result


def collect_windows_http(server, timeout=12, selected_items=None):
    timer = Timer()
    try:
        with timer:
            url = server.api_url or f'http://{server.ip}:9180/inspection'
            payload = _request(url, token=server.api_token or '', verify_ssl=server.verify_ssl, timeout=timeout, selected_items=selected_items, error_body=True)
            physical_disks = payload.get('physical_disks') if isinstance(payload, dict) else None
            field_errors = _windows_collection_errors(payload, selected_items, server.api_token or '')
            payload = selected_fields(payload, selected_items, WINDOWS_FIELDS)
            if field_errors:
                payload['collection_errors'] = field_errors
        field_errors = payload.pop('collection_errors', {})
        data = {}
        missing = []
        incomplete = []
        diagnostics = []
        for item in WINDOWS_FIELDS if selected_items is None else selected_items:
            value = next((payload[key] for key in WINDOWS_FIELDS.get(item, (item,)) if key in payload), None)
            if item in ('services', 'logs', 'storage_status'):
                valid = isinstance(value, list) and all(isinstance(row, (dict, str)) and bool(row) for row in value)
            elif item == 'computer_name':
                valid = isinstance(value, str) and bool(value.strip())
            elif item in ('cpu', 'memory'):
                number = value.get('usage_percent' if item == 'cpu' else 'used_percent') if isinstance(value, dict) else None
                valid = isinstance(number, (int, float)) and not isinstance(number, bool) and 0 <= number <= 100
            elif item == 'network_info':
                valid = _valid_windows_network(value)
            else:
                valid = isinstance(value, dict) and bool(value) and any(v is not None for v in value.values())
            # Only services can retain a non-empty partial array: other field
            # errors make even a well-shaped value incomplete evidence.  An
            # empty service array with an error cannot prove no rows were omitted.
            if item in field_errors and (item != 'services' or value == []):
                valid = False
            if valid:
                data[item] = value
            else:
                missing.append(item)
            if item in field_errors:
                diagnostics.append(item + '（' + '；'.join(field_errors[item]) + '）')
                if valid:
                    incomplete.append(item)
        if 'storage_status' in data and isinstance(physical_disks, list):
            data['physical_disks'] = physical_disks
            payload['physical_disks'] = physical_disks
        status = 'partial' if (missing or incomplete) and data else 'failed' if missing else 'success'
        message = '缺少有效采集证据：' + ', '.join(missing) if missing else ''
        if incomplete:
            message += ('；' if message else '') + '已取得部分证据，采集受限：' + ', '.join(incomplete)
        if diagnostics:
            message += ('；' if message else '') + '采集错误：' + '；'.join(diagnostics)
        if field_errors:
            payload['collection_errors'] = field_errors
        return CollectionResult(True, status, message,
                                data=data, raw=payload, duration_ms=timer.duration_ms)
    except (requests.RequestException, ValueError, ET.ParseError) as exc:
        return CollectionResult(False, 'failed', 'Windows HTTP 采集失败：' + sanitize(str(exc), secrets=(server.api_token or '',)), duration_ms=getattr(timer, 'duration_ms', 0))


def collect_security_api(device, timeout=12, selected_items=None):
    if selected_items is not None and 'config_info' in selected_items:
        # Independent item results: failure of a status read must not discard a
        # successful native configuration section (or vice versa).
        others = [item for item in selected_items if item != 'config_info']
        deadline = monotonic() + max(0, float(timeout))
        result = collect_security_api(device, timeout / 2, others) if others else CollectionResult(True, 'success')
        remaining = deadline - monotonic()
        config = (collect_native_configuration(device, remaining) if remaining > 0 else
                  {'status': 'failed', 'message': 'API 采集时限已到，未读取原生配置。'})
        result.reachable = result.reachable or config['status'] == 'success'
        result.data['config_info'] = config
        result.raw['config_info'] = config
        successes = (1 if config['status'] == 'success' else 0) + len([key for key in result.data if key != 'config_info'])
        result.status = 'success' if config['status'] == 'success' and result.status == 'success' else ('partial' if successes else 'failed')
        result.message = '' if result.status == 'success' else '部分巡检项未成功；请查看各项证据。'
        return result
    vendor = (device.vendor or '').lower()
    timer = Timer()
    try:
        with timer:
            payload = _request(
                device.api_url,
                token=device.api_token or '',
                username=device.api_username or '',
                password=device.api_password or '',
                verify_ssl=device.verify_ssl,
                timeout=timeout,
                digest=any(name in vendor for name in ('hikvision', '海康', 'dahua', '大华')),
                selected_items=selected_items,
                structured_xml=True,
            )
            data, raw = normalize_security_payload(payload)
            data = selected_fields(data, selected_items)
            raw = selected_fields(raw, selected_items, SECURITY_FIELDS)
        missing = [item for item in (selected_items or []) if item not in data]
        status, message = 'success', ''
        if missing or (selected_items is None and not data):
            status = 'partial' if data else 'failed'
            message = '安防设备 API 无法映射请求的巡检项：' + ', '.join(missing or ['status_data'])
        return CollectionResult(True, status, message, data=data, raw=raw, duration_ms=timer.duration_ms)
    except (requests.RequestException, ValueError, ET.ParseError) as exc:
        return CollectionResult(False, 'failed', f'安防设备 API 采集失败：{exc}', duration_ms=getattr(timer, 'duration_ms', 0))
