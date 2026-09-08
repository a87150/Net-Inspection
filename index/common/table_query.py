from datetime import date, datetime
from dataclasses import replace
from urllib.parse import urlencode

from django.core.exceptions import FieldError, ValidationError
from django.db.models import CharField, F, Q, Value
from django.db.models.functions import Coalesce, Concat
from django.db.models.query import QuerySet
from django.utils.dateparse import parse_date
from django.utils import timezone

from index.common.table_options import (
    build_field_option_context,
    include_active_filter_options,
)
from index.common.table_registry import TableDefinition, get_table_definition


PAGE_SIZES = (20, 50, 100, 200, 500)


def _parameter(prefix, name):
    return f'{prefix}_{name}' if prefix else name


def _parse_boolean(value):
    values = {
        'true': True, '1': True, 'yes': True, 'normal': True,
        'false': False, '0': False, 'no': False, 'abnormal': False,
    }
    return values.get(value.strip().lower())


def _parse_filter_date(value):
    if not value:
        return None
    try:
        return parse_date(value)
    except ValueError:
        return None


def _page_size(value, default):
    try:
        requested = int(value)
    except (TypeError, ValueError):
        return default
    if requested in PAGE_SIZES:
        return requested
    if requested <= PAGE_SIZES[0]:
        return PAGE_SIZES[0]
    if requested >= PAGE_SIZES[-1]:
        return PAGE_SIZES[-1]
    return max(size for size in PAGE_SIZES if size < requested)


def _table_state(request, definition, prefix, filters, sort, order, *, include_legacy_status=True):
    q = request.GET.get(_parameter(prefix, 'q'), '').strip()
    page_size = _page_size(
        request.GET.get(_parameter(prefix, 'page_size')),
        definition.default_page_size,
    )
    state = {
        'q': q,
        'filters': filters,
        'sort': sort,
        'order': order,
        'page_size': page_size,
        'field_definitions': definition.fields,
        'default_visible_fields': tuple(field for field in definition.fields if field.default_visible),
        'default_filter_fields': tuple(field for field in definition.fields if field.default_filter),
        'prefix': prefix,
        'parameter_names': {
            name: _parameter(prefix, name)
            for name in ('q', 'sort', 'order', 'page_size')
        },
    }
    state['active_filter_keys'] = tuple(filters)
    fields = {field.key: field for field in definition.fields}
    state['has_active_optional_filters'] = any(
        not fields[key].default_filter for key in filters
    )
    if include_legacy_status:
        state['status'] = request.GET.get(_parameter(prefix, 'status'), '').strip()
        state['parameter_names']['status'] = _parameter(prefix, 'status')
    return state


def _requested_sort(request, definition, prefix):
    sortable_fields = {field.key: field for field in definition.fields if field.sortable}
    requested = request.GET.get(_parameter(prefix, 'sort'), definition.default_sort)
    sort = requested if requested in sortable_fields else definition.default_sort
    order = request.GET.get(_parameter(prefix, 'order'), definition.default_order).lower()
    return sortable_fields[sort], sort, order if order in {'asc', 'desc'} else definition.default_order


def _requested_filters(request, definition, prefix):
    filters = {}
    for field in definition.fields:
        if not field.filterable:
            continue
        if field.kind in {'date', 'datetime'}:
            from_value = request.GET.get(_parameter(prefix, f'filter_{field.key}_from'), '').strip()
            to_value = request.GET.get(_parameter(prefix, f'filter_{field.key}_to'), '').strip()
            parsed_from = _parse_filter_date(from_value)
            parsed_to = _parse_filter_date(to_value)
            if parsed_from or parsed_to:
                filters[field.key] = {
                    'from': parsed_from.isoformat() if parsed_from else '',
                    'to': parsed_to.isoformat() if parsed_to else '',
                    '_from_date': parsed_from,
                    '_to_date': parsed_to,
                }
            continue
        value = request.GET.get(_parameter(prefix, f'filter_{field.key}'), '').strip()
        if not value:
            continue
        if field.kind == 'choice':
            allowed_values = {choice[0] for choice in field.choices}
            if allowed_values and value not in allowed_values:
                continue
            if field.comparison == 'boolean' and _parse_boolean(value) is None:
                continue
        if field.kind == 'boolean' and _parse_boolean(value) is None:
            continue
        filters[field.key] = value
    return filters


