"""PC logical volume capacity and usage; encryption percentage is never utilization."""
from decimal import Decimal, InvalidOperation


def volumes(payload):
    value = payload.get('磁盘空间情况')
    if value is not None:
        return value if isinstance(value, list) else None
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
