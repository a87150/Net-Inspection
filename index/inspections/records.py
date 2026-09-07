from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import BooleanField, Case, Exists, OuterRef, Q, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from index.common.table_query import (
    PAGE_SIZES,
    apply_table_filters,
    preserve_table_parameters,
    query_without_page,
)
from index.common.table_registry import get_table_definition
from net.models import (
    ComputerAnalysis,
    Error_Computer,
    Monitor_Inspection,
    Network_Device_Inspection,
    RecordStatus,
    Server_Inspection,
)
from net.inspections.task_summary import (
    build_project_task_metrics,
    project_task_queryset,
    summarize_task,
)
from .tasks import task_modal_context
from index.alerts.views import alert_modal_context


@dataclass(frozen=True)
class RecordPage:
    model: type
    asset_field: str
    title: str


RECORD_PAGES = {
    'networks': RecordPage(Network_Device_Inspection, 'device', '网络设备'),
    'servers': RecordPage(Server_Inspection, 'server', '服务器'),
    'monitors': RecordPage(Monitor_Inspection, 'monitor', '安防设备'),
}


def _record_page(kind):
    try:
        return RECORD_PAGES[kind]
    except KeyError as exc:
        raise Http404('未知的巡检类型') from exc


def _record_row(kind, page, inspection):
    ok = inspection.is_reachable and inspection.status == RecordStatus.SUCCESS
    return {
        'execution_status': inspection.execution_status,
        'task_source': inspection.task_source,
        'error_count': inspection.error_count,
        'key_metrics': inspection.key_metrics,
        'category': page.title,
        'target_id': getattr(inspection, f'{page.asset_field}_id'),
        'asset': str(getattr(inspection, page.asset_field, None) or '未知设备'),
        'time': inspection.created_at,
        'ok': ok,
        'summary': inspection.summary or ('巡检正常' if ok else '设备不可达'),
        'url': reverse('record_detail', args=[kind, inspection.pk]),
    }


def _latest_project_task(kind):
    return project_task_queryset(kind).first()


def _infrastructure_records(kind, *, target='', latest_only=True):
    page = _record_page(kind)
    queryset = page.model.objects.select_related(page.asset_field)
    if latest_only:
        latest_task = _latest_project_task(kind)
        if latest_task:
            queryset = queryset.filter(task_target__task=latest_task)
    if target:
        try:
            queryset = queryset.filter(**{f'{page.asset_field}_id': target})
        except (ValidationError, ValueError):
            queryset = queryset.none()
    return [
        _record_row(kind, page, inspection)
        for inspection in queryset.order_by('-created_at')
    ]


def _computer_analysis_records(target=''):
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
    latest_task = _latest_project_task('computers')
    if latest_task:
        analyses = analyses.filter(task_target__task=latest_task)
    if target:
        try:
            analyses = analyses.filter(computer_id=target)
        except (ValidationError, ValueError):
            analyses = analyses.none()
    return analyses


def _project_workspace_context(request, kind):
    tasks = list(project_task_queryset(kind))
    task_page = Paginator(tasks, 7).get_page(request.GET.get('task_page'))
    task_page.object_list = [summarize_task(task) for task in task_page.object_list]
    return {
        'task_metrics': build_project_task_metrics(tasks),
        'task_page': task_page,
        'latest_task': tasks[0] if tasks else None,
        'task_section_title': (
            '日志分析任务' if kind == 'computers' else '巡检任务'
        ),
        'task_empty_title': (
            '暂无日志分析任务' if kind == 'computers' else '暂无巡检任务'
        ),
    }


