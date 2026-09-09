"""Administrator-only access to saved configuration versions."""
import re
import unicodedata

from django.core.exceptions import ImproperlyConfigured
from django.http import Http404, HttpResponse, HttpResponseNotFound
from django.shortcuts import get_object_or_404, render
from django.utils.http import content_disposition_header
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from index.common.access import admin_required
from net.models import Network_Device, SecurityDevice


def _asset(kind, pk):
    models = {'networks': Network_Device, 'monitors': SecurityDevice}
    if kind not in models:
        raise Http404('此资产类型不支持配置备份')
    return get_object_or_404(models[kind], pk=pk)


@never_cache
@admin_required
@require_GET
def configuration_backup_list(request, kind, pk):
    from net.devices.configuration_backups import list_configuration_backups

    try:
        asset = _asset(kind, pk)
    except Http404:
        return HttpResponseNotFound('未找到设备或配置备份。')
    return render(request, 'devices/configuration_backups.html', {
        'asset': asset, 'item_key': kind,
        'backups': list_configuration_backups(asset)[:10],
    })


@never_cache
@admin_required
@require_GET
def configuration_backup_download(request, kind, pk, backup_id):
    from net.devices.configuration_backups import (
        list_configuration_backups, read_configuration_backup,
    )

    try:
        asset = _asset(kind, pk)
        backup = get_object_or_404(
            list_configuration_backups(asset), pk=backup_id,
            device_type='network_device' if kind == 'networks' else 'monitor',
            device_id=asset.pk,
        )
    except Http404:
        return HttpResponseNotFound('未找到设备或配置备份。')

    try:
        raw = read_configuration_backup(backup)
    except (ValueError, ImproperlyConfigured):
        # Never expose ciphertext, plaintext or key diagnostics in error pages.
        return HttpResponse('备份暂时无法读取或校验失败，请联系管理员。', status=503,
                            content_type='text/plain; charset=utf-8')

    filename = re.split(r'[/\\]', backup.filename or '')[-1]
    filename = ''.join(
        char for char in filename
        if not unicodedata.category(char).startswith('C') and char not in '<>:"|?*'
    ).strip(' .')[:180]
    filename = filename or f'configuration-{backup.pk}.cfg'
    response = HttpResponse(raw, content_type='application/octet-stream')
    response['Content-Disposition'] = content_disposition_header(True, filename)
    response['Content-Length'] = str(len(raw))
    response['X-Content-Type-Options'] = 'nosniff'
    return response
