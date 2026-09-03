"""Latest-state summary data for dashboard asset cards."""

import logging

from django.db import DatabaseError
from django.db.models import (
    BooleanField,
    Case,
    CharField,
    Count,
    Exists,
    Max,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)

from net.models import (
    Computer,
    ComputerAnalysis,
    Domain_Account,
    Domain_Computer,
    Domain_Group,
    Error_Computer,
    Error_Monitor,
    Error_Network_Device,
    Error_Server,
    SecurityDevice,
    Monitor_Inspection,
    Network_Device,
    Network_Device_Inspection,
    People,
    RecordStatus,
    Server,
    Server_Inspection,
)


# Keep the historical logger name stable for deployments that route or alert
# on this category while the implementation lives in its feature package.
logger = logging.getLogger('net.dashboard.assets')
DASHBOARD_SUMMARY_ERROR_MESSAGE = '统计查询失败，请稍后重试。'

INFRASTRUCTURE_ERROR_MODELS = {
    Network_Device_Inspection: Error_Network_Device,
    Server_Inspection: Error_Server,
    Monitor_Inspection: Error_Monitor,
}


def _static_summary(model, key):
    values = model.objects.aggregate(
        total=Count('pk'),
        normal=Count('pk', filter=Q(is_active=True)),
        abnormal=Count('pk', filter=Q(is_active=False)),
    )
    return {
        'key': key,
        **values,
        'unchecked': 0,
        'last_run_at': None,
    }


def _domain_group_summary():
    values = Domain_Group.objects.aggregate(
        total=Count('pk'),
        normal=Count('pk', filter=Q(group_category=Domain_Group.Category.SECURITY)),
        abnormal=Count('pk', filter=Q(group_category=Domain_Group.Category.DISTRIBUTION)),
    )
    return {
        'key': 'domain_groups', **values, 'unchecked': 0, 'last_run_at': None,
    }


def with_latest_status(queryset, inspection_model, foreign_key):
    """Annotate infrastructure assets with their latest inspection state."""
    latest = inspection_model.objects.filter(
        **{f'{foreign_key}_id': OuterRef('pk')},
    ).order_by('-created_at', '-pk').annotate(
        has_errors=Exists(
            INFRASTRUCTURE_ERROR_MODELS[inspection_model].objects.filter(
                inspection_id=OuterRef('pk'),
            ),
        ),
    )
    return queryset.annotate(
        latest_reachable=Subquery(latest.values('is_reachable')[:1]),
        latest_inspected_at=Subquery(latest.values('created_at')[:1]),
        latest_collection_status=Subquery(latest.values('status')[:1]),
        latest_has_errors=Subquery(
            latest.values('has_errors')[:1], output_field=BooleanField(),
        ),
    ).annotate(
        latest_status=Case(
            When(latest_collection_status__isnull=True, then=Value('unchecked')),
            When(
                latest_reachable=True,
                latest_collection_status=RecordStatus.SUCCESS,
                latest_has_errors=False,
                then=Value('normal'),
            ),
            default=Value('abnormal'),
            output_field=CharField(),
        ),
    )


def _infrastructure_summary(model, inspection_model, foreign_key, key):
    assets = with_latest_status(model.objects.all(), inspection_model, foreign_key)
    values = assets.aggregate(
        total=Count('pk'),
        normal=Count('pk', filter=Q(latest_status='normal')),
        abnormal=Count('pk', filter=Q(latest_status='abnormal')),
        unchecked=Count('pk', filter=Q(latest_status='unchecked')),
        last_run_at=Max('latest_inspected_at'),
    )
    return {'key': key, **values}


def _computer_summary():
    latest = ComputerAnalysis.objects.filter(
        computer_id=OuterRef('pk'),
    ).order_by('-created_at', '-pk').annotate(
        has_errors=Exists(
            Error_Computer.objects.filter(inspection_id=OuterRef('pk')),
        ),
    )
    assets = Computer.objects.annotate(
        latest_analysis_status=Subquery(latest.values('status')[:1]),
        latest_analysis_has_errors=Subquery(
            latest.values('has_errors')[:1], output_field=BooleanField(),
        ),
        latest_analyzed_at=Subquery(latest.values('created_at')[:1]),
    ).annotate(
        latest_status=Case(
            When(latest_analysis_status__isnull=True, then=Value('unchecked')),
            When(
                latest_analysis_status=RecordStatus.SUCCESS,
                latest_analysis_has_errors=False,
                then=Value('normal'),
            ),
            default=Value('abnormal'),
            output_field=CharField(),
        ),
    )
    values = assets.aggregate(
        total=Count('pk'),
        normal=Count('pk', filter=Q(latest_status='normal')),
        abnormal=Count('pk', filter=Q(latest_status='abnormal')),
        unchecked=Count('pk', filter=Q(latest_status='unchecked')),
        last_run_at=Max('latest_analyzed_at'),
    )
    return {'key': 'computers', **values}


def _safe_summary(key, builder):
    try:
        return builder()
    except DatabaseError:
        logger.exception('Dashboard summary query failed for category %s', key)
        return {
            'key': key,
            'error': DASHBOARD_SUMMARY_ERROR_MESSAGE,
        }


def build_asset_card_summaries() -> list[dict]:
    """Return bounded-query latest state for every dashboard asset category."""
    return [
        _safe_summary('people', lambda: _static_summary(People, 'people')),
        _safe_summary(
            'domain_accounts',
            lambda: _static_summary(Domain_Account, 'domain_accounts'),
        ),
        _safe_summary(
            'domain_computers',
            lambda: _static_summary(Domain_Computer, 'domain_computers'),
        ),
        _safe_summary('domain_groups', _domain_group_summary),
        _safe_summary('computers', _computer_summary),
        _safe_summary(
            'networks',
            lambda: _infrastructure_summary(
                Network_Device, Network_Device_Inspection, 'device', 'networks',
            ),
        ),
        _safe_summary(
            'servers',
            lambda: _infrastructure_summary(
                Server, Server_Inspection, 'server', 'servers',
            ),
        ),
        _safe_summary(
            'monitors',
            lambda: _infrastructure_summary(
                SecurityDevice, Monitor_Inspection, 'monitor', 'monitors',
            ),
        ),
    ]
