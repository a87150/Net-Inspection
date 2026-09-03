"""Persist trusted, static inventory values from normalized collection results."""

from decimal import Decimal, InvalidOperation

from net.models import Computer, Network_Device, SecurityDevice, Server


_SOURCE_FIELD_ALLOWLIST = {
    Computer: (
        'manufacturer', 'model', 'serial_number', 'architecture', 'cpu_model',
        'cpu_physical_core_count', 'cpu_logical_processor_count',
        'memory_total_gb', 'disk_total_gb',
    ),
    Network_Device: (
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
    changed_fields = set()
    for field_name in allowed_fields:
        value = _value_for_field(field_name, normalized_result.get(field_name))
        if value is None or getattr(asset, field_name) == value:
            continue
        setattr(asset, field_name, value)
        changed_fields.add(field_name)
    if changed_fields:
        asset.save(update_fields=sorted(changed_fields))
    return changed_fields
