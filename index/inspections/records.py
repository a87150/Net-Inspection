from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Case, CharField, Exists, F, OuterRef, Q, Value, When
from django.db.models.functions import Cast, Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from index.common.table_query import (
    PAGE_SIZES,
    apply_table_filters,
    preserve_table_parameters,
    query_without_page,
)
from index.common.table_registry import get_table_definition, project_record_definition
from net.models import (
    ComputerAnalysis,
    Error_Computer,
    Monitor_Inspection,
    Network_Device_Inspection,
    RecordStatus,
    Server_Inspection,
)
from net.inspections.task_summary import project_task_queryset
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
    from net.devices.pc.severity import grade_issue, RANK
    from net.inspections.issues import issue_categories
    issues = [] if 'details' in inspection.get_deferred_fields() else inspection.details.get('issue_findings', [])
    if not issues and not ok:
        issues = [{'analysis_item': 'inspection_collection', 'severity': 'critical'}]
    level = max((grade_issue(issue)['severity'] for issue in issues), key=RANK.get, default='normal')
    if hasattr(inspection, '_report_level'):
        level = inspection._report_level
    if level in {'warning', 'critical'}:
        ok = False
    return {
        'pk': inspection.pk,
        'problem_types': inspection.report_problem_types if hasattr(inspection, '_report_level') else issue_categories(issues),
        'result_level': level,
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


def _infrastructure_records(kind, *, target='', latest_only=True, task=None):
    page = _record_page(kind)
    from .result_query import infrastructure_queryset
    queryset = infrastructure_queryset(kind, page)
    if task is not None:
        queryset = queryset.filter(task_target__task=task)
    elif latest_only:
        latest_task = _latest_project_task(kind)
        if latest_task:
            queryset = queryset.filter(task_target__task=latest_task)
    if target:
        try:
            queryset = queryset.filter(**{f'{page.asset_field}_id': target})
        except (ValidationError, ValueError):
            queryset = queryset.none()
    return queryset


def _computer_analysis_records(target='', *, task=None):
    from .result_query import computer_queryset, people_analysis_rows
    analyses = computer_queryset()
    latest_task = task if task is not None else _latest_project_task('computers')
    if latest_task:
        analyses = analyses.filter(task_target__task=latest_task)
    if target:
        try:
            analyses = analyses.filter(computer_id=target)
        except (ValidationError, ValueError):
            analyses = analyses.none()
    if latest_task and not target and latest_task.profile_snapshot.get('matching_mode') == 'people':
        return people_analysis_rows(analyses, latest_task.parameters_snapshot.get('personnel_roster', []))
    return analyses


def _project_workspace_context(request, kind):
    from net.inspections.task_summary import project_workspace_context
    return project_workspace_context(request, kind)


def record_list(request, kind):
    page = _record_page(kind)
    table_definition = project_record_definition(kind)
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
    from net.inspections.selection import NETWORK_FUNCTION_ITEMS
    detail_fields = [(NETWORK_FUNCTION_ITEMS.get(key,key),value) for key,value in (inspection.details or {}).items()]
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
    from .analysis_summary import latest_analysis_statistics
    context = {
        'record_type': 'computer_analysis',
        'item_name': 'PC',
    }
    context.update(_project_workspace_context(request, 'computers'))
    context['latest_analysis_statistics'] = latest_analysis_statistics(context['latest_task'])
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


class _GlobalRecordRows:
    """A lazy, database-backed union whose rows gain only their detail URL in Python."""
    def __init__(self, query_or_projections, url_for):
        self.projections = tuple(query_or_projections) if isinstance(query_or_projections, (list, tuple)) else None
        self.query = self._union(self.projections) if self.projections else query_or_projections
        self.url_for = url_for

    @staticmethod
    def _union(projections):
        return projections[0].union(*projections[1:], all=True)

    def _row(self, row):
        row['url'] = self.url_for(row)
        return row

    def __iter__(self):
        yield from (self._row(row) for row in self.query.iterator())

    def iterator(self, chunk_size=1000):
        yield from (self._row(row) for row in self.query.iterator(chunk_size=chunk_size))

    def __getitem__(self, index):
        result = self.query[index]
        if isinstance(index, slice):
            return [self._row(row) for row in result]
        return self._row(result)

    def count(self):
        return self.query.count()

    def apply_query(self, request, definition, *, prefix='', include_legacy_status=True):
        # A UNION cannot be filtered afterwards on every supported backend. Apply the
        # allowlisted table contract to each projection before composing the UNION.
        from index.common.table_query import (
            _filter_queryset, _requested_filters, _requested_sort, _table_state,
        )
        filters = _requested_filters(request, definition, prefix)
        q = request.GET.get(f'{prefix}_q' if prefix else 'q', '').strip()
        category = request.GET.get(f'{prefix}_category' if prefix else 'category', '').strip()
        branches = self.projections or (self.query,)
        filtered = []
        for branch in branches:
            branch = _filter_queryset(branch, definition, filters.copy())
            if q:
                condition = Q()
                for field_key in definition.search_fields:
                    field = next(field for field in definition.fields if field.key == field_key)
                    condition |= Q(**{f'{field.query_source or field.source}__icontains': q})
                branch = branch.filter(condition)
            if category:
                branch = branch.filter(category=category)
            filtered.append(branch.order_by())
        query = self._union(filtered)
        sort_field, sort, order = _requested_sort(request, definition, prefix)
        source = sort_field.query_source or sort_field.source
        query = query.order_by(f'-{source}' if order == 'desc' else source, 'record_id')
        state = _table_state(request, definition, prefix, filters, sort, order,
                             include_legacy_status=include_legacy_status)
        from index.common.table_options import SUPPORTED_OPTION_MODES, include_active_filter_options
        option_source = self.projections or (self.query,)
        options, modes = {}, {}
        for field in definition.fields:
            mode = field.option_mode if field.option_mode in SUPPORTED_OPTION_MODES else 'none'
            if not field.filterable or mode == 'none':
                options[field.key], modes[field.key] = (), 'none'
                continue
            if mode == 'fixed':
                options[field.key] = tuple((str(value), str(label)) for value, label in field.choices)
                modes[field.key] = 'fixed'
                continue
            values = set()
            limit = None if definition.complete_options else field.option_limit + 1
            for branch in option_source:
                lookup = field.query_source or field.source
                candidate = branch.order_by().exclude(**{f'{lookup}__isnull': True})
                try:
                    candidate = candidate.exclude(**{lookup: ''})
                except (TypeError, ValueError):
                    pass
                candidate = candidate.values_list(lookup, flat=True).distinct().order_by(lookup)
                values.update(candidate if limit is None else candidate[:limit])
            values = sorted(values, key=lambda value: str(value))
            overflow = not definition.complete_options and len(values) > field.option_limit
            options[field.key] = tuple((str(value), str(value)) for value in (
                values if definition.complete_options else values[:field.option_limit]
            ))
            modes[field.key] = 'suggest' if mode == 'suggest' or overflow else 'distinct'
        include_active_filter_options(options, modes, filters)
        state['field_options'], state['field_option_modes'] = options, modes
        state['category'] = category
        state['categories'] = [value for value, _label in options.get('category', ())]
        state['parameter_names']['category'] = f'{prefix}_category' if prefix else 'category'
        return _GlobalRecordRows(query, self.url_for), state


def _global_url(row):
    if row['record_kind'] == 'computer':
        return reverse('computer_analysis_detail', args=[row['record_id']])
    return reverse('record_detail', args=[row['record_kind'], row['record_id']])


def _inspection_records(per_type=None):
    from .result_query import computer_queryset, infrastructure_queryset
    fields = ('problem_types', 'result_level', 'category', 'asset', 'time', 'ok',
              'summary', 'execution_status', 'task_source', 'error_count',
              'key_metrics', 'record_id', 'record_kind')
    computer = computer_queryset().annotate(
        problem_types=F('report_problem_types'), result_level=F('_report_level'),
        category=Value('PC 分析'), asset=F('computer__computer_name'), time=F('created_at'),
        execution_status=F('_report_execution_status'), task_source=F('_report_task_source'),
        error_count=F('_report_error_count'), key_metrics=F('report_metrics'),
        record_id=F('pk'), record_kind=Value('computer'),
    ).order_by().values(*fields)
    projections = [computer]
    for kind, page in RECORD_PAGES.items():
        projections.append(infrastructure_queryset(kind, page).annotate(
            problem_types=F('report_problem_types'), result_level=F('_report_level'),
            category=F('_report_category'), asset=F('_report_asset'), time=F('created_at'),
            execution_status=F('_report_execution_status'),
            task_source=F('_report_task_source'), error_count=F('_report_error_count'),
            key_metrics=F('report_metrics'), record_id=F('pk'),
            record_kind=Value(kind),
        ).order_by().values(*fields))
    return _GlobalRecordRows(projections, _global_url)


def _error_records(per_type=None):
    from .result_query import infrastructure_queryset
    fields = ('category', 'asset', 'time', 'type', 'message', 'record_id', 'record_kind')
    computer = Error_Computer.objects.annotate(
        category=Value('PC'), asset=F('inspection__computer__computer_name'),
        time=F('inspection__created_at'), type=F('error_type'), message=F('error_message'),
        record_id=F('inspection_id'), record_kind=Value('computer'),
    ).order_by().values(*fields)
    projections = [computer]
    actionable = Q(is_reachable=False) | ~Q(status=RecordStatus.SUCCESS) | Q(report_severity__in=('warning', 'critical'))
    for kind, page in RECORD_PAGES.items():
        projections.append(infrastructure_queryset(kind, page).filter(actionable).annotate(
            category=F('_report_category'), asset=F('_report_asset'), time=F('created_at'),
            type=Case(
                When(is_reachable=False, then=Value('设备不可达')),
                When(~Q(status=RecordStatus.SUCCESS), then=Value('采集异常')),
                When(report_severity__in=('warning', 'critical'), then=Value('业务告警')),
                default=Value('采集异常'), output_field=CharField(),
            ),
            message=Coalesce(Cast('summary', CharField()), Value('设备巡检未完整成功'), output_field=CharField()),
            record_id=F('pk'), record_kind=Value(kind),
        ).order_by().values(*fields))
    return _GlobalRecordRows(projections, _global_url)


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
