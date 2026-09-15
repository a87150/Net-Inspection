"""Manage synced account/computer members from the group-list modal."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction, OperationalError
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_http_methods

from index.common.access import admin_required
from net.models import Domain_Group, DomainMembership, Domain_Account, Domain_Computer, Domain_Controller_Config
from net.domain.tasks import enqueue_domain_operation
from net.domain.validation import validate_dn_within_base
from net.inspections.queue import is_sqlite_busy


TARGET_MODELS = {'account': Domain_Account, 'computer': Domain_Computer}


def _candidates(group, object_type, base_dn):
    model = TARGET_MODELS[object_type]
    if not base_dn:
        return model.objects.none()
    return (model.objects.filter(distinguished_name__iendswith=base_dn)
            .exclude(distinguished_name='').exclude(memberships__group=group))


def _selected_ids(request, field):
    selected = request.POST.getlist(field)
    if not selected or len(selected) > 500 or len(set(selected)) != len(selected):
        raise ValidationError('请选择 1–500 个不重复的成员。')
    return selected


@admin_required
@require_http_methods(['GET', 'POST'])
def domain_group_members(request, pk):
    group = get_object_or_404(Domain_Group, pk=pk)
    config = Domain_Controller_Config.objects.filter(pk=1).first()
    base_dn = config.base_dn if config else ''
    mode = 'add' if request.GET.get('mode') == 'add' else 'members'
    object_type = request.GET.get('object_type', 'account')
    if object_type not in TARGET_MODELS:
        object_type = 'account'
    tasks, selected = [], []
    action = request.POST.get('action', 'remove_group')
    if request.method == 'POST':
        try:
            if action not in {'add_group', 'remove_group'}:
                raise ValidationError('成员操作无效。')
            mode = 'add' if action == 'add_group' else 'members'
            selected = _selected_ids(request, 'target_ids' if mode == 'add' else 'membership_ids')
            with transaction.atomic():
                # Queue creation and directory synchronization share this lock.
                config = Domain_Controller_Config.objects.select_for_update().filter(pk=1).first()
                if config is None:
                    raise ValidationError('请先配置并同步域控。')
                base_dn = config.base_dn
                group = Domain_Group.objects.get(pk=pk)
                if not group.is_available:
                    raise ValidationError('分组不在最近同步范围内，请重新同步。')
                validate_dn_within_base(group.distinguished_name, base_dn)
                if mode == 'add':
                    object_type = request.POST.get('object_type', '')
                    if object_type not in TARGET_MODELS:
                        raise ValidationError('请选择域账户或域计算机。')
                    model = TARGET_MODELS[object_type]
                    try:
                        ids = [model._meta.pk.to_python(value) for value in selected]
                    except (ValueError, TypeError, ValidationError):
                        raise ValidationError('成员选择无效，请刷新窗口。') from None
                    rows = list(_candidates(group, object_type, base_dn).filter(pk__in=ids))
                    if len(rows) != len(selected):
                        raise ValidationError('所选对象已在组内、缺少目录信息或不在当前域范围，请刷新后重新选择。')
                    for row in rows:
                        validate_dn_within_base(row.distinguished_name, base_dn)
                    targets = {object_type: [str(row.pk) for row in rows]}
                else:
                    if any(not value.isdecimal() for value in selected):
                        raise ValidationError('成员选择无效，请刷新窗口。')
                    rows = list(DomainMembership.objects.filter(pk__in=selected, group=group))
                    if len(rows) != len(selected) or any(row.is_primary for row in rows):
                        raise ValidationError('所选成员已变化或包含主组成员，请刷新后重新选择。')
                    targets = {kind: [str(getattr(row, kind + '_id')) for row in rows if getattr(row, kind + '_id')]
                               for kind in TARGET_MODELS}
                for kind, ids in targets.items():
                    if ids:
                        operation = enqueue_domain_operation(requested_by=request.user,
                            object_type=kind, action=action, target_ids=ids,
                            parameters={'group_dn': group.distinguished_name})
                        tasks.append(operation.task)
            messages.success(request, ('添加' if mode == 'add' else '移出') + '成员任务已入队，完成后刷新查看成员关系。')
            selected = []
        except ValidationError as exc:
            tasks = []
            messages.error(request, '；'.join(exc.messages))
        except OperationalError as exc:
            if not is_sqlite_busy(exc):
                raise
            tasks = []
            messages.error(request, '数据库正忙，未创建任务，请稍后重试。')
    if object_type not in TARGET_MODELS:
        object_type = 'account'
    query = request.GET.get('q', '').strip()
    if mode == 'add':
        members = _candidates(group, object_type, base_dn)
        if object_type == 'account':
            if query:
                members = members.filter(Q(login_name__icontains=query) | Q(account_name__icontains=query))
            members = members.order_by('login_name', 'pk')
        else:
            if query:
                members = members.filter(computer_name__icontains=query)
            members = members.order_by('computer_name', 'pk')
    else:
        members = DomainMembership.objects.filter(group=group).select_related('account', 'computer').order_by('account__login_name', 'computer__computer_name', 'pk')
        if query:
            members = members.filter(Q(account__login_name__icontains=query) | Q(account__account_name__icontains=query) | Q(computer__computer_name__icontains=query))
    page = Paginator(members, 50).get_page(request.GET.get('page'))
    return render(request, 'domain/group_members.html', {'group': group, 'page_obj': page,
        'member_query': query, 'member_mode': mode, 'candidate_type': object_type,
        'selected_members': selected, 'operation_tasks': tasks})
