"""Build a presentation-safe enterprise network topology payload."""

import re
from ipaddress import ip_interface

from django.urls import reverse


ROLE_TIERS = {
    'firewall': 0,
    'router': 0,
    'core_switch': 1,
    'distribution_switch': 2,
    'access_switch': 3,
    'wireless_controller': 3,
    'network_other': 3,
}
DEFAULT_PREFIX_LENGTH = {4: 24, 6: 64}
ATTACHMENT_ROLE_PRIORITY = {
    'access_switch': 0,
    'wireless_controller': 1,
    'distribution_switch': 2,
    'core_switch': 3,
    'router': 4,
    'firewall': 5,
    'network_other': 6,
}


def _device_text(device):
    return ' '.join(
        str(value or '').strip() for value in (
            device.device_type, device.device_name, device.model,
        ) if str(value or '').strip()
    ).casefold()


def _has_code(value, code):
    return bool(re.search(rf'(?<![a-z0-9]){re.escape(code)}(?![a-z0-9])', value))


def network_role(device):
    value = _device_text(device)
    if any(token in value for token in ('access point', '无线接入点')) or _has_code(value, 'ap'):
        return 'access_point'
    if any(token in value for token in ('firewall', '防火墙', '安全网关', '出口网关')):
        return 'firewall'
    if any(token in value for token in ('core switch', '核心交换机', '核心')):
        return 'core_switch'
    if any(token in value for token in ('distribution switch', 'aggregation', '汇聚交换机', '汇聚')):
        return 'distribution_switch'
    if any(token in value for token in ('wireless controller', '无线控制器')) or _has_code(value, 'wlc') or _has_code(value, 'ac'):
        return 'wireless_controller'
    if any(token in value for token in ('access switch', '接入交换机', '交换机', 'switch')):
        return 'access_switch'
    if any(token in value for token in ('router', '路由器')):
        return 'router'
    if any(token in value for token in ('gateway', '网关')):
        return 'firewall'
    return 'network_other'


def network_tier(role):
    return ROLE_TIERS.get(role, 3)


def address_networks(value):
    networks = set()
    for candidate in re.split(r'[,;|\s]+', str(value or '').strip()):
        if not candidate:
            continue
        try:
            interface = ip_interface(candidate)
            if '/' not in candidate:
                interface = ip_interface(
                    f'{candidate}/{DEFAULT_PREFIX_LENGTH[interface.version]}'
                )
        except ValueError:
            continue
        networks.add(interface.network)
    return tuple(sorted(
        networks,
        key=lambda network: (network.version, -network.prefixlen, str(network)),
    ))


def _subnet_parent(endpoint_networks, backbone_nodes):
    candidates = []
    for node in backbone_nodes:
        overlaps = [
            max(endpoint.prefixlen, backbone.prefixlen)
            for endpoint in endpoint_networks
            for backbone in node['_networks']
            if endpoint.version == backbone.version and endpoint.overlaps(backbone)
        ]
        if overlaps:
            candidates.append((max(overlaps), node))
    return min(candidates, key=lambda item: (
        -item[0],
        ATTACHMENT_ROLE_PRIORITY[item[1]['role']],
        item[1]['tier'],
        item[1]['id'],
    ), default=(None, None))[1]


def _node(*, node_id, kind, role, label, subtitle, ip, vendor='', model='',
          status='unknown', url='', tier=None, parent_id=None,
          attachment_source='', child_count=0):
    return {
        'id': node_id,
        'kind': kind,
        'role': role,
        'tier': tier,
        'label': label,
        'subtitle': subtitle,
        'ip': ip,
        'vendor': vendor or '',
        'model': model or '',
        'status': status,
        'url': url,
        'parent_id': parent_id,
        'attachment_source': attachment_source,
        'child_count': child_count,
    }


def _network_node(device):
    role = network_role(device)
    kind = 'endpoint' if role == 'access_point' else 'backbone'
    node = _node(
        node_id=f'networks:{device.pk}',
        kind=kind,
        role=role,
        tier=None if kind == 'endpoint' else network_tier(role),
        label=device.device_name or device.ip,
        subtitle=device.device_type or '其他网络设备',
        ip=device.ip,
        vendor=device.vendor,
        model=device.model,
        url=reverse('asset_detail', args=['networks', device.pk]),
    )
    node['_networks'] = address_networks(device.ip)
    return node


