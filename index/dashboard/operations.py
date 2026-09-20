"""Presentation-safe data for the network topology page."""

import re
from ipaddress import ip_interface

from django.db import DatabaseError
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from net.models import Computer, Network_Device, SecurityDevice, Server
from net.topology.read_model import current_topology_payload


ASSET_NODE_LIMIT_PER_KIND = 80
DEFAULT_PREFIX_LENGTH = {4: 24, 6: 64}


def _safe_region(name, builder):
    try:
        return builder()
    except DatabaseError:
        return {'error': f'{name}_unavailable'}


def _node(node_id, kind, label, url='', parent_id=None, *, subtitle='', status='unknown',
          asset_type=''):
    return {
        'id': node_id,
        'kind': kind,
        'label': label,
        'subtitle': subtitle,
        'status': status,
        'url': url,
        'parent_id': parent_id,
        'asset_type': asset_type,
    }


def _network_segments(value):
    segments = set()
    for candidate in re.split(r'[,;|\s]+', str(value or '').strip()):
        if not candidate:
            continue
        try:
            interface = ip_interface(candidate)
            if '/' not in candidate:
                interface = ip_interface(f'{candidate}/{DEFAULT_PREFIX_LENGTH[interface.version]}')
        except ValueError:
            continue
        segments.add(str(interface.network))
    return sorted(segments)


def _asset_sources():
    return (
        (
            'computers',
            Computer.objects.only('id', 'computer_name', 'ip_addresses', 'is_active'),
            lambda row: row.computer_name,
            lambda row: row.ip_addresses or '',
            lambda row: reverse('asset_detail', args=['computers', row.pk]),
            lambda row: 'normal' if row.is_active else 'disabled',
        ),
        (
            'networks',
            Network_Device.objects.only('id', 'device_name', 'ip'),
            lambda row: row.device_name or row.ip,
            lambda row: row.ip,
            lambda row: reverse('asset_detail', args=['networks', row.pk]),
            lambda row: 'unknown',
        ),
        (
            'servers',
            Server.objects.only('id', 'name', 'ip'),
            lambda row: row.name or row.ip,
            lambda row: row.ip,
            lambda row: reverse('asset_detail', args=['servers', row.pk]),
            lambda row: 'unknown',
        ),
        (
            'monitors',
            SecurityDevice.objects.only('id', 'device_name', 'ip'),
            lambda row: row.device_name or row.ip,
            lambda row: row.ip,
            lambda row: reverse('asset_detail', args=['monitors', row.pk]),
            lambda row: 'unknown',
        ),
    )


def _selected_rows(key, queryset, required_network_ids):
    total = queryset.count()
    if key != 'networks' or not required_network_ids:
        rows = list(queryset.order_by('pk')[:ASSET_NODE_LIMIT_PER_KIND])
        return rows, max(0, total - len(rows))

    required_rows = list(queryset.filter(pk__in=required_network_ids).order_by('pk'))
    remaining = max(0, ASSET_NODE_LIMIT_PER_KIND - len(required_rows))
    rows = required_rows + list(
        queryset.exclude(pk__in=required_network_ids).order_by('pk')[:remaining]
    )
    return rows, max(0, total - len(rows))


def _build_asset_nodes(required_network_ids=()):
    nodes = []
    edges = []
    subnets = set()
    truncation = {}
    required_network_ids = {str(value) for value in required_network_ids if value}

    for key, queryset, label, addresses, url, status in _asset_sources():
        rows, omitted = _selected_rows(key, queryset, required_network_ids)
        if omitted:
            truncation[key] = omitted
        for row in rows:
            node_id = f'{key}:{row.pk}'
            segments = _network_segments(addresses(row)) or ['unassigned']
            parent_id = f'subnet:{segments[0]}'
            nodes.append(_node(
                node_id,
                'asset',
                label(row),
                url(row),
                parent_id,
                subtitle=addresses(row),
                status=status(row),
                asset_type=key,
            ))
            for segment in segments:
                subnet_id = f'subnet:{segment}'
                subnets.add(segment)
                edges.append({
                    'id': f'membership:{subnet_id}:{node_id}',
                    'source': subnet_id,
                    'target': node_id,
                    'relationship': 'subnet_membership',
                })

    subnet_nodes = [
        _node(
            f'subnet:{segment}',
            'subnet',
            '未识别网段' if segment == 'unassigned' else segment,
            subtitle='缺少有效 IP 地址' if segment == 'unassigned' else 'IP 网段',
        )
        for segment in sorted(subnets, key=lambda value: (value == 'unassigned', value))
    ]
    return subnet_nodes + nodes, edges, truncation


def _build_topology():
    physical = current_topology_payload(include_stale=True, limit=1000)
    physical_edges = physical['links']
    required_network_ids = {
        value
        for edge in physical_edges
        for value in (edge.get('local_device_id'), edge.get('remote_device_id'))
        if value
    }
    nodes, membership_edges, truncation = _build_asset_nodes(required_network_ids)
    return {
        'mode': 'hybrid' if physical_edges else 'subnet',
        'nodes': nodes,
        'logical_edges': membership_edges,
        'interfaces': physical['interfaces'],
        'physical_edges': physical_edges,
        'asset_truncation': truncation,
    }


def build_operations_snapshot(*, now=None):
    generated_at = now or timezone.now()
    return {
        'schema_version': 2,
        'generated_at': generated_at.isoformat(),
        'topology': _safe_region('topology', _build_topology),
    }


@require_GET
def operations_overview(request):
    return render(
        request,
        'dashboard/operations_overview.html',
        {'snapshot': build_operations_snapshot()},
    )


@require_GET
def operations_overview_data(request):
    return JsonResponse(build_operations_snapshot())
