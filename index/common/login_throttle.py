"""Failed-login throttling.

django-axes is not a dependency, and these counters only have to outlive a burst of
attempts, so the cache is the right store. Every cache call is guarded: a cache outage
must fail open (nobody gets locked out), never closed.
"""
import hashlib

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.shortcuts import redirect


def _key(kind, value):
    digest = hashlib.sha256(value.encode('utf-8', 'replace')).hexdigest()[:24]
    return 'net-login-fail:%s:%s' % (kind, digest)


def _get(key):
    try:
        return cache.get(key)
    except Exception:
        return None


def _set(key, value, timeout):
    try:
        cache.set(key, value, timeout)
    except Exception:
        pass


def _pairs(username, address):
    return (('user', username), ('ip', address))


def is_locked(username, address):
    if settings.NET_LOGIN_LOCK_SECONDS <= 0:
        return False
    return any(_get(_key('lock-' + kind, value)) for kind, value in _pairs(username, address))


def record_failure(username, address):
    if settings.NET_LOGIN_MAX_FAILURES <= 0:
        return
    limits = {'user': settings.NET_LOGIN_MAX_FAILURES,
              'ip': settings.NET_LOGIN_IP_MAX_FAILURES}
    for kind, value in _pairs(username, address):
        if not value:
            continue
        key = _key('fail-' + kind, value)
        try:
            count = cache.incr(key)
        except ValueError:
            _set(key, 1, settings.NET_LOGIN_WINDOW_SECONDS)
            count = 1
        except Exception:
            continue
        if count >= limits[kind] and settings.NET_LOGIN_LOCK_SECONDS > 0:
            _set(_key('lock-' + kind, value), 1, settings.NET_LOGIN_LOCK_SECONDS)


def clear(username, address):
    """A successful login lifts the counters immediately."""
    for kind, value in _pairs(username, address):
        if value:
            _set(_key('fail-' + kind, value), 0, 1)
            _set(_key('lock-' + kind, value), 0, 1)


def _address(request):
    return request.META.get('REMOTE_ADDR', '')[:64]


def _username(request):
    return (request.POST.get('username') or '').strip()[:150]


class ThrottledLoginView(LoginView):
    """LoginView plus a per-account and per-source failure counter."""

    def dispatch(self, request, *args, **kwargs):
        if is_locked(_username(request), _address(request)):
            messages.error(request, '登录失败次数过多，请稍后再试。')
            return redirect(settings.LOGIN_URL)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        clear(_username(self.request), _address(self.request))
        return super().form_valid(form)

    def form_invalid(self, form):
        record_failure(_username(self.request), _address(self.request))
        return super().form_invalid(form)