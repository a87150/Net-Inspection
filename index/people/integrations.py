"""Personnel import HTTP orchestration. External I/O belongs to the Worker."""

from urllib.parse import urlencode

from django.contrib import messages
from index.common.access import is_admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.forms.utils import ErrorDict, ErrorList
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST

from index.people.forms import PeopleProviderForm, PeopleScheduleForm
from net.models import Schedule, TaskRun
from net.people.directory.sync import PeopleSyncError, SyncPreview
from net.people.providers import PROVIDERS, get_provider_source
from net.people.tasks import (
    apply_people_task,
    enqueue_people_task,
    acknowledge_people_task,
    owns_people_task,
    pending_people_tasks,
    remember_people_task,
    session_digest,
)


PROVIDER_FORM_FEEDBACK_SESSION_KEY = 'people_provider_form_feedback'
PROVIDER_FORM_PUBLIC_FIELDS = ('root_department_ids', 'is_enabled')


def _provider_source(provider):
    if provider not in PROVIDERS:
        raise Http404('未知人员 API 平台。')
    return get_provider_source(provider)


def require_people_owner(request, task):
    if not owns_people_task(task, request.session.session_key):
        raise Http404('人员目录操作不存在或不属于当前会话。')


def people_modal_context(
    request, *, form=None, source=None, provider=None, open_modal=False, error='',
):
    if not is_admin(request.user):
        return {}
    feedback = request.session.pop(PROVIDER_FORM_FEEDBACK_SESSION_KEY, None)
    provider = provider or request.GET.get('provider') or (
        feedback.get('provider') if feedback else None
    ) or 'csv'
    if source is not None:
        provider = source.source_type
    if provider not in {'csv', *PROVIDERS}:
        provider = 'csv'

    tabs = []
    for key, definition in PROVIDERS.items():
        current = get_provider_source(key)
        schedule = Schedule.objects.filter(people_source=current).first() if current else None
        tab_form = form if form is not None and key == provider else None
        if tab_form is None and feedback and feedback.get('provider') == key:
            tab_form = PeopleProviderForm(
                feedback.get('data', {}), source=current, provider=key,
            )
            tab_form.is_valid()
            restored_errors = ErrorDict()
            for field_name, field_errors in feedback.get('errors', {}).items():
                restored_errors[field_name] = ErrorList(field_errors)
            tab_form._errors = restored_errors
            tab_form.cleaned_data = {}
        if tab_form is None:
            tab_form = PeopleProviderForm(source=current, provider=key)
        tabs.append({
            'provider': key,
            'label': definition.label,
            'selected': current.public_data() if current else None,
            'form': tab_form.public_form(),
            'schedule': schedule,
            'schedule_form': PeopleScheduleForm(
                source=current, schedule=schedule,
            ) if current else None,
            'latest_sync_task': TaskRun.objects.filter(
                task_type=TaskRun.TaskType.PEOPLE_SYNC,
                people_source=current,
            ).order_by('-created_at').first() if current else None,
            'active': provider == key,
        })

    return {
        'people_provider_tabs': tabs,
        'people_active_tab': provider,
        'people_import_error': error or request.session.pop('people_import_error', ''),
        'open_import_modal': (
            open_modal or request.GET.get('import') in {'api', 'people'}
        ),
        'people_recent_tasks': list(TaskRun.objects.filter(
            task_type__in=TaskRun.PEOPLE_TASK_TYPES,
            parameters_snapshot__owner_session_digest=session_digest(
                request.session.session_key,
            ),
        ).order_by('-created_at')[:5]) if request.session.session_key else [],
    }


def _render_modal(request, **kwargs):
    # A full table must never live at a POST-only URL: restoring page size
    # would turn it into a GET of the operation endpoint (HTTP 405).
    request.session['people_import_error'] = kwargs.get('error', '')
    source = kwargs.get('source')
    provider = source.source_type if source else kwargs.get('provider', 'csv')
    return redirect(_provider_redirect(provider))


def _provider_redirect(provider):
    query = urlencode({'import': 'people', 'provider': provider})
    return f"{reverse('asset_list', args=['people'])}?{query}"


def _redirect_invalid_provider_form(request, form, provider):
    request.session[PROVIDER_FORM_FEEDBACK_SESSION_KEY] = {
        'provider': provider,
        'data': {
            field: form.data.get(field, '')
            for field in PROVIDER_FORM_PUBLIC_FIELDS
            if field in form.data
        },
        'errors': {
            field: [str(message) for message in field_errors]
            for field, field_errors in form.errors.items()
        },
    }
    return redirect(_provider_redirect(provider))


@sensitive_post_parameters('app_id', 'app_key', 'app_secret')
@require_POST
def people_provider_save(request, provider):
    source = _provider_source(provider)
    form = PeopleProviderForm(request.POST, source=source, provider=provider)
    if form.is_valid():
        try:
            form.save()
            messages.success(
                request,
                f'{PROVIDERS[provider].label} API 设置已保存，请先测试连接。',
            )
            return redirect(_provider_redirect(provider))
        except (ValidationError, IntegrityError):
            form.add_error(None, '平台配置无效，请检查后重试。')
    return _redirect_invalid_provider_form(request, form, provider)


