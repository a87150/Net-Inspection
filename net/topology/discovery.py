"""Deterministic, I/O-free physical topology endpoint resolution."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
import hashlib
import re


def _value(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def _norm_name(value):
    return re.sub(r'\s+', '', str(value or '')).casefold()


def _norm_mac(value):
    compact = re.sub(r'[^0-9a-f]', '', str(value or '').casefold())
    return ':'.join(compact[i:i + 2] for i in range(0, 12, 2)) if len(compact) == 12 else ''


@dataclass(frozen=True)
class InterfaceCandidate:
    if_index: int | None = None
    name: str = ''
    description: str = ''
    mac_address: str = ''
    admin_status: str = 'unknown'
    oper_status: str = 'unknown'
    speed_bps: int | None = None
    vlan_ids: tuple[int, ...] = ()
    stable_key_hint: str = ''


@dataclass(frozen=True)
class NeighborCandidate:
    local_key_hint: str = ''
    local_port_id: str = ''
    local_port_description: str = ''
    remote_chassis_id: str = ''
    remote_port_id: str = ''
    remote_port_description: str = ''
    remote_system_name: str = ''
    remote_management_addresses: tuple[str, ...] = ()
    protocol: str = 'lldp'
    evidence: object = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedObservation:
    local_device_id: object
    local_interface_key: str
    remote_device_id: object | None
    remote_interface_key: str | None
    remote_chassis_id: str
    remote_port_id: str
    remote_port_description: str
    remote_system_name: str
    remote_management_addresses: tuple[str, ...]
    protocols: tuple[str, ...]
    protocol: str
    resolution_status: str
    confidence: Decimal
    stable_link_key: str
    evidence: object = field(default_factory=dict)


@dataclass(frozen=True)
class DiscoveryPayload:
    status: str
    complete: bool
    protocols: tuple[str, ...]
    interfaces: tuple[InterfaceCandidate, ...]
    observations: tuple[ResolvedObservation, ...]
    collected_at: object
    message: str = ''


def stable_interface_key(candidate: InterfaceCandidate) -> str:
    if candidate.if_index is not None:
        return f'ifindex:{int(candidate.if_index)}'
    mac = _norm_mac(candidate.mac_address)
    if mac:
        return 'mac:' + mac
    name = _norm_name(candidate.name or candidate.stable_key_hint.removeprefix('name:'))
    if name:
        return 'name:' + name
    hint = _norm_name(candidate.stable_key_hint)
    return 'hint:' + hashlib.sha256(hint.encode()).hexdigest()[:24]


def stable_link_key(observation) -> str:
    local = f"{_value(observation, 'local_device_id')}:{_value(observation, 'local_interface_key')}"
    remote_device = _value(observation, 'remote_device_id')
    remote_interface = _value(observation, 'remote_interface_key')
    if remote_device is not None:
        remote = f'{remote_device}:{remote_interface or _norm_name(_value(observation, "remote_port_id"))}'
        identity = '|'.join(sorted((local, remote)))
    else:
        identity = '|'.join((local, _norm_mac(_value(observation, 'remote_chassis_id')),
                             _norm_name(_value(observation, 'remote_port_id')),
                             _norm_name(_value(observation, 'remote_system_name')),
                             str(_value(observation, 'protocol', 'lldp'))))
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()


def _candidate_interface(value):
    vlans = tuple(sorted({int(item) for item in (_value(value, 'vlan_ids', []) or []) if str(item).isdigit()}))
    return InterfaceCandidate(
        if_index=_value(value, 'if_index'), name=str(_value(value, 'name', '') or ''),
        description=str(_value(value, 'description', '') or ''),
        mac_address=_norm_mac(_value(value, 'mac_address', _value(value, 'mac', ''))),
        admin_status=str(_value(value, 'admin_status', 'unknown') or 'unknown'),
        oper_status=str(_value(value, 'oper_status', 'unknown') or 'unknown'),
        speed_bps=_value(value, 'speed_bps'), vlan_ids=vlans,
        stable_key_hint=str(_value(value, 'stable_key_hint', '') or ''),
    )


def _resolve_neighbor(neighbor, known_devices, known_interfaces):
    chassis = _norm_mac(_value(neighbor, 'remote_chassis_id', ''))
    addresses = tuple(dict.fromkeys(str(item) for item in (_value(neighbor, 'remote_management_addresses', []) or [])))
    system_name = _norm_name(_value(neighbor, 'remote_system_name', ''))
    tiers = []
    if chassis:
        tiers.append({str(_value(item, 'device_id')) for item in known_interfaces if _norm_mac(_value(item, 'mac_address', '')) == chassis})
    if addresses:
        tiers.append({str(_value(item, 'pk')) for item in known_devices if str(_value(item, 'ip', '')) in addresses})
    if system_name:
        tiers.append({str(_value(item, 'pk')) for item in known_devices
                      if _norm_name(_value(item, 'device_name', _value(item, 'name', ''))) == system_name})
    device_id = None
    status = 'unresolved'
    confidence = Decimal('0.60') if chassis or addresses else Decimal('0.40')
    for matches in tiers:
        matches.discard('None')
        if len(matches) > 1:
            return None, None, 'conflict', Decimal('0.25')
        if len(matches) == 1:
            value = next(iter(matches))
            device_id = next((_value(item, 'pk') for item in known_devices if str(_value(item, 'pk')) == value), value)
            status, confidence = 'resolved', Decimal('0.75')
            break
    remote_key = None
    if device_id is not None:
        port = _norm_name(_value(neighbor, 'remote_port_id', ''))
        choices = [item for item in known_interfaces if str(_value(item, 'device_id')) == str(device_id)
                   and port and _norm_name(_value(item, 'name', '')) == port]
        if len(choices) > 1:
            return None, None, 'conflict', Decimal('0.25')
        if len(choices) == 1:
            remote_key = str(_value(choices[0], 'stable_key'))
            confidence = Decimal('0.90')
    return device_id, remote_key, status, confidence


def build_discovery_payload(device, protocol_results, known_devices, known_interfaces, collected_at) -> DiscoveryPayload:
    interfaces = {}
    raw_observations = []
    protocols = set()
    complete = True
    statuses = []
    for result in protocol_results:
        status = str(_value(result, 'status', 'failed'))
        statuses.append(status)
        complete = complete and bool(_value(result, 'complete', False))
        protocols.update(_value(result, 'protocols', []) or [])
        evidence = _value(result, 'evidence', {})
        for value in _value(result, 'interfaces', []) or []:
            candidate = _candidate_interface(value)
            interfaces[stable_interface_key(candidate)] = candidate
        for value in _value(result, 'neighbors', []) or []:
            local_hint = str(_value(value, 'local_key_hint', '') or '')
            local_name = str(_value(value, 'local_port_id', '') or '')
            candidate = _candidate_interface({'stable_key_hint': local_hint, 'name': local_name,
                                              'description': _value(value, 'local_port_description', '')})
            local_key = local_hint if local_hint.startswith(('ifindex:', 'mac:', 'name:', 'lldp-port:')) else stable_interface_key(candidate)
            if local_key.startswith('lldp-port:'):
                matching = next((key for key, item in interfaces.items() if item.stable_key_hint == local_key), None)
                local_key = matching or local_key
            interfaces.setdefault(local_key, candidate)
            raw_observations.append((local_key, value, evidence))
    merged = {}
    for local_key, neighbor, evidence in raw_observations:
        device_id, remote_key, resolution, confidence = _resolve_neighbor(neighbor, known_devices, known_interfaces)
        protocol = str(_value(neighbor, 'protocol', 'lldp') or 'lldp')
        draft = ResolvedObservation(
            local_device_id=_value(device, 'pk'), local_interface_key=local_key,
            remote_device_id=device_id, remote_interface_key=remote_key,
            remote_chassis_id=_norm_mac(_value(neighbor, 'remote_chassis_id', '')) or str(_value(neighbor, 'remote_chassis_id', '') or ''),
            remote_port_id=str(_value(neighbor, 'remote_port_id', '') or ''),
            remote_port_description=str(_value(neighbor, 'remote_port_description', '') or ''),
            remote_system_name=str(_value(neighbor, 'remote_system_name', '') or ''),
            remote_management_addresses=tuple(_value(neighbor, 'remote_management_addresses', []) or []),
            protocols=(protocol,), protocol=protocol, resolution_status=resolution,
            confidence=confidence, stable_link_key='', evidence=evidence,
        )
        draft = replace(draft, stable_link_key=stable_link_key(draft))
        previous = merged.get(draft.stable_link_key)
        if previous:
            draft = replace(previous, protocols=tuple(sorted(set(previous.protocols) | {protocol})),
                            confidence=max(previous.confidence, draft.confidence))
        merged[draft.stable_link_key] = draft
    if statuses and all(status == 'success' for status in statuses) and complete:
        status = 'success'
    elif any(status in {'success', 'partial'} for status in statuses):
        status = 'partial'
    else:
        status = 'failed'
    return DiscoveryPayload(status=status, complete=complete and status == 'success', protocols=tuple(sorted(protocols)),
                            interfaces=tuple(interfaces.values()), observations=tuple(merged.values()),
                            collected_at=collected_at,
                            message='' if status == 'success' else '拓扑证据未完整采集。')
