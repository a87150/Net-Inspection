from dataclasses import dataclass

from django.contrib.auth.decorators import permission_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from index.common.table_query import PAGE_SIZES, apply_table_filters, query_without_page
from index.common.table_registry import get_table_definition
from index.domain.forms import DomainControllerConfigForm
from net.models import (
    Domain_Account,
    Domain_Computer,
    Domain_Group,
    Domain_Controller_Config,
)
from net.domain.sync import sync_domain, test_domain_connection


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
        ('unlock', '解锁'),
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


@permission_required('net.manage_domain_operations', raise_exception=True)
def _domain_controller_settings_mutation(request):
    config, _ = Domain_Controller_Config.objects.get_or_create(pk=1)
    action = request.POST.get('action', 'save')
    form_fields = DomainControllerConfigForm.base_fields
    if action == 'sync' and not any(field in request.POST for field in form_fields):
        try:
            account_count, computer_count, group_count = sync_domain(config)
            messages.success(
                request,
                f'域控同步完成：{account_count} 个账号，{computer_count} 台域计算机，{group_count} 个分组。',
            )
        except Exception:
            messages.error(request, '域控操作失败。')
        return redirect('domain_controller_settings')

    form = DomainControllerConfigForm(request.POST, instance=config)
    if form.is_valid():
        config = form.save()
        try:
            if action == 'test':
                messages.success(request, test_domain_connection(config))
            elif action == 'sync':
                account_count, computer_count, group_count = sync_domain(config)
                messages.success(
                    request,
                    f'域控同步完成：{account_count} 个账号，{computer_count} 台域计算机，{group_count} 个分组。',
                )
            else:
                messages.success(request, '域控配置已保存。')
        except Exception:
            messages.error(request, '域控操作失败。')
        return redirect('domain_controller_settings')

    return _render_domain_settings(request, config, form, open_domain_modal=True)


def _render_domain_settings(request, config, form, *, open_domain_modal=False):
    can_manage_domain = request.user.has_perm('net.manage_domain_operations')
    return render(request, 'domain/controller_settings.html', {
        'form': form,
        'config': config,
        'open_domain_modal': open_domain_modal,
        'can_manage_domain': can_manage_domain,
        'account_total': Domain_Account.objects.count(),
        'account_active': Domain_Account.objects.filter(is_active=True).count(),
        'account_inactive': Domain_Account.objects.filter(is_active=False).count(),
        'computer_total': Domain_Computer.objects.count(),
        'computer_active': Domain_Computer.objects.filter(is_active=True).count(),
        'computer_inactive': Domain_Computer.objects.filter(is_active=False).count(),
        'group_total': Domain_Group.objects.count(),
        'security_group_total': Domain_Group.objects.filter(group_category='security').count(),
        'distribution_group_total': Domain_Group.objects.filter(group_category='distribution').count(),
    })


def domain_controller_settings(request):
    if request.method == 'POST':
        return _domain_controller_settings_mutation(request)
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
            and request.user.has_perm('net.manage_domain_operations')
        ),
        'domain_object_type': {
            'accounts': 'account', 'computers': 'computer', 'groups': 'group',
        }[object_type],
        'domain_operation_actions': DOMAIN_OPERATION_ACTIONS.get(
            {'accounts': 'account', 'computers': 'computer'}.get(object_type),
            (),
        ),
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
