"""Personnel import HTTP orchestration. External I/O belongs to the Worker."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from index.people.forms import PeopleSourceForm
from net.people.directory.sync import PeopleSyncError, SyncPreview
from net.models import PeopleSyncSource, TaskRun
from net.people.tasks import apply_people_task, enqueue_people_task, owns_people_task, session_digest


def _source(source_id):
    if not source_id:
        return None
    try:
        return PeopleSyncSource.objects.get(pk=source_id)
    except (PeopleSyncSource.DoesNotExist, ValidationError, ValueError):
        raise Http404('人员目录来源不存在。') from None


def require_people_owner(request, task):
    if not owns_people_task(task, request.session.session_key):
        raise Http404('人员目录操作不存在或不属于当前会话。')


def people_modal_context(request, *, form=None, source=None, provider=None, open_modal=False, error=''):
    remembered_provider = request.session.pop('people_import_provider', 'csv')
    provider = provider or request.GET.get('provider', remembered_provider)
    if provider not in {'csv', 'feishu', 'dingtalk'}:
        provider = 'csv'
    selected = source or _source(request.GET.get('source_id'))
    if selected:
        provider = selected.source_type
    sources = [value.public_data() for value in PeopleSyncSource.objects.order_by('name', 'pk')]
    tabs = []
    for key, label in PeopleSyncSource.SourceType.choices:
        current = selected if selected and selected.source_type == key else None
        tab_form = form if form is not None and key == provider else PeopleSourceForm(source=current, provider=key)
        tabs.append({'provider': key, 'label': label,
                     'sources': [value for value in sources if value['source_type'] == key],
                     'selected': current.public_data() if current else None,
                     'form': tab_form.public_form(), 'active': provider == key})
    return {'people_provider_tabs': tabs, 'people_active_tab': provider,
            'people_import_error': error,
            'open_import_modal': open_modal or request.GET.get('import') == 'api',
            'people_recent_tasks': list(TaskRun.objects.filter(
                task_type__in=TaskRun.PEOPLE_TASK_TYPES,
                parameters_snapshot__owner_session_digest=session_digest(request.session.session_key),
            ).order_by('-created_at')[:5]) if request.session.session_key else []}


def _render_modal(request, **kwargs):
    from index.devices.views import asset_list
    return asset_list(request, 'people', integration_context=people_modal_context(request, open_modal=True, **kwargs))


@sensitive_post_parameters('app_id', 'app_key', 'app_secret')
@require_POST
def people_source_save(request):
    source = _source(request.POST.get('source_id'))
    provider = request.POST.get('source_type', '')
    if provider not in PeopleSyncSource.SourceType.values:
        raise Http404('未知人员目录平台。')
    form = PeopleSourceForm(request.POST, source=source, provider=provider)
    if form.is_valid():
        try:
            with transaction.atomic():
                # Rebind under lock so a blank secret never overwrites a concurrent rotation.
                if source:
                    form._source = PeopleSyncSource.objects.select_for_update().get(pk=source.pk)
                saved = form.save()
            messages.success(request, '人员目录来源已保存；请生成预览并明确确认后应用。')
            return redirect(f"{reverse('asset_list', args=['people'])}?import=api&provider={provider}&source_id={saved.pk}")
        except (ValidationError, IntegrityError):
            form.add_error(None, '来源配置无效或稳定来源标识已被使用。')
    return _render_modal(request, form=form, source=source, provider=provider)


def _queue_operation(request, task_type):
    source = _source(request.POST.get('source_id'))
    if source is None:
        return _render_modal(request, provider=request.POST.get('provider', 'feishu'),
                             error='请先配置并选择一个人员目录来源。')
    if not request.session.session_key:
        request.session.create()
    try:
        task = enqueue_people_task(source.pk, task_type, request.session.session_key)
    except ValidationError as exc:
        return _render_modal(request, source=source, error=' '.join(exc.messages))
    return redirect('people_operation', pk=task.pk)


@require_POST
def people_test(request):
    return _queue_operation(request, TaskRun.TaskType.PEOPLE_TEST)


@require_POST
def people_preview(request):
    return _queue_operation(request, TaskRun.TaskType.PEOPLE_PREVIEW)


def people_operation(request, pk, *, error=''):
    task = get_object_or_404(TaskRun, pk=pk, task_type__in=TaskRun.PEOPLE_TASK_TYPES)
    require_people_owner(request, task)
    source = _source(task.people_source_id)
    target = task.target_runs.first()
    preview = None
    if (task.task_type == TaskRun.TaskType.PEOPLE_PREVIEW and task.status == TaskRun.Status.SUCCESS
            and target and target.status == TaskRun.Status.SUCCESS and not task.people_applied_at):
        try:
            preview = SyncPreview.from_dict(target.result_snapshot.get('preview'))
        except PeopleSyncError:
            pass
    context = people_modal_context(request, source=source)
    context.update({'people_operation': task, 'people_operation_source': source.public_data(),
                    'people_operation_error': error,
                    'people_operation_target': target, 'people_preview': preview,
                    'open_import_modal': False})
    from index.devices.views import asset_list
    return asset_list(request, 'people', integration_context=context)


@require_POST
def people_apply(request):
    try:
        task = TaskRun.objects.get(pk=request.POST.get('task_id'), task_type__in=TaskRun.PEOPLE_TASK_TYPES)
    except (TaskRun.DoesNotExist, ValidationError, ValueError):
        raise Http404('人员目录操作不存在。') from None
    require_people_owner(request, task)
    if request.POST.get('confirm') != 'yes':
        return HttpResponseBadRequest('必须明确确认预览后才能应用。')
    try:
        result = apply_people_task(task.pk, request.session.session_key, request.POST.get('preview_token', ''))
    except PeopleSyncError:
        response = people_operation(request, task.pk, error='预览已失效、已应用或配置已变更，请重新生成预览。')
        response.status_code = 400
        return response
    messages.success(request, f'人员导入完成：新增 {result.created} 条，更新 {result.updated} 条，停用 {result.deactivated} 条。')
    return redirect('asset_list', kind='people')
