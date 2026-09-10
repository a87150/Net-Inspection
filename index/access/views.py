from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from index.access.forms import AccessRecordSourceForm
from index.common.access import admin_required, is_admin
from index.common.table_query import PAGE_SIZES, apply_table_filters, query_without_page
from index.common.table_registry import get_table_definition
from net.access.adapters import V6600_V6000_COMPAT_VERSION, build_adapter
from net.access.tasks import enqueue_access_sync
from net.models.access import AccessRecord, AccessRecordSource


@require_GET
def access_record_list(request):
    definition = get_table_definition('access_records')
    records, table_state = apply_table_filters(
        request, AccessRecord.objects.select_related('source', 'source__security_device'), definition,
        include_legacy_status=False,
    )
    return render(request, 'access/record_list.html', {
        'page_obj': Paginator(records, table_state['page_size']).get_page(request.GET.get('page')),
        'table_definition': definition, 'table_preference_key': 'access_records',
        'table_state': table_state, 'page_sizes': PAGE_SIZES,
        'pagination_query': query_without_page(request),
        'access_sources': AccessRecordSource.objects.filter(is_enabled=True).order_by('name'),
        'form': AccessRecordSourceForm() if is_admin(request.user) else None,
        'sources': AccessRecordSource.objects.order_by('name') if is_admin(request.user) else [],
        'compatibility_version': V6600_V6000_COMPAT_VERSION,
    })


@admin_required
def access_source_settings(request):
    source = get_object_or_404(AccessRecordSource, pk=request.GET.get('source')) if request.GET.get('source') else None
    return render(request, 'access/source_settings.html', {
        'form': AccessRecordSourceForm(instance=source), 'source': source,
        'source_saved': request.GET.get('saved') == '1',
        'sources': AccessRecordSource.objects.order_by('name'),
        'compatibility_version': V6600_V6000_COMPAT_VERSION,
    })


def _source_form_response(request, source, form, status=400):
    return render(request, 'access/source_settings.html', {
        'form': form, 'source': source, 'sources': AccessRecordSource.objects.order_by('name'),
        'compatibility_version': V6600_V6000_COMPAT_VERSION,
    }, status=status)


@admin_required
@require_POST
def access_source_save(request):
    source = get_object_or_404(AccessRecordSource, pk=request.POST.get('source_id')) if request.POST.get('source_id') else None
    form = AccessRecordSourceForm(request.POST, instance=source)
    if not form.is_valid():
        return _source_form_response(request, source, form)
    saved = form.save()
    messages.success(request, f'已保存门禁平台来源“{saved.name}”。')
    return redirect(f"{reverse('access_source_settings')}?source={saved.pk}&saved=1")


@admin_required
@require_POST
def access_source_test(request):
    source = get_object_or_404(AccessRecordSource, pk=request.POST.get('source_id')) if request.POST.get('source_id') else None
    # Save the submitted values first: testing cannot discard an edited URL,
    # endpoint, authentication method, or replacement token.
    form = AccessRecordSourceForm(request.POST, instance=source)
    if not form.is_valid():
        return _source_form_response(request, source, form)
    source = form.save()
    try:
        # Adapter probes exactly one page and never writes records/cursor.
        build_adapter(source).test_connection()
    except (ValidationError, ValueError) as exc:
        messages.error(request, f'连接测试失败：{exc}')
    except Exception as exc:
        messages.error(request, f'连接测试失败（{type(exc).__name__}）。')
    else:
        messages.success(request, '连接测试成功，平台事件接口可访问。')
    return redirect(f"{reverse('access_source_settings')}?source={source.pk}&saved=1")


@admin_required
@require_POST
def access_record_sync(request):
    try:
        tasks = enqueue_access_sync(request.POST.getlist('source_ids'))
    except ValidationError as exc:
        messages.error(request, '；'.join(exc.messages))
    else:
        messages.success(request, f'已创建 {len(tasks)} 个门禁记录采集任务。')
    return redirect('access_record_list')