@require_POST
def people_schedule_save(request, provider):
    source = _provider_source(provider)
    if source is None:
        messages.error(request, f'请先保存{PROVIDERS[provider].label} API 设置。')
        return redirect(_provider_redirect(provider))
    schedule = Schedule.objects.filter(people_source=source).first()
    form = PeopleScheduleForm(request.POST, source=source, schedule=schedule)
    if form.is_valid():
        form.save()
        messages.success(request, f'{PROVIDERS[provider].label}自动同步计划已保存。')
    else:
        messages.error(
            request,
            '自动同步计划保存失败：' + ' '.join(
                str(message) for errors in form.errors.values() for message in errors
            ),
        )
    return redirect(_provider_redirect(provider))


def _queue_operation(request, task_type):
    provider = request.POST.get('provider', '')
    source = _provider_source(provider)
    if source is None:
        return _render_modal(
            request,
            provider=provider,
            error=f'请保存{PROVIDERS[provider].label} API 设置后再操作。',
        )
    if not request.session.session_key:
        request.session.create()
    try:
        task = enqueue_people_task(source.pk, task_type, request.session.session_key)
    except ValidationError as exc:
        return _render_modal(request, source=source, error=' '.join(exc.messages))
    remember_people_task(request.session, task.pk)
    messages.info(
        request,
        f'{source.get_source_type_display()}{task.get_task_type_display()}正在后台运行。',
    )
    return redirect(_provider_redirect(provider))


@require_POST
def people_test(request):
    return _queue_operation(request, TaskRun.TaskType.PEOPLE_TEST)


@require_POST
def people_preview(request):
    return _queue_operation(request, TaskRun.TaskType.PEOPLE_PREVIEW)


def _task_status_message(task):
    if task.status in TaskRun.ACTIVE_STATUSES:
        return f'{task.people_source.get_source_type_display()}{task.get_task_type_display()}正在后台运行。'
    if task.status == TaskRun.Status.SUCCESS:
        return f'{task.get_task_type_display()}已完成。'
    if task.status == TaskRun.Status.CANCELLED:
        return f'{task.get_task_type_display()}已取消。'
    return f'{task.get_task_type_display()}失败，请查看结果。'


@require_GET
def people_task_status(request):
    running_acks = {str(value) for value in request.session.get('people_directory_running_acks', [])}
    terminal_acks = {str(value) for value in request.session.get('people_directory_terminal_acks', [])}
    tasks = []
    for task in pending_people_tasks(request.session):
        task_id = str(task.pk)
        is_terminal = task.status in TaskRun.TERMINAL_STATUSES
        if is_terminal and task_id in terminal_acks:
            continue
        tasks.append({
            'id': task_id,
            'provider': task.people_source.source_type,
            'provider_label': task.people_source.get_source_type_display(),
            'operation': task.task_type,
            'operation_label': task.get_task_type_display(),
            'status': task.status,
            'status_label': task.get_status_display(),
            'show_running': not is_terminal and task_id not in running_acks,
            'show_terminal': is_terminal and task_id not in terminal_acks,
            'message': _task_status_message(task),
            'jump_url': reverse('people_operation', args=[task.pk]),
            'ack_url': reverse('people_task_acknowledge', args=[task.pk]),
        })
    return JsonResponse({'tasks': tasks})


@require_POST
def people_task_acknowledge(request, pk):
    task = get_object_or_404(
        TaskRun,
        pk=pk,
        task_type__in=TaskRun.PEOPLE_INTERACTIVE_TASK_TYPES,
    )
    require_people_owner(request, task)
    kind = request.POST.get('kind', '')
    if kind not in {'running', 'terminal'}:
        return HttpResponseBadRequest('未知确认类型。')
    acknowledge_people_task(request.session, task.pk, kind=kind)
    return JsonResponse({'acknowledged': True})


def people_operation(request, pk, *, error=''):
    task = get_object_or_404(
        TaskRun.objects.select_related('people_source'),
        pk=pk, task_type__in=TaskRun.PEOPLE_INTERACTIVE_TASK_TYPES,
    )
    require_people_owner(request, task)
    source = task.people_source
    target = task.target_runs.first()
    preview = None
    if (
        task.task_type == TaskRun.TaskType.PEOPLE_PREVIEW
        and task.status == TaskRun.Status.SUCCESS
        and target
        and target.status == TaskRun.Status.SUCCESS
        and not task.people_applied_at
    ):
        try:
            preview = SyncPreview.from_dict(target.result_snapshot.get('preview'))
        except PeopleSyncError:
            pass
    context = people_modal_context(request, source=source)
    context.update({
        'people_operation': task,
        'people_operation_source': source.public_data(),
        'people_operation_error': error,
        'people_operation_target': target,
        'people_preview': preview,
        'open_import_modal': False,
    })
    from index.devices.views import asset_list
    return asset_list(request, 'people', integration_context=context)


@require_POST
def people_apply(request):
    try:
        task = TaskRun.objects.get(
            pk=request.POST.get('task_id'),
            task_type=TaskRun.TaskType.PEOPLE_PREVIEW,
        )
    except (TaskRun.DoesNotExist, ValidationError, ValueError):
        raise Http404('人员目录操作不存在。') from None
    require_people_owner(request, task)
    if request.POST.get('confirm') != 'yes':
        return HttpResponseBadRequest('必须明确确认预览后才能应用。')
    try:
        result = apply_people_task(
            task.pk,
            request.session.session_key,
            request.POST.get('preview_token', ''),
        )
    except PeopleSyncError:
        response = people_operation(
            request, task.pk,
            error='预览已失效、已应用或配置已变更，请重新生成预览。',
        )
        response.status_code = 400
        return response
    messages.success(
        request,
        f'人员导入完成：新增 {result.created} 条，更新 {result.updated} 条，'
        f'停用 {result.deactivated} 条。',
    )
    return redirect('people_operation', pk=task.pk)
