"""Protocol orchestration for network-device inspections."""

from collections import Counter

from net.devices.network.snmp import SNMP_ITEMS, collect_network_snmp
from net.devices.network.ssh import collect_network_ssh
from net.infrastructure.collection import CollectionResult
from net.inspections.selection import NETWORK_FIELDS


SSH_ONLY_ITEMS = frozenset({'logs', 'config_info'})
_DEFAULT_ITEMS = tuple(dict.fromkeys(NETWORK_FIELDS[:-1]))


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


def _network_item_plan(mode, selected_items):
    """Return ordered SNMP/SSH selections and whether SNMP may fall back."""
    requested = _ordered_unique(_DEFAULT_ITEMS if selected_items is None else selected_items)
    mode = mode if mode in {'ssh', 'snmp', 'hybrid', 'auto'} else 'ssh'
    if mode == 'ssh':
        return [], requested, False
    snmp_items = [item for item in requested if item in SNMP_ITEMS]
    if mode == 'snmp':
        return snmp_items, [], False
    ssh_items = [item for item in requested if item in SSH_ONLY_ITEMS]
    return snmp_items, ssh_items, mode == 'auto'


def _merge_network_results(requested, results):
    """Merge protocol evidence and derive status only from requested data."""
    requested = _ordered_unique(requested)
    requested_set = set(requested)
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
    for protocol, result in results:
        reachable = reachable or bool(result.reachable)
        duration_ms += max(0, int(result.duration_ms or 0))
        if result.message and result.message not in messages:
            messages.append(result.message)
        if isinstance(result.data, dict):
            for item, value in result.data.items():
                if item not in requested_set:
                    continue
                value_completed = _item_completed(value)
                if item not in data or (
                    value_completed and not _item_completed(data[item])
                ):
                    data[item] = value
                if value_completed:
                    completed.add(item)
        if isinstance(result.raw, dict):
            for key, value in result.raw.items():
                preferred = f'{protocol}:{key}' if raw_counts[key] > 1 else key
                raw[_allocate_raw_key(raw, preferred)] = value

    missing = [item for item in requested if item not in completed]
    status = 'failed' if missing and not completed else 'partial' if missing else 'success'
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
    requested = _ordered_unique(_DEFAULT_ITEMS if selected_items is None else selected_items)
    mode = getattr(device, 'effective_connection_type', None)
    if mode not in {'ssh', 'snmp', 'hybrid', 'auto'}:
        mode = getattr(device, 'connection_type', 'ssh')
    snmp_items, ssh_items, auto_fallback = _network_item_plan(mode, requested)
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
        results.append(('snmp', snmp_result))
        if auto_fallback:
            snmp_data = snmp_result.data if isinstance(snmp_result.data, dict) else {}
            missing_snmp = [
                item
                for item in snmp_items
                if item not in snmp_data or not _item_completed(snmp_data[item])
            ]
            fallback = set(missing_snmp)
            ssh_items = [item for item in requested if item in SSH_ONLY_ITEMS or item in fallback]

    if ssh_items:
        if use_default_ssh and (
            not getattr(device, 'username', '') or not getattr(device, 'password', '')
        ):
            ssh_result = CollectionResult(
                False, 'failed', '未配置网络设备 SSH 账号和密码',
            )
        else:
            ssh_result = ssh_collector(device, timeout, selected_items=ssh_items)
        results.append((
            'ssh',
            ssh_result,
        ))
    return _merge_network_results(requested, results)


__all__ = [
    'SSH_ONLY_ITEMS',
    '_merge_network_results',
    '_network_item_plan',
    'collect_network',
]
