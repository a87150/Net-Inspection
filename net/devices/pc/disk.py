"""PC logical volume capacity and usage; encryption percentage is never utilization."""
from decimal import Decimal, InvalidOperation
import re


def volumes(payload):
    hardware = payload.get('计算机硬件资源情况')
    if isinstance(hardware, dict) and '磁盘卷信息' in hardware:
        value = hardware['磁盘卷信息']
        return value if isinstance(value, list) else None
    value = payload.get('磁盘空间情况')
    if value is not None:
        return value if isinstance(value, list) else None
    if isinstance(hardware, dict):
        summary = hardware.get('磁盘摘要') or hardware.get('disk_summary')
        if summary:
            return _summary_volumes(summary)
    bitlocker = payload.get('BitLocker状态')
    value = bitlocker.get('磁盘卷信息') if isinstance(bitlocker, dict) else None
    if not isinstance(value, list):
        return None
    from net.devices.pc.snapshot import _gib
    result = []
    for row in value:
        if not isinstance(row, dict):
            result.append({})
            continue
        size = _gib(row.get('大小'))
        result.append({'device': row.get('卷'), 'total_bytes': int(size * 1024**3) if size is not None else None})
    return result


_SUMMARY_VOLUME = re.compile(
    r'(?P<device>[^;]+?)\s+(?P<total>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<total_unit>GiB|GB|B)'
    r'\s*\(\s*(?P<free>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<free_unit>GiB|GB|B)\s+free\s*\)', re.I)


def _summary_volumes(summary):
    """New summaries use exact bytes; old GB summaries retain their source precision."""
    if not isinstance(summary, str):
        return None
    result = []
    for fragment in summary.split(';'):
        match = _SUMMARY_VOLUME.fullmatch(fragment.strip())
        if not match:
            result.append({})  # Keep incomplete volumes visible to missing-data checks.
            continue
        row = {'device': match['device'].strip()}
        for field in ('total', 'free'):
            value = number(match[field].replace(',', ''))
            factor = 1 if match[field + '_unit'].upper() == 'B' else 1024**3
            row[field + '_bytes'] = int((value * factor).to_integral_value()) if value is not None else None
        result.append(row)
    return result


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = Decimal(str(value))
    except (ValueError, InvalidOperation):
        return None
    return value if value.is_finite() and 0 <= value < 10**18 else None


def total_gib(payload):
    rows = volumes(payload)
    if not rows:
        return None
    seen, total = set(), Decimal(0)
    for row in rows:
        if not isinstance(row, dict):
            return None
        device = str(row.get('device') or '').strip().rstrip(':').casefold()
        size = number(row.get('total_bytes'))
        if not device or device in seen or size is None or size <= 0:
            return None
        seen.add(device)
        total += size
    return (total / 1024**3).quantize(Decimal('.01'))


def check_disk(payload, issues, maximum):
    rows = volumes(payload)
    details = {'volumes': rows, 'max_percent': maximum, 'data_state': 'known'}
    missing = not rows
    seen = set()
    for row in rows or []:
        if not isinstance(row, dict):
            missing = True
            continue
        device = str(row.get('device') or '').strip()
        key = device.rstrip(':').casefold()
        total, free = number(row.get('total_bytes')), number(row.get('free_bytes'))
        if not key or key in seen or total is None or total <= 0 or free is None or free > total:
            missing = True
            continue
        seen.add(key)
        used = (total - free) * 100 / total
        if used > maximum:
            issues.append({'问题类型': '磁盘空间不足', '详细问题': f'{device} 使用率 {used:.2f}% 超过阈值 {maximum}%'})
    if missing:
        details['data_state'] = 'unknown'
        issues.append({'问题类型': '磁盘空间数据缺失', '详细问题': '缺少有效的卷容量或剩余空间，请更新采集脚本并生成新日志。', 'data_state': 'unknown'})
    return details
