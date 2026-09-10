"""GET-only exports over saved snapshots and the asset table's query allowlist."""
from urllib.parse import quote
from uuid import UUID

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from index.common.table_query import apply_table_filters
from index.common.access import admin_required
from index.common.table_registry import get_table_definition
from net.data_exchange.configuration import build_configuration_zip, latest_configuration
from net.models import Network_Device, SecurityDevice


def _model(kind):
    model = {'networks': Network_Device, 'monitors': SecurityDevice}.get(kind)
    if model is None:
        raise Http404('不支持此资产类型的配置导出。')
    return model


def _response(content, media_type, filename='', status=200):
    response = HttpResponse(content, content_type=media_type, status=status)
    response['Cache-Control'] = 'no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    if filename:
        response['Content-Disposition'] = f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
    return response


def _configuration_response(asset):
    result = latest_configuration(asset)
    if result.status != 'success':
        status = 404 if result.status == 'missing' else 409
        return _response(result.message, 'text/plain; charset=utf-8', status=status)
    return _response(result.content, result.media_type, result.filename)


def _selected_ids(request):
    raw = request.GET.get('target_ids')
    if raw is None:
        return None
    values = [value.strip() for value in raw.split(',') if value.strip()]
    if not values or len(values) > 500:
        return ()
    try:
        return tuple(dict.fromkeys(UUID(value) for value in values))
    except (TypeError, ValueError):
        return ()


@never_cache
@admin_required
@require_GET
def configuration_download(request, kind, pk):
    asset = get_object_or_404(_model(kind), pk=pk)
    return _configuration_response(asset)


@never_cache
@admin_required
@require_GET
def configuration_zip(request, kind):
    model = _model(kind)
    selected_ids = _selected_ids(request)
    if selected_ids is not None:
        if not selected_ids:
            return _response('请选择有效的设备。', 'text/plain; charset=utf-8', status=400)
        assets = model.objects.filter(pk__in=selected_ids)
        if len(selected_ids) == 1:
            asset = assets.first()
            if asset is None:
                return _response('所选设备不存在。', 'text/plain; charset=utf-8', status=404)
            return _configuration_response(asset)
    else:
        assets, _ = apply_table_filters(request, model.objects.all(),
                                        get_table_definition(kind), include_legacy_status=False)
    return _response(build_configuration_zip(assets), 'application/zip', f'{kind}-configurations.zip')
