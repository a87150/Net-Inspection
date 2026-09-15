"""Fast Web entrypoints for the database-backed inspection task queue."""

from types import SimpleNamespace
from urllib.parse import urlsplit

from django.contrib import messages
from index.common.access import is_admin, admin_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from index.inspections.forms import (
    ComputerAnalysisProfileConfigForm,
    InspectionProfileConfigForm,
    ManualTaskForm,
    analysis_item_choices,
    inspection_item_choices,
)
from index.common.table_query import PAGE_SIZES, apply_table_filters, query_without_page
from index.common.table_registry import get_table_definition, project_record_definition
from index.devices.pc.software_policy import (
    software_policy_target,
    store_software_policy_upload,
)
from net.models import (
    ComputerAnalysisProfile,
    ComputerLogFile,
    InspectionProfile,
    SecurityDevice,
    Network_Device,
    Schedule,
    Server,
    TaskRun,
    TaskTargetRun,
)
from net.inspections.executor import _database_guard
from net.inspections.queue import cancel_task, enqueue_computer_fetch_task, enqueue_task, is_sqlite_busy


PROJECTS = {
    'networks': (InspectionProfile.DeviceType.NETWORK_DEVICE, Network_Device, 'networks'),
    'servers': (InspectionProfile.DeviceType.SERVER, Server, 'servers'),
    'monitors': (InspectionProfile.DeviceType.MONITOR, SecurityDevice, 'monitors'),
}


def _safe_next(request, value, fallback):
    candidate = (value or '').strip()
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        parsed = urlsplit(candidate)
        if parsed.path.startswith('/') and not parsed.path.startswith('//'):
            return candidate
    return fallback


def _remember_modal(request, kind):
    request.session['task_ui_modal'] = {'kind': kind}


def _take_modal_state(request):
    value = request.session.pop('task_ui_modal', {})
    return value if isinstance(value, dict) else {}


def _project_profiles(project_kind):
    if project_kind == 'computers':
        return ComputerAnalysisProfile.objects.filter(is_enabled=True).order_by('name', 'pk')
    if project_kind in PROJECTS:
        device_type, _model, _table = PROJECTS[project_kind]
        return InspectionProfile.objects.filter(
            is_enabled=True, device_type=device_type,
        ).order_by('name', 'pk')
    return InspectionProfile.objects.none()


