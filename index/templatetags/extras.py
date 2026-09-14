# templatetags/extras.py
from datetime import date, datetime

from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def get_attr(obj, attr_name):
    for part in attr_name.split('__'):
        obj = obj.get(part, '') if isinstance(obj, dict) else getattr(obj, part, '')
    return obj


@register.filter
def get_item(mapping, key):
    return mapping.get(key, '') if hasattr(mapping, 'get') else ''


@register.filter
def format_table_value(value, kind):
    if value is None or value == '':
        return '-'
    if kind == 'boolean':
        return '是' if value else '否'
    if kind == 'datetime' and isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.strftime('%Y-%m-%d %H:%M:%S')
    if kind == 'date' and isinstance(value, date):
        return value.strftime('%Y-%m-%d')
    return value


@register.filter
def format_table_field(value, field):
    formatted = format_table_value(value, field.kind)
    if formatted != '-' and field.key in {'memory_total_gb', 'disk_total_gb'}:
        return f'{formatted} GB'
    return formatted


@register.simple_tag
def query_transform(request, **changes):
    query = request.GET.copy()
    for key, value in changes.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = value
    return query.urlencode()


@register.simple_tag
def table_filter_parameter(table_state, field, boundary=''):
    prefix = table_state.get('prefix', '')
    name = f'filter_{field.key}'
    if boundary:
        name = f'{name}_{boundary}'
    return f'{prefix}_{name}' if prefix else name
@register.simple_tag
def page_window(page_obj, radius=2):
    """Return nearby page numbers; first and last are rendered separately."""
    radius = max(0, int(radius))
    last_page = page_obj.paginator.num_pages
    start = max(2, page_obj.number - radius)
    end = min(last_page - 1, page_obj.number + radius)
    if end < start:
        return ()
    return tuple(range(start, end + 1))