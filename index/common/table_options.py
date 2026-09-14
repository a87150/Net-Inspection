from collections.abc import Mapping


SUPPORTED_OPTION_MODES = frozenset({'fixed', 'distinct', 'suggest', 'none'})


def _option(value):
    submitted = str(value)
    return submitted, submitted


def _queryset_values(queryset, field, *, complete=False):
    lookup = field.query_source or field.source
    values_query = (
        queryset.order_by()
        .exclude(**{f'{lookup}__isnull': True})
    )
    try:
        values_query = values_query.exclude(**{lookup: ''})
    except (TypeError, ValueError):
        # Numeric and other non-text fields cannot prepare an empty string.
        pass
    values_query = (
        values_query
        .values_list(lookup, flat=True)
        .distinct()
        .order_by(lookup)
    )
    return list(values_query if complete else values_query[:field.option_limit + 1])


def _record_value(record, source):
    if isinstance(record, Mapping):
        return record.get(source)
    value = record
    for part in source.split('__'):
        value = value.get(part) if isinstance(value, Mapping) else getattr(value, part, None)
        if value is None:
            break
    return value


def _record_values(records, field, *, complete=False):
    values = {
        value
        for record in records
        if (value := _record_value(record, field.source)) not in (None, '')
    }
    values = sorted(values, key=lambda value: str(value))
    return values if complete else values[:field.option_limit + 1]


def build_field_option_context(source, definition):
    """Return allowlisted field options and their effective rendering modes."""
    options = {}
    modes = {}
    is_queryset = hasattr(source, 'values_list') and hasattr(source, 'order_by')
    records = None if is_queryset else list(source)

    for field in definition.fields:
        mode = field.option_mode
        if mode not in SUPPORTED_OPTION_MODES:
            mode = 'none'
        if not field.filterable or mode == 'none':
            options[field.key] = ()
            modes[field.key] = 'none'
            continue
        if mode == 'fixed':
            options[field.key] = tuple(
                (str(value), str(label)) for value, label in field.choices
            )
            modes[field.key] = 'fixed'
            continue

        values = (
            _queryset_values(source, field, complete=definition.complete_options)
            if is_queryset
            else _record_values(records, field, complete=definition.complete_options)
        )
        overflow = len(values) > field.option_limit
        options[field.key] = tuple(
            _option(value) for value in (values if definition.complete_options else values[:field.option_limit])
        )
        modes[field.key] = 'suggest' if mode == 'suggest' or overflow else 'distinct'

    return options, modes


def build_field_options(queryset, definition):
    options, _modes = build_field_option_context(queryset, definition)
    return options


def include_active_filter_options(options, modes, filters):
    """Keep active dynamic values representable after source values disappear."""
    for key, value in filters.items():
        if isinstance(value, dict) or value in (None, ''):
            continue
        if modes.get(key) not in {'distinct', 'suggest'}:
            continue
        submitted = str(value)
        current = options.get(key, ())
        if submitted not in {option[0] for option in current}:
            options[key] = current + ((submitted, f'{submitted}（当前筛选）'),)
    return options
