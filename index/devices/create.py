from django.contrib import messages
from index.common.access import admin_required
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST
from net.models import TaskRun, TaskTargetRun

from .forms import DEVICE_KINDS, device_form, preserve_empty_secrets


@admin_required
@require_POST
def asset_create(request, kind):
    if kind not in DEVICE_KINDS:
        raise Http404('该项目不支持手动添加设备。')
    form = device_form(kind, request.POST)
    if form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            form.add_error(None, '设备保存冲突，请检查 IP 是否已存在。')
        else:
            messages.success(request, '设备已添加，可在列表中查看；尚未执行巡检。')
            return redirect('asset_list', kind=kind)
    from .views import asset_list
    return asset_list(request, kind, creation_form=form)


_CONNECTION_FIELDS = {
    'networks': {'vendor', 'ip', 'connection_type', 'port', 'username', 'password', 'snmp_version', 'snmp_port', 'snmp_community', 'snmp_security_level', 'snmp_username', 'snmp_auth_protocol', 'snmp_auth_password', 'snmp_priv_protocol', 'snmp_priv_password', 'snmp_context_name', 'snmp_retries'},
    'servers': {'server_type', 'ip', 'port', 'username', 'password', 'api_url', 'api_token', 'verify_ssl'},
    'monitors': {'vendor', 'device_type', 'ip', 'api_url', 'api_username', 'api_password', 'api_token', 'verify_ssl'},
}
_TARGET_TYPES = {
    'networks': TaskTargetRun.TargetType.NETWORK_DEVICE,
    'servers': TaskTargetRun.TargetType.SERVER,
    'monitors': TaskTargetRun.TargetType.MONITOR,
}


def _connection_changes_during_active_task(kind, form, asset):
    original = type(asset).objects.get(pk=asset.pk)
    changed = [
        name for name in _CONNECTION_FIELDS[kind]
        if name in form.cleaned_data and form.cleaned_data[name] != getattr(original, name)
    ]
    if not changed:
        return False
    return TaskTargetRun.objects.filter(
        target_type=_TARGET_TYPES[kind], target_id=str(asset.pk),
        task__status__in=TaskRun.ACTIVE_STATUSES,
    ).exists()

@admin_required
def asset_edit(request, kind, pk):
    if kind not in DEVICE_KINDS:
        raise Http404('该项目不支持手动修改设备。')
    from .views import _asset_page, asset_list
    asset = get_object_or_404(_asset_page(kind).model, pk=pk)
    if request.method != 'POST':
        return asset_list(request, kind, edit_device=asset)
    form = device_form(kind, request.POST, instance=asset)
    if form.is_valid():
        try:
            with transaction.atomic():
                preserve_empty_secrets(form)
                if _connection_changes_during_active_task(kind, form, asset):
                    form.add_error(None, '该设备存在活动巡检任务，任务结束后才能修改连接配置。')
                else:
                    form.save()
        except IntegrityError:
            form.add_error(None, '设备保存冲突，请检查 IP 是否已存在。')
        else:
            if not form.errors:
                messages.success(request, '设备配置已更新；硬件与系统信息仍由巡检自动采集。')
                return redirect('asset_edit', kind=kind, pk=asset.pk)
    return asset_list(request, kind, creation_form=form, edit_device=asset)