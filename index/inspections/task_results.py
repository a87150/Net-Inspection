"""Use project result tables for a specific task, never the current latest task."""
from django.core.paginator import Paginator
from django.urls import reverse
from index.common.table_query import apply_table_filters, preserve_table_parameters, query_without_page
from index.common.table_registry import project_record_definition


def task_result_context(request, task):
    from .records import _computer_analysis_records, _infrastructure_records
    if task.task_type == 'computer_analysis':
        kind = 'computers'
        rows = _computer_analysis_records(task=task)
        table_key, record_type = 'computer_inspections', 'computer_analysis'
        export_path = reverse('table_export', args=[table_key])
    elif task.task_type == 'inspection':
        device_type = (task.inspection_profile.device_type if task.inspection_profile_id
                       else task.profile_snapshot.get('device_type'))
        kind = {'network_device': 'networks', 'server': 'servers', 'monitor': 'monitors'}.get(device_type)
        if not kind:
            return {}
        rows = _infrastructure_records(kind, task=task)
        table_key, record_type = 'inspection_records', 'infrastructure'
        export_path = reverse('table_export_scoped', args=[table_key, kind])
    else:
        return {}
    definition = project_record_definition(kind)
    rows, state = apply_table_filters(request, rows, definition, include_legacy_status=False)
    preserve_table_parameters(state, {'task': str(task.pk)})
    unresolved = task.target_runs.filter(result_id='').order_by('pk')
    return {
        'show_result_table': True, 'record_type': record_type, 'record_kind': kind,
        'latest_task': task, 'table_definition': definition, 'table_state': state,
        'table_preference_key': table_key if kind == 'computers' else f'{table_key}-{kind}',
        'page_obj': Paginator(rows, state['page_size']).get_page(request.GET.get('page')),
        'table_export_path': export_path, 'pagination_query': query_without_page(request),
        'execution_page': Paginator(unresolved, 20).get_page(request.GET.get('execution_page')),
    }
