"""Safe, bounded metrics shared by record lists, details and CSV exports."""
from net.services.sanitization import sanitize


def key_metrics(details):
    details = details if isinstance(details, dict) else {}
    parts = []
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
    for key, label in (('storage_status', '磁盘'), ('services', '服务'), ('logs', '日志'),
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
    for key, label in (('status_data', '设备状态'), ('interface_status', '接口'), ('vlan_status', 'VLAN')):
        if key in details:
            parts.append(f'{label}已采集')
    return sanitize('；'.join(parts))[:1000] or '无可用指标'
