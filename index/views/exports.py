from django.core.exceptions import ValidationError
from django.db.models import BooleanField, Case, Exists, OuterRef, Value, When
from django.http import Http404

from index.table_registry import get_table_definition
from net.exports import export_filtered_csv
from net.models import (
    Computer,
    ComputerLogFile,
    ComputerAnalysis,
    Domain_Account,
    Domain_Computer,
    Error_Computer,
    Monitor,
    Network_Device,
    People,
    RecordStatus,
    Server,
    TaskRun,
    TaskTargetRun,
    AlertEvent,
)

from .records import RECORD_PAGES, _error_records, _infrastructure_records, _inspection_records


MODEL_TABLES = {
    'computer_logs': ComputerLogFile,
    'people': People,
    'computers': Computer,
    'networks': Network_Device,
    'servers': Server,
    'monitors': Monitor,
    'domain_accounts': Domain_Account,
    'domain_computers': Domain_Computer,
    'task_runs': TaskRun,
    'alert_events': AlertEvent,
}


def _computer_analyses(target):
    error_exists = Error_Computer.objects.filter(inspection_id=OuterRef('pk'))
    analyses = ComputerAnalysis.objects.select_related(
        'computer', 'log_file',
    ).annotate(
        has_errors=Exists(error_exists),
        ok=Case(
            When(status=RecordStatus.SUCCESS, has_errors=False, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
    )
    if target:
        try:
            analyses = analyses.filter(computer_id=target)
        except (ValidationError, ValueError):
            analyses = analyses.none()
    return analyses


def _table_source(request, table_key, scope):
    if table_key in MODEL_TABLES:
        if scope:
            raise Http404('该表不支持分组导出')
        queryset = MODEL_TABLES[table_key].objects.all()
        if table_key == 'alert_events':
            queryset = queryset.prefetch_related('deliveries__channel')
        return queryset
    if table_key == 'computer_inspections':
        if scope:
            raise Http404('该表不支持分组导出')
        return _computer_analyses(request.GET.get('target', '').strip())
    if table_key == 'computer_errors':
        if scope:
            raise Http404('该表不支持分组导出')
        return Error_Computer.objects.select_related(
            'inspection__computer', 'inspection__log_file',
        )
    if table_key == 'inspection_records':
        if scope:
            if scope not in RECORD_PAGES:
                raise Http404('未知的巡检类型')
            return _infrastructure_records(
                scope, target=request.GET.get('target', '').strip(),
            )
        return _inspection_records()
    if table_key == 'error_records' and not scope:
        return _error_records()
    if table_key == 'task_targets' and scope:
        try:
            task = TaskRun.objects.get(pk=scope)
        except (TaskRun.DoesNotExist, ValidationError, ValueError) as exc:
            raise Http404('未知的任务') from exc
        if task.task_type in TaskRun.PEOPLE_TASK_TYPES:
            from .integrations import require_people_owner
            require_people_owner(request, task)
        return TaskTargetRun.objects.filter(task=task)
    raise Http404('未知的数据表')


def table_export(request, table_key, scope=''):
    try:
        definition = get_table_definition(table_key)
    except KeyError as exc:
        raise Http404('未知的数据表') from exc
    source = _table_source(request, table_key, scope)
    return export_filtered_csv(
        request, definition, source, f'{table_key}.csv',
    )