def record_list(request, kind):
    page = _record_page(kind)
    table_definition = get_table_definition('inspection_records')
    target = request.GET.get('target', '').strip()
    records, table_state = apply_table_filters(
        request,
        _infrastructure_records(kind, target=target),
        table_definition,
    )
    preserve_table_parameters(table_state, {'target': target})
    page_obj = Paginator(records, table_state['page_size']).get_page(
        request.GET.get('page'),
    )
    context = {
        'record_type': 'infrastructure',
        'record_kind': kind,
        'item_name': page.title,
        'page_obj': page_obj,
        'table_definition': table_definition,
        'table_preference_key': f'inspection_records-{kind}',
        'table_state': table_state,
        'page_sizes': PAGE_SIZES,
        'table_export_path': reverse(
            'table_export_scoped', args=['inspection_records', kind],
        ),
        'pagination_query': query_without_page(request),
    }
    context.update(_project_workspace_context(request, kind))
    context.update(task_modal_context(request, kind, target_source='records'))
    context.update(alert_modal_context(request, profile=context['task_default_profile']))
    return render(request, 'inspections/record_list.html', context)


def record_detail(request, kind, pk):
    page = _record_page(kind)
    inspection = get_object_or_404(
        page.model.objects.select_related(page.asset_field), pk=pk,
    )
    detail_fields = list((inspection.details or {}).items())
    if inspection.raw_output:
        detail_fields.append(('原始响应', inspection.raw_output))
    error_manager = getattr(inspection, 'errors', None)
    return render(request, 'inspections/record_detail.html', {
        'record_type': 'infrastructure',
        'record_kind': kind,
        'item_name': page.title,
        'asset_name': str(getattr(inspection, page.asset_field, None) or '未知设备'),
        'inspection': inspection,
        'detail_fields': [
            (name, value) for name, value in detail_fields if value
        ],
        'inspection_errors': (
            list(error_manager.all()) if error_manager is not None else []
        ),
    })


def computer_analysis_list(request):
    target = request.GET.get('target', '').strip()
    analyses = _computer_analysis_records(target)
    table_definition = get_table_definition('computer_inspections')
    analyses, table_state = apply_table_filters(
        request, analyses, table_definition, include_legacy_status=False,
    )
    preserve_table_parameters(table_state, {'target': target})
    page_obj = Paginator(analyses, table_state['page_size']).get_page(
        request.GET.get('page'),
    )
    context = {
        'record_type': 'computer_analysis',
        'item_name': 'PC',
        'page_obj': page_obj,
        'table_definition': table_definition,
        'table_state': table_state,
        'page_sizes': PAGE_SIZES,
        'table_export_path': reverse(
            'table_export', args=['computer_inspections'],
        ),
        'pagination_query': query_without_page(request),
    }
    context.update(_project_workspace_context(request, 'computers'))
    context.update(task_modal_context(request, 'computers'))
    context.update(alert_modal_context(request, profile=context['task_default_profile']))
    return render(request, 'inspections/record_list.html', context)


def computer_analysis_detail(request, pk):
    analysis = get_object_or_404(
        ComputerAnalysis.objects.select_related(
            'computer', 'log_file',
        ).prefetch_related('errors'),
        pk=pk,
    )
    return render(request, 'inspections/record_detail.html', {
        'record_type': 'computer_analysis',
        'analysis': analysis,
        'detail_fields': list((analysis.details or {}).items()),
    })


def _inspection_records(per_type=None):
    records = []
    computer_error = Error_Computer.objects.filter(inspection_id=OuterRef('pk'))
    analyses = ComputerAnalysis.objects.select_related('computer').annotate(
        has_errors=Exists(computer_error),
    ).order_by('-created_at')
    if per_type is not None:
        analyses = analyses[:per_type]
    for analysis in analyses:
        ok = analysis.status == RecordStatus.SUCCESS and not analysis.has_errors
        records.append({
            'execution_status': analysis.execution_status,
            'task_source': analysis.task_source,
            'error_count': analysis.error_count,
            'key_metrics': analysis.key_metrics,
            'category': 'PC 分析',
            'asset': analysis.computer.computer_name,
            'time': analysis.created_at,
            'ok': ok,
            'summary': analysis.summary or ('分析正常' if ok else '发现异常'),
            'url': reverse('computer_analysis_detail', args=[analysis.pk]),
        })
    for kind, page in RECORD_PAGES.items():
        queryset = page.model.objects.select_related(page.asset_field).order_by(
            '-created_at',
        )
        if per_type is not None:
            queryset = queryset[:per_type]
        records.extend(_record_row(kind, page, inspection) for inspection in queryset)
    return sorted(records, key=lambda record: record['time'], reverse=True)


