"""Bulk analysis of the latest retained database log for each selected PC."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from index.common.table_query import apply_table_filters
from index.common.table_registry import get_table_definition
from index.inspections.forms import ManualTaskForm
from net.devices.pc.analysis_scope import enqueue_latest_analysis, latest_logs
from net.models import ComputerAnalysisProfile, ComputerLogFile, TaskRun


def filtered_computer_ids(request):
    rows, _state = apply_table_filters(
        request, ComputerLogFile.objects.filter(retained=True),
        get_table_definition('computer_logs'),
    )
    return rows.exclude(computer_id=None).values_list('computer_id', flat=True).distinct()


def bulk_context(request, *, error='', bound_form=None):
    open_modal = request.POST.get('bulk_mode') if request.method == 'POST' else request.GET.get('bulk_mode')
    profiles = list(ComputerAnalysisProfile.objects.filter(is_enabled=True).order_by('name', 'pk'))
    chosen = request.POST.get('profile_id') if request.method == 'POST' else request.GET.get('profile_id')
    latest = TaskRun.objects.filter(task_type='computer_analysis').order_by('-created_at').first()
    profile = next((item for item in profiles if str(item.pk) == (chosen or str(getattr(latest, 'analysis_profile_id', '')))), profiles[0] if profiles else None)
    form = bound_form or (ManualTaskForm(profile=profile, initial={'selected_items': profile.analysis_items}) if profile else None)
    computer_ids = filtered_computer_ids(request)
    rows = latest_logs(computer_ids=computer_ids)
    return {
        'bulk_mode': open_modal, 'bulk_open': open_modal == 'filtered', 'bulk_error': error,
        'bulk_profiles': profiles, 'bulk_profile': profile, 'bulk_form': form,
        'bulk_count': rows.count(),
        'bulk_get_params': [(key, value) for key, values in request.GET.lists()
                            if key not in {'profile_id', 'bulk_mode'} for value in values],
    }


@login_required
@require_POST
def computer_logs_analyze_bulk(request):
    form = None
    try:
        profile = ComputerAnalysisProfile.objects.filter(pk=request.POST.get('profile_id'), is_enabled=True).first()
        if profile is None:
            raise ValidationError('请选择启用的分析配置。')
        data = request.POST.copy()
        data['target_mode'] = 'selected'
        form = ManualTaskForm(data, profile=profile)
        if not form.is_valid():
            raise ValidationError('请选择当前配置允许的分析项目。')
        if request.POST.get('mode') != 'filtered':
            raise ValidationError('无效的批量分析范围。')
        computer_ids = filtered_computer_ids(request)
        count = latest_logs(computer_ids=computer_ids).count()
        if not count:
            raise ValidationError('该筛选范围没有可分析的当前日志，未创建分析任务。')
        task = enqueue_latest_analysis(profile, 'manual', overrides={'selected_items': form.cleaned_data['selected_items']}, computer_ids=computer_ids)
    except (ValidationError, ValueError, TypeError) as exc:
        from .logs import computer_log_list
        error = '；'.join(exc.messages) if isinstance(exc, ValidationError) else '无法创建批量分析任务，请检查选择。'
        return computer_log_list(request, bulk=bulk_context(request, error=error, bound_form=form))
    messages.success(request, f'已创建后台分析任务，共 {count} 台 PC 的最新日志。')
    return redirect('task_detail', pk=task.pk)
