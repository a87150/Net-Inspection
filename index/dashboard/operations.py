"""Presentation-safe data for the network topology page."""

from django.db import DatabaseError
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from net.models import Computer, Network_Device, SecurityDevice, Server
from net.topology.read_model import current_topology_payload
from index.dashboard.topology_presentation import build_enterprise_topology


ASSET_NODE_LIMIT_PER_KIND = 80


def _safe_region(name, builder):
    try:
        return builder()
    except DatabaseError:
        return {'error': f'{name}_unavailable'}


def _asset_sources():
    return (
        (
            'computers',
            Computer.objects.only('id', 'computer_name', 'ip_addresses', 'is_active'),
        ),
        (
            'networks',
            Network_Device.objects.only('id', 'device_name', 'ip'),
        ),
        (
            'servers',
            Server.objects.only('id', 'name', 'ip'),
        ),
        (
            'monitors',
            SecurityDevice.objects.only('id', 'device_name', 'ip'),
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


def _load_assets(required_network_ids=()):
    selected = {}
    truncation = {}
    required_network_ids = {str(value) for value in required_network_ids if value}
    for key, queryset in _asset_sources():
        rows, omitted = _selected_rows(key, queryset, required_network_ids)
        selected[key] = rows
        if omitted:
            truncation[key] = omitted
    return selected, truncation


def _build_topology():
    physical = current_topology_payload(include_stale=True, limit=1000)
    physical_edges = physical['links']
    required_network_ids = {
        value
        for edge in physical_edges
        for value in (edge.get('local_device_id'), edge.get('remote_device_id'))
        if value
    }
    assets, truncation = _load_assets(required_network_ids)
    return build_enterprise_topology(
        network_devices=assets['networks'],
        computers=assets['computers'],
        servers=assets['servers'],
        monitors=assets['monitors'],
        physical=physical,
        asset_truncation=truncation,
    )


def build_operations_snapshot(*, now=None):
    generated_at = now or timezone.now()
    return {
        'schema_version': 3,
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
