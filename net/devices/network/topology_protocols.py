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




_RECORD_ALIASES = {
    'local_port_id': ('local_port_id', 'local_interface', 'local_intf', 'local_port', 'local_port'),
    'local_port_description': ('local_port_description',),
    'remote_chassis_id': ('remote_chassis_id', 'chassis_id', 'neighbor_chassis_id'),
    'remote_port_id': ('remote_port_id', 'neighbor_interface', 'neighbor_port_id', 'port_id'),
    'remote_port_description': ('remote_port_description', 'neighbor_port_description', 'port_description'),
    'remote_system_name': ('remote_system_name', 'system_name', 'neighbor_name', 'neighbor', 'device_id'),
    'remote_management_addresses': ('remote_management_addresses', 'management_addresses', 'management_address', 'mgmt_address', 'ip_address'),
}
_EMPTY_TOPOLOGY = re.compile(r'(?im)(?:total\s+(?:number\s+of\s+)?neighbors?\s*[:=]\s*0|no\s+(?:lldp|cdp)\s+neighbors?)')
_INTERFACE = re.compile(r'^(?:[A-Za-z-]*Ethernet|Eth|GE|Gi|Te|Fa|XGE|Ten|Gig|Hu)\S+$', re.I)


def _first(row, names):
    lowered = {str(key).lower(): value for key, value in row.items()}
    return next((lowered[name] for name in names if lowered.get(name) not in (None, '')), '')


def _record_neighbor(row, protocol):
    neighbor = {name: _first(row, aliases) for name, aliases in _RECORD_ALIASES.items()}
    neighbor['remote_chassis_id'] = _identity(neighbor['remote_chassis_id'])
    addresses = neighbor['remote_management_addresses']
    if isinstance(addresses, str):
        addresses = [part.strip() for part in re.split(r'[,;\s]+', addresses) if part.strip()]
    elif not isinstance(addresses, list):
        addresses = []
    neighbor['remote_management_addresses'] = addresses[:MAX_ADDRESSES]
    neighbor['protocol'] = protocol
    for field in ('local_port_id', 'local_port_description', 'remote_port_id', 'remote_port_description', 'remote_system_name'):
        neighbor[field] = _text(neighbor[field])[:500 if 'description' in field else 255]
    if not neighbor['local_port_id'] or not any(neighbor[name] for name in (
        'remote_chassis_id', 'remote_port_id', 'remote_system_name', 'remote_management_addresses'
    )):
        return None
    neighbor['local_key_hint'] = 'name:' + neighbor['local_port_id'].casefold()
    return neighbor


def _raw_neighbors(command, output, vendor):
    protocol = 'cdp' if 'cdp' in command.casefold() else 'lldp'
    result = []
    for line in str(output).splitlines():
        tokens = line.split()
        if len(tokens) < 4 or line.lstrip().startswith(('-', '=')):
            continue
        if vendor in {'huawei', 'h3c'} and _INTERFACE.match(tokens[0]):
            row = {'local_port_id': tokens[0], 'remote_chassis_id': tokens[1],
                   'remote_port_id': tokens[2], 'remote_system_name': tokens[3]}
        elif len(tokens) >= 4 and _INTERFACE.match(tokens[1]) and _INTERFACE.match(tokens[-1]):
            row = {'remote_system_name': tokens[0], 'local_port_id': tokens[1],
                   'remote_port_id': tokens[-1]}
        else:
            continue
        neighbor = _record_neighbor(row, protocol)
        if neighbor:
            result.append(neighbor)
    return result


def normalize_ssh_neighbors(value: object, raw: dict, vendor: str) -> dict:
    """Normalize validated LLDP/CDP template records and bounded raw evidence."""
    vendor = (vendor or '').strip().lower()
    if isinstance(value, dict) and 'records' in value:
        records = value.get('records') or []
        inherited_partial = value.get('status') == 'partial'
    elif isinstance(value, list):
        records, inherited_partial = value, False
    elif isinstance(value, dict) and value and not value.get('status'):
        records, inherited_partial = [value], False
    else:
        records, inherited_partial = [], isinstance(value, dict) and value.get('status') == 'partial'
    protocols = set()
    neighbors = []
    for row in records[:MAX_NEIGHBORS]:
        if not isinstance(row, dict):
            continue
        protocol = 'cdp' if str(row.get('protocol', '')).casefold() == 'cdp' else 'lldp'
        neighbor = _record_neighbor(row, protocol)
        if neighbor:
            neighbors.append(neighbor)
            protocols.add('ssh_' + protocol)
    relevant_raw = {}
    any_nonempty = False
    explicit_empty = False
    for command, output in (raw or {}).items():
        if not isinstance(output, str) or not any(name in str(command).casefold() for name in ('lldp', 'cdp')):
            continue
        text = output[:MAX_EVIDENCE_BYTES]
        relevant_raw[str(command)[:255]] = text
        any_nonempty = any_nonempty or bool(text.strip())
        explicit_empty = explicit_empty or bool(_EMPTY_TOPOLOGY.search(text))
        for neighbor in _raw_neighbors(str(command), text, vendor):
            marker = json.dumps(neighbor, ensure_ascii=False, sort_keys=True)
            if marker not in {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in neighbors}:
                neighbors.append(neighbor)
            protocols.add('ssh_' + neighbor['protocol'])
            if len(neighbors) >= MAX_NEIGHBORS:
                break
    complete = bool(neighbors) and not inherited_partial or explicit_empty and not inherited_partial
    status = 'success' if complete else 'partial' if neighbors else 'failed'
    interfaces = []
    seen = set()
    for neighbor in neighbors:
        name = neighbor['local_port_id']
        key = name.casefold()
        if key not in seen:
            interfaces.append({'if_index': None, 'stable_key_hint': 'name:' + key, 'name': name,
                               'description': neighbor['local_port_description']})
            seen.add(key)
    evidence = _bounded_evidence({'raw': relevant_raw, 'record_count': len(records), 'parsed_count': len(neighbors)})
    return {'status': status, 'complete': complete, 'protocols': sorted(protocols),
            'interfaces': interfaces, 'neighbors': neighbors, 'evidence': evidence,
            'message': '' if complete else ('SSH 拓扑回显未完整解析。' if any_nonempty else 'SSH 拓扑命令未返回有效证据。')}
