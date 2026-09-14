"""Frozen report projection for migration 0036. Do not import runtime application code."""
"""One recursive sanitizer for persisted collector output and error surfaces."""

import re
from urllib.parse import quote, quote_plus, unquote, urlsplit, urlunsplit


REDACTED = '[REDACTED]'
_SECRET_KEY = re.compile(r'password|passwd|pwd|secret|token|authorization|credential|api[_-]?key|cookie|signature|^(?:auth|key|sig)$', re.I)
_AUTH = re.compile(r'\b(Bearer|Basic)\s+(?!\[REDACTED\])[^\s,;\"\'<>]+', re.I)
_ASSIGNMENT = re.compile(
    r'''(?ix)(\b(?:[\w%-]*(?:password|passwd|pwd|secret|token|authorization|credential|api[_-]?key|cookie|signature)[\w%-]*|auth|key|sig)["']?\s*[:=]\s*)
    ("[^"\r\n]*"|'[^'\r\n]*'|\[REDACTED\]|[^\s,;&<>]+)'''
)
_URL = re.compile(r'https?://[^\s<>"\']+', re.I)


def public_connection_url(value):
    """Read-only endpoint label, never a usable copy of connection credentials.

    Drop ALL query parameters and fragments: provider-specific token names are
    not enumerable. Do not use this for storage, snapshots or collector input.
    """
    if not value:
        return ''
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname:
            return REDACTED
        # Validate the port before publishing an otherwise malformed authority.
        parsed.port
        return urlunsplit((parsed.scheme, parsed.netloc.rsplit('@', 1)[-1], parsed.path, '', ''))
    except (ValueError, TypeError):
        return REDACTED


def sanitize(value, *, secrets=()):
    """Keep data shape, redact sensitive keys and text, including resolved secrets.

    The secret set exists only during execution and is never added to snapshots.
    """
    replacements = set()
    for secret in secrets:
        if isinstance(secret, str) and secret:
            replacements.update((secret, quote(secret, safe=''), quote_plus(secret)))

    def scrub_url(match):
        return public_connection_url(match.group())

    def text(raw):
        raw = _URL.sub(scrub_url, raw)
        raw = _AUTH.sub(lambda match: match.group(1) + ' ' + REDACTED, raw)
        raw = _ASSIGNMENT.sub(lambda match: match.group(1) + REDACTED, raw)
        for secret in sorted(replacements, key=len, reverse=True):
            raw = raw.replace(secret, REDACTED)
        return raw

    def visit(item):
        if isinstance(item, dict):
            return {text(str(key)): REDACTED if _SECRET_KEY.search(unquote(str(key))) else visit(child)
                    for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [visit(child) for child in item]
        if isinstance(item, str):
            return text(item)
        return item

    return visit(value)



"""Safe, bounded metrics shared by record lists, details and CSV exports."""



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
            mount = disk.get('mount') or disk.get('name') or disk.get('filesystem')
            usage = disk.get('usage_percent')
            if usage is None:
                usage = disk.get('usage')
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
    return sanitize('；'.join(parts))[:1000] or '无可用指标'

"""Shared stable problem categories and administrator-configurable rule keys."""
RULES = {
    'software': ('软件', '软件策略'), 'processes': ('软件', '进程信息'),
    'browser_extensions': ('软件', '浏览器插件'),
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

"""PC finding health is independent from analysis execution status."""

LEVELS = {'info': '提示', 'warning': '警告', 'critical': '严重'}
RANK = {'info': 0, 'warning': 1, 'critical': 2}


def grade_issue(issue, *, overrides=None):
    result = dict(issue)
    title = str(result.get('问题类型', ''))

    item = str(result.get('analysis_item') or '')
    rules = PROJECT_RULES.get(result.get('project'), RULES)
    result['category'] = rules.get(item, ('其他', ''))[0]
    missing = result.get('data_state') in {'missing', 'unknown', 'partial', 'empty'} or '数据缺失' in title
    rule_key = f'missing.{item}' if missing else item
    result['rule_key'] = rule_key
    if result.get('severity') in LEVELS:
        severity = result['severity']
    elif missing:
        severity = 'info'
    elif title in {'域信任问题', 'CPU温度问题'}:
        severity = 'critical'
    else:
        severity = result.get('severity', 'warning')
    if severity not in LEVELS:
        severity = 'warning'
    if overrides is not None and overrides.get(rule_key) in LEVELS:
        severity = overrides[rule_key]
    result.update(severity=severity, severity_label=LEVELS[severity])
    return result


def severity_counts(issues):
    counts = dict.fromkeys(LEVELS, 0)
    for issue in issues:
        counts[grade_issue(issue)['severity']] += 1
    return counts

def issue_categories(issues):
    return '、'.join(sorted({grade_issue(issue)['category'] for issue in issues}))


REPORT_FIELDS = ('report_metrics', 'report_problem_types', 'report_severity', 'report_enrichment')


def record_report_values(record):
    """Small report projection, computed at ingestion without querying relations."""
    details = record.details if isinstance(record.details, dict) else {}
    if hasattr(record, 'exceptions'):
        findings = record.exceptions or []
    else:
        findings = details.get('issue_findings') or []
        if not findings and (not record.is_reachable or record.status != 'success'):
            findings = [{'analysis_item': 'inspection_collection', 'severity': 'critical'}]
    enrichment = details.get('enrichment') or {}
    return {
        'report_metrics': key_metrics(details),
        'report_problem_types': issue_categories(findings),
        'report_severity': max((grade_issue(issue)['severity'] for issue in findings),
                               key=RANK.get, default=''),
        'report_enrichment': {key: enrichment[key] for key in (
            'personnel_id', 'employee_number', 'personnel_name', 'department',
            'user_ou', 'computer_ou', 'site',
        ) if key in enrichment},
    }
