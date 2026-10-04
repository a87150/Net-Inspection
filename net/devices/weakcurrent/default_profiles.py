"""Version-scoped, editable defaults for weak-current devices. Mirrors the
network defaults: idempotent creation that never overwrites an administrator's
template.

Weak-current gear has no CLI to template -- it is reached over SNMP, a vendor
HTTP API or plain Ping -- so the useful default is the standard SNMP mapping
and which inspection items each subtype actually has, not a command list.
"""
from copy import deepcopy

from net.devices.collection_profiles import SUBTYPES

# Inspection items are net.inspections.issues.PROJECT_RULES['weakcurrent'].
# item_methods is deliberately left unset: 'auto' already walks the documented
# SNMP -> vendor API -> Ping fallback, so naming a method here would only
# disable a fallback the operator probably wants.

# Channels only exist where the subtype actually has them: an NVR multiplexes
# cameras, a door controller drives turnstiles, a plain sensor has neither.
SUBTYPE_ITEMS = {
    'nvr': ('device_info', 'status_data', 'channel_status', 'storage_status'),
    'camera': ('device_info', 'status_data'),
    'access': ('device_info', 'status_data', 'channel_status'),
    'intercom': ('device_info', 'status_data'),
    'broadcast': ('device_info', 'status_data'),
    'printer': ('device_info', 'status_data'),
    'environment': ('device_info', 'status_data'),
    'other': ('device_info', 'status_data'),
}
# 标签只有 SUBTYPES 一份来源；这里派生而不是手抄，否则模板名会和界面对不上
# （历史上这里写的是「摄像机」，界面用的是「监控摄像头」）。
TYPES = dict(SUBTYPES['weakcurrent'][1:])

# Standard MIB scalars every SNMP-capable box exposes. cpu/memory/temperature
# stay unset on purpose: weak-current hardware does not implement
# HOST-RESOURCES-MIB reliably, and a wrong OID reads worse than a missing one.
SNMP_OIDS = {
    'sys_descr': '1.3.6.1.2.1.1.1.0',
    'sys_object_id': '1.3.6.1.2.1.1.2.0',
    'sys_uptime': '1.3.6.1.2.1.1.3.0',
    'sys_name': '1.3.6.1.2.1.1.5.0',
}


def base_settings():
    """Every subtype shares this base; children only enable what applies."""
    return {
        'version': 1, 'vendor': '', 'subtype': '',
        'snmp_oids': deepcopy(SNMP_OIDS),
    }


def subtype_settings(subtype):
    """Enable the items this subtype has and disable the ones it cannot."""
    active = SUBTYPE_ITEMS[subtype]
    return {
        'subtype': subtype,
        'item_enabled': {
            item: item in active
            for item in ('device_info', 'status_data', 'channel_status',
                         'storage_status', 'config_info')
        },
    }


def create_default_weak_current_templates():
    """Idempotently create one base and one child per subtype."""
    from django.db import transaction
    from net.models import DeviceCollectionTemplate
    created = []
    with transaction.atomic():
        base = DeviceCollectionTemplate.objects.filter(
            kind='weakcurrent', vendor='', subtype='', version_match='',
        ).first()
        if base is None:
            base = DeviceCollectionTemplate(
                name='弱电设备 基础模板', kind='weakcurrent', vendor='',
                subtype='', settings=base_settings(),
            )
            base.full_clean()
            base.save()
            created.append(base)
        for subtype, label in TYPES.items():
            if DeviceCollectionTemplate.objects.filter(
                    kind='weakcurrent', vendor='', subtype=subtype,
                    version_match='').exists():
                continue
            row = DeviceCollectionTemplate(
                name='弱电设备 ' + label, kind='weakcurrent', vendor='',
                subtype=subtype, parent=base, settings=subtype_settings(subtype),
            )
            row.full_clean()
            row.save()
            created.append(row)
    return created