def task_modal_context(request, project_kind, *, allow_target_selection=False, target_source='assets'):
    """Shared modal state for list/record pages without mutating state on GET."""
    if not is_admin(request.user):
        return {'task_default_profile': None}
    state = _take_modal_state(request)
    profiles = list(_project_profiles(project_kind))
    default_profile = next(
        (profile for profile in profiles if str(profile.pk) == request.GET.get('task_profile')),
        profiles[0] if profiles else None,
    )
    if project_kind == 'computers':
        item_choices = analysis_item_choices()
        action_label = '手动执行分析'
        config_label = '分析配置'
    elif project_kind in PROJECTS:
        item_choices = inspection_item_choices(PROJECTS[project_kind][0])
        action_label = '手动执行巡检'
        config_label = '巡检配置'
    else:
        item_choices = ()
        action_label = '手动执行巡检'
        config_label = '巡检配置'
    filter_values = [
        (key, value)
        for key, values in request.GET.lists()
        for value in values
        if key in ('q', 'target') or key.startswith('filter_')
    ]
    pc_source = pc_source_form = pc_analysis_form = None
    if project_kind == 'computers':
        from django.conf import settings
        from net.models import PCLogSourceConfig
        from index.devices.pc.simple_source_form import SimplePCLogSourceForm
        pc_source = PCLogSourceConfig.load()
        pc_source_form = SimplePCLogSourceForm(instance=pc_source, initial={
            'source_type': getattr(pc_source, 'source_type', 'smb'),
            'port': getattr(pc_source, 'port', 445),
            'remote_incoming_directory': getattr(pc_source, 'remote_incoming_directory', 'incoming'),
            'file_time_mode': getattr(pc_source, 'file_time_mode', 'recent_days'),
            'local_staging_directory': getattr(pc_source, 'local_staging_directory',
                                               str(settings.BASE_DIR / 'runtime' / 'pc-staging')),
        })
        pc_analysis_form = ComputerAnalysisProfileConfigForm(
            instance=default_profile,
            schedule=_schedule_for_profile(default_profile) if default_profile else None,
            initial={'name': getattr(default_profile, 'name', 'PC 默认分析'),
                     'concurrent_workers': getattr(default_profile, 'concurrent_workers', 4)})
        from django import forms
        for field in pc_analysis_form.fields.values():
            if isinstance(field.widget, forms.CheckboxSelectMultiple):
                continue
            field.widget.attrs['class'] = ('form-check-input' if isinstance(field.widget, forms.CheckboxInput)
                                          else 'form-select' if isinstance(field.widget, forms.Select)
                                          else 'form-control')
        pc_analysis_form.fields['daily_time'].widget = forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'})
        pc_analysis_form.fields['kms_servers_text'].widget.attrs['rows'] = 3
        pc_analysis_form.fields['site_ip_prefixes'].widget.attrs['rows'] = 3
        if not default_profile:
            defaults = ComputerAnalysisProfile()
            for name in ('minimum_windows_release', 'defender_update_max_days', 'defender_scan_max_days',
                         'patch_max_days', 'uptime_max_hours', 'cpu_max_percent', 'memory_max_percent', 'disk_max_percent',
                         'cpu_temperature_max_celsius'):
                pc_analysis_form.initial[name] = getattr(defaults, name)
        if not pc_analysis_form.initial.get('interval_value'):
            pc_analysis_form.initial['interval_value'] = 30
    single_asset = None
    if allow_target_selection and project_kind in PROJECTS and request.GET.get('task_single_target'):
        try:
            single_asset = PROJECTS[project_kind][1].objects.get(pk=request.GET['task_single_target'])
        except (ValidationError, ValueError, PROJECTS[project_kind][1].DoesNotExist):
            raise Http404
    return {
        'task_single_asset': single_asset,
        'pc_log_source': pc_source,
        'pc_log_source_form': pc_source_form,
        'pc_analysis_form': pc_analysis_form,
        'target_rule_form': (InspectionProfileConfigForm(device_type=PROJECTS[project_kind][0], instance=default_profile)
                             if project_kind in PROJECTS else None),
        'task_project_kind': project_kind,
        'task_target_source': target_source,
        'task_selected_ids': request.GET.get('task_targets', '').split(','),
        'task_target_mode': request.GET.get('task_mode', 'all'),
        'task_profiles': profiles,
        'task_default_profile': default_profile,
        'task_default_items': (
            default_profile.analysis_items
            if isinstance(default_profile, ComputerAnalysisProfile)
            else (default_profile.selected_items if default_profile is not None else [])
        ),
        'task_default_schedule': (
            _schedule_for_profile(default_profile)
            if default_profile is not None else None
        ),
        'task_device_type': (
            PROJECTS[project_kind][0] if project_kind in PROJECTS else ''
        ),
        'task_item_choices': item_choices,
        'task_filter_values': filter_values,
        'task_action_label': action_label,
        'task_config_label': config_label,
        'task_modal_auto_open': (state.get('kind') or request.GET.get('task_modal')) == 'run',
        'profile_modal_auto_open': (state.get('kind') or request.GET.get('task_modal')) == 'profile',
        # Only asset tables expose checkboxes whose IDs match queue targets.
        # Record rows and Computer assets are not direct task target IDs.
        'task_allow_target_selection': (
            allow_target_selection and project_kind in PROJECTS
        ),
    }