def _error_records(per_type=None):
    records = []
    computer_errors = Error_Computer.objects.select_related(
        'inspection__computer',
    ).order_by('-inspection__created_at')
    if per_type is not None:
        computer_errors = computer_errors[:per_type]
    for error in computer_errors:
        records.append({
            'category': 'PC',
            'asset': error.inspection.computer.computer_name,
            'time': error.inspection.created_at,
            'type': error.error_type,
            'message': error.error_message,
            'url': reverse(
                'computer_analysis_detail', args=[error.inspection_id],
            ),
            'analysis_id': error.inspection_id,
        })
    for kind, page in RECORD_PAGES.items():
        queryset = page.model.objects.select_related(page.asset_field).filter(
            Q(is_reachable=False) | ~Q(status=RecordStatus.SUCCESS),
        ).order_by('-created_at')
        if per_type is not None:
            queryset = queryset[:per_type]
        for inspection in queryset:
            records.append({
                'category': page.title,
                'asset': str(
                    getattr(inspection, page.asset_field, None) or '未知设备'
                ),
                'time': inspection.created_at,
                'type': '设备不可达' if not inspection.is_reachable else '采集异常',
                'message': inspection.summary or '设备巡检未完整成功',
                'url': reverse('record_detail', args=[kind, inspection.pk]),
            })
    return sorted(records, key=lambda record: record['time'], reverse=True)


def inspection_records(request):
    table_definition = get_table_definition('inspection_records')
    records, table_state = apply_table_filters(
        request, _inspection_records(), table_definition,
    )
    page_obj = Paginator(records, table_state['page_size']).get_page(
        request.GET.get('page'),
    )
    return render(request, 'inspections/records.html', {
        'page_obj': page_obj,
        'table_definition': table_definition,
        'table_preference_key': 'inspection_records-global',
        'table_state': table_state,
        'page_sizes': PAGE_SIZES,
        'table_export_path': reverse(
            'table_export', args=['inspection_records'],
        ),
        'pagination_query': query_without_page(request),
    })


def error_records(request):
    table_definition = get_table_definition('error_records')
    records, table_state = apply_table_filters(
        request, _error_records(), table_definition,
    )
    page_obj = Paginator(records, table_state['page_size']).get_page(
        request.GET.get('page'),
    )
    return render(request, 'inspections/errors.html', {
        'page_obj': page_obj,
        'table_definition': table_definition,
        'table_state': table_state,
        'page_sizes': PAGE_SIZES,
        'table_export_path': reverse('table_export', args=['error_records']),
        'pagination_query': query_without_page(request),
    })


def computer_error_list(request):
    table_definition = get_table_definition('computer_errors')
    errors = Error_Computer.objects.select_related(
        'inspection__computer', 'inspection__log_file',
    )
    errors, table_state = apply_table_filters(request, errors, table_definition)
    page_obj = Paginator(errors, table_state['page_size']).get_page(
        request.GET.get('page'),
    )
    return render(request, 'devices/pc/error_list.html', {
        'page_obj': page_obj,
        'table_definition': table_definition,
        'table_state': table_state,
        'page_sizes': PAGE_SIZES,
        'table_export_path': reverse('table_export', args=['computer_errors']),
        'pagination_query': query_without_page(request),
    })


def infrastructure_inspection_detail(request, category, pk):
    return record_detail(request, category, pk)
