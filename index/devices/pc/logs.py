"""Imported evidence navigation and enqueue-only reanalysis."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from index.common.table_query import PAGE_SIZES, apply_table_filters, query_without_page
from index.common.table_registry import get_table_definition
from index.inspections.forms import ManualTaskForm
from net.models import ComputerAnalysisProfile, ComputerLogFile
from net.inspections.queue import enqueue_task


def computer_log_list(request, *, bulk=None):
    from .bulk_analysis import bulk_context
    definition = get_table_definition('computer_logs')
    rows, state = apply_table_filters(request, ComputerLogFile.objects.all(), definition)
    return render(request, 'devices/pc/log_list.html', {
        **(bulk if bulk is not None else bulk_context(request)),
        'table_definition': definition, 'table_state': state, 'page_sizes': PAGE_SIZES,
        'table_export_path': reverse('table_export', args=['computer_logs']),
        'page_obj': Paginator(rows, state['page_size']).get_page(request.GET.get('page')),
        'pagination_query': query_without_page(request)})


def computer_log_detail(request, pk):
    log = get_object_or_404(ComputerLogFile, pk=pk)
    profiles = list(ComputerAnalysisProfile.objects.filter(is_enabled=True).order_by('name', 'pk'))
    profile = next((item for item in profiles if str(item.pk) == request.GET.get('profile_id')), profiles[0] if profiles else None)
    form = ManualTaskForm(profile=profile, initial={'selected_items': profile.analysis_items}) if profile else None
    return render(request, 'devices/pc/log_detail.html', {'log': log, 'profiles': profiles, 'profile': profile,
        'form': form, 'analyses': log.analyses.order_by('-created_at', '-pk'),
        'transfers': log.transfers.select_related('source', 'task_target').order_by('-created_at'),
        'archives': log.archives.order_by('-created_at')})


@require_POST
def computer_log_analyze(request, pk):
    log = get_object_or_404(ComputerLogFile, pk=pk)
    try:
        profile = ComputerAnalysisProfile.objects.filter(pk=request.POST.get('profile_id'), is_enabled=True).first()
        if profile is None or log.import_status != 'imported':
            raise ValidationError('仅已导入日志和启用的分析配置可以执行。')
        data = request.POST.copy()
        data['target_mode'] = 'selected'
        form = ManualTaskForm(data, profile=profile)
        if not form.is_valid():
            raise ValidationError('请选择当前配置允许的分析项目。')
        task = enqueue_task(profile, [log.pk], 'manual', overrides={'selected_items': form.cleaned_data['selected_items']})
    except (ValidationError, ValueError, TypeError) as exc:
        return HttpResponseBadRequest('无法创建分析任务：' + str(exc), content_type='text/plain; charset=utf-8')
    messages.success(request, '重新分析已排队；原始证据与历史结果保留。')
    return redirect('task_detail', pk=task.pk)