def _profile_from_post(value):
    try:
        return InspectionProfile.objects.filter(pk=value).first() or (
            ComputerAnalysisProfile.objects.filter(pk=value).first()
        )
    except (ValidationError, ValueError, TypeError):
        return None


def _filtered_queryset(post_data, profile):
    """Apply the exact allowlisted table filters submitted from the open page."""
    request = SimpleNamespace(GET=post_data)
    if isinstance(profile, ComputerAnalysisProfile):
        source = ComputerLogFile.objects.filter(import_status='imported')
        definition = get_table_definition('computer_logs')
    else:
        _device_type, model, table_key = next(
            project for project in PROJECTS.values()
            if project[0] == profile.device_type
        )
        source = model.objects.all()
        definition = get_table_definition(table_key)
    return apply_table_filters(
        request, source, definition, include_legacy_status=False,
    )


def _target_ids_from_request(post_data, profile, target_mode):
    if target_mode == 'filtered' and post_data.get('target_source') == 'records' and isinstance(profile, InspectionProfile):
        from .records import _infrastructure_records
        kind = next(key for key, value in PROJECTS.items() if value[0] == profile.device_type)
        rows, _state = apply_table_filters(
            SimpleNamespace(GET=post_data),
            _infrastructure_records(kind, target=post_data.get('target', '')),
            project_record_definition(kind),
        )
        ids = {str(row['target_id']) for row in rows if row['target_id'] is not None}
        model = PROJECTS[kind][1]
        target_ids = list(model.objects.filter(pk__in=ids).order_by('pk').values_list('pk', flat=True))
        if not target_ids:
            raise ValidationError({'target_ids': '目标范围没有可执行对象。'})
        return target_ids
    filtered, state = _filtered_queryset(post_data, profile)
    if target_mode == 'all':
        if isinstance(profile, ComputerAnalysisProfile):
            source = ComputerLogFile.objects.filter(import_status='imported')
        else:
            _device_type, source, _table_key = next(
                project for project in PROJECTS.values()
                if project[0] == profile.device_type
            )
            source = source.objects.all()
        target_ids = list(source.order_by('pk').values_list('pk', flat=True))
    elif target_mode == 'filtered':
        if not state['filters'] and not state['q']:
            raise ValidationError({'target_mode': '当前没有有效筛选条件，请选择全部资产。'})
        target_ids = list(filtered.values_list('pk', flat=True))
    elif target_mode == 'selected':
        selected_ids = [value for value in post_data.getlist('target_ids') if value]
        if not selected_ids:
            raise ValidationError({'target_ids': '请至少选择一个目标。'})
        allowed_ids = {str(value) for value in filtered.values_list('pk', flat=True)}
        if any(str(value) not in allowed_ids for value in selected_ids):
            raise ValidationError({'target_ids': '所选目标不在当前筛选范围内。'})
        target_ids = selected_ids
    else:
        raise ValidationError({'target_mode': '目标范围模式无效。'})
    if not target_ids:
        raise ValidationError({'target_ids': '目标范围没有可执行对象。'})
    return target_ids


@admin_required
@require_POST
def single_device_task_create(request, kind, pk):
    if kind not in PROJECTS:
        raise Http404
    device_type, model, _table = PROJECTS[kind]
    asset = get_object_or_404(model, pk=pk)
    profile = _profile_from_post(request.POST.get('profile_id'))
    if not isinstance(profile, InspectionProfile) or not profile.is_enabled or profile.device_type != device_type:
        profile = None
    fallback = reverse('asset_list', kwargs={'kind': kind}) + f'?task_modal=run&task_single_target={asset.pk}'
    if profile is None:
        messages.error(request, '请选择该设备类型的启用巡检配置。')
        return redirect(fallback)
    try:
        # The URL owns the target. Posted ranges can never expand a row task.
        task = enqueue_task(profile, [asset.pk], TaskRun.Source.MANUAL)
    except ValidationError as exc:
        messages.error(request, '任务未创建：' + '；'.join(exc.messages))
        return redirect(fallback)
    messages.success(request, '单台巡检已入队，共 1 个目标。')
    return redirect('task_detail', pk=task.pk)


