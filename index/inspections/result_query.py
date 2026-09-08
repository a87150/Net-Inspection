"""Lazy report queries. Payloads stay on detail views; rows are formatted after slicing."""
from django.db.models import BooleanField, Case, CharField, Count, F, Func, IntegerField, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce, Concat, Lower, NullIf
from django.db.models.query import ModelIterable
from django.db.models.fields.json import KeyTextTransform

from net.models import RecordStatus, TaskRun


class JsonKeyType(Func):
    """Check the JSON type, not the extracted text (which may literally be 'null')."""
    function = 'JSON_TYPE'
    output_field = CharField()

    def as_mysql(self, compiler, connection, **extra_context):
        return self.as_sql(compiler, connection,
                           template='JSON_TYPE(JSON_EXTRACT(%(expressions)s))', **extra_context)


def report_queryset(model):
    errors = model._meta.get_field('errors').related_model.objects.filter(
        inspection_id=OuterRef('pk'),
    ).order_by().values('inspection_id').annotate(total=Count('pk')).values('total')
    queryset = model.objects.select_related('task_target__task').defer(
        'details', 'task_target__result_snapshot', 'task_target__target_snapshot',
        'task_target__task__selected_items_snapshot', 'task_target__task__parameters_snapshot',
        'task_target__task__target_scope_snapshot', 'task_target__task__profile_snapshot',
    ).annotate(
        _error_total=Coalesce(Subquery(errors, output_field=IntegerField()), Value(0)),
        _report_execution_status=Case(*[
            When(status=value, then=Value(label)) for value, label in RecordStatus.choices
        ], default=F('status'), output_field=CharField()),
        _report_task_source=Case(
            When(task_target__isnull=True, then=Value('未关联任务')),
            *[When(task_target__task__source=value, then=Value(label)) for value, label in TaskRun.Source.choices],
            default=F('task_target__task__source'), output_field=CharField(),
        ),
    ).annotate(
        _report_error_count=Case(When(_error_total=0, then=Case(
            When(status='success', then=Value(0)), default=Value(1), output_field=IntegerField(),
        )), default=F('_error_total'), output_field=IntegerField()),
    )
    return queryset


def computer_queryset():
    from net.models import ComputerAnalysis
    enrichment = {}
    enrichment_types = {}
    for key in ('employee_number', 'personnel_name', 'department', 'user_ou', 'computer_ou', 'site'):
        type_source = f'_report_enrichment_type_{key}'
        enrichment_types[type_source] = Lower(JsonKeyType(F('report_enrichment'), Value(f'$."{key}"')))
        enrichment[f'_report_enrichment_{key}'] = Case(
            When(**{type_source: 'null'}, then=Value(None)),
            default=KeyTextTransform(key, 'report_enrichment'), output_field=CharField(),
        )
    return report_queryset(ComputerAnalysis).select_related('computer', 'log_file').defer(
        'exceptions', 'analysis_items', 'log_file__payload',
    ).annotate(
        has_errors=Case(When(_error_total__gt=0, then=Value(True)), default=Value(False)),
        _report_level=Case(
            When(~Q(report_severity=''), then=F('report_severity')),
            When(status='failed', then=Value('critical')),
            When(_error_total__gt=0, then=Value('warning')),
            default=Value('normal'), output_field=CharField(),
        ),
        ok=Case(When(status='success', _error_total=0, then=Value(True)), default=Value(False)),
    ).alias(**enrichment_types).annotate(**enrichment).order_by('-created_at', 'pk')


class InfrastructureRows(ModelIterable):
    def __iter__(self):
        from .records import RECORD_PAGES, _record_row
        kind, page = next((kind, page) for kind, page in RECORD_PAGES.items()
                          if page.model is self.queryset.model)
        for inspection in super().__iter__():
            yield _record_row(kind, page, inspection)


def infrastructure_queryset(kind, page):
    asset = page.asset_field
    name = f'{asset}__name' if kind == 'servers' else f'{asset}__device_name'
    fallback = Value(page.title)
    if kind == 'servers':
        from net.models import Server
        fallback = Case(*[When(server__server_type=value, then=Value(label))
                          for value, label in Server._meta.get_field('server_type').choices],
                        default=F('server__server_type'), output_field=CharField())
    queryset = report_queryset(page.model).select_related(asset).defer('raw_output').annotate(
        _report_category=Value(page.title, output_field=CharField()),
        _report_asset=Concat(Coalesce(NullIf(F(name), Value('')), fallback),
                             Value(' ('), F(f'{asset}__ip'), Value(')'), output_field=CharField()),
        _report_level=Coalesce(NullIf('report_severity', Value('')), Value('normal'), output_field=CharField()),
    ).annotate(
        ok=Case(When(is_reachable=True, status='success', _report_level__in=['normal', 'info'],
                     then=Value(True)), default=Value(False), output_field=BooleanField()),
    ).annotate(
        _report_summary=Coalesce(NullIf('summary', Value('')), Case(
            When(ok=True, then=Value('巡检正常')), default=Value('设备不可达'),
            output_field=CharField()), output_field=CharField()),
    ).order_by('-created_at', 'pk')
    queryset._iterable_class = InfrastructureRows
    return queryset


def people_analysis_rows(analyses, roster):
    from .people_result_query import PeopleResultRows
    return PeopleResultRows.from_roster(analyses, roster)
