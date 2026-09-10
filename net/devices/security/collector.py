"""Protocol selection and evidence conversion for security devices."""

from collections.abc import Mapping

from net.devices.network.collector import _has_snmp_credentials
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
    if 'device_info' in requested and 'device_info' in source:
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


def collect_security(device, timeout=12, selected_items=None, *, api_collector=None):
    """Use API, SNMP, or ICMP without manufacturing unsupported evidence."""
    settings = _settings(device)
    if settings.get('item_methods'):
        from copy import copy
        from net.devices.network.collector import _merge_network_results
        requested=_requested(selected_items)
        groups={}
        for item in requested:
            method=settings['item_methods'].get(item,'auto')
            protocol=settings.get('protocol','auto') if method=='auto' else method
            groups.setdefault(protocol,[]).append(item)
        results=[]
        for protocol,items in groups.items():
            context=copy(device)
            context.collection_settings={**settings,'item_methods':{},'protocol':protocol}
            results.append((protocol,collect_security(context,timeout,selected_items=items,api_collector=api_collector)))
        return _merge_network_results(requested,results)
    mode = settings.get('protocol', 'auto')
    mode = mode if mode in {'auto', 'api', 'snmp', 'ping'} else 'auto'
    api_result = None
    if mode == 'api' or (mode == 'auto' and getattr(device, 'api_url', '')):
        api_result = (api_collector or collect_security_api)(device, timeout, selected_items=selected_items)
        if mode == 'api' or api_result.status == 'success' or api_result.reachable:
            return api_result
    if mode in {'auto', 'snmp'}:
        _prepare_snmp(device, settings)
        if _has_snmp_credentials(device):
            requested = _requested(selected_items)
            result = _convert_snmp(
                collect_network_snmp(device, timeout, selected_items=_snmp_selection(requested)), requested
            )
            if api_result is not None:
                result.status = 'partial' if result.reachable else 'failed'
                result.message = '；'.join(part for part in (api_result.message, result.message) if part)
            return result
        if mode == 'snmp':
            return CollectionResult(False, 'failed', '未配置安防设备 SNMP 凭据')
    result = _ping_result(device, timeout, _requested(selected_items, ping=True))
    if api_result is not None:
        result.status = 'partial' if result.reachable else 'failed'
        result.message = '；'.join(part for part in (api_result.message, result.message) if part)
    return result


__all__ = ['collect_security']
