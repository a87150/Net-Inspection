from dataclasses import dataclass

from django.contrib import messages
from index.common.access import admin_required, is_admin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from index.common.table_query import PAGE_SIZES, apply_table_filters, query_without_page
from index.common.table_registry import get_table_definition
from index.domain.forms import DomainControllerConfigForm
from index.domain.connection_form import DomainInactivityForm
from net.models import (
    Domain_Account,
    Domain_Computer,
    Domain_Group,
    Domain_Controller_Config,
    Schedule, TaskRun,
)
from net.domain.sync import test_domain_connection
from net.domain.statistics import get_domain_statistics
from net.domain.sync_tasks import enqueue_domain_sync
from index.domain.schedule_form import DomainSyncScheduleForm


@dataclass(frozen=True)
class DomainObjectPage:
    model: type
    table_key: str
    export_key: str
    title: str
    detail_route: str


DOMAIN_OBJECT_PAGES = {
    'accounts': DomainObjectPage(
        Domain_Account,
        'domain_accounts',
        'accounts',
        '域账户',
        'domain_account_detail',
    ),
    'computers': DomainObjectPage(
        Domain_Computer,
        'domain_computers',
        'domain_computers',
        '域计算机',
        'domain_computer_detail',
    ),
    'groups': DomainObjectPage(
        Domain_Group,
        'domain_groups',
        'domain_groups',
        '域分组',
        'domain_group_detail',
    ),
}

DOMAIN_OPERATION_ACTIONS = {
    'account': (
        ('create_user', '新增用户'),
        ('move_ou', '移动到 OU'),
        ('add_group', '加入安全组'),
        ('reset_password', '重置密码'),
        ('must_change_password', '下次登录修改密码'),
        ('password_never_expires', '密码永不过期'),
        ('unlock', '解锁'),
        ('enable', '启用'),
        ('disable', '停用'),
    ),
    'computer': (
        ('move_ou', '移动到 OU'),
        ('add_group', '加入安全组'),
        ('enable', '启用'),
        ('disable', '停用'),
    ),
}


def _domain_page(object_type):
    try:
        return DOMAIN_OBJECT_PAGES[object_type]
    except KeyError as exc:
        raise Http404('未知的域对象类型') from exc


def _display_value(obj, field):
    value = getattr(obj, field.source, None)
    display_method = getattr(obj, f'get_{field.source}_display', None)
    return display_method() if callable(display_method) else value


@admin_required
def _domain_controller_settings_mutation(request):
    config, _ = Domain_Controller_Config.objects.get_or_create(pk=1)
    action = request.POST.get('action', 'save')
    if action == 'inactivity':
        inactivity_form = DomainInactivityForm(request.POST)
        if inactivity_form.is_valid():
            config.inactive_days = inactivity_form.cleaned_data['inactive_days']
            config.save(update_fields=['inactive_days'])
            messages.success(request, '未登录天数设置已保存，统计已更新。')
            return redirect(reverse('domain_controller_settings') + '?inactivity_modal=1')
        return _render_domain_settings(request, config, DomainControllerConfigForm(instance=config),
                                       inactivity_form=inactivity_form)
    if action == 'schedule':
        schedule = Schedule.objects.filter(domain_config=config).first()
        schedule_form = DomainSyncScheduleForm(request.POST, config=config, instance=schedule)
        if schedule_form.is_valid():
            schedule_form.save()
            messages.success(request, '域控定时同步设置已保存，后台 Worker 将按计划创建任务。')
        else:
            messages.error(request, '定时设置未保存：' + '；'.join(
                str(error) for errors in schedule_form.errors.values() for error in errors))
            return _render_domain_settings(request, config, DomainControllerConfigForm(instance=config),
                                           open_domain_modal=True, schedule_form=schedule_form)
        return redirect(reverse('domain_controller_settings') + '?modal=1')
    form_fields = DomainControllerConfigForm.base_fields
    if action == 'sync' and not any(field in request.POST for field in form_fields):
        try:
            task = enqueue_domain_sync()
            messages.success(
                request,
                '域控同步任务已加入后台队列，可在下方同步任务列表查看进度。',
            )
        except ValidationError as exc:
            messages.error(request, '；'.join(exc.messages))
        return redirect('domain_controller_settings')

    form = DomainControllerConfigForm(request.POST, instance=config)
    if form.is_valid():
        config = form.save()
        try:
            if action == 'test':
                messages.success(request, test_domain_connection(config))
            elif action == 'sync':
                task = enqueue_domain_sync()
                messages.success(
                    request,
                    '域控同步任务已加入后台队列，可在同步任务列表查看进度。',
                )
            else:
                messages.success(request, '域控配置已保存。')
        except Exception:
            messages.error(request, '域控操作失败。')
        return redirect(reverse('domain_controller_settings') + '?modal=1')

    return _render_domain_settings(request, config, form, open_domain_modal=True)


