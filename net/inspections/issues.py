"""Shared stable problem categories and administrator-configurable rule keys."""
RULES = {
    'software': ('软件', '软件策略'), 'processes': ('软件', '进程信息'),
    'browser_extensions': ('软件', '浏览器插件'),
    'disk': ('硬件', '磁盘空间'),
    'cpu_health': ('硬件', 'CPU 温度/频率'), 'resource': ('硬件', '资源占用'),
    'system': ('操作系统', '系统版本'), 'activation': ('操作系统', '系统激活'),
    'uptime': ('操作系统', '连续开机时间'),
    'patches': ('系统更新', '系统补丁'), 'defender': ('杀毒', 'Defender 状态'),
    'bitlocker': ('磁盘加密', 'BitLocker 状态'),
    'identity_match': ('人员身份', '计算机与登录用户'),
    'domain_trust': ('域与组策略', '域信任'), 'group_policy': ('域与组策略', '组策略'),
    'event_findings': ('系统事件', '事件日志'),
    'inspection_collection': ('采集连接', '设备巡检采集'),
}


PROJECT_LABELS = {'computers': 'PC 日志分析', 'networks': '网络设备巡检', 'servers': '服务器巡检', 'monitors': '安防设备巡检'}
DEVICE_PROJECTS = {'network_device': 'networks', 'server': 'servers', 'monitor': 'monitors'}
PROJECT_RULES = {
    'computers': {key: value for key, value in RULES.items() if key != 'inspection_collection'},
    'networks': {key: (label, label) for key, label in (
        ('cpu', 'CPU'), ('memory', '内存'), ('temperature', '温度'),
        ('disk_usage', '磁盘使用率'), ('bandwidth_usage', '带宽使用率'),
        ('online_users', '在线用户数'), ('sessions', '会话数'), ('inside_libraries', '内置库'),
        ('log_statistics', '行为日志统计'), ('system_time', '系统时间'), ('throughput', '实时吞吐量'),
        ('traffic', '实时接口流量'), ('interface_status', '接口状态'), ('vlan_status', 'VLAN'),
        ('device_info', '设备信息'), ('logs', '设备日志'), ('config_info', '设备配置'),
        ('inspection_collection', '采集连接'))},
    'servers': {key: (label, label) for key, label in (
        ('cpu', 'CPU'), ('memory', '内存'), ('storage_status', '磁盘存储'), ('network_info', '网络'),
        ('services', '服务'), ('logs', '系统日志'), ('system_info', '操作系统'),
        ('computer_name', '设备名称'), ('inspection_collection', '采集连接'))},
    'monitors': {key: (label, label) for key, label in (
        ('device_info', '设备信息'), ('status_data', '设备运行状态'),
        ('channel_status', '视频通道/门禁闸机通道'), ('storage_status', '录像/存储'),
        ('config_info', '设备配置'), ('inspection_collection', '采集连接'))},
}
from net.inspections.selection import NETWORK_FUNCTION_ITEMS
PROJECT_RULES['networks'].update({key:(label,label) for key,label in NETWORK_FUNCTION_ITEMS.items()})

# PC keys are the existing analysis profile parameters. Omitted values retain that profile's setting.
PC_THRESHOLD_FIELDS = {
    'disk_max_percent': ('disk', '磁盘使用率（%）', 100),
    'cpu_max_percent': ('resource', 'CPU 使用率（%）', 100),
    'memory_max_percent': ('resource', '内存使用率（%）', 100),
    'cpu_temperature_max_celsius': ('cpu_health', 'CPU 温度（℃）', 150),
    'uptime_max_hours': ('uptime', '连续开机时间（小时）', 87600),
    'patch_max_days': ('patches', '补丁最大间隔（天）', 3650),
    'defender_update_max_days': ('defender', '病毒库最大间隔（天）', 3650),
    'defender_scan_max_days': ('defender', '扫描最大间隔（天）', 3650),
}


METRIC_DEFAULTS = {'networks': {'cpu': 90, 'memory': 90, 'temperature': 85, 'traffic': 90, 'disk_usage': 90, 'bandwidth_usage': 90},
                   'servers': {'cpu': 90, 'memory': 90, 'storage_status': 90}}


def configuration_snapshot(project='computers'):
    from net.models import IssueSeverityPolicy
    row = IssueSeverityPolicy.objects.filter(project=project).first()
    return (dict(row.overrides) if row else {},
            {**METRIC_DEFAULTS.get(project, {}), **(row.thresholds if row else {})})


def policy_snapshot(project='computers'):
    return configuration_snapshot(project)[0]


def threshold_snapshot(project):
    return configuration_snapshot(project)[1]


def issue_categories(issues):
    from net.devices.pc.severity import grade_issue
    return '、'.join(sorted({grade_issue(issue)['category'] for issue in issues}))
