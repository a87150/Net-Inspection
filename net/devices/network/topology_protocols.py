"""Pure LLDP/CDP normalization shared by SNMP and SSH collectors."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re

MAX_NEIGHBORS = 2048
MAX_ADDRESSES = 32
MAX_EVIDENCE_BYTES = 64 * 1024

LLDP_LOCAL_PORT_ID = '1.0.8802.1.1.2.1.3.7.1.3'
LLDP_LOCAL_PORT_DESCRIPTION = '1.0.8802.1.1.2.1.3.7.1.4'
LLDP_REMOTE_CHASSIS_ID = '1.0.8802.1.1.2.1.4.1.1.5'
LLDP_REMOTE_PORT_ID = '1.0.8802.1.1.2.1.4.1.1.7'
LLDP_REMOTE_PORT_DESCRIPTION = '1.0.8802.1.1.2.1.4.1.1.8'
LLDP_REMOTE_SYSTEM_NAME = '1.0.8802.1.1.2.1.4.1.1.9'
LLDP_REMOTE_MANAGEMENT_ADDRESS = '1.0.8802.1.1.2.1.4.2.1.4'

LLDP_TABLE_GROUPS = {
    'local_ports': (LLDP_LOCAL_PORT_ID, LLDP_LOCAL_PORT_DESCRIPTION),
    'neighbors': (
        LLDP_REMOTE_CHASSIS_ID, LLDP_REMOTE_PORT_ID,
        LLDP_REMOTE_PORT_DESCRIPTION, LLDP_REMOTE_SYSTEM_NAME,
    ),
    'management': (LLDP_REMOTE_MANAGEMENT_ADDRESS,),
}
_ALIASES = {
    'local_port_id': LLDP_LOCAL_PORT_ID,
    'local_port_description': LLDP_LOCAL_PORT_DESCRIPTION,
    'remote_chassis_id': LLDP_REMOTE_CHASSIS_ID,
    'remote_port_id': LLDP_REMOTE_PORT_ID,
    'remote_port_description': LLDP_REMOTE_PORT_DESCRIPTION,
    'remote_system_name': LLDP_REMOTE_SYSTEM_NAME,
    'remote_management_address': LLDP_REMOTE_MANAGEMENT_ADDRESS,
}


def _text(value):
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace').strip('\x00 ')
    return str(value).strip()


def _identity(value):
    if isinstance(value, bytes) and len(value) == 6:
        return ':'.join(f'{part:02x}' for part in value)
    text = _text(value)
    compact = re.sub(r'[^0-9a-fA-F]', '', text)
    if len(compact) == 12:
        return ':'.join(compact[index:index + 2].lower() for index in range(0, 12, 2))
    return text[:255]


def _tables(snapshot):
    source = snapshot.get('tables', {}) if isinstance(snapshot, dict) else {}
    result = {}
    for name, oid in _ALIASES.items():
        result[oid] = list(source.get(oid, source.get(name, [])) or [])
    return result


def _indexed(rows, length):
    result = {}
    malformed = False
    for suffix, value in rows:
        parts = str(suffix).strip('.').split('.')
        if len(parts) < length or not all(part.isdigit() for part in parts[:length]):
            malformed = True
            continue
        result['.'.join(parts[:length])] = value
    return result, malformed


def _management_addresses(rows):
    result = {}
    malformed = False
    for suffix, _value in rows:
        parts = str(suffix).strip('.').split('.')
        if len(parts) < 6 or not all(part.isdigit() for part in parts):
            malformed = True
            continue
        key = '.'.join(parts[:3])
        subtype, length = int(parts[3]), int(parts[4])
        octets = [int(part) for part in parts[5:5 + length]]
        if len(octets) != length or any(part > 255 for part in octets):
            malformed = True
            continue
        try:
            if subtype == 1 and length == 4:
                address = str(ipaddress.IPv4Address(bytes(octets)))
            elif subtype == 2 and length == 16:
                address = str(ipaddress.IPv6Address(bytes(octets)))
            else:
                continue
        except ipaddress.AddressValueError:
            malformed = True
            continue
        values = result.setdefault(key, [])
        if address not in values and len(values) < MAX_ADDRESSES:
            values.append(address)
    return result, malformed


def _bounded_evidence(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode('utf-8')
    if len(encoded) > MAX_EVIDENCE_BYTES:
        return {'truncated': True, 'sha256': hashlib.sha256(encoded).hexdigest(), 'bytes': len(encoded)}
    return value


def parse_lldp_snapshot(snapshot: dict, table_states: dict) -> dict:
    """Return normalized LLDP evidence without treating failed walks as empty."""
    tables = _tables(snapshot)
    local_ids, bad_local_id = _indexed(tables[LLDP_LOCAL_PORT_ID], 1)
    local_desc, bad_local_desc = _indexed(tables[LLDP_LOCAL_PORT_DESCRIPTION], 1)
    chassis, bad_chassis = _indexed(tables[LLDP_REMOTE_CHASSIS_ID], 3)
    port_ids, bad_port = _indexed(tables[LLDP_REMOTE_PORT_ID], 3)
    port_desc, bad_port_desc = _indexed(tables[LLDP_REMOTE_PORT_DESCRIPTION], 3)
    system_names, bad_name = _indexed(tables[LLDP_REMOTE_SYSTEM_NAME], 3)
    addresses, bad_address = _management_addresses(tables[LLDP_REMOTE_MANAGEMENT_ADDRESS])
    malformed = any((bad_local_id, bad_local_desc, bad_chassis, bad_port, bad_port_desc, bad_name, bad_address))

    interfaces = []
    for index in sorted(set(local_ids) | set(local_desc), key=lambda value: int(value)):
        interfaces.append({
            'if_index': None,
            'local_port_number': int(index),
            'stable_key_hint': f'lldp-port:{index}',
            'name': _text(local_ids.get(index)),
            'description': _text(local_desc.get(index)),
        })
    neighbors = []
    remote_keys = set(chassis) | set(port_ids) | set(port_desc) | set(system_names) | set(addresses)
    for key in sorted(remote_keys, key=lambda value: tuple(int(part) for part in value.split('.')))[:MAX_NEIGHBORS]:
        _time_mark, local_number, _remote_index = key.split('.')
        neighbor = {
            'local_key_hint': f'lldp-port:{local_number}',
            'local_port_id': _text(local_ids.get(local_number)),
            'local_port_description': _text(local_desc.get(local_number)),
            'remote_chassis_id': _identity(chassis.get(key)),
            'remote_port_id': _text(port_ids.get(key))[:255],
            'remote_port_description': _text(port_desc.get(key))[:500],
            'remote_system_name': _text(system_names.get(key))[:255],
            'remote_management_addresses': addresses.get(key, []),
            'protocol': 'lldp',
        }
        if neighbor['remote_chassis_id'] or neighbor['remote_port_id'] or neighbor['remote_system_name'] or neighbor['remote_management_addresses']:
            neighbors.append(neighbor)

    states = {name: table_states.get(name, 'not_started') for name in LLDP_TABLE_GROUPS}
    complete = all(value == 'success' for value in states.values()) and not malformed
    any_success = any(value == 'success' for value in states.values())
    status = ('success' if complete else 'failed' if states['neighbors'] in {'timeout', 'failed', 'not_started'}
              else 'partial' if any_success else 'failed')
    evidence = _bounded_evidence({'table_states': states, 'interfaces': interfaces, 'neighbors': neighbors})
    return {
        'status': status,
        'complete': complete,
        'protocols': ['snmp_lldp'],
        'interfaces': interfaces,
        'neighbors': neighbors,
        'evidence': evidence,
    }



