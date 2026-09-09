"""Permission-protected bulk Active Directory operation endpoints."""

from django.contrib import messages
from index.common.access import admin_required
from django.core.exceptions import ValidationError
from django.db import OperationalError
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from index.domain.forms import DomainAccountImportForm, DomainOperationForm
from net.domain.user_import import (
    csv_template_bytes, enqueue_imported_accounts, parse_account_upload,
    xlsx_template_bytes,
)
from net.domain.tasks import enqueue_domain_operation, retry_failed_domain_operation
from net.inspections.queue import is_sqlite_busy


def _list_route(object_type):
    return 'domain_account_list' if object_type == 'account' else 'domain_computer_list'


@admin_required
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
        except OperationalError as exc:
            if not is_sqlite_busy(exc):
                raise
            messages.error(request, '数据库正忙，本次域控任务未创建。请稍后重试；已有任务请在后台任务中查看，不要重复提交。')
        else:
            messages.success(request, '域控操作已加入后台队列。')
            return redirect('task_detail', pk=operation.task_id)
    else:
        messages.error(request, '域控操作参数无效，未创建任务。')
    return redirect(f'{reverse(_list_route(object_type))}?domain_modal=operation')


@admin_required
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
    except OperationalError as exc:
        if not is_sqlite_busy(exc):
            raise
        messages.error(request, '数据库正忙，本次重试任务未创建，请稍后重试。')
        return redirect('task_list')
    messages.success(request, '失败目标已重新加入后台队列。')
    return redirect('task_detail', pk=operation.task_id)


@admin_required
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
    return redirect(f'{reverse("domain_account_list")}?domain_modal=account_import')


@admin_required
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
