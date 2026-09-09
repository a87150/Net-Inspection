"""Opt-in, per-session HTML microcache using Django's ``pages`` cache alias.

Place after authentication, AccessMiddleware, and MessageMiddleware. Lookup is
in process_view: earlier authorization hooks must run before a hit can return.
Guest public responses returned by AccessMiddleware are intentionally uncached.
View decorators still do not run on hits, so only audited display routes belong
in DISPLAY_ROUTES; never add admin/configuration/action routes here.

Unsafe methods rotate a session epoch before dispatch, including non-display
routes. Other sessions see changes within the TTL (15 seconds by default).
In-flight reads can finish against their old epoch; no global invalidation or
Redis scans are needed. Configure backend socket/connect timeouts in settings.

Cache-Control no-store from a view is respected. Our browser no-store header
and the outer AccessMiddleware's headers are added AFTER the internal cache
decision. Explicit response cookies and pending session cookies are excluded.
A template's get_token() may renew the SAME incoming CSRF secret in the outer
CsrfViewMiddleware after our plain-content snapshot is stored. That renewal is
safe; creation, rotation, invalid cookies, and legacy masked-cookie conversion
bypass storage. Hits reuse only the matching session/cookie's form token and
do not replay Set-Cookie or renew the cookie expiry themselves.
"""
import hashlib
import json
from uuid import uuid4

from django.conf import settings
from django.contrib.messages import get_messages
from django.core.cache import caches
from django.http import HttpResponse
from django.utils import timezone, translation
from django.utils.cache import add_never_cache_headers, patch_vary_headers
from django.utils.deprecation import MiddlewareMixin


# These views only read display data. List/workspace views that consume modal
# session state are deliberately excluded, as are task polling and downloads.
DISPLAY_ROUTES = frozenset({'index', 'people_statistics', 'asset_detail', 'person_detail'})
DISPLAY_QUERY_KEYS = frozenset({'page', 'task_page'})
SESSION_EPOCH = '_net_page_cache_epoch_v1'
NAMESPACE = 'net:pages:v1:'


def _directives(value):
    return {part.strip().split('=', 1)[0].lower() for part in value.split(',')}


class PageCacheMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request._net_page_cache = None
        if (getattr(settings, 'NET_PAGE_CACHE_ENABLED', False)
                and request.method not in {'GET', 'HEAD', 'OPTIONS', 'TRACE'}
                and getattr(request, 'session', None) is not None
                and request.session.session_key):
            # Session persistence belongs to SessionMiddleware. No Redis cache
            # dependency for invalidation, and no credential-bearing logging.
            request.session[SESSION_EPOCH] = uuid4().hex

    def _identity(self, request):
        session = getattr(request, 'session', None)
        user = getattr(request, 'user', None)
        csrf = request.COOKIES.get(settings.CSRF_COOKIE_NAME)
        if (session is None or not session.session_key or session.modified
                or user is None or not user.is_authenticated or not user.is_active
                or not csrf or settings.CSRF_USE_SESSIONS
                or settings.SESSION_SAVE_EVERY_REQUEST
                # get_token() marks even unchanged secrets for renewal. Compare
                # the actual secret instead of treating that flag as rotation.
                or request.META.get('CSRF_COOKIE') != csrf
                or len(get_messages(request))):
            return None
        return [
            request.build_absolute_uri(), session.session_key,
            session.get(SESSION_EPOCH, ''), str(user.pk),
            bool(user.is_authenticated), bool(user.is_staff),
            bool(user.is_superuser), bool(user.is_active),
            hashlib.sha256(csrf.encode()).hexdigest(),
            # Vary: Cookie can cover cookies besides CSRF and session cookies.
            sorted(request.COOKIES.items()),
            getattr(request, 'LANGUAGE_CODE', None), translation.get_language(),
            timezone.get_current_timezone_name(),
            request.headers.get('Accept-Language', ''),
            request.headers.get('Accept', ''),
        ]

    def _key(self, request):
        identity = self._identity(request)
        if identity is None:
            return None
        digest = hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()
        return NAMESPACE + digest

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not getattr(settings, 'NET_PAGE_CACHE_ENABLED', False):
            return None
        match = request.resolver_match
        if (request.method != 'GET' or match is None or match.namespace
                or match.url_name not in DISPLAY_ROUTES
                or not set(request.GET).issubset(DISPLAY_QUERY_KEYS)
                or request.headers.get('X-Requested-With', '').lower() == 'xmlhttprequest'
                or request.headers.get('HX-Request', '').lower() == 'true'
                or 'Range' in request.headers
                or 'application/json' in request.headers.get('Accept', '').lower()
                or _directives(request.headers.get('Cache-Control', '')) & {'no-store', 'no-cache'}
                or 'no-cache' in request.headers.get('Pragma', '').lower()):
            return None
        try:
            seconds = int(getattr(settings, 'NET_PAGE_CACHE_SECONDS', 15))
        except (TypeError, ValueError, OverflowError):
            return None
        if seconds <= 0:
            return None
        key = self._key(request)
        if key is None:
            return None
        try:
            backend = caches['pages']
            cached = backend.get(key)
            if cached is not None:
                content, headers = cached
                response = HttpResponse(content, headers=headers)
                add_never_cache_headers(response)
                patch_vary_headers(response, ('Cookie',))
                return response
        except Exception:
            # Cache failures must not break the display view. Do not log the
            # exception: Redis errors may embed connection URLs/credentials.
            return None
        request._net_page_cache = (backend, key, seconds)
        return None

    def process_response(self, request, response):
        pending = getattr(request, '_net_page_cache', None)
        if pending is None:
            return response
        backend, key, seconds = pending
        if (response.status_code != 200 or response.streaming or response.cookies
                or response.has_header('Set-Cookie')
                or response.has_header('Content-Disposition')
                or response.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'text/html'
                or 'no-store' in _directives(response.get('Cache-Control', ''))
                or _directives(response.get('Vary', '')) - {'', 'cookie', 'accept-language'}
                or self._key(request) != key):
            return response
        # Store a plain snapshot, never a request, lazy template, or response
        # object with resource closers. Hits do not refresh the TTL.
        try:
            backend.set(key, (response.content, dict(response.items())), timeout=seconds)
        except Exception:
            pass
        add_never_cache_headers(response)
        patch_vary_headers(response, ('Cookie',))
        return response
