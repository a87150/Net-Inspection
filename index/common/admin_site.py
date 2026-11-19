"""Admin 登录接入与 /login/ 同一套失败计数。

admin 能改全部设备口令和域控配置，却是唯一没有限速的登录入口：中间件对
admin namespace 直接放行，Django 默认的 AdminSite.login 也不计失败。
"""
from django.contrib import messages
from django.contrib.admin import AdminSite
from django.contrib.admin.apps import AdminConfig
from django.shortcuts import redirect

LOCKED_MESSAGE = '登录失败次数过多，请稍后再试。'


def _credentials(request):
    username = (request.POST.get('username') or '').strip()[:150]
    address = request.META.get('REMOTE_ADDR', '')[:64]
    return username, address


class ThrottledAdminSite(AdminSite):
    """A locked-out admin login is refused before the form is even evaluated."""

    def login(self, request, extra_context=None):
        # 这个模块在 app 加载期就被 import，此时还不能碰 auth 视图。
        from .login_throttle import clear, is_locked, record_failure

        if request.method != 'POST':
            return super().login(request, extra_context)
        username, address = _credentials(request)
        if is_locked(username, address):
            messages.error(request, LOCKED_MESSAGE)
            return redirect(request.get_full_path())
        response = super().login(request, extra_context)
        # super() 成功时会 login(request, user)，失败时 request.user 仍是匿名。
        if request.user.is_authenticated:
            clear(username, address)
        else:
            record_failure(username, address)
        return response


class ThrottledAdminConfig(AdminConfig):
    default_site = 'index.common.admin_site.ThrottledAdminSite'
