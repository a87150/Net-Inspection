"""Safe, bounded metrics shared by record lists, details and CSV exports."""
from net.infrastructure.sanitization import sanitize


def key_metrics(details):
    details = details if isinstance(details, dict) else {}
    parts = []
    traffic = details.get('traffic')
    if isinstance(traffic, dict):
        rows = [row for row in traffic.get('interfaces', []) if isinstance(row, dict) and row.get('data_state') == 'known']
        if rows:
            parts.append(f"接口峰值收/发 {max(row.get('rx_mbps') or 0 for row in rows):g}/{max(row.get('tx_mbps') or 0 for row in rows):g} Mbps")
    for key, label, names in (
        ('cpu', 'CPU', ('usage_percent', 'percent', 'load_percent')),
        ('memory', '内存', ('used_percent', 'usage_percent', 'percent')),
    ):
        value = details.get(key)
        if isinstance(value, dict):
            number = next((value[name] for name in names if value.get(name) is not None), None)
            if isinstance(number, (int, float)) and not isinstance(number, bool):
                parts.append(f'{label} {number}%')
            elif key == 'memory' and value.get('used_bytes') is not None:
                parts.append(f'内存 {value["used_bytes"]}/{value.get("total_bytes", "?")} bytes')
    temperature = details.get('temperature', {})
    if isinstance(temperature, dict) and temperature.get('values_celsius'):
        parts.append('温度 ' + ', '.join(map(str, temperature['values_celsius'][:8])) + ' °C')
    storage = details.get('storage_status')
    if isinstance(storage, list):
        disk_parts = []
        for disk in storage[:4]:
            if not isinstance(disk, dict):
                continue
            mount = disk.get('mount') or disk.get('device') or disk.get('name') or disk.get('filesystem')
            usage = disk.get('usage_percent')
            if usage is None:
                usage = disk.get('used_percent', disk.get('usage'))
            if mount and usage is not None:
                usage_text = str(usage)
                disk_parts.append(f'{mount} {usage_text if usage_text.endswith("%") else usage_text + "%"}')
        parts.append('磁盘 ' + ', '.join(disk_parts) if disk_parts else f'磁盘 {len(storage)} 项')
    computer_info = details.get('computer_info')
    if isinstance(computer_info, dict):
        disk_summary = computer_info.get('磁盘摘要') or computer_info.get('disk_summary')
        if disk_summary:
            parts.append(f'磁盘 {disk_summary}')
    for key, label in (('services', '服务'), ('logs', '日志'),
                       ('channel_status', '通道'), ('software', '软件'), ('processes', '进程'),
                       ('patches', '补丁'), ('event_findings', '事件')):
        value = details.get(key)
        if isinstance(value, list):
            parts.append(f'{label} {len(value)} 项')
    resource = details.get('resource')
    if isinstance(resource, dict):
        for key, label in (('当前CPU占用率', 'CPU'), ('当前内存使用率', '内存')):
            if resource.get(key) is not None:
                parts.append(f'{label} {resource[key]}')
    interface_status = details.get('interface_status')
    if isinstance(interface_status, dict):
        if interface_status.get('up') is not None or interface_status.get('down') is not None:
            parts.append(
                f'接口 Up {interface_status.get("up", 0)} / Down {interface_status.get("down", 0)}'
            )
        else:
            parts.append('接口已采集')
    for key, label in (('status_data', '设备状态'), ('vlan_status', 'VLAN')):
        if key in details:
            parts.append(f'{label}已采集')
    from net.inspections.selection import NETWORK_FUNCTION_ITEMS
    for key,label in NETWORK_FUNCTION_ITEMS.items():
        value=details.get(key)
        if isinstance(value,dict) and isinstance(value.get('records'),list):parts.append(f'{label} {len(value["records"])} 条')
    return sanitize('；'.join(parts))[:1000] or '无可用指标'
