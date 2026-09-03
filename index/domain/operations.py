"""Permission-protected bulk Active Directory operation endpoints."""

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from index.domain.forms import DomainAccountImportForm, DomainOperationForm
from net.domain.user_import import (
    csv_template_bytes, enqueue_imported_accounts, parse_account_upload,
    xlsx_template_bytes,
)
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


@permission_required('net.manage_domain_operations', raise_exception=True)
@require_POST
def domain_account_import(request):
    form = DomainAccountImportForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            rows = parse_account_upload(form.cleaned_data['file'])
            operations = enqueue_imported_accounts(
                requested_by=request.user,
                rows=rows,
                password=form.cleaned_data['initial_password'],
            )
        except ValidationError:
            messages.error(request, '域账号表格校验失败，未创建任务。')
        else:
            messages.success(request, f'已创建 {len(operations)} 个域账号任务。')
            return redirect('task_list')
    else:
        messages.error(request, '域账号表格校验失败，未创建任务。')
    return redirect('domain_account_list')


@permission_required('net.manage_domain_operations', raise_exception=True)
@require_GET
def domain_account_import_template(request, file_format):
    if file_format == 'csv':
        response = HttpResponse(csv_template_bytes(), content_type='text/csv; charset=utf-8')
        filename = 'domain-accounts-template.csv'
    elif file_format == 'xlsx':
        response = HttpResponse(
            xlsx_template_bytes(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        filename = 'domain-accounts-template.xlsx'
    else:
        raise Http404('不支持的模板格式')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
