from dataclasses import dataclass
from io import StringIO

from django.contrib import messages
from index.common.access import is_admin
from django.core.management import call_command
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from index.common.table_query import PAGE_SIZES, apply_table_filters, query_without_page
from index.common.table_registry import get_table_definition
from net.models import Computer, Network_Device, People, SecurityDevice, Server
from net.data_exchange.inventory_csv import IMPORTABLE_ENTITIES, inventory_import_guide
from index.alerts.views import alert_modal_context
from index.inspections.tasks import task_modal_context


@dataclass(frozen=True)
class AssetPage:
    model: type
    table_key: str
    title: str
    inspection_kind: str = ''
    collection_hint: str = ''


ASSET_PAGES = {
    'people': AssetPage(People, 'people', '人员'),
    'computers': AssetPage(Computer, 'computers', 'PC'),
    'networks': AssetPage(
        Network_Device,
        'networks',
        '网络设备',
        'networks',
        '默认通过 SNMP + SSH 采集；深信服 AC 使用开放 API。请按导入窗口的字段示例填写。',
    ),
    'servers': AssetPage(
        Server,
        'servers',
        '服务器',
        'servers',
        'Linux 服务器通过 SSH 采集；Windows 服务器访问专用 HTTP JSON 服务。',
    ),
    'monitors': AssetPage(
        SecurityDevice,
        'monitors',
        '安防设备',
        'monitors',
        '通过设备自带 API 采集；海康和大华设备支持 Digest 认证。',
    ),
}


def _asset_page(kind):
    try:
        return ASSET_PAGES[kind]
    except KeyError as exc:
        raise Http404('未知的资产类型') from exc


def _display_value(obj, field):
    value = obj
    for name in field.source.split('__'):
        value = getattr(value, name, None)
        if value is None:
            break
    display_method = getattr(obj, f'get_{field.source}_display', None)
    if callable(display_method):
        value = display_method()
    return value


def _history_url(kind, asset):
    if kind == 'computers':
        return f"{reverse('computer_analysis_list')}?target={asset.pk}"
    if kind in {'networks', 'servers', 'monitors'}:
        return f"{reverse('record_list', args=[kind])}?target={asset.pk}"
    return ''


def asset_list(request, kind, *, integration_context=None, creation_form=None, edit_device=None):
    page = _asset_page(kind)
    table_definition = get_table_definition(page.table_key)
    objects, table_state = apply_table_filters(
        request,
        page.model.objects.all(),
        table_definition,
        include_legacy_status=False,
    )
    page_obj = Paginator(objects, table_state['page_size']).get_page(
        request.GET.get('page'),
    )
    context = {
        'inventory_import_guide': inventory_import_guide(kind) if is_admin(request.user) else [],
        'item_key': kind,
        'item_name': page.title,
        'table_definition': table_definition,
        'page_obj': page_obj,
        'table_state': table_state,
        'page_sizes': PAGE_SIZES,
        'table_export_path': reverse('table_export', args=[page.table_key]),
        'configuration_selection_path': (
            reverse('configuration_zip', args=[kind])
            if is_admin(request.user) and kind in {'networks', 'monitors'} else ''
        ),
        'pagination_query': query_without_page(request),
        'inspection_type': page.inspection_kind,
        'collection_hint': page.collection_hint,
        'import_enabled': is_admin(request.user) and kind in IMPORTABLE_ENTITIES,
        'personnel_api_import': is_admin(request.user) and kind == 'people',
        'data_source_note': (
            'PC 资料由终端采集器通过 API 上报日志后自动建立，无需导入设备清单。'
            if kind == 'computers' else ''
        ),
        'open_import_modal': (
            request.session.pop('open_import_modal', None) == kind
        ),
    }
    from .forms import DEVICE_KINDS, device_form, device_form_sections
    if kind in DEVICE_KINDS and is_admin(request.user):
        form = creation_form if creation_form is not None else device_form(kind, instance=edit_device)
        context.update(device_form=form, device_form_sections=device_form_sections(form),
                       open_add_device_modal=creation_form is not None and edit_device is None,
                       open_edit_device_modal=edit_device is not None,
                       edit_device=edit_device,
                       device_modal_mode='edit' if edit_device is not None else 'add')
    if kind == 'people' and is_admin(request.user):
        from index.people.integrations import people_modal_context
        modal_context = integration_context if integration_context is not None else people_modal_context(request)
        modal_context['open_import_modal'] = modal_context.get('open_import_modal') or context['open_import_modal']
        context.update(modal_context)
    if kind in {'computers', 'networks', 'servers', 'monitors'}:
        context.update(task_modal_context(
            request, kind, allow_target_selection=kind in {'networks', 'servers', 'monitors'},
        ))
        context.update(alert_modal_context(
            request, profile=context['task_default_profile'],
        ))
    return render(request, 'devices/list.html', context)


