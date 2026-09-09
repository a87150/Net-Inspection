"""GET-only exports over saved snapshots and the asset table's query allowlist."""
from urllib.parse import quote

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


@never_cache
@admin_required
@require_GET
def configuration_download(request, kind, pk):
    asset = get_object_or_404(_model(kind), pk=pk)
    result = latest_configuration(asset)
    if result.status != 'success':
        return _response(result.message, 'text/plain; charset=utf-8', status=404 if result.status == 'missing' else 409)
    return _response(result.content, result.media_type, result.filename)


@never_cache
@admin_required
@require_GET
def configuration_zip(request, kind):
    assets, _ = apply_table_filters(request, _model(kind).objects.all(),
                                   get_table_definition(kind), include_legacy_status=False)
    return _response(build_configuration_zip(assets), 'application/zip', f'{kind}-configurations.zip')
