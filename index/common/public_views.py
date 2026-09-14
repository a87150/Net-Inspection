"""Guest presentation: independent queries, explicit projections, safe links.

Do not reuse authenticated view/context builders here. Adding a model field
must never implicitly add a public column, search term or sort key.
"""
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.template.response import TemplateResponse
from django.urls import reverse

from net.models import Computer, Network_Device, People, SecurityDevice, Server


PAGE_SIZES = (20, 50, 100, 200, 500)
# (public key, database column, label); None denotes a fixed public type.
PUBLIC_INVENTORIES = {
    'people': (People, '人员', (
        ('name', 'name', '姓名'), ('department', 'department', '部门'),
        ('is_active', 'is_active', '在职'),
    )),
    'computers': (Computer, 'PC', (
        ('name', 'computer_name', '设备名称'), ('ip', 'ip_addresses', 'IP'),
        ('type', None, '类型'), ('manufacturer', 'manufacturer', '厂商'),
    )),
    'networks': (Network_Device, '网络设备', (
        ('name', 'device_name', '设备名称'), ('ip', 'ip', 'IP'),
        ('type', 'device_type', '类型'), ('manufacturer', 'vendor', '厂商'),
    )),
    'servers': (Server, '服务器', (
        ('name', 'name', '设备名称'), ('ip', 'ip', 'IP'),
        ('type', 'server_type', '类型'), ('manufacturer', 'manufacturer', '厂商'),
    )),
    'monitors': (SecurityDevice, '安防设备', (
        ('name', 'device_name', '设备名称'), ('ip', 'ip', 'IP'),
        ('type', 'device_type', '类型'), ('manufacturer', 'vendor', '厂商'),
    )),
}


def _summary(request, view_name):
    people = People.objects.aggregate(total=Count('pk'), active=Count('pk', filter=Q(is_active=True)))
    people['inactive'] = people['total'] - people['active']
    context = {'title': '人员统计' if view_name == 'people_statistics' else '资产概览',
               'people_summary': people}
    if view_name == 'index':
        context['inventories'] = [
            {'title': title, 'total': model.objects.count(),
             'url': reverse('asset_list', args=[kind])}
            for kind, (model, title, _fields) in PUBLIC_INVENTORIES.items()
        ]
    else:
        context['departments'] = list(People.objects.order_by('department').values('department').annotate(
            total=Count('pk'), active=Count('pk', filter=Q(is_active=True)),
            inactive=Count('pk', filter=Q(is_active=False)),
        ))
    return TemplateResponse(request, 'public/summary.html', context)


def _inventory(request, kind):
    if kind not in PUBLIC_INVENTORIES:
        raise Http404('未知的公开资产类型')
    model, title, fields = PUBLIC_INVENTORIES[kind]
    sources = {key: source for key, source, _label in fields}
    # Accept the existing names for basic device columns, without accepting any
    # arbitrary model lookup or relationship traversal supplied by the client.
    aliases = {source: key for key, source, _label in fields if source}
    sort = request.GET.get('sort', 'name')
    sort = aliases.get(sort, sort)
    if sort not in sources:
        sort = 'name'
    order = request.GET.get('order', 'asc')
    if order not in ('asc', 'desc'):
        order = 'asc'
    try:
        size = int(request.GET.get('page_size', 50))
    except (ValueError, TypeError):
        size = 50
    size = max((n for n in PAGE_SIZES if n <= size), default=20)
    safe_params = {'sort': sort, 'order': order, 'page_size': size}
    queryset = model.objects.all()
    # Search is deliberately name-only, never the rich table's search fields.
    for param in ('q', 'filter_name'):
        value = request.GET.get(param, '').strip()[:255]
        if param == 'filter_name' and not value:
            value = request.GET.get('filter_' + sources['name'], '').strip()[:255]
        if value:
            queryset = queryset.filter(**{sources['name'] + '__icontains': value})
            safe_params[param] = value
    sort_source = sources[sort] or sources['name']
    queryset = queryset.order_by(('-' if order == 'desc' else '') + sort_source, 'pk')
    # Evaluate only the selected page, converting every row into a plain dict.
    projection = tuple(source for source in sources.values() if source)
    page = Paginator(queryset.values(*projection), size).get_page(request.GET.get('page'))
    page.object_list = [
        {key: row[source] if source else title for key, source, _label in fields}
        for row in page.object_list
    ]
    columns = []
    for key, _source, label in fields:
        params = dict(safe_params, sort=key, order='desc' if sort == key and order == 'asc' else 'asc')
        columns.append({'key': key, 'label': label, 'url': '?' + urlencode(params)})
    context = {
        'title': title, 'page_obj': page, 'columns': columns,
        'page_sizes': PAGE_SIZES, 'page_size': size, 'sort': sort, 'order': order,
        'q': safe_params.get('q', ''), 'filter_name': safe_params.get('filter_name', ''),
        'previous_url': '?' + urlencode(dict(safe_params, page=page.previous_page_number())) if page.has_previous() else '',
        'next_url': '?' + urlencode(dict(safe_params, page=page.next_page_number())) if page.has_next() else '',
    }
    return TemplateResponse(request, 'public/list.html', context)


def public_response(request, view_name, kwargs):
    """AccessMiddleware dispatch target for the four approved public routes."""
    if view_name in ('index', 'people_statistics'):
        return _summary(request, view_name)
    if view_name == 'asset_list':
        return _inventory(request, kwargs.get('kind'))
    if view_name == 'item_list':
        return _inventory(request, kwargs.get('item'))
    raise Http404('未知的公开页面')
