"""Inspection item to protocol field mappings (no implicit collect-all for [])."""

LINUX_FIELDS = {
    'computer_name': ('hostname',), 'system_info': ('system', 'uptime'),
    'cpu': ('cpu',), 'memory': ('memory',), 'storage_status': ('storage',),
    'network_info': ('network',), 'services': ('services',), 'logs': ('logs',),
}
NETWORK_FIELDS = (
    'device_info', 'device_info', 'cpu', 'memory', 'temperature',
    'interface_status', 'vlan_status', 'logs',
    'config_info',
)
WINDOWS_FIELDS = {
    'computer_name': ('computer_name', 'hostname'), 'system_info': ('system_info', 'system'),
    'cpu': ('cpu',), 'memory': ('memory',),
    'storage_status': ('storage_status', 'storage', 'disks'),
    'network_info': ('network_info', 'network'), 'services': ('services',), 'logs': ('logs',),
}
SECURITY_FIELDS = {
    'device_info': ('device_info', 'device', 'deviceName', 'model', 'serialNumber', 'firmwareVersion'),
    'status_data': ('status_data', 'status'), 'channel_status': ('channel_status', 'channels'),
    'storage_status': ('storage_status', 'storage', 'disks'),
    'config_info': ('config_info',),
}


def selected_fields(payload, selected_items, aliases=None):
    if not isinstance(payload, dict):
        return {}
    if selected_items is None:
        return dict(payload)
    aliases = aliases or {}
    keys = {key for item in selected_items for key in aliases.get(item, (item,))}
    return {key: value for key, value in payload.items() if key in keys}

# Keep NETWORK_FIELDS aligned with the legacy positional command table.
NETWORK_FUNCTION_ITEMS = {
    'routing_table': 'IPv4 路由表', 'arp_table': 'ARP 地址表',
    'mac_table': 'MAC 地址表', 'lldp_neighbors': 'LLDP 邻居',
    'wireless_aps': '无线 AP 状态', 'wireless_clients': '无线客户端',
}
