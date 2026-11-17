"""Administrator downloads for daily PC collection scripts."""
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET
from net.models import ComputerAnalysisProfile, PCUploadConfig
from net.scripts.generator import PLATFORM_TEMPLATES, generate_pc_download

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
    source = PCUploadConfig.load()
    if source is None:
        return HttpResponseBadRequest('请先保存 PC 采集 API 配置。')
    try:
        filename, content_type, content = generate_pc_download(profile, source, platform)
    except (ValueError, OSError) as exc:
        return HttpResponseBadRequest(str(exc) if isinstance(exc, ValueError) else '采集包依赖不完整，请检查服务器端硬件库和许可证文件。', content_type='text/plain; charset=utf-8')
    response = HttpResponse(content, content_type=content_type)
    response['Cache-Control'] = 'no-store'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
