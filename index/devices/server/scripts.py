"""Administrator download of the Windows server HTTP inspection agent."""
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.views.decorators.http import require_GET


@login_required
@require_GET
def windows_server_script_download(request):
    if not (request.user.is_staff or request.user.is_superuser):
        return HttpResponseForbidden('Administrator access is required.')
    path = settings.BASE_DIR / 'agents/server/windows/InspectionHttpService.ps1'
    try:
        content = path.read_bytes()
    except FileNotFoundError as exc:
        raise Http404('Windows server inspection script is missing.') from exc
    response = HttpResponse(content, content_type='text/plain; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="InspectionHttpService.ps1"'
    response['Cache-Control'] = 'no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