@require_POST
def manual_task_create(request):
    fallback = reverse('index')
    next_url = _safe_next(request, request.POST.get('next'), fallback)
    profile = _profile_from_post(request.POST.get('profile_id'))
    if profile is None or not profile.is_enabled:
        messages.error(request, '请选择一个启用的任务配置。')
        _remember_modal(request, 'run')
        return redirect(next_url)
    if request.POST.get('single_target_id') and not request.POST.get('target_mode'):
        messages.error(request, '单台巡检范围丢失，请从设备列表重新打开。')
        return redirect(next_url)
    if not request.POST.get('target_mode'):
        try:
            if isinstance(profile, ComputerAnalysisProfile):
                task = enqueue_computer_fetch_task(profile, TaskRun.Source.MANUAL)
            else:
                from net.inspections.schedules import _selected_target_ids
                task = enqueue_task(
                    profile,
                    _selected_target_ids(profile),
                    TaskRun.Source.MANUAL,
                )
        except ValidationError as exc:
            messages.error(request, '任务未创建：' + '；'.join(exc.messages))
            _remember_modal(request, 'run')
            return redirect(next_url)
        messages.success(request, f'任务已入队，共 {task.total_targets} 个目标。')
        return redirect('task_detail', pk=task.pk)
    single_target_id = request.POST.get('single_target_id')
    if single_target_id and (request.POST.get('target_mode') != 'selected' or request.POST.getlist('target_ids') != [single_target_id]):
        messages.error(request, '任务未创建：单设备巡检只能包含当前设备。')
        _remember_modal(request, 'run')
        return redirect(next_url)
    if request.POST.get('target_mode') == 'selected' and not request.POST.getlist('selected_items') and isinstance(profile, InspectionProfile):
        try:
            task = enqueue_task(profile, _target_ids_from_request(request.POST, profile, 'selected'), TaskRun.Source.MANUAL)
        except ValidationError as exc:
            messages.error(request, '任务未创建：' + '；'.join(exc.messages))
            _remember_modal(request, 'run')
            return redirect(next_url)
        messages.success(request, f'任务已入队，共 {task.total_targets} 个目标。')
        return redirect('task_detail', pk=task.pk)
    form = ManualTaskForm(request.POST, profile=profile)
    if not form.is_valid():
        messages.error(request, '任务未创建：' + '；'.join(
            message for errors in form.errors.values() for message in errors
        ))
        _remember_modal(request, 'run')
        return redirect(next_url)

    cleaned = form.cleaned_data
    overrides = {'selected_items': cleaned['selected_items']}
    if cleaned.get('concurrent_workers') is not None:
        overrides['parameters'] = {
            'concurrent_workers': cleaned['concurrent_workers'],
        }
    try:
        if isinstance(profile, ComputerAnalysisProfile) and cleaned['target_mode'] == 'fetch':
            task = enqueue_computer_fetch_task(
                profile, TaskRun.Source.MANUAL, overrides=overrides,
            )
        else:
            target_ids = _target_ids_from_request(
                request.POST, profile, cleaned['target_mode'],
            )
            task = enqueue_task(
                profile, target_ids, TaskRun.Source.MANUAL, overrides=overrides,
            )
    except ValidationError as exc:
        messages.error(request, '任务未创建：' + '；'.join(exc.messages))
        _remember_modal(request, 'run')
        return redirect(next_url)
    messages.success(request, f'任务已入队，共 {task.total_targets} 个目标。')
    return redirect('task_detail', pk=task.pk)


def _schedule_for_profile(profile):
    if isinstance(profile, InspectionProfile):
        return Schedule.objects.filter(inspection_profile=profile).order_by('created_at', 'pk').first()
    return Schedule.objects.filter(analysis_profile=profile).order_by('created_at', 'pk').first()


