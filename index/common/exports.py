from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404

from index.common.table_registry import get_table_definition, project_record_definition
from net.data_exchange.table_csv import export_filtered_csv
from net.models import (
    Computer,
    ComputerLogFile,
    Domain_Account,
    Domain_Computer,
    Domain_Group,
    Error_Computer,
    SecurityDevice,
    Network_Device,
    People,
    Server,
    TaskRun,
    TaskTargetRun,
    AlertEvent,
)

from index.inspections.records import (
    RECORD_PAGES,
    _computer_analysis_records,
    _error_records,
    _infrastructure_records,
    _inspection_records,
)


MODEL_TABLES = {
    'computer_logs': ComputerLogFile,
    'people': People,
    'computers': Computer,
    'networks': Network_Device,
    'servers': Server,
    'monitors': SecurityDevice,
    'domain_accounts': Domain_Account,
    'domain_computers': Domain_Computer,
    'domain_groups': Domain_Group,
    'task_runs': TaskRun,
    'alert_events': AlertEvent,
}


def _computer_analyses(target):
    return _computer_analysis_records(target)


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
        task = None
        if request.GET.get('task'):
            try:
                task = get_object_or_404(TaskRun, pk=request.GET['task'], task_type='computer_analysis')
            except (ValidationError, ValueError):
                raise Http404('任务无效')
        return _computer_analysis_records(request.GET.get('target', '').strip(), task=task)
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
            task = None
            if request.GET.get('task'):
                device_type = {'networks': 'network_device', 'servers': 'server', 'monitors': 'monitor'}[scope]
                try:
                    task = get_object_or_404(TaskRun, pk=request.GET['task'], task_type='inspection',
                                             inspection_profile__device_type=device_type)
                except (ValidationError, ValueError):
                    raise Http404('任务无效')
            return _infrastructure_records(
                scope, target=request.GET.get('target', '').strip(), task=task,
            )
        return _inspection_records()
    if table_key == 'error_records' and not scope:
        return _error_records()
    if table_key == 'task_targets' and scope:
        try:
            task = TaskRun.objects.get(pk=scope)
        except (TaskRun.DoesNotExist, ValidationError, ValueError) as exc:
            raise Http404('未知的任务') from exc
        if task.task_type in TaskRun.PEOPLE_INTERACTIVE_TASK_TYPES:
            from index.people.integrations import require_people_owner
            require_people_owner(request, task)
        return TaskTargetRun.objects.filter(task=task)
    raise Http404('未知的数据表')


def table_export(request, table_key, scope=''):
    try:
        definition = get_table_definition(table_key)
    except KeyError as exc:
        raise Http404('未知的数据表') from exc
    source = _table_source(request, table_key, scope)
    if table_key == 'computer_inspections':
        definition = project_record_definition('computers')
    elif table_key == 'inspection_records' and scope in RECORD_PAGES:
        definition = project_record_definition(scope)
    return export_filtered_csv(
        request, definition, source, f'{table_key}.csv',
    )
