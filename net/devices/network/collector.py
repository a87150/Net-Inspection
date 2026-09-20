"""Protocol orchestration for network-device inspections."""

from collections import Counter

from net.devices.network.snmp import SNMP_ITEMS, collect_network_snmp
from net.devices.network.ssh import collect_network_ssh
from net.devices.network.sangfor import collect_sangfor_ac
from net.infrastructure.collection import CollectionResult
from net.inspections.selection import NETWORK_FIELDS, NETWORK_FUNCTION_ITEMS


SSH_ONLY_ITEMS = frozenset({'logs', 'config_info'}) | frozenset(NETWORK_FUNCTION_ITEMS)
_DEFAULT_ITEMS = ('device_info', 'cpu', 'memory', 'temperature', 'interface_status', 'vlan_status', 'logs')


def _ordered_unique(items):
    return list(dict.fromkeys(items))


def _item_completed(value):
    return not (
        isinstance(value, dict)
        and 'status' in value
        and value.get('status') != 'success'
    )


def _allocate_raw_key(raw, preferred):
    if preferred not in raw:
        return preferred
    suffix = 2
    while f'{preferred}#{suffix}' in raw:
        suffix += 1
    return f'{preferred}#{suffix}'


def _network_item_plan(mode, selected_items, item_methods=None):
    """Return ordered SNMP/SSH selections and whether SNMP may fall back."""
    requested = _ordered_unique(_DEFAULT_ITEMS if selected_items is None else selected_items)
    mode = mode if mode in {'ssh', 'snmp', 'hybrid', 'auto'} else 'ssh'
    methods = item_methods or {}
    snmp_items = []
    ssh_items = []
    for item in requested:
        method = methods.get(item)
        if method == 'snmp':
            snmp_items.append(item)
            continue
        if method == 'ssh':
            ssh_items.append(item)
            continue
        if item == 'lldp_neighbors' and method == 'auto':
            if mode in {'snmp', 'hybrid', 'auto'}:
                snmp_items.append(item)
            if mode in {'ssh', 'hybrid', 'auto'}:
                ssh_items.append(item)
            continue
        if mode == 'ssh':
            (snmp_items if item == 'traffic' else ssh_items).append(item)
        elif mode == 'snmp':
            if item in SNMP_ITEMS:
                snmp_items.append(item)
            elif item == 'config_info':
                ssh_items.append(item)
        else:
            if item in SNMP_ITEMS:
                snmp_items.append(item)
            if item in SSH_ONLY_ITEMS:
                ssh_items.append(item)
    return snmp_items, ssh_items, mode == 'auto' and not any(
        method == 'auto' for method in methods.values()
    )
def _merge_network_results(requested, results, *, item_completed=None):
    """Merge protocol evidence and derive status only from requested data."""
    requested = _ordered_unique(requested)
    requested_set = set(requested)
    is_completed = item_completed or (lambda item, value: _item_completed(value))
    data = {}
    raw = {}
    raw_counts = Counter(
        key
        for _protocol, result in results
        if isinstance(result.raw, dict)
        for key in result.raw
    )
    reachable = False
    duration_ms = 0
    messages = []
    completed = set()
    partial_items = set()
    for protocol, result in results:
        reachable = reachable or bool(result.reachable)
        duration_ms += max(0, int(result.duration_ms or 0))
        if result.message and result.message not in messages:
            messages.append(result.message)
        if isinstance(result.data, dict):
            for item, value in result.data.items():
                if item not in requested_set:
                    continue
                value_completed = is_completed(item, value)
                if item not in data or (
                    value_completed and not is_completed(item, data[item])
                ):
                    data[item] = value
                if value_completed:
                    completed.add(item)
        if isinstance(result.raw, dict):
            for key, value in result.raw.items():
                preferred = f'{protocol}:{key}' if raw_counts[key] > 1 else key
                raw[_allocate_raw_key(raw, preferred)] = value

    if 'lldp_neighbors' in requested:
        from net.devices.network.topology_protocols import merge_topology_results
        topology_values = [result.data.get('lldp_neighbors') for _protocol, result in results
                           if isinstance(result.data, dict) and isinstance(result.data.get('lldp_neighbors'), dict)]
        if topology_values:
            data['lldp_neighbors'] = merge_topology_results(topology_values)
            if data['lldp_neighbors']['status'] == 'success':
                completed.add('lldp_neighbors')
            else:
                completed.discard('lldp_neighbors')
                if data['lldp_neighbors']['status'] == 'partial':
                    partial_items.add('lldp_neighbors')
    missing = [item for item in requested if item not in completed]
    status = 'failed' if missing and not (completed or partial_items) else 'partial' if missing else 'success'
    message = '缺少有效采集证据：' + ', '.join(missing) if missing else ''
    if messages:
        message = '; '.join(([message] if message else []) + messages)
    return CollectionResult(reachable, status, message, data, raw, duration_ms)


