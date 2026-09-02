"""Authenticated downloads for PC collection scripts."""

from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from net.models import ComputerAnalysisProfile
from net.scripts.generator import PLATFORM_TEMPLATES, generate_pc_script


def _origin(value):
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {'http', 'https'}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {'', '/'}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError('Public base URL must be an HTTP(S) origin.')
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError('Public base URL must use a valid port.') from exc
    if not hostname or parsed.netloc.endswith(':') or (port is not None and not 1 <= port <= 65535):
        raise ValueError('Public base URL must use a valid host and port.')
    host = f'[{hostname}]' if ':' in hostname else hostname
    port_suffix = f':{port}' if port is not None else ''
    return f'{parsed.scheme}://{host}{port_suffix}'


def _public_origin(request):
    configured = getattr(settings, 'NET_PUBLIC_BASE_URL', '')
    if configured:
        return _origin(configured)
    return _origin(request.build_absolute_uri('/'))


@login_required
@require_GET
def pc_script_download(request, profile_id, platform):
    """Return a profile-bound Windows or macOS collection script."""
    if platform not in PLATFORM_TEMPLATES:
        raise Http404('Unsupported PC script platform.')
    profile = get_object_or_404(ComputerAnalysisProfile, pk=profile_id)
    if not profile.is_enabled:
        return HttpResponseBadRequest(
            '该分析配置已停用，请先启用分析配置后再下载采集脚本。',
            content_type='text/plain; charset=utf-8',
        )
    try:
        script = generate_pc_script(profile, platform, _public_origin(request))
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc), content_type='text/plain; charset=utf-8')

    response = HttpResponse(script.as_bytes(), content_type=script.content_type)
    response['Cache-Control'] = 'no-store'
    response['Content-Disposition'] = f'attachment; filename="{script.filename}"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