def _filter_queryset(queryset, definition, filters):
    fields = {field.key: field for field in definition.fields}
    invalid_keys = []
    for key, value in filters.items():
        field = fields[key]
        field = replace(field, source=field.query_source or field.source)
        try:
            if field.kind == 'text':
                queryset = queryset.filter(**{f'{field.source}__icontains': value})
            elif field.kind == 'choice':
                allowed_values = {choice[0] for choice in field.choices}
                if not allowed_values or value in allowed_values:
                    if field.comparison == 'boolean':
                        boolean = _parse_boolean(value)
                        if boolean is not None:
                            queryset = queryset.filter(**{field.source: boolean})
                    else:
                        if field.source == '_report_level' and value == 'abnormal':
                            queryset = queryset.filter(**{f'{field.source}__in': ('warning', 'critical')})
                        else:
                            queryset = queryset.filter(**{field.source: value})
                        if field.comparison == 'related':
                            queryset = queryset.distinct()
            elif field.kind == 'boolean':
                boolean = _parse_boolean(value)
                if boolean is not None:
                    queryset = queryset.filter(**{field.source: boolean})
            elif field.kind in {'date', 'datetime'}:
                from_value = value['_from_date']
                to_value = value['_to_date']
                lookup_source = (
                    f'{field.source}__date'
                    if field.kind == 'datetime'
                    else field.source
                )
                if from_value:
                    queryset = queryset.filter(
                        **{f'{lookup_source}__gte': from_value},
                    )
                if to_value:
                    queryset = queryset.filter(
                        **{f'{lookup_source}__lte': to_value},
                    )
        except (FieldError, TypeError, ValidationError, ValueError):
            invalid_keys.append(key)
    for key in invalid_keys:
        filters.pop(key, None)
    return queryset


def apply_table_query(request, queryset, definition: TableDefinition, *, prefix='', include_legacy_status=True):
    field_options, field_option_modes = build_field_option_context(
        queryset, definition,
    )
    filters = _requested_filters(request, definition, prefix)
    queryset = _filter_queryset(queryset, definition, filters)
    include_active_filter_options(
        field_options, field_option_modes, filters,
    )

    q = request.GET.get(_parameter(prefix, 'q'), '').strip()
    if q:
        fields = {field.key: field for field in definition.fields}
        condition = Q()
        for field_key in definition.search_fields:
            field = fields[field_key]
            condition |= Q(**{f'{field.query_source or field.source}__icontains': q})
        if definition.record_semantics and len(definition.search_fields) > 1:
            parts = []
            for field_key in definition.search_fields:
                if parts:
                    parts.append(Value(' '))
                field = fields[field_key]
                parts.append(Coalesce(F(field.query_source or field.source), Value(''), output_field=CharField()))
            queryset = queryset.alias(_table_search=Concat(*parts, output_field=CharField())).filter(_table_search__icontains=q)
        else:
            queryset = queryset.filter(condition)

    sort_field, sort, order = _requested_sort(request, definition, prefix)
    source = sort_field.query_source or sort_field.source
    ordering = f'-{source}' if order == 'desc' else source
    if definition.record_semantics:
        ordering = F(source).desc(nulls_first=True) if order == 'desc' else F(source).asc(nulls_last=True)
    table_state = _table_state(
        request, definition, prefix, filters, sort, order,
        include_legacy_status=include_legacy_status,
    )
    table_state['field_options'] = field_options
    table_state['field_option_modes'] = field_option_modes
    if definition.record_semantics:
        category = request.GET.get(_parameter(prefix, 'category'), '').strip()
        field = next((field for field in definition.fields if field.key == 'category'), None)
        table_state['category'] = category
        table_state['categories'] = [value for value, _ in field_options.get('category', ())]
        table_state['parameter_names']['category'] = _parameter(prefix, 'category')
        if category:
            queryset = queryset.filter(**{field.query_source or field.source: category}) if field else queryset.none()
    return queryset.order_by(ordering, 'pk'), table_state


def query_without_page(request, page_parameter='page'):
    params = request.GET.copy()
    params.pop(page_parameter, None)
    return params.urlencode()


def preserve_table_parameters(table_state, parameters):
    preserved = {
        str(name): str(value)
        for name, value in parameters.items()
        if value not in (None, '')
    }
    table_state['preserved_parameters'] = preserved
    table_state['reset_query'] = urlencode(preserved)
    return table_state


def _legacy_definition(search_fields, default_sort, model_name):
    definitions = {
        (('name', 'employee_id', 'department', 'email', 'leader'), 'name', 'People'): 'people',
        (('computer_name', 'os', 'user_name'), 'computer_name', 'Computer'): 'computers',
        (('device_name', 'ip', 'device_type', 'vendor'), 'device_name', 'Network_Device'): 'networks',
        (('device_name', 'ip', 'device_type', 'vendor'), 'device_name', 'SecurityDevice'): 'monitors',
        (('name', 'ip', 'server_type', 'os'), 'name', 'Server'): 'servers',
        (('account_name', 'login_name', 'ou', 'allowed_workstations'), 'login_name', 'Domain_Account'): 'domain_accounts',
        (('computer_name', 'os', 'ou'), 'computer_name', 'Domain_Computer'): 'domain_computers',
    }
    key = definitions.get((tuple(search_fields), default_sort, model_name))
    return get_table_definition(key) if key else None


