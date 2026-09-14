"""Batch reanalysis of stored evidence, never remote collection."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Exists, OuterRef, Q
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from index.common.table_query import apply_table_filters
from index.common.table_registry import get_table_definition
from index.inspections.forms import ManualTaskForm
from net.models import ComputerAnalysisProfile, ComputerLogFile, ComputerLogTransfer, TaskRun, TaskTargetRun
from net.inspections.queue import enqueue_task


def latest_batch():
    """Latest fetch containing analyzable evidence, ignoring empty fetches."""
    linked_logs = TaskTargetRun.objects.filter(
        task_id=OuterRef('pk'), fetched_logs__import_status='imported',
    )
    transferred_logs = ComputerLogTransfer.objects.filter(
        task_target__task_id=OuterRef('pk'), log_file__import_status='imported',
    )
    return TaskRun.objects.filter(task_type='computer_fetch').annotate(
        has_linked_logs=Exists(linked_logs), has_transferred_logs=Exists(transferred_logs),
    ).filter(Q(has_linked_logs=True) | Q(has_transferred_logs=True)).order_by('-created_at', '-pk').first()


def batch_logs(batch):
    if batch is None:
        return ComputerLogFile.objects.none()
    # Include duplicate-content transfers and repaired evidence as well as the
    # immutable fetched-log links created by successful original imports.
    return ComputerLogFile.objects.filter(import_status='imported').filter(
        Q(intended_scans__task=batch) | Q(transfers__task_target__task=batch),
    ).distinct()


def filtered_logs(request):
    rows, _state = apply_table_filters(request, ComputerLogFile.objects.all(), get_table_definition('computer_logs'))
    return rows.filter(import_status='imported')


def bulk_context(request, *, error='', bound_form=None):
    mode = request.POST.get('mode') if request.method == 'POST' else request.GET.get('bulk_mode')
    profiles = list(ComputerAnalysisProfile.objects.filter(is_enabled=True).order_by('name', 'pk'))
    chosen = request.POST.get('profile_id') if request.method == 'POST' else request.GET.get('profile_id')
    latest = TaskRun.objects.filter(task_type__in=['computer_analysis', 'computer_fetch']).order_by('-created_at').first()
    preferred = chosen or (str(latest.analysis_profile_id) if latest else '')
    profile = next((p for p in profiles if str(p.pk) == preferred), profiles[0] if profiles else None)
    form = bound_form if bound_form is not None else (ManualTaskForm(
        profile=profile, initial={'selected_items': profile.analysis_items}) if profile else None)
    batch = latest_batch()
    rows = batch_logs(batch) if mode == 'latest' else filtered_logs(request)
    return {'bulk_mode': mode, 'bulk_open': mode in {'filtered', 'latest'}, 'bulk_error': error,
            'bulk_profiles': profiles, 'bulk_profile': profile, 'bulk_form': form,
            'bulk_batch': batch, 'bulk_count': rows.count(),
            'bulk_batch_active': bool(batch and batch.status in TaskRun.ACTIVE_STATUSES),
            'bulk_get_params': [(k, v) for k, values in request.GET.lists() if k not in {'profile_id', 'bulk_mode'} for v in values]}


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
        mode = request.POST.get('mode')
        if mode == 'filtered':
            rows = filtered_logs(request)
        elif mode == 'latest':
            batch = latest_batch()
            if batch is None or str(batch.pk) != request.POST.get('batch_id'):
                raise ValidationError('最新获取批次已变化，请关闭窗口后重新选择。')
            if batch.status in TaskRun.ACTIVE_STATUSES:
                raise ValidationError('最新批次仍在获取中，请等待任务结束。')
            rows = batch_logs(batch)
        else:
            raise ValidationError('无效的批量分析范围。')
        ids = list(rows.order_by('pk').values_list('pk', flat=True))
        if not ids:
            raise ValidationError('该范围没有已成功入库的日志，未创建分析任务。')
        task = enqueue_task(profile, ids, 'manual', overrides={'selected_items': form.cleaned_data['selected_items']})
    except (ValidationError, ValueError, TypeError) as exc:
        from .logs import computer_log_list
        error = '；'.join(exc.messages) if isinstance(exc, ValidationError) else '无法创建批量分析任务，请检查选择。'
        return computer_log_list(request, bulk=bulk_context(request, error=error, bound_form=form))
    messages.success(request, f'已创建后台分析任务，共 {len(ids)} 份日志；原始证据和历史结果保留。')
    return redirect('task_detail', pk=task.pk)