def _save_schedule(profile, form):
    schedule = _schedule_for_profile(profile)
    timing_fields = ('is_enabled', 'kind', 'interval_value', 'interval_unit', 'daily_time')
    previous = tuple(getattr(schedule, key) for key in timing_fields) if schedule else None
    if not form.cleaned_data['schedule_enabled']:
        if schedule is not None and schedule.is_enabled:
            schedule.is_enabled = False
            schedule.save(update_fields=['is_enabled', 'updated_at'])
        return
    schedule = schedule or Schedule()
    schedule.inspection_profile = profile if isinstance(profile, InspectionProfile) else None
    schedule.analysis_profile = profile if isinstance(profile, ComputerAnalysisProfile) else None
    schedule.kind = form.cleaned_data['schedule_kind']
    schedule.is_enabled = True
    if schedule.kind == Schedule.Kind.INTERVAL:
        schedule.interval_value = form.cleaned_data['interval_value']
        schedule.interval_unit = form.cleaned_data['interval_unit']
        schedule.daily_time = None
    else:
        schedule.interval_value = None
        schedule.interval_unit = ''
        schedule.daily_time = form.cleaned_data['daily_time']
    schedule.full_clean()
    if previous != tuple(getattr(schedule, key) for key in timing_fields):
        from net.inspections.schedules import next_run_at
        schedule.next_run_at = next_run_at(schedule, timezone.now())
    schedule.save()


def _save_profile(profile, form):
    if not profile._state.adding:
        profile = type(profile).objects.select_for_update().get(pk=profile.pk)
    for field, value in form.profile_values().items():
        setattr(profile, field, value)
    profile.full_clean()
    profile.save()
    _save_schedule(profile, form)
    return profile


@require_POST
def inspection_profile_configure(request):
    # SQLite needs process serialization before the first read; other databases
    # serialize the profile and its schedule using the row lock in _save_profile.
    with _database_guard():
        return _inspection_profile_configure(request)


def _inspection_profile_configure(request):
    next_url = _safe_next(request, request.POST.get('next'), reverse('index'))
    existing = None
    if request.POST.get('profile_id'):
        existing = get_object_or_404(InspectionProfile, pk=request.POST['profile_id'])
        device_type = existing.device_type
    else:
        device_type = request.POST.get('device_type', '')
        if device_type not in InspectionProfile.DeviceType.values:
            messages.error(request, '请选择要配置的巡检项目。')
            _remember_modal(request, 'profile')
            return redirect(next_url)
    form = InspectionProfileConfigForm(
        request.POST, device_type=device_type, instance=existing,
        schedule=_schedule_for_profile(existing) if existing else None,
    )
    if not form.is_valid():
        messages.error(request, '配置未保存：' + '；'.join(
            message for errors in form.errors.values() for message in errors
        ))
        _remember_modal(request, 'profile')
        return redirect(next_url)
    try:
        with transaction.atomic():
            profile = existing or InspectionProfile(device_type=device_type)
            profile = _save_profile(profile, form)
    except ValidationError as exc:
        messages.error(request, '配置未保存：' + '；'.join(exc.messages))
        _remember_modal(request, 'profile')
        return redirect(next_url)
    messages.success(request, f'已保存“{profile.name}”的巡检配置。')
    _remember_modal(request, 'profile')
    return redirect(next_url)


@require_POST
def computer_analysis_profile_configure(request):
    with _database_guard():
        return _computer_analysis_profile_configure(request)


