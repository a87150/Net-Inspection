"""Build a presentation-safe enterprise network topology payload."""

import re

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
    return _node(
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


def _endpoint_nodes(computers, servers, monitors):
    nodes = []
    for computer in computers:
        nodes.append(_node(
            node_id=f'computers:{computer.pk}', kind='endpoint', role='computer',
            label=computer.computer_name, subtitle='PC', ip=computer.ip_addresses or '',
            vendor=computer.manufacturer, model=computer.model,
            status='normal' if computer.is_active else 'disabled',
            url=reverse('asset_detail', args=['computers', computer.pk]),
        ))
    for server in servers:
        nodes.append(_node(
            node_id=f'servers:{server.pk}', kind='endpoint', role='server',
            label=server.name or server.ip, subtitle=server.get_server_type_display(),
            ip=server.ip, vendor=server.manufacturer, model=server.model,
            url=reverse('asset_detail', args=['servers', server.pk]),
        ))
    for monitor in monitors:
        nodes.append(_node(
            node_id=f'monitors:{monitor.pk}', kind='endpoint', role='security_device',
            label=monitor.device_name or monitor.ip,
            subtitle=monitor.device_type or '安防设备', ip=monitor.ip,
            vendor=monitor.vendor, model=monitor.model,
            url=reverse('asset_detail', args=['monitors', monitor.pk]),
        ))
    return nodes


def build_enterprise_topology(*, network_devices, computers, servers, monitors,
                              physical, asset_truncation=None):
    network_nodes = [_network_node(device) for device in network_devices]
    nodes = network_nodes + _endpoint_nodes(computers, servers, monitors)
    nodes.sort(key=lambda node: (
        0 if node['kind'] == 'backbone' else 1,
        node['tier'] if node['tier'] is not None else 99,
        node['label'], node['id'],
    ))
    backbone_count = sum(node['kind'] == 'backbone' for node in nodes)
    physical_edges = list(physical.get('links', []))
    has_physical_links = bool(physical_edges)
    return {
        'mode': 'empty' if not backbone_count else ('hybrid' if has_physical_links else 'backbone'),
        'nodes': nodes,
        'attachment_edges': [],
        'interfaces': list(physical.get('interfaces', [])),
        'physical_edges': physical_edges,
        'asset_truncation': dict(asset_truncation or {}),
        'has_physical_links': has_physical_links,
    }