def asset_detail(request, kind, pk):
    page = _asset_page(kind)
    asset = get_object_or_404(page.model, pk=pk)
    table_definition = get_table_definition(page.table_key)
    detail_fields = [
        {
            'label': field.label,
            'value': _display_value(asset, field),
            'kind': field.kind,
        }
        for field in table_definition.fields
    ]
    latest = None
    latest_url = ''
    if kind == 'computers':
        latest = asset.analyses.order_by('-created_at', '-pk').first()
        if latest:
            latest_url = reverse('computer_analysis_detail', args=[latest.pk])
    elif kind in ('servers', 'networks', 'monitors'):
        latest = asset.inspections.order_by('-created_at', '-pk').first()
        if latest:
            latest_url = reverse('record_detail', args=[kind, latest.pk])
    return render(request, 'devices/detail.html', {
        'latest_record': latest,
        'latest_url': latest_url,
        'asset': asset,
        'inventory_import_guide': inventory_import_guide(kind) if is_admin(request.user) else [],
        'item_key': kind,
        'item_name': page.title,
        'detail_fields': detail_fields,
        'back_url': reverse('asset_list', args=[kind]),
        'history_url': _history_url(kind, asset),
        'inspection_type': page.inspection_kind,
        'manual_action_label': '手动执行分析' if kind == 'computers' else (
            '手动执行巡检' if page.inspection_kind else ''
        ),
    })


def person_detail(request, pk):
    return asset_detail(request, 'people', pk)


def people_statistics(request):
    as_of = timezone.localdate()
    counts = People.objects.aggregate(
        total=Count('pk'),
        active=Count('pk', filter=Q(is_active=True)),
        departed=Count('pk', filter=Q(is_active=False)),
        hires_this_year=Count(
            'pk', filter=Q(hire_date__year=as_of.year, hire_date__lte=as_of),
        ),
        departures_this_year=Count(
            'pk',
            filter=Q(
                is_active=False,
                departure_date__year=as_of.year,
                departure_date__lte=as_of,
            ),
        ),
    )
    valid_hire_dates = list(People.objects.filter(
        is_active=True,
        hire_date__isnull=False,
        hire_date__lte=as_of,
    ).values_list('hire_date', flat=True))
    average_tenure = (
        round(
            sum((as_of - hire_date).days for hire_date in valid_hire_dates)
            / len(valid_hire_dates)
            / 365.25,
            1,
        )
        if valid_hire_dates else 0.0
    )
    metrics = {
        **counts,
        'active_rate': (
            round(counts['active'] * 100 / counts['total'], 1)
            if counts['total'] else 0.0
        ),
        'average_active_tenure_years': average_tenure,
        'tenure_sample_count': len(valid_hire_dates),
    }

    department_totals = {}
    grouped_departments = People.objects.values('department').annotate(
        total=Count('pk'),
        active=Count('pk', filter=Q(is_active=True)),
    )
    for group in grouped_departments:
        department = (group['department'] or '').strip() or '未分配部门'
        row = department_totals.setdefault(
            department, {'department': department, 'total': 0, 'active': 0},
        )
        row['total'] += group['total']
        row['active'] += group['active']
    departments = []
    for row in department_totals.values():
        row['departed'] = row['total'] - row['active']
        row['active_rate'] = round(row['active'] * 100 / row['total'], 1)
        departments.append(row)
    departments.sort(key=lambda row: (-row['total'], row['department']))

    return render(request, 'people/statistics.html', {
        'as_of': as_of,
        'metrics': metrics,
        'departments': departments,
    })


def item_list(request, item):
    if item == 'accounts':
        return redirect('domain_controller_settings')
    return asset_list(request, item)


@require_POST
def run_infrastructure_inspection(request):
    asset_type = request.POST.get('asset_type', 'all')
    if asset_type not in {'all', 'networks', 'servers', 'monitors'}:
        raise Http404('未知的巡检类型')
    asset_id = request.POST.get('asset_id') or None
    output = StringIO()
    try:
        call_command(
            'run_network_checks',
            asset_type=asset_type,
            asset_id=asset_id,
            timeout=1500,
            stdout=output,
        )
        messages.success(request, output.getvalue().strip() or '巡检完成。')
    except Exception as exc:
        messages.error(request, f'巡检失败：{exc}')

    if asset_type == 'all':
        return redirect('index')
    return redirect('item_list', item=asset_type)