def _computer_analysis_profile_configure(request):
    next_url = _safe_next(request, request.POST.get('next'), reverse('computer_analysis_list'))
    existing = None
    if request.POST.get('profile_id'):
        existing = get_object_or_404(ComputerAnalysisProfile, pk=request.POST['profile_id'])
    form = ComputerAnalysisProfileConfigForm(
        request.POST, request.FILES, instance=existing,
        schedule=_schedule_for_profile(existing) if existing else None,
    )
    if not form.is_valid():
        messages.error(request, '配置未保存：' + '；'.join(
            message for errors in form.errors.values() for message in errors
        ))
        _remember_modal(request, 'profile')
        return redirect(next_url)
    try:
        with transaction.atomic():
            profile = existing or ComputerAnalysisProfile()
            profile = _save_profile(profile, form)
            uploaded_policy = form.cleaned_data.get('software_policy_file')
            if uploaded_policy is not None:
                policy_path = software_policy_target(profile.pk)
                profile.software_policy_path = str(policy_path)
                profile.full_clean()
                profile.save(update_fields=['software_policy_path', 'updated_at'])
                store_software_policy_upload(uploaded_policy, policy_path)
    except ValidationError as exc:
        messages.error(request, '配置未保存：' + '；'.join(exc.messages))
        _remember_modal(request, 'profile')
        return redirect(next_url)
    messages.success(request, f'已保存“{profile.name}”的分析配置。')
    _remember_modal(request, 'profile')
    return redirect(next_url)


def task_list(request):
    definition = get_table_definition('task_runs')
    tasks, table_state = apply_table_filters(
        request,
        TaskRun.objects.select_related('inspection_profile', 'analysis_profile', 'schedule'),
        definition,
        include_legacy_status=False,
    )
    page_obj = Paginator(tasks, table_state['page_size']).get_page(request.GET.get('page'))
    return render(request, 'inspections/task_list.html', {
        'page_obj': page_obj,
        'table_definition': definition,
        'table_state': table_state,
        'page_sizes': PAGE_SIZES,
        'table_export_path': reverse('table_export', args=['task_runs']),
        'pagination_query': query_without_page(request),
    })


@login_required
@require_POST
def task_cancel(request, pk):
    task = get_object_or_404(TaskRun, pk=pk)
    if task.task_type in TaskRun.PEOPLE_INTERACTIVE_TASK_TYPES:
        from index.people.integrations import require_people_owner
        require_people_owner(request, task)
    if (
        task.task_type in {TaskRun.TaskType.DOMAIN_OPERATION, TaskRun.TaskType.DOMAIN_SYNC}
        and not is_admin(request.user)
    ):
        raise PermissionDenied
    default_url = (
        reverse('people_operation', args=[task.pk])
        if task.task_type in TaskRun.PEOPLE_INTERACTIVE_TASK_TYPES
        else reverse('task_detail', args=[task.pk])
    )
    next_url = _safe_next(request, request.POST.get('next'), default_url)
    try:
        cancel_task(task.pk)
    except ValidationError as exc:
        messages.error(request, '任务未结束：' + '；'.join(exc.messages))
    except OperationalError as exc:
        if not is_sqlite_busy(exc):
            raise
        messages.error(request, '任务未结束：数据库正忙，请稍后重试结束任务。')
    else:
        messages.success(request, '任务已结束；正在执行的外部调用将在超时后退出。')
    return redirect(next_url)


def _target_result_url(target):
    if target.analysis_handoff_task_id:
        return reverse('task_detail', args=[target.analysis_handoff_task_id])
    route_map = {
        'computer_analysis': ('computer_analysis_detail', (target.result_id,)),
        'network_device_inspection': ('record_detail', ('networks', target.result_id)),
        'server_inspection': ('record_detail', ('servers', target.result_id)),
        'monitor_inspection': ('record_detail', ('monitors', target.result_id)),
        'computer_analysis_task': ('task_detail', (target.result_id,)),
    }
    try:
        route, args = route_map[target.result_type]
        return reverse(route, args=args)
    except (KeyError, NoReverseMatch, ValueError):
        return ''


