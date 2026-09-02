"""Permission-protected bulk Active Directory operation endpoints."""

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from index.domain.forms import DomainOperationForm
from net.domain.tasks import enqueue_domain_operation, retry_failed_domain_operation


def _list_route(object_type):
    return 'domain_account_list' if object_type == 'account' else 'domain_computer_list'


@permission_required('net.manage_domain_operations', raise_exception=True)
@require_POST
def domain_operation_create(request):
    object_type = request.POST.get('object_type', '')
    target_ids = request.POST.getlist('target_ids')
    form = DomainOperationForm(
        request.POST,
        target_choices=target_ids,
    )
    if form.is_valid():
        try:
            operation = enqueue_domain_operation(
                requested_by=request.user,
                object_type=form.cleaned_data['object_type'],
                action=form.cleaned_data['action'],
                target_ids=form.cleaned_data['target_ids'],
                parameters=form.operation_parameters(),
                password=form.operation_password() or None,
            )
        except ValidationError:
            messages.error(request, '域控操作参数无效，未创建任务。')
        else:
            messages.success(request, '域控操作已加入后台队列。')
            return redirect('task_detail', pk=operation.task_id)
    else:
        messages.error(request, '域控操作参数无效，未创建任务。')
    return redirect(_list_route(object_type))


@permission_required('net.manage_domain_operations', raise_exception=True)
@require_POST
def domain_operation_retry(request, pk):
    try:
        operation = retry_failed_domain_operation(
            pk,
            requested_by=request.user,
            password=request.POST.get('password') or None,
        )
    except ValidationError:
        messages.error(request, '域控失败目标无法重试；结果不确定时需要人工核查目录对象。')
        return redirect('task_list')
    messages.success(request, '失败目标已重新加入后台队列。')
    return redirect('task_detail', pk=operation.task_id)
