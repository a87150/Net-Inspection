"""Read-only Sangfor AC Open API collector."""
from hashlib import md5
from datetime import datetime
import math
from time import monotonic
from urllib.parse import urlencode, urljoin
import uuid

from net.infrastructure.collection import CollectionResult

def _number(value, *, integer=False, percentage=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('设备返回的指标不是数值')
    if not math.isfinite(value) or value < 0 or (percentage and value > 100) or (integer and int(value) != value):
        raise ValueError('设备返回的指标超出有效范围')
    return int(value) if integer else value


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('设备返回的文本信息为空')
    return value.strip()


def _libraries(value):
    if not isinstance(value, list) or any(not isinstance(row, dict) or not row.get('name') for row in value):
        raise ValueError('设备返回的内置库列表无效')
    return {'libraries': value}


def _log_counts(value):
    if not isinstance(value, dict):
        raise ValueError('设备返回的日志统计无效')
    return {key: _number(value.get(key), integer=True) for key in ('block', 'record')}


def _system_time(value):
    text = _text(value)
    datetime.strptime(text, '%Y-%m-%d %H:%M:%S')
    return {'value': text}


def _throughput(value):
    if not isinstance(value, dict) or value.get('unit') not in ('bytes', 'bits'):
        raise ValueError('设备返回的流速单位无效')
    return {'send': _number(value.get('send')), 'recv': _number(value.get('recv')), 'unit': value['unit']}


STATUS_ENDPOINTS = {
    'device_info': ('version', lambda value: {'version': _text(value)}),
    'online_users': ('online-user', lambda value: {'count': _number(value, integer=True)}),
    'sessions': ('session-num', lambda value: {'count': _number(value, integer=True)}),
    'inside_libraries': ('insidelib', _libraries),
    'log_statistics': ('log', _log_counts),
    'cpu': ('cpu-usage', lambda value: {'usage_percent': _number(value, percentage=True)}),
    'memory': ('mem-usage', lambda value: {'usage_percent': _number(value, percentage=True)}),
    'disk_usage': ('disk-usage', lambda value: {'usage_percent': _number(value, percentage=True)}),
    'system_time': ('sys-time', _system_time),
    'bandwidth_usage': ('bandwidth-usage', lambda value: {'usage_percent': _number(value, percentage=True)}),
    'throughput': ('throughput', _throughput),
}
UNSUPPORTED_ITEMS = frozenset({'temperature', 'interface_status', 'vlan_status', 'logs', 'config_info', 'traffic', 'routing_table', 'arp_table', 'mac_table', 'lldp_neighbors', 'wireless_aps', 'wireless_clients'})
DEFAULT_ITEMS = tuple(STATUS_ENDPOINTS)


def _url(base, endpoint, random_value=None, signature=None, *, method_override=False):
    root = (base or '').strip()
    if not root:
        raise ValueError('未配置深信服 AC Open API 地址')
    query = {} if method_override else {'random': random_value, 'md5': signature}
    if method_override:
        query['_method'] = 'GET'
    return urljoin(root.rstrip('/') + '/', 'v1/status/' + endpoint) + '?' + urlencode(query)


def _request_json(request, url, *, timeout, verify_ssl, method='GET', payload=None):
    response = request(method, url, timeout=timeout, verify=verify_ssl, headers={'Accept-Language': 'zh-CN', 'Content-Type': 'application/json'}, json=payload, allow_redirects=False)
    if isinstance(response, dict):
        return response
    try:
        response.raise_for_status()
        if 300 <= response.status_code < 400:
            raise ValueError('设备接口返回重定向，请检查 API 地址')
        return response.json()
    finally:
        response.close()


def collect_sangfor_ac(device, timeout=12, selected_items=None, *, request=None):
    """Collect only documented read-only /v1/status evidence."""
    if not getattr(device, 'api_shared_secret', ''):
        return CollectionResult(False, 'failed', '未配置深信服 AC Open API 共享密钥')
    if request is None:
        import requests
        request = requests.request
    selected = list(dict.fromkeys(DEFAULT_ITEMS if selected_items is None else selected_items))
    data, raw, messages, completed = {}, {}, [], set()
    started = monotonic()
    for item in selected:
        if item in UNSUPPORTED_ITEMS or item not in STATUS_ENDPOINTS:
            data[item] = {'status': 'unsupported', 'message': '提供的深信服 AC API 文档未包含此巡检项。'}
            continue
        endpoint, convert = STATUS_ENDPOINTS[item]
        nonce = uuid.uuid4()
        # Some AC firmware rejects hexadecimal nonces in POST JSON authentication.
        # Decimal UUID text preserves the entropy and signs exactly what is sent.
        random_value = str(nonce.int) if item == 'throughput' else nonce.hex
        signature = md5((str(device.api_shared_secret) + random_value).encode('utf-8')).hexdigest()
        try:
            method = 'POST' if item == 'throughput' else 'GET'
            remaining = max(0.01, float(timeout) - (monotonic() - started))
            if remaining <= 0.01 and monotonic() - started >= float(timeout):
                raise TimeoutError('collection deadline exceeded')
            post_payload = {'random': random_value, 'md5': signature} if method == 'POST' else None
            payload = _request_json(request, _url(device.api_url, endpoint, random_value, signature, method_override=item == 'throughput'), timeout=remaining, verify_ssl=bool(getattr(device, 'verify_ssl', True)), method=method, payload=post_payload)
            if not isinstance(payload, dict) or type(payload.get('code')) is not int or payload['code'] != 0 or 'data' not in payload:
                raise ValueError('device did not return success')
            value = convert(payload.get('data'))
            data[item] = value
            raw['api:' + endpoint] = {'code': 0, 'data': payload.get('data')}
            completed.add(item)
        except Exception as exc:
            data[item] = {'status': 'failed', 'message': '深信服 AC 状态查询失败'}
            import requests
            if isinstance(exc, requests.HTTPError) and exc.response is not None:
                detail = f'HTTP {exc.response.status_code}，请检查开放接口、IP 白名单和共享密钥'
            elif isinstance(exc, (requests.Timeout, TimeoutError)):
                detail = '采集超时'
            elif isinstance(exc, requests.exceptions.SSLError):
                detail = 'TLS 证书校验失败'
            elif isinstance(exc, requests.ConnectionError):
                detail = '连接失败，请检查 API 地址、端口和网络'
            else:
                detail = '接口拒绝请求或返回数据无效，请检查共享密钥、白名单和接口版本'
            data[item]['message'] = detail
            messages.append(f'{item}: {detail}')
    missing = [item for item in selected if item not in completed]
    status = 'success' if not missing else 'partial' if (completed or all(isinstance(data.get(item), dict) and data[item].get('status') == 'unsupported' for item in missing)) else 'failed'
    return CollectionResult(bool(completed), status, '；'.join(messages), data, raw, max(0, round((monotonic() - started) * 1000)))