def _endpoint_nodes(computers, servers, weak_current):
    nodes = []
    for computer in computers:
        node = _node(
            node_id=f'computers:{computer.pk}', kind='endpoint', role='computer',
            label=computer.computer_name, subtitle='PC', ip=computer.ip_addresses or '',
            vendor=computer.manufacturer, model=computer.model,
            status='normal' if computer.is_active else 'disabled',
            url=reverse('asset_detail', args=['computers', computer.pk]),
        )
        node['_networks'] = address_networks(computer.ip_addresses)
        nodes.append(node)
    for server in servers:
        node = _node(
            node_id=f'servers:{server.pk}', kind='endpoint', role='server',
            label=server.name or server.ip, subtitle=server.get_server_type_display(),
            ip=server.ip, vendor=server.manufacturer, model=server.model,
            url=reverse('asset_detail', args=['servers', server.pk]),
        )
        node['_networks'] = address_networks(server.ip)
        nodes.append(node)
    for monitor in weak_current:
        node = _node(
            node_id=f'weakcurrent:{monitor.pk}', kind='endpoint', role='weak_current_device',
            label=monitor.device_name or monitor.ip,
            subtitle=monitor.device_type or '弱电设备', ip=monitor.ip,
            vendor=monitor.vendor, model=monitor.model,
            url=reverse('asset_detail', args=['weakcurrent', monitor.pk]),
        )
        node['_networks'] = address_networks(monitor.ip)
        nodes.append(node)
    return nodes


def _physical_parent(endpoint, backbone_by_id, physical_edges):
    if endpoint['role'] != 'access_point':
        return None
    endpoint_pk = endpoint['id'].split(':', 1)[1]
    for edge in sorted(physical_edges, key=lambda row: str(row.get('id', ''))):
        if edge.get('resolution_status') != 'resolved':
            continue
        local_id = str(edge.get('local_device_id') or '')
        remote_id = str(edge.get('remote_device_id') or '')
        if local_id == endpoint_pk:
            parent = backbone_by_id.get(f'networks:{remote_id}')
        elif remote_id == endpoint_pk:
            parent = backbone_by_id.get(f'networks:{local_id}')
        else:
            parent = None
        if parent is not None:
            return parent
    return None


def attach_endpoints(backbone_nodes, endpoint_nodes, physical_edges):
    backbone_by_id = {node['id']: node for node in backbone_nodes}
    edges = []
    for endpoint in endpoint_nodes:
        parent = _physical_parent(endpoint, backbone_by_id, physical_edges)
        source = 'physical_discovered' if parent is not None else ''
        if parent is None:
            parent = _subnet_parent(endpoint['_networks'], backbone_nodes)
            source = 'inferred_subnet' if parent is not None else ''
        if parent is None:
            continue
        endpoint['parent_id'] = parent['id']
        endpoint['attachment_source'] = source
        parent['child_count'] += 1
        edges.append({
            'id': f'attachment:{parent["id"]}:{endpoint["id"]}',
            'kind': 'endpoint_attachment',
            'source': parent['id'],
            'target': endpoint['id'],
            'attachment_source': source,
        })
    return sorted(edges, key=lambda edge: edge['id'])


def build_enterprise_topology(*, network_devices, computers, servers, weak_current,
                              physical, asset_truncation=None):
    network_nodes = [_network_node(device) for device in network_devices]
    backbone_nodes = [node for node in network_nodes if node['kind'] == 'backbone']
    endpoint_nodes = [
        *[node for node in network_nodes if node['kind'] == 'endpoint'],
        *_endpoint_nodes(computers, servers, weak_current),
    ]
    physical_edges = list(physical.get('links', []))
    attachment_edges = attach_endpoints(backbone_nodes, endpoint_nodes, physical_edges)
    nodes = backbone_nodes + endpoint_nodes
    nodes.sort(key=lambda node: (
        0 if node['kind'] == 'backbone' else 1,
        node['tier'] if node['tier'] is not None else 99,
        node['label'], node['id'],
    ))
    backbone_count = sum(node['kind'] == 'backbone' for node in nodes)
    has_physical_links = bool(physical_edges)
    for node in nodes:
        node.pop('_networks', None)
    return {
        'mode': 'empty' if not backbone_count else ('hybrid' if has_physical_links else 'backbone'),
        'nodes': nodes,
        'attachment_edges': attachment_edges,
        'interfaces': list(physical.get('interfaces', [])),
        'physical_edges': physical_edges,
        'asset_truncation': dict(asset_truncation or {}),
        'has_physical_links': has_physical_links,
    }