def task_detail(request, pk):
    task = get_object_or_404(
        TaskRun.objects.select_related('inspection_profile', 'analysis_profile', 'schedule'),
        pk=pk,
    )
    if task.task_type in TaskRun.PEOPLE_INTERACTIVE_TASK_TYPES:
        from index.people.integrations import require_people_owner
        require_people_owner(request, task)
        return redirect('people_operation', pk=task.pk)
    definition = get_table_definition('task_targets')
    from net.inspections.state import target_progress
    progress_counts = target_progress(task.target_runs.all())
    task.completed_targets = progress_counts['completed']
    task.successful_targets = progress_counts['successful']
    task.failed_targets = task.completed_targets - task.successful_targets
    task.progress = int(100 * task.completed_targets / progress_counts['total']) if progress_counts['total'] else 0
    target_source = task.target_runs.all()
    if task.task_type in {TaskRun.TaskType.INSPECTION, TaskRun.TaskType.COMPUTER_ANALYSIS}:
        target_source = target_source.defer('result_snapshot', 'target_snapshot')
    targets, table_state = apply_table_filters(
        request, target_source, definition, include_legacy_status=False,
    )
    page_obj = Paginator(targets, table_state['page_size']).get_page(request.GET.get('page'))
    domain_operation = getattr(task, 'domain_operation', None)
    domain_manual_intervention = bool(
        domain_operation and any(
            isinstance(snapshot, dict)
            and snapshot.get('stage') == 'manual_intervention_required'
            for snapshot in task.target_runs.filter(status=TaskRun.Status.FAILED)
            .values_list('result_snapshot', flat=True)
        )
    )
    domain_retry_available = bool(
        domain_operation
        and is_admin(request.user)
        and task.status in TaskRun.AGGREGATED_TERMINAL_STATUSES
        and task.failed_targets > 0
        and not domain_manual_intervention
    )
    domain_retry_requires_password = bool(
        domain_retry_available
        and domain_operation.action in {'create_user', 'reset_password'}
    )
    for target in page_obj:
        target.result_url = _target_result_url(target)
        if task.task_type == TaskRun.TaskType.PEOPLE_SYNC:
            snapshot = target.result_snapshot if isinstance(target.result_snapshot, dict) else {}
            target.people_sync_counts = snapshot.get('counts', {})
        if domain_operation:
            snapshot = target.target_snapshot if isinstance(target.target_snapshot, dict) else {}
            target.domain_name = snapshot.get('name') or target.target_id
            target.domain_dn = snapshot.get('distinguished_name', '')
    context = {
        'task': task,
        'page_obj': page_obj,
        'table_definition': definition,
        'table_state': table_state,
        'page_sizes': PAGE_SIZES,
        'table_export_path': reverse('table_export_scoped', args=['task_targets', task.pk]),
        'pagination_query': query_without_page(request),
        'domain_operation': domain_operation,
        'domain_retry_available': domain_retry_available,
        'domain_retry_requires_password': domain_retry_requires_password,
        'domain_manual_intervention': domain_manual_intervention,
    }
    if task.task_type == TaskRun.TaskType.COMPUTER_FETCH:
        fetch_target = task.target_runs.select_related('analysis_handoff_task').first()
        child = fetch_target.analysis_handoff_task if fetch_target else None
        if child is None and fetch_target and fetch_target.result_type == 'computer_analysis_task':
            child = TaskRun.objects.filter(pk=fetch_target.result_id, analysis_profile_id=task.analysis_profile_id).first()
        context['pc_analysis_task'] = child
        context['pc_fetch_result'] = fetch_target.result_snapshot if fetch_target else {}
        context['pc_reused_count'] = task.parameters_snapshot.get('reused_log_count', 0)
    from .task_results import task_result_context
    context.update(task_result_context(request, task))
    if context.get('show_result_table'):
        diagnostics = task.target_runs.exclude(alert_processing_error='').only(
            'pk', 'target_id', 'alert_processing_error', 'alert_attempted_at').order_by('pk')
        context['alert_processing_page'] = Paginator(diagnostics, 20).get_page(request.GET.get('alert_page'))
    return render(request, 'inspections/task_detail.html', context)
