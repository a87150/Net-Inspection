"""Persist trusted, static inventory values from normalized collection results."""

from decimal import Decimal, InvalidOperation
import re

from net.models import Computer, Network_Device, SecurityDevice, Server


_SOURCE_FIELD_ALLOWLIST = {
    Computer: (
        'manufacturer', 'model', 'serial_number', 'architecture', 'cpu_model',
        'cpu_physical_core_count', 'cpu_logical_processor_count',
        'memory_total_gb', 'disk_total_gb',
    ),
    Network_Device: (
        'os_version',
        'cpu_model', 'memory_total_gb', 'disk_total_gb',
        'port_count', 'vlan_count',
    ),
    Server: (
        'os_version', 'os_build', 'system_installed_at', 'manufacturer',
        'model', 'serial_number', 'architecture', 'cpu_model',
        'cpu_physical_core_count', 'cpu_logical_processor_count',
        'memory_total_gb', 'disk_total_gb',
    ),
    SecurityDevice: ('cpu_model', 'memory_total_gb', 'disk_total_gb'),
}
_COUNT_FIELDS = {
    'cpu_physical_core_count', 'cpu_logical_processor_count',
    'port_count', 'vlan_count',
}
_CAPACITY_FIELDS = {'memory_total_gb', 'disk_total_gb'}
_GIB = Decimal(1024 ** 3)


def _linux_static_inventory(result):
    """Extract only stable Linux facts from independently collected evidence."""
    values = {}
    system = result.get('system_info')
    if isinstance(system, dict):
        release_values = {match.group(1): match.group(2).strip().strip('"') for match in re.finditer(r'(?m)^([A-Z][A-Z0-9_]*)=(.*)$', str(system.get('os_release', '')))}
        if release_values.get('PRETTY_NAME'):
            values['os_version'] = release_values['PRETTY_NAME']
        uname = str(system.get('uname', '')).split()
        if len(uname) >= 3 and uname[0].lower() == 'linux':
            values['os_build'] = uname[2]
    cpu = result.get('cpu')
    if isinstance(cpu, dict):
        cpu_fields = {match.group(1).strip(): match.group(2).strip() for match in re.finditer(r'(?m)^\s*([^:\n]+):\s*(.+?)\s*$', str(cpu.get('raw', '')))}
        for source, target in (('Architecture', 'architecture'), ('Model name', 'cpu_model'), ('CPU(s)', 'cpu_logical_processor_count')):
            if cpu_fields.get(source):
                values[target] = cpu_fields[source]
        cores = _value_for_field('cpu_physical_core_count', cpu_fields.get('Core(s) per socket'))
        sockets = _value_for_field('cpu_physical_core_count', cpu_fields.get('Socket(s)'))
        if cores is not None and sockets is not None:
            values['cpu_physical_core_count'] = cores * sockets
    memory = result.get('memory')
    if isinstance(memory, dict):
        total = _value_for_field('memory_total_gb', memory.get('total_bytes'))
        if total is not None:
            values['memory_total_gb'] = total / _GIB
    return values

def _windows_static_inventory(result):
    values = {}
    system = result.get('system_info')
    if isinstance(system, dict):
        for source, target in (('caption', 'os_version'), ('build_number', 'os_build'), ('architecture', 'architecture')):
            value = system.get(source)
            if isinstance(value, str) and value.strip():
                values[target] = value.strip()
    cpu = result.get('cpu')
    if isinstance(cpu, dict):
        if isinstance(cpu.get('model'), str) and cpu['model'].strip():
            values['cpu_model'] = cpu['model'].strip()
        for source, target in (('physical_cores', 'cpu_physical_core_count'), ('logical_processors', 'cpu_logical_processor_count')):
            value = cpu.get(source)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < 1000000 and int(value) == value:
                values[target] = int(value)
    memory = result.get('memory')
    if isinstance(memory, dict):
        total = _positive_bytes(memory.get('total_bytes'))
        if total is not None:
            values['memory_total_gb'] = (total / _GIB).quantize(Decimal('.01'))
    disks = result.get('physical_disks')
    if isinstance(disks, list) and disks:
        seen, total = set(), Decimal(0)
        for disk in disks:
            if not isinstance(disk, dict):
                break
            device = disk.get('device')
            size = _positive_bytes(disk.get('total_bytes'))
            if not isinstance(device, str) or not device.strip() or device.casefold() in seen or size is None:
                break
            seen.add(device.casefold())
            total += size
        else:
            values['disk_total_gb'] = (total / _GIB).quantize(Decimal('.01'))
    return values


def _positive_bytes(value):
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and 0 < number < 10 ** 18 and number == number.to_integral_value() else None


def _value_for_field(field_name, value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if field_name in _COUNT_FIELDS:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number >= 0 else None
    if field_name in _CAPACITY_FIELDS:
        try:
            number = Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            return None
        return number if number >= 0 else None
    return str(value).strip()


def refresh_asset_inventory(asset, normalized_result) -> set[str]:
    """Update only explicit static fields; utilization values are never read."""
    if not isinstance(normalized_result, dict):
        return set()
    allowed_fields = _SOURCE_FIELD_ALLOWLIST.get(type(asset), ())
    source = dict(normalized_result)
    if isinstance(asset, Server) and str(getattr(asset, 'server_type', '')).lower() == 'linux':
        source.update(_linux_static_inventory(normalized_result))
    if isinstance(asset, Server) and str(getattr(asset, 'server_type', '')).lower() == 'windows':
        source.update(_windows_static_inventory(normalized_result))
    if isinstance(asset, Network_Device):
        info = normalized_result.get('device_info')
        if isinstance(info, dict) and info.get('status', 'success') == 'success':
            for key in ('version', 'os_version', 'version_output', 'description'):
                value = info.get(key)
                if isinstance(value, str) and value.strip():
                    source['os_version'] = ' '.join(value.split())[:1024]
                    break
    changed_fields = set()
    for field_name in allowed_fields:
        value = _value_for_field(field_name, source.get(field_name))
        if value is None or getattr(asset, field_name) == value:
            continue
        setattr(asset, field_name, value)
        changed_fields.add(field_name)
    if changed_fields:
        asset.save(update_fields=sorted(changed_fields))
    return changed_fields
