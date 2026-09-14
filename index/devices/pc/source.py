from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.utils import timezone
from net.models import PCLogSourceConfig
from net.devices.pc.connectors.factory import build_connector
from net.devices.pc.connectors.base import PCLogConnectionError
from .forms import PCLogSourceForm
from .simple_source_form import SimplePCLogSourceForm


def require_source_admin(request):
    if not (request.user.is_staff or request.user.is_superuser):
        raise PermissionDenied


def _source_config_redirect():
    return redirect(f"{reverse('computer_analysis_list')}?task_modal=profile")


@login_required
@require_POST
def pc_log_source_save(request):
    require_source_admin(request)
    form_class = SimplePCLogSourceForm if 'shared_path' in request.POST or 'ftp_directory' in request.POST else PCLogSourceForm
    form = form_class(request.POST, instance=PCLogSourceConfig.load())
    try:
        if not form.is_valid():
            return render(request, 'devices/pc/source_form_errors.html', {'form': form}, status=400)
        form.save()
    except ValidationError as exc:
        form.add_error(None, '；'.join(exc.messages))
        return render(request, 'devices/pc/source_form_errors.html', {'form': form}, status=400)
    messages.success(request, 'PC 日志来源已保存。可以测试连接或预览日志。')
    return _source_config_redirect()


@login_required
@require_POST
def pc_log_source_test(request):
    require_source_admin(request)
    source = PCLogSourceConfig.load()
    if source is None:
        messages.error(request, '请先保存日志来源。')
        return _source_config_redirect()
    connector = None
    try:
        connector = build_connector(source)
        connector.probe()
    except (PCLogConnectionError, ValidationError, OSError, ValueError):
        source.last_test_error = '连接测试失败，请检查地址、账号、协议和读取/创建/重命名/删除权限。'
        if source.source_type == 'smb' and source.smb_auth_mode == 'system':
            source.last_test_error = '当前 Windows 运行账号无法完成共享目录读取或归档权限测试。请检查运行账号权限，或改选“手动指定账号密码”并保存后重试。'
        messages.error(request, source.last_test_error)
    else:
        source.last_test_error = ''
        messages.success(request, '连接测试成功，已验证列目录、读写、归档重命名和删除临时测试文件权限。')
    finally:
        if connector is not None:
            connector.close()
    source.last_tested_at = timezone.now()
    source.save(update_fields=['last_tested_at', 'last_test_error'])
    return _source_config_redirect()


@login_required
@require_POST
def pc_log_source_preview(request):
    require_source_admin(request)
    source = PCLogSourceConfig.load()
    if source is None:
        messages.error(request, '请先保存日志来源。')
        return _source_config_redirect()
    connector = None
    try:
        connector = build_connector(source)
        rows = connector.list_json()
    except (PCLogConnectionError, ValidationError, OSError, ValueError):
        messages.error(request, '预览失败，请先测试连接并检查汇总目录和文件时间范围。')
        return _source_config_redirect()
    finally:
        if connector is not None:
            connector.close()
    return render(request, 'devices/pc/source_preview.html',
                  {'source': source, 'rows': rows[:500], 'total': len(rows)})
