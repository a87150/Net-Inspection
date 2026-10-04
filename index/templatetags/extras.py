# templatetags/extras.py
from datetime import date, datetime

from django import template
from django.utils import timezone

register = template.Library()

DELIVERY_STATUS_ORDER = {
    'pending': 0,
    'sending': 1,
    'sent': 2,
    'retry': 3,
    'failed': 4,
}


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
    # choice 字段存的是代码（camera/nvr），筛选用的是中文标签；表格要跟筛选一致。
    # CSV 导出（data_exchange/table_csv.py）一直是这么映射的，这里补齐 HTML 侧。
    labels = dict(field.choices or ())
    if field.kind == 'choice' and value in labels:
        value = labels[value]
    formatted = format_table_value(value, field.kind)
    if formatted != '-' and field.key in {'memory_total_gb', 'disk_total_gb'}:
        return f'{formatted} GB'
    return formatted


@register.filter
def group_delivery_outcomes(deliveries):
    """Group channel names by delivery status for compact table rendering."""
    grouped = {}
    for delivery in deliveries:
        group = grouped.setdefault(delivery.status, {
            'status': delivery.status,
            'status_label': delivery.get_status_display(),
            'channel_names': [],
        })
        group['channel_names'].append(delivery.channel.name)
    results = []
    for group in sorted(
        grouped.values(),
        key=lambda item: (DELIVERY_STATUS_ORDER.get(item['status'], 99), item['status']),
    ):
        channel_names = '、'.join(sorted(group['channel_names'], key=str.casefold))
        results.append({
            'status': group['status'],
            'label': f"{channel_names}：{group['status_label']}",
        })
    return results


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
