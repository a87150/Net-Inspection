"""Protocol selection and evidence conversion for security devices."""

from collections.abc import Mapping
from copy import copy
from time import monotonic

from net.devices.network.collector import _has_snmp_credentials, _item_completed, _merge_network_results
from net.devices.network.snmp import collect_network_snmp
from net.devices.security.api import collect_security_api
from net.infrastructure.collection import CollectionResult
from net.infrastructure.reachability import ping_host


_SECURITY_ITEMS = ('device_info', 'status_data', 'channel_status', 'storage_status', 'config_info')
_SNMP_ATTRIBUTES = (
    'snmp_version', 'snmp_port', 'snmp_community', 'snmp_security_level',
    'snmp_username', 'snmp_auth_protocol', 'snmp_auth_password',
    'snmp_priv_protocol', 'snmp_priv_password', 'snmp_context_name', 'snmp_retries',
)


def _settings(device):
    value = getattr(device, 'collection_settings', {})
    return dict(value) if isinstance(value, Mapping) else {}


def _requested(items, *, ping=False):
    if items is None:
        return ['status_data'] if ping else list(_SECURITY_ITEMS)
    return list(dict.fromkeys(item for item in items if item in _SECURITY_ITEMS))


def _prepare_snmp(device, settings):
    values = settings.get('snmp')
    values = values if isinstance(values, Mapping) else {}
    for name in _SNMP_ATTRIBUTES:
        if name in values:
            setattr(device, name, values[name])
    for name, default in (
        ('snmp_version', 'v2c'), ('snmp_port', 161), ('snmp_security_level', 'noAuthNoPriv'),
        ('snmp_retries', 1), ('snmp_context_name', ''),
    ):
        if not hasattr(device, name):
            setattr(device, name, default)


def _snmp_selection(requested):
    selected = []
    if 'device_info' in requested:
        selected.append('device_info')
    if 'status_data' in requested:
        selected.extend(('cpu', 'memory', 'temperature'))
    return selected


def _convert_snmp(result, requested):
    source = result.data if isinstance(result.data, dict) else {}
    data = {}
    if 'device_info' in requested and source.get('device_info') and _item_completed(source['device_info']):
        data['device_info'] = source['device_info']
    if 'status_data' in requested:
        status = {'source': 'snmp'}
        cpu, memory, temperature = source.get('cpu'), source.get('memory'), source.get('temperature')
        if isinstance(cpu, Mapping) and isinstance(cpu.get('usage_percent'), (int, float)):
            status['cpu_usage_percent'] = cpu['usage_percent']
        if isinstance(memory, Mapping) and isinstance(memory.get('usage_percent'), (int, float)):
            status['memory_usage_percent'] = memory['usage_percent']
        if isinstance(temperature, Mapping) and temperature.get('values_celsius'):
            status['temperature_celsius'] = temperature['values_celsius']
        if len(status) > 1:
            data['status_data'] = status
    missing = [item for item in requested if item not in data]
    status = 'failed' if missing and not data else 'partial' if missing else 'success'
    message = '缺少有效采集证据：' + ', '.join(missing) if missing else ''
    if result.message:
        message = '；'.join(part for part in (message, result.message) if part)
    return CollectionResult(result.reachable, status, message, data, dict(result.raw or {}), result.duration_ms)


def _ping_result(device, timeout, requested):
    reachable, diagnostic = ping_host(device.ip, timeout)
    if reachable:
        data = {'status_data': {'online': True, 'source': 'ping'}} if 'status_data' in requested else {}
        missing = [item for item in requested if item not in data]
        return CollectionResult(True, 'partial' if missing else 'success',
                                '缺少有效采集证据：' + ', '.join(missing) if missing else '', data)
    return CollectionResult(False, 'failed',
                            f'{diagnostic}；ICMP 不可达可能被设备或网络策略拦截，不能据此判定断电。')


def _security_item_completed(item, value):
    # A vendor's health status (e.g. storage status=failed) is valid evidence.
    # Only the configuration capture uses status to signal collection failure.
    return value is not None and value != {} and (
        item != 'config_info' or _item_completed(value))


def _missing_items(requested, results):
    completed = {item for _, result in results for item, value in (result.data or {}).items()
                 if _security_item_completed(item, value)}
    return [item for item in requested if item not in completed]


def _collect_auto(device, timeout, selected_items, settings, api_collector):
    _prepare_snmp(device, settings)
    has_snmp = _has_snmp_credentials(device)
    has_api = bool(getattr(device, 'api_url', ''))
    requested = _requested(selected_items, ping=not has_snmp and not has_api)
    if not requested:
        return CollectionResult(False, 'success', '未选择采集项目。')
    results = []
    deadline = monotonic() + max(0, float(timeout))
    snmp_items = [item for item in requested if item in {'device_info', 'status_data'}]
    stages = []
    if has_snmp and snmp_items:
        stages.append('snmp')
    if has_api:
        stages.append('api')
    stages.append('ping')
    for index, protocol in enumerate(stages):
        missing = _missing_items(requested, results)
        if not missing:
            break
        remaining = deadline - monotonic()
        if remaining <= 0:
            results.append((protocol, CollectionResult(False, 'failed', '安防采集总时限已到，未继续回退。')))
            break
        # Reserve time for the later fallback stages instead of multiplying the task timeout.
        budget = remaining / (len(stages) - index)
        try:
            if protocol == 'snmp':
                result = _convert_snmp(collect_network_snmp(device, budget,
                    selected_items=_snmp_selection(snmp_items), total_timeout=budget), snmp_items)
            elif protocol == 'api':
                result = api_collector(device, budget, selected_items=missing)
            else:
                result = _ping_result(device, budget, missing)
        except Exception:
            result = CollectionResult(False, 'failed', f'{protocol.upper()} 采集异常，继续检查其他可用方式。')
        if result.message:
            result = CollectionResult(result.reachable, result.status,
                f'{protocol.upper()}：{result.message}', result.data, result.raw, result.duration_ms)
        results.append((protocol, result))
    if len(results) == 1 and results[0][0] == 'api' and not _missing_items(requested, results):
        return results[0][1]
    merged = _merge_network_results(requested, results, item_completed=_security_item_completed)
    if len(results) > 1 and any(protocol == 'ping' for protocol, _ in results):
        # ICMP proves reachability, not the health of failed SNMP/API inspection items.
        if any(protocol == 'ping' and result.reachable for protocol, result in results):
            merged.status = 'partial'
        merged.message = '；'.join(part for part in (
            merged.message, '已回退到 Ping 在线检查，未获取到的业务指标仍属缺失。') if part)
    return merged


def collect_security(device, timeout=12, selected_items=None, *, api_collector=None):
    """Auto: SNMP first, API fills missing items, ICMP is the final fallback."""
    settings = _settings(device)
    if settings.get('item_methods'):
        requested = _requested(selected_items)
        groups = {}
        for item in requested:
            method = settings['item_methods'].get(item, 'auto')
            protocol = settings.get('protocol', 'auto') if method == 'auto' else method
            groups.setdefault(protocol, []).append(item)
        results = []
        deadline = monotonic() + max(0, float(timeout))
        ordered = sorted(groups, key=lambda value: {'snmp': 0, 'auto': 1, 'api': 2, 'ping': 3}.get(value, 1))
        for index, protocol in enumerate(ordered):
            remaining = deadline - monotonic()
            if remaining <= 0:
                results.append((protocol, CollectionResult(False, 'failed', '安防采集总时限已到。')))
                break
            context = copy(device)
            context.collection_settings = {**settings, 'item_methods': {}, 'protocol': protocol}
            result = collect_security(context, remaining / (len(ordered) - index),
                                      selected_items=groups[protocol], api_collector=api_collector)
            results.append((protocol, result))
        merged = _merge_network_results(requested, results, item_completed=_security_item_completed)
        if merged.status == 'success' and any(result.status != 'success' for _, result in results):
            merged.status = 'partial'
        return merged
    mode = settings.get('protocol', 'auto')
    mode = mode if mode in {'auto', 'api', 'snmp', 'ping'} else 'auto'
    if mode == 'auto':
        return _collect_auto(device, timeout, selected_items, settings,
                             api_collector or collect_security_api)
    if mode == 'api':
        return (api_collector or collect_security_api)(device, timeout, selected_items=selected_items)
    if mode == 'snmp':
        _prepare_snmp(device, settings)
        if not _has_snmp_credentials(device):
            return CollectionResult(False, 'failed', '未配置安防设备 SNMP 凭据')
        requested = _requested(selected_items)
        return _convert_snmp(collect_network_snmp(device, timeout,
                             selected_items=_snmp_selection(requested)), requested)
    return _ping_result(device, timeout, _requested(selected_items, ping=True))


__all__ = ['collect_security']