def _render_domain_settings(request, config, form, *, open_domain_modal=False, schedule_form=None, inactivity_form=None):
    can_manage_domain = is_admin(request.user)
    return render(request, 'domain/controller_settings.html', {
        'form': form if can_manage_domain else None,
        'config': config if can_manage_domain else None,
        'open_domain_modal': open_domain_modal or request.GET.get('modal') == '1',
        'domain_schedule': Schedule.objects.filter(domain_config=config).first() if can_manage_domain else None,
        'domain_schedule_form': (schedule_form if schedule_form is not None else DomainSyncScheduleForm(config=config, instance=Schedule.objects.filter(domain_config=config).first())) if can_manage_domain else None,
        'domain_sync_tasks': Paginator(TaskRun.objects.filter(task_type='domain_sync').order_by('-created_at'), 10).get_page(request.GET.get('sync_page')),
        'can_manage_domain': can_manage_domain,
        'inactivity_form': inactivity_form if inactivity_form is not None else DomainInactivityForm(initial={'inactive_days': config.inactive_days if config else 60}),
        'open_inactivity_modal': inactivity_form is not None or request.GET.get('inactivity_modal') == '1',
        **get_domain_statistics(inactive_days=config.inactive_days if config else 60),
    })


def domain_controller_settings(request):
    if request.method == 'POST':
        return _domain_controller_settings_mutation(request)
    if not is_admin(request.user):
        return _render_domain_settings(request, None, None)
    config, _ = Domain_Controller_Config.objects.get_or_create(pk=1)
    form = DomainControllerConfigForm(instance=config)
    return _render_domain_settings(request, config, form)


def domain_object_list(request, object_type):
    page = _domain_page(object_type)
    table_definition = get_table_definition(page.table_key)
    objects, table_state = apply_table_filters(
        request, page.model.objects.all(), table_definition,
    )
    page_obj = Paginator(objects, table_state['page_size']).get_page(
        request.GET.get('page'),
    )
    return render(request, 'domain/object_list.html', {
        'item_key': page.export_key,
        'export_key': page.export_key,
        'item_name': page.title,
        'table_definition': table_definition,
        'table_state': table_state,
        'page_obj': page_obj,
        'page_sizes': PAGE_SIZES,
        'table_export_path': reverse('table_export', args=[page.table_key]),
        'pagination_query': query_without_page(request),
        'detail_route': page.detail_route,
        'can_manage_domain': (
            object_type != 'groups'
            and is_admin(request.user)
        ),
        'domain_object_type': {
            'accounts': 'account', 'computers': 'computer', 'groups': 'group',
        }[object_type],
        'domain_operation_actions': DOMAIN_OPERATION_ACTIONS.get(
            {'accounts': 'account', 'computers': 'computer'}.get(object_type),
            (),
        ) if is_admin(request.user) else (),
    })


def domain_object_detail(request, object_type, pk):
    page = _domain_page(object_type)
    domain_object = get_object_or_404(page.model, pk=pk)
    table_definition = get_table_definition(page.table_key)
    detail_fields = [
        {
            'label': field.label,
            'value': _display_value(domain_object, field),
            'kind': field.kind,
        }
        for field in table_definition.fields
    ]
    list_route = {
        'accounts': 'domain_account_list',
        'computers': 'domain_computer_list',
        'groups': 'domain_group_list',
    }[object_type]
    return render(request, 'devices/detail.html', {
        'asset': domain_object,
        'item_name': page.title,
        'detail_fields': detail_fields,
        'back_url': reverse(list_route),
        'history_url': '',
        'inspection_type': '',
        'domain_object': True,
    })


def domain_account_detail(request, pk):
    return domain_object_detail(request, 'accounts', pk)


def domain_computer_detail(request, pk):
    return domain_object_detail(request, 'computers', pk)


def domain_group_detail(request, pk):
    return domain_object_detail(request, 'groups', pk)
