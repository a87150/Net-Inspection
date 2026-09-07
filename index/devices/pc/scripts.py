"""Administrator downloads for daily PC collection scripts."""
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET
from net.models import ComputerAnalysisProfile, PCLogSourceConfig
from net.scripts.generator import PLATFORM_TEMPLATES, generate_pc_script

@login_required
@require_GET
def pc_script_download(request, profile_id, platform):
    if not (request.user.is_staff or request.user.is_superuser):
        return HttpResponseForbidden('Administrator access is required.')
    if platform not in PLATFORM_TEMPLATES:
        raise Http404('Unsupported PC script platform.')
    profile = get_object_or_404(ComputerAnalysisProfile, pk=profile_id)
    if not profile.is_enabled:
        return HttpResponseBadRequest('请先启用分析配置后再下载采集脚本。')
    source = PCLogSourceConfig.load()
    if source is None:
        return HttpResponseBadRequest('请先保存 PC 日志来源并配置终端共享目录。')
    try:
        script = generate_pc_script(profile, source, platform)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc), content_type='text/plain; charset=utf-8')
    response = HttpResponse(script.as_bytes(), content_type=script.content_type)
    response['Cache-Control'] = 'no-store'
    response['Content-Disposition'] = f'attachment; filename="{script.filename}"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
