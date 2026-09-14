from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
import re

from net.models import Computer, ComputerAnalysis


SNAPSHOT_FIELD_NAMES = (
    'login_account',
    'ip_addresses',
    'mac_addresses',
    'os_version',
    'os_build',
    'system_installed_at',
    'manufacturer',
    'model',
    'serial_number',
    'architecture',
    'cpu_model',
    'cpu_physical_core_count',
    'cpu_logical_processor_count',
    'memory_total_gb',
    'disk_total_gb',
)


def _text(value: object) -> str:
    return '' if value is None else str(value).strip()


def _join_unique(values: Iterable[object]) -> str:
    unique_values = []
    for value in values:
        text = _text(value)
        if text and text not in unique_values:
            unique_values.append(text)
    return ', '.join(unique_values)


def _first_text(source, *keys) -> str:
    if not isinstance(source, dict):
        return ''
    for key in keys:
        value = _text(source.get(key))
        if value:
            return value
    return ''


def _non_negative_integer(value: object):
    text = _text(value)
    if not text:
        return None
    try:
        number = int(text)
    except ValueError:
        return None
    return number if number >= 0 else None


def _gib(value: object):
    text = _text(value).lower().replace(' ', '')
    if re.fullmatch(r'[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?(?:tib|tb|gib|gb)?', text):
        text = text.replace(',', '')
    if not text:
        return None
    match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)(tib|tb|gib|gb)?', text)
    if not match:
        return None
    try:
        number = Decimal(match.group(1))
    except InvalidOperation:
        return None
    if number < 0:
        return None
    if match.group(2) in {'tib', 'tb'}:
        number *= 1024
    return number.quantize(Decimal('0.01'))


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def extract_computer_snapshot(
    system_info: object,
    network_info: object,
    computer_info: object,
    disk_payload=None,
) -> dict[str, object]:
    system = system_info if isinstance(system_info, dict) else {}
    adapters = network_info if isinstance(network_info, list) else []
    hardware = computer_info if isinstance(computer_info, dict) else {}
    snapshot = {
        'login_account': _text(system.get('当前登录用户工号')),
        'ip_addresses': _join_unique(adapter.get('IP地址') for adapter in adapters if isinstance(adapter, dict)),
        'mac_addresses': _join_unique(adapter.get('MAC地址') for adapter in adapters if isinstance(adapter, dict)),
        'os_version': _text(system.get('系统主要版本名')),
        'os_build': _text(system.get('系统详细版本')),
        'system_installed_at': _text(system.get('系统安装日期')),
    }
    inventory = {
        'manufacturer': _first_text(system, '制造商', 'manufacturer') or _first_text(hardware, '制造商', 'manufacturer'),
        'model': _first_text(system, '型号', 'model') or _first_text(hardware, '型号', 'model'),
        'serial_number': _first_text(system, '序列号', 'BIOS序列号', 'serial_number') or _first_text(hardware, '序列号', 'serial_number'),
        'architecture': _first_text(system, '系统架构', 'architecture') or _first_text(hardware, '系统架构', 'architecture'),
        'cpu_model': _first_text(hardware, 'CPU型号', 'CPU 型号', 'cpu_model'),
        'cpu_physical_core_count': _non_negative_integer(_first_text(
            hardware,
            'CPU物理核心数', 'CPU 物理核心数', '物理核心数',
            'cpu_physical_core_count', 'physical_core_count',
            'CPU核心数', 'CPU 核心数', 'cpu_core_count',
        )),
        'cpu_logical_processor_count': _non_negative_integer(_first_text(
            hardware,
            'CPU逻辑处理器数', 'CPU 逻辑处理器数', '逻辑处理器数',
            'cpu_logical_processor_count', 'logical_processor_count',
        )),
        'memory_total_gb': _gib(_first_text(hardware, '当前内存容量', '内存总量', 'memory_total_gb')),
        'disk_total_gb': _gib(_first_text(hardware, '磁盘总量', '磁盘总容量', 'disk_total_gb')),
    }
    if inventory['disk_total_gb'] is None and isinstance(disk_payload, dict):
        from net.devices.pc.disk import total_gib
        inventory['disk_total_gb'] = total_gib(disk_payload)
    snapshot.update({key: value for key, value in inventory.items() if not _is_blank(value)})
    return snapshot


def update_computer_snapshot(computer: Computer, analysis: ComputerAnalysis) -> Computer:
    system_info = analysis.details.get('system_info', {})
    network_info = analysis.details.get('network_info', [])
    computer_info = analysis.details.get('computer_info', {})
    snapshot = extract_computer_snapshot(
        system_info,
        network_info,
        computer_info,
    )
    changed_fields = [
        field_name
        for field_name, value in snapshot.items()
        if getattr(computer, field_name) != value
        and not (_is_blank(value) and not _is_blank(getattr(computer, field_name)))
    ]
    if changed_fields:
        for field_name in changed_fields:
            setattr(computer, field_name, snapshot[field_name])
        computer.save(update_fields=changed_fields)
    return computer
