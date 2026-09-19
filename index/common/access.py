"""Central application authorization; new routes are administrator-only by default."""
from functools import wraps
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden
from django.utils.cache import add_never_cache_headers, patch_vary_headers
from django.utils.deprecation import MiddlewareMixin


PUBLIC_VIEWS = frozenset({'index', 'asset_list', 'people_statistics', 'item_list'})
READER_VIEWS = PUBLIC_VIEWS | frozenset({
    'access_record_list',
    'asset_detail', 'person_detail', 'computer_analysis_list', 'computer_log_list',
    'computer_log_detail', 'computer_analysis_detail', 'computer_error_list',
    'task_list', 'task_detail', 'alert_list', 'alert_detail',
    'table_export', 'table_export_scoped', 'domain_account_list', 'domain_account_detail',
    'domain_computer_list', 'domain_computer_detail', 'domain_group_list', 'domain_group_detail',
    'inspection_records', 'error_records', 'record_list', 'record_detail',
    'infrastructure_inspection_detail', 'analysis_problem_list',
})
AUTH_VIEWS = frozenset({
    'login', 'logout', 'admin_password_reset', 'password_reset_done',
    'password_reset_confirm', 'password_reset_complete',
})
SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})


def can_read_private(user):
    return bool(user.is_authenticated and user.is_active)


def is_admin(user):
    return bool(can_read_private(user) and (user.is_staff or user.is_superuser))


def access_context(request):
    return {'can_administer': is_admin(request.user),
            'can_view_private': can_read_private(request.user)}


def admin_required(view):
    @wraps(view)
    def protected(request, *args, **kwargs):
        if not can_read_private(request.user):
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        if not is_admin(request.user):
            return HttpResponseForbidden('此功能仅管理员可使用。')
        return view(request, *args, **kwargs)
    return protected


class AccessMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        if match.namespace == 'admin':
            # Django's admin site independently enforces staff/model permissions.
            return None
        name = match.url_name
        if name == 'pc_log_upload':
            # Endpoint enforces a write-only bearer token, independently of sessions.
            return None
        if name in AUTH_VIEWS:
            return None
        if is_admin(request.user):
            return None
        if not can_read_private(request.user):
            if request.method in SAFE_METHODS and name in PUBLIC_VIEWS:
                from .public_views import public_response
                return public_response(request, name, view_kwargs)
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)
        if request.method not in SAFE_METHODS or name not in READER_VIEWS:
            return HttpResponseForbidden('此功能仅管理员可使用。')
        return None

    def process_response(self, request, response):
        # Never let a shared/browser cache reuse a private page after logout.
        add_never_cache_headers(response)
        patch_vary_headers(response, ('Cookie',))
        return response