def apply_queryset_table(
    request,
    queryset,
    *,
    search_fields,
    sort_fields,
    default_sort,
    status_handler=None,
    prefix='',
):
    definition = _legacy_definition(search_fields, default_sort, queryset.model.__name__)
    if definition is not None:
        queryset, state = apply_table_query(request, queryset, definition, prefix=prefix)
        if state['status'] and status_handler:
            queryset = status_handler(queryset, state['status'])
        return queryset, state

    q = request.GET.get(_parameter(prefix, 'q'), '').strip()
    status = request.GET.get(_parameter(prefix, 'status'), '').strip()
    requested_sort = request.GET.get(_parameter(prefix, 'sort'), default_sort)
    sort = requested_sort if requested_sort in sort_fields else default_sort
    order = request.GET.get(_parameter(prefix, 'order'), 'asc').lower()
    order = order if order in {'asc', 'desc'} else 'asc'

    if q:
        condition = Q()
        for field in search_fields:
            condition |= Q(**{f'{field}__icontains': q})
        queryset = queryset.filter(condition)
    if status and status_handler:
        queryset = status_handler(queryset, status)

    ordering = sort_fields[sort]
    if order == 'desc':
        ordering = f'-{ordering}'
    queryset = queryset.order_by(ordering, 'pk')
    return queryset, {
        'q': q,
        'status': status,
        'sort': sort,
        'order': order,
        'prefix': prefix,
        'parameter_names': {
            name: _parameter(prefix, name) for name in ('q', 'status', 'sort', 'order')
        },
    }


def _record_value(record, field):
    from net.data_exchange.table_csv import _raw_value
    return _raw_value(record, field.source)


def _record_matches(record, field, value):
    actual = _record_value(record, field)
    if field.kind == 'text':
        return value.lower() in str(actual or '').lower()
    if field.kind == 'choice':
        if field.source == 'result_level' and value == 'abnormal':
            return actual in ('warning', 'critical')
        if field.comparison == 'boolean':
            boolean = _parse_boolean(value)
            return boolean is None or actual is boolean
        return actual == value
    if field.kind == 'boolean':
        boolean = _parse_boolean(value)
        return boolean is None or actual is boolean
    if field.kind in {'date', 'datetime'}:
        from_value = value['_from_date']
        to_value = value['_to_date']
        if isinstance(actual, datetime):
            actual = (timezone.localtime(actual) if timezone.is_aware(actual) else actual).date()
        if isinstance(actual, date):
            return (from_value is None or actual >= from_value) and (to_value is None or actual <= to_value)
        return False
    return True


def apply_record_table(request, records, definition, *, prefix=''):
    if isinstance(definition, str):
        definition = get_table_definition(definition)
    records = list(records)
    field_options, field_option_modes = build_field_option_context(
        records, definition,
    )
    filters = _requested_filters(request, definition, prefix)
    include_active_filter_options(
        field_options, field_option_modes, filters,
    )
    fields = {field.key: field for field in definition.fields}
    filtered = [
        record for record in records
        if all(_record_matches(record, fields[key], value) for key, value in filters.items())
    ]

    q = request.GET.get(_parameter(prefix, 'q'), '').strip().lower()
    if q:
        filtered = [
            record for record in filtered
            if q in ' '.join(str(_record_value(record, fields[source]) or '') for source in definition.search_fields).lower()
        ]

    category = request.GET.get(_parameter(prefix, 'category'), '').strip()
    if category:
        from net.data_exchange.table_csv import _raw_value
        filtered = [record for record in filtered if _raw_value(record, 'category') == category]
    sort_field, sort, order = _requested_sort(request, definition, prefix)
    filtered.sort(
        key=lambda record: (_record_value(record, sort_field) is None, _record_value(record, sort_field)),
        reverse=order == 'desc',
    )
    state = _table_state(
        request, definition, prefix, filters, sort, order,
        include_legacy_status=False,
    )
    state['category'] = category
    from net.data_exchange.table_csv import _raw_value
    state['categories'] = sorted({value for record in records if (value := _raw_value(record, 'category'))})
    state['parameter_names']['category'] = _parameter(prefix, 'category')
    state['field_options'] = field_options
    state['field_option_modes'] = field_option_modes
    return filtered, state


def apply_table_filters(
    request,
    source,
    definition,
    *,
    prefix='',
    include_legacy_status=True,
):
    """Apply one allowlisted filter/sort contract to ORM or record sources."""
    # Old PC report bookmarks used a binary outcome. Keep that selection
    # visible and filter both actionable severity levels instead of dropping it.
    if (definition.key == 'computer_inspections'
            and request.GET.get(_parameter(prefix, 'filter_status'), '').strip() == 'abnormal'):
        definition = replace(definition, fields=tuple(
            replace(field, choices=field.choices + (('abnormal', '异常'),))
            if field.key == 'status' else field
            for field in definition.fields
        ))
    if hasattr(source, 'apply_query'):
        return source.apply_query(request, definition, prefix=prefix,
                                  include_legacy_status=include_legacy_status)
    if isinstance(source, QuerySet):
        return apply_table_query(
            request,
            source,
            definition,
            prefix=prefix,
            include_legacy_status=include_legacy_status,
        )
    return apply_record_table(request, source, definition, prefix=prefix)
