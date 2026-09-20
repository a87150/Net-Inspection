"""Presentation-safe data for the operations overview page."""

from django.db import DatabaseError
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from net.dashboard.assets import build_asset_card_summaries
from net.models import (
    AlertEvent,
    Computer,
    Domain_Account,
    Domain_Computer,
    Domain_Group,
    Network_Device,
    People,
    SecurityDevice,
    Server,
    TaskRun,
)
from net.topology.read_model import current_topology_payload


def _iso(value):
    return value.isoformat() if value else None


def _safe_region(name, builder):
    try:
        return builder()
    except DatabaseError:
        return {'error': f'{name}_unavailable'}


def _summary_value(value):
    if not value:
        return {'total': 0, 'normal': 0, 'abnormal': 0, 'unchecked': 0}
    if value.get('error'):
        return {'error': 'category_unavailable'}
    return {
        'total': value['total'],
        'normal': value['normal'],
        'abnormal': value['abnormal'],
        'unchecked': value['unchecked'],
        'last_run_at': _iso(value.get('last_run_at')),
    }


def _build_summary():
    summaries = {row['key']: row for row in build_asset_card_summaries()}
    domain_parts = [
        summaries.get('domain_accounts'),
        summaries.get('domain_computers'),
        summaries.get('domain_groups'),
    ]
    if any(not row or row.get('error') for row in domain_parts):
        domain = {'error': 'category_unavailable'}
    else:
        domain = {
            'total': sum(row['total'] for row in domain_parts),
            'normal': sum(row['normal'] for row in domain_parts),
            'abnormal': sum(row['abnormal'] for row in domain_parts),
            'unchecked': 0,
        }
    task_counts = TaskRun.objects.aggregate(
        total=Count('pk'),
        running=Count('pk', filter=Q(status__in=TaskRun.ACTIVE_STATUSES)),
        failed=Count('pk', filter=Q(status__in=(TaskRun.Status.FAILED, TaskRun.Status.PARTIAL))),
    )
    return {
        'people': _summary_value(summaries.get('people')),
        'computers': _summary_value(summaries.get('computers')),
        'networks': _summary_value(summaries.get('networks')),
        'servers': _summary_value(summaries.get('servers')),
        'monitors': _summary_value(summaries.get('monitors')),
        'domain': domain,
        'tasks': task_counts,
    }


def _task_payload(task):
    return {
        'id': str(task.pk),
        'type': task.task_type,
        'type_label': task.get_task_type_display(),
        'source_label': task.get_source_display(),
        'status': task.status,
        'status_label': task.get_status_display(),
        'progress': task.progress,
        'total_targets': task.total_targets,
        'completed_targets': task.completed_targets,
        'successful_targets': task.successful_targets,
        'failed_targets': task.failed_targets,
        'created_at': _iso(task.created_at),
        'started_at': _iso(task.started_at),
        'finished_at': _iso(task.finished_at),
        'url': reverse('task_detail', args=[task.pk]),
    }


def _build_tasks():
    rows = TaskRun.objects.defer(
        'parameters_snapshot', 'target_scope_snapshot', 'profile_snapshot',
        'error_summary',
    ).order_by('-created_at', '-pk')[:10]
    return {'items': [_task_payload(row) for row in rows]}


def _delivery_payload(delivery):
    return {
        'name': delivery.channel.name,
        'type': delivery.channel.channel_type,
        'type_label': delivery.channel.get_channel_type_display(),
        'status': delivery.status,
        'status_label': delivery.get_status_display(),
    }


def _alert_payload(event):
    deliveries = sorted(
        event.deliveries.all(), key=lambda row: (row.channel.name, str(row.pk)),
    )
    return {
        'id': str(event.pk),
        'severity': event.severity or 'unknown',
        'status': event.status,
        'status_label': event.get_status_display(),
        'summary': event.summary,
        'target_type': event.target_type,
        'occurred_at': _iso(event.occurred_at),
        'url': reverse('alert_detail', args=[event.pk]),
        'deliveries': [_delivery_payload(row) for row in deliveries],
    }


def _build_alerts():
    rows = AlertEvent.objects.prefetch_related('deliveries__channel').order_by(
        '-occurred_at', '-pk',
    )[:10]
    return {'items': [_alert_payload(row) for row in rows]}


def _node(node_id, kind, label, url, parent_id, *, subtitle='', status='unknown'):
    return {
        'id': node_id,
        'kind': kind,
        'label': label,
        'subtitle': subtitle,
        'status': status,
        'url': url,
        'parent_id': parent_id,
    }


def _build_logical_nodes():
    nodes = [_node('root', 'root', '运维管理台', reverse('index'), None)]
    edges = []
    categories = (
        ('people', '人员', reverse('asset_list', args=['people'])),
        ('computers', 'PC', reverse('asset_list', args=['computers'])),
        ('networks', '网络设备', reverse('asset_list', args=['networks'])),
        ('servers', '服务器', reverse('asset_list', args=['servers'])),
        ('monitors', '安防设备', reverse('asset_list', args=['monitors'])),
        ('domain', '域控对象', reverse('domain_account_list')),
    )
    for key, label, url in categories:
        node_id = f'category:{key}'
        nodes.append(_node(node_id, 'category', label, url, 'root'))
        edges.append({'id': f'logical:root:{key}', 'source': 'root', 'target': node_id,
                      'relationship': 'logical_membership'})

    assets = (
        ('people', People.objects.only('id', 'name', 'employee_id', 'is_active'),
         lambda row: row.name or '未命名人员', lambda row: row.employee_id or '',
         lambda row: reverse('person_detail', args=[row.pk]), lambda row: 'normal' if row.is_active else 'disabled'),
        ('computers', Computer.objects.only('id', 'computer_name', 'ip_addresses', 'is_active'),
         lambda row: row.computer_name, lambda row: row.ip_addresses or '',
         lambda row: reverse('asset_detail', args=['computers', row.pk]), lambda row: 'normal' if row.is_active else 'disabled'),
        ('networks', Network_Device.objects.only('id', 'device_name', 'ip'),
         lambda row: row.device_name or row.ip, lambda row: row.ip,
         lambda row: reverse('asset_detail', args=['networks', row.pk]), lambda row: 'unknown'),
        ('servers', Server.objects.only('id', 'name', 'ip'),
         lambda row: row.name or row.ip, lambda row: row.ip,
         lambda row: reverse('asset_detail', args=['servers', row.pk]), lambda row: 'unknown'),
        ('monitors', SecurityDevice.objects.only('id', 'device_name', 'ip'),
         lambda row: row.device_name or row.ip, lambda row: row.ip,
         lambda row: reverse('asset_detail', args=['monitors', row.pk]), lambda row: 'unknown'),
        ('domain', Domain_Account.objects.only('id', 'login_name', 'is_active'),
         lambda row: row.login_name, lambda row: '域账号',
         lambda row: reverse('domain_account_detail', args=[row.pk]), lambda row: 'normal' if row.is_active else 'disabled'),
        ('domain', Domain_Computer.objects.only('id', 'computer_name', 'is_active'),
         lambda row: row.computer_name, lambda row: '域计算机',
         lambda row: reverse('domain_computer_detail', args=[row.pk]), lambda row: 'normal' if row.is_active else 'disabled'),
        ('domain', Domain_Group.objects.only('id', 'group_name', 'is_available'),
         lambda row: row.group_name, lambda row: '域分组',
         lambda row: reverse('domain_group_detail', args=[row.pk]), lambda row: 'normal' if row.is_available else 'disabled'),
    )
    for category, queryset, label, subtitle, url, status in assets:
        parent_id = f'category:{category}'
        for row in queryset.iterator():
            node_id = f'{category}:{row.pk}'
            nodes.append(_node(node_id, 'asset', label(row), url(row), parent_id,
                               subtitle=subtitle(row), status=status(row)))
            edges.append({'id': f'logical:{parent_id}:{row.pk}', 'source': parent_id,
                          'target': node_id, 'relationship': 'logical_membership'})
    return nodes, edges


def _build_topology():
    nodes, logical_edges = _build_logical_nodes()
    physical = current_topology_payload(include_stale=True, limit=1000)
    physical_edges = physical['links']
    return {
        'mode': 'hybrid' if physical_edges else 'logical',
        'nodes': nodes,
        'logical_edges': logical_edges,
        'interfaces': physical['interfaces'],
        'physical_edges': physical_edges,
    }


def build_operations_snapshot(*, now=None):
    generated_at = now or timezone.now()
    return {
        'schema_version': 1,
        'generated_at': generated_at.isoformat(),
        'summary': _safe_region('summary', _build_summary),
        'tasks': _safe_region('tasks', _build_tasks),
        'alerts': _safe_region('alerts', _build_alerts),
        'topology': _safe_region('topology', _build_topology),
    }