def _has_snmp_credentials(device):
    if getattr(device, 'snmp_version', 'v2c') == 'v2c':
        return bool(getattr(device, 'snmp_community', ''))
    level = getattr(device, 'snmp_security_level', 'noAuthNoPriv')
    return (
        bool(getattr(device, 'snmp_username', ''))
        and (level == 'noAuthNoPriv' or bool(getattr(device, 'snmp_auth_password', '')))
        and (level != 'authPriv' or bool(getattr(device, 'snmp_priv_password', '')))
    )


def collect_network(
    device,
    timeout=12,
    selected_items=None,
    *,
    snmp_collector=None,
    ssh_collector=None,
):
    """Collect network items with deterministic SSH/SNMP routing."""
    if getattr(device, 'connection_type', '') == 'sangfor_api':
        return collect_sangfor_ac(device, timeout, selected_items=selected_items)
    requested = _ordered_unique(_DEFAULT_ITEMS if selected_items is None else selected_items)
    mode = getattr(device, 'effective_connection_type', None)
    if mode not in {'ssh', 'snmp', 'hybrid', 'auto'}:
        mode = getattr(device, 'connection_type', 'ssh')
    settings = getattr(device, 'collection_settings', {}) or {}
    methods = {item:method for item,method in settings.get('item_methods',{}).items() if item in requested and method in {'snmp','ssh','auto'}}
    snmp_items, ssh_items, auto_fallback = _network_item_plan(mode, requested, methods)
    use_default_snmp = snmp_collector is None
    use_default_ssh = ssh_collector is None
    snmp_collector = snmp_collector or collect_network_snmp
    ssh_collector = ssh_collector or collect_network_ssh
    results = []

    if snmp_items:
        if use_default_snmp and not _has_snmp_credentials(device):
            snmp_result = CollectionResult(
                False, 'failed', '未配置网络设备 SNMP 凭据',
            )
        else:
            snmp_result = snmp_collector(device, timeout, selected_items=snmp_items)
        if 'lldp_neighbors' in snmp_items and 'lldp_neighbors' not in (snmp_result.data or {}):
            snmp_result.data['lldp_neighbors'] = {
                'status': 'failed', 'complete': False, 'protocols': ['snmp_lldp'],
                'interfaces': [], 'neighbors': [], 'evidence': {},
            }
        results.append(('snmp', snmp_result))
        if auto_fallback:
            snmp_data = snmp_result.data if isinstance(snmp_result.data, dict) else {}
            missing_snmp = [
                item
                for item in snmp_items
                if item not in snmp_data or not _item_completed(snmp_data[item])
            ]
            fallback = {item for item in missing_snmp if item not in methods}
            ssh_items = [item for item in requested if (item in SSH_ONLY_ITEMS and methods.get(item)!='snmp') or methods.get(item)=='ssh' or (item in fallback and item != 'traffic')]

    settings = getattr(device, 'collection_settings', {}) or {}
    template_items = {item for item in settings.get('commands', {}) if item in requested and methods.get(item)!='snmp' and (mode!='snmp' or methods.get(item)=='ssh')}
    if template_items:
        ssh_items = [item for item in requested if item in ssh_items or item in template_items]

    if ssh_items:
        if use_default_ssh and (
            not getattr(device, 'username', '') or not getattr(device, 'password', '')
        ):
            ssh_result = CollectionResult(
                False, 'failed', '未配置网络设备 SSH 账号和密码',
            )
        else:
            ssh_result = ssh_collector(device, timeout, selected_items=ssh_items)
        if 'lldp_neighbors' in ssh_items and 'lldp_neighbors' not in (ssh_result.data or {}):
            ssh_result.data['lldp_neighbors'] = {
                'status': 'failed', 'complete': False, 'protocols': ['ssh_lldp'],
                'interfaces': [], 'neighbors': [], 'evidence': {},
            }
        results.append((
            'ssh',
            ssh_result,
        ))
    merged = _merge_network_results(requested, results)
    if template_items and ssh_items:
        # An explicit device command is authoritative when it produced valid evidence.
        for item in template_items:
            value = (ssh_result.data or {}).get(item)
            if value is not None and _item_completed(value):
                merged.data[item] = value
            elif merged.status == 'success':
                merged.status = 'partial'
    return merged


__all__ = [
    'SSH_ONLY_ITEMS',
    '_merge_network_results',
    '_network_item_plan',
    'collect_network',
]


