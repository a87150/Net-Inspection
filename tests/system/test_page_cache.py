"""DB/network-free middleware tests; run: python -m tests.system.test_page_cache."""
if __name__ == '__main__':
    from django.conf import settings
    settings.configure(
        SECRET_KEY='page-cache-tests-only',
        INSTALLED_APPS=['django.contrib.auth', 'django.contrib.contenttypes',
                        'django.contrib.sessions', 'django.contrib.messages', 'net'],
        DATABASES={}, USE_TZ=True,
    )
    import django
    django.setup()

import importlib.util
import re
import time
from types import SimpleNamespace
from unittest import main
from unittest.mock import patch

from django.contrib import messages
from django.contrib.messages.storage.cookie import CookieStorage
from django.contrib.sessions.backends.cache import SessionStore
from django.core.cache import caches
from django.http import HttpResponse, StreamingHttpResponse
from django.middleware.csrf import rotate_token
from django.template import Engine, RequestContext
from django.test import Client, RequestFactory, SimpleTestCase, override_settings
from django.urls import include, path


calls = 0
render_page = lambda request: HttpResponse('display')


def display(request):
    global calls
    calls += 1
    response = render_page(request)
    response['X-Render'] = str(calls)
    return response


class TestIdentityMiddleware:
    """Supply identity without an auth database; all other middleware is real."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.user = SimpleNamespace(
            pk=request.headers.get('Test-User', '1'),
            is_authenticated=request.headers.get('Test-Guest') != '1',
            is_active=request.headers.get('Test-Inactive') != '1',
            is_staff=request.headers.get('Test-Staff', '1') == '1',
            is_superuser=request.headers.get('Test-Super') == '1',
        )
        request.LANGUAGE_CODE = request.headers.get('Test-Language', 'en')
        return self.get_response(request)


urlpatterns = [
    path('', display, name='index'),
    path('statistics/', display, name='people_statistics'),
    path('detail/', display, name='asset_detail'),
    path('person/', display, name='person_detail'),
    path('settings/', display, name='domain_controller_settings'),
    path('download/', display, name='configuration_download'),
    path('status/', display, name='people_task_status'),
    path('unknown/', display, name='future_route'),
    path('admin/', include(([path('', display, name='index')], 'admin'))),
]


@override_settings(
    ROOT_URLCONF=__name__, ALLOWED_HOSTS=['testserver', 'other.invalid'],
    NET_PAGE_CACHE_ENABLED=True, NET_PAGE_CACHE_SECONDS=15,
    SESSION_ENGINE='django.contrib.sessions.backends.cache',
    SESSION_CACHE_ALIAS='default', SESSION_SAVE_EVERY_REQUEST=False,
    CSRF_USE_SESSIONS=False, LOGIN_URL='/login/',
    CACHES={
        'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                    'LOCATION': 'page-cache-test-sessions'},
        'pages': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                  'LOCATION': 'page-cache-test-pages'},
    },
    MIDDLEWARE=[
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.middleware.csrf.CsrfViewMiddleware',
        __name__ + '.TestIdentityMiddleware',
        'index.common.access.AccessMiddleware',
        'django.contrib.messages.middleware.MessageMiddleware',
        'net.infrastructure.page_cache.PageCacheMiddleware',
    ],
)
class PageCacheTests(SimpleTestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('net.infrastructure.page_cache'),
                             'Page cache middleware has not been implemented')
        global calls, render_page
        calls = 0
        render_page = lambda request: HttpResponse('display')
        caches['default'].clear()
        caches['pages'].clear()
        self.client = self.new_client()

    def new_client(self):
        client = Client()
        session = SessionStore()
        session['fixture'] = True
        session.save()
        client.cookies['sessionid'] = session.session_key
        client.cookies['csrftoken'] = 'a' * 32
        return client

    def test_hit_preserves_html_and_browser_privacy(self):
        first = self.client.get('/')
        second = self.client.get('/')
        self.assertEqual(first.content, second.content)
        self.assertEqual(second['X-Render'], '1')
        self.assertEqual(calls, 1)
        self.assertIn('private', second['Cache-Control'])
        self.assertIn('no-store', second['Cache-Control'])
        self.assertIn('Cookie', second['Vary'])

    def test_each_allowlisted_display_route_hits(self):
        for url in ('/', '/statistics/', '/detail/', '/person/'):
            with self.subTest(url=url):
                first = self.client.get(url)
                self.assertEqual(self.client.get(url)['X-Render'], first['X-Render'])

    def test_absolute_url_and_query_are_isolated(self):
        for url, options in [('/', {}), ('/?page=2', {}), ('/?page=3', {}),
                             ('/?page=2&page=3', {}), ('/', {'secure': True}),
                             ('/', {'HTTP_HOST': 'other.invalid'})]:
            before = calls
            first = self.client.get(url, **options)
            self.assertEqual(calls, before + 1)
            self.assertEqual(self.client.get(url, **options)['X-Render'], first['X-Render'])

    def test_users_roles_locale_and_sessions_are_isolated(self):
        for headers in ({}, {'HTTP_TEST_USER': '2'}, {'HTTP_TEST_STAFF': '0'},
                        {'HTTP_TEST_SUPER': '1'}, {'HTTP_TEST_LANGUAGE': 'zh-hans'}):
            before = calls
            first = self.client.get('/', **headers)
            self.assertEqual(calls, before + 1)
            self.assertEqual(self.client.get('/', **headers)['X-Render'], first['X-Render'])
        other = self.new_client()
        self.assertNotEqual(other.get('/')['X-Render'], self.client.get('/')['X-Render'])

    def test_csrf_cookie_isolation_and_rotation(self):
        global render_page
        render_page = lambda request: HttpResponse(request.COOKIES.get('csrftoken', ''))
        self.assertEqual(self.client.get('/').content, b'a' * 32)
        self.client.cookies['csrftoken'] = 'b' * 32
        self.assertEqual(self.client.get('/').content, b'b' * 32)
        other = self.new_client()
        self.assertEqual(other.get('/').content, b'a' * 32)

    def test_access_denies_before_warm_cache_and_guest_public_response_bypasses(self):
        self.client.get('/detail/')
        self.assertEqual(self.client.get('/detail/', HTTP_TEST_INACTIVE='1').status_code, 302)
        self.assertEqual(self.client.get('/detail/', HTTP_TEST_GUEST='1').status_code, 302)
        self.client.get('/')
        with patch('index.common.public_views.public_response',
                   side_effect=lambda *args: HttpResponse('public')):
            for _ in range(2):
                self.assertEqual(self.client.get('/', HTTP_TEST_GUEST='1').content, b'public')
        self.assertEqual(calls, 2)

    def test_excluded_routes_never_hit_even_for_admin(self):
        for url in ('/admin/', '/settings/', '/download/', '/status/', '/unknown/'):
            with self.subTest(url=url):
                first = self.client.get(url)
                self.assertNotEqual(self.client.get(url)['X-Render'], first['X-Render'])

    def test_ajax_modal_and_non_display_queries_bypass_warm_cache(self):
        for query in ('task_modal=run', 'alert_modal=policy', 'modal=', 'import=api',
                      'provider=feishu', 'task_profile=1', 'future_modal=1', 'download=1'):
            with self.subTest(query=query):
                first = self.client.get('/?' + query)
                self.assertNotEqual(self.client.get('/?' + query)['X-Render'], first['X-Render'])
        self.client.get('/')
        for headers in ({'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'},
                        {'HTTP_HX_REQUEST': 'true'}, {'HTTP_ACCEPT': 'application/json'},
                        {'HTTP_RANGE': 'bytes=0-10'}, {'HTTP_CACHE_CONTROL': 'no-cache'}):
            first = self.client.get('/', **headers)
            self.assertNotEqual(self.client.get('/', **headers)['X-Render'], first['X-Render'])

    def test_writes_invalidate_only_current_session_even_on_nonallowlisted_route(self):
        other = self.new_client()
        first = self.client.get('/')
        other_first = other.get('/')
        for method in ('post', 'put', 'patch', 'delete'):
            getattr(self.client, method)('/unknown/')
            fresh = self.client.get('/')
            self.assertNotEqual(fresh['X-Render'], first['X-Render'])
            self.assertEqual(other.get('/')['X-Render'], other_first['X-Render'])
            first = fresh

    def test_head_and_options_do_not_read_or_store(self):
        self.client.get('/')
        for method in ('head', 'options'):
            before = calls
            getattr(self.client, method)('/')
            getattr(self.client, method)('/')
            self.assertEqual(calls, before + 2)

    def test_messages_bypass_hit_without_consuming_and_generated_messages_do_not_store(self):
        global render_page
        self.client.get('/')
        storage = CookieStorage(RequestFactory().get('/'))
        storage.add(messages.INFO, 'notice')
        response = HttpResponse()
        storage.update(response)
        self.client.cookies['messages'] = response.cookies['messages']
        render_page = lambda request: HttpResponse('|'.join(str(m) for m in messages.get_messages(request)))
        self.assertEqual(self.client.get('/').content, b'notice')
        self.assertEqual(self.client.get('/').content, b'')
        caches['pages'].clear()
        def generated(request):
            messages.info(request, 'new')
            return HttpResponse('|'.join(str(m) for m in messages.get_messages(request)))
        render_page = generated
        first = self.client.get('/')
        self.assertNotEqual(self.client.get('/')['X-Render'], first['X-Render'])

    def test_unsafe_responses_are_not_stored(self):
        global render_page
        def cookie(request):
            response = HttpResponse('cookie')
            response.set_cookie('custom', 'value')
            return response
        def session_change(request):
            request.session['changed'] = True
            return HttpResponse('changed')
        def rotated(request):
            rotate_token(request)
            return HttpResponse('rotated')
        factories = [cookie, session_change, rotated,
                     lambda r: StreamingHttpResponse(iter([b'stream'])),
                     lambda r: HttpResponse('error', status=500),
                     lambda r: HttpResponse('missing', status=404),
                     lambda r: HttpResponse(status=302),
                     lambda r: HttpResponse('{}', content_type='application/json'),
                     lambda r: HttpResponse('download', headers={'Content-Disposition': 'attachment'}),
                     lambda r: HttpResponse('secret', headers={'Cache-Control': 'private, No-Store'}),
                     lambda r: HttpResponse('variant', headers={'Vary': 'X-Unknown'}),
                     lambda r: HttpResponse('variant', headers={'Vary': '*'})]
        for factory in factories:
            with self.subTest(factory=factory):
                caches['pages'].clear()
                render_page = factory
                first = self.client.get('/')
                self.assertNotEqual(self.client.get('/')['X-Render'], first['X-Render'])

    def test_private_header_alone_allows_internal_storage(self):
        global render_page
        render_page = lambda r: HttpResponse('private', headers={'Cache-Control': 'private'})
        first = self.client.get('/')
        self.assertEqual(self.client.get('/')['X-Render'], first['X-Render'])

    def test_disabled_missing_session_and_missing_csrf_bypass(self):
        with override_settings(NET_PAGE_CACHE_ENABLED=False):
            self.client.get('/')
            self.client.get('/')
            self.assertEqual(calls, 2)
        for cookie in ('sessionid', 'csrftoken'):
            client = self.new_client()
            del client.cookies[cookie]
            first = client.get('/')
            self.assertNotEqual(client.get('/')['X-Render'], first['X-Render'])

    def test_short_ttl_expires_without_sleep_and_hits_do_not_extend_it(self):
        with patch('time.time', return_value=time.time()) as clock:
            first = self.client.get('/')
            clock.return_value += 14
            self.assertEqual(self.client.get('/')['X-Render'], first['X-Render'])
            clock.return_value += 2
            self.assertNotEqual(self.client.get('/')['X-Render'], first['X-Render'])
        with override_settings(NET_PAGE_CACHE_SECONDS=0):
            first = self.client.get('/')
            self.assertNotEqual(self.client.get('/')['X-Render'], first['X-Render'])

    def test_cache_errors_fail_open_without_logging_secrets(self):
        for operation in ('get', 'set'):
            caches['pages'].clear()
            with patch.object(caches['pages'], operation,
                              side_effect=ConnectionError('redis://secret@host')), self.assertNoLogs():
                first = self.client.get('/')
                self.assertEqual(first.status_code, 200)
                self.assertNotEqual(self.client.get('/')['X-Render'], first['X-Render'])
        with override_settings(CACHES={'default': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                'LOCATION': 'page-cache-test-sessions'}}):
            self.assertEqual(self.client.get('/').status_code, 200)

    def test_missing_enable_setting_defaults_off(self):
        from django.conf import settings
        with override_settings():
            del settings.NET_PAGE_CACHE_ENABLED
            first = self.client.get('/')
            self.assertNotEqual(self.client.get('/')['X-Render'], first['X-Render'])

    def test_default_and_custom_ttl(self):
        from django.conf import settings
        with override_settings():
            del settings.NET_PAGE_CACHE_SECONDS
            with patch('time.time', return_value=time.time()) as clock:
                first = self.client.get('/')
                clock.return_value += 16
                self.assertNotEqual(self.client.get('/')['X-Render'], first['X-Render'])
        caches['pages'].clear()
        with override_settings(NET_PAGE_CACHE_SECONDS=2):
            with patch('time.time', return_value=time.time()) as clock:
                first = self.client.get('/')
                clock.return_value += 3
                self.assertNotEqual(self.client.get('/')['X-Render'], first['X-Render'])

    def test_cookie_variants_and_accept_language_are_isolated(self):
        first = self.client.get('/')
        self.client.cookies['display_preference'] = 'different'
        second = self.client.get('/')
        self.assertNotEqual(second['X-Render'], first['X-Render'])
        self.assertNotEqual(self.client.get('/', HTTP_ACCEPT_LANGUAGE='fr')['X-Render'],
                            second['X-Render'])

    def test_pending_outer_session_cookies_bypass(self):
        with override_settings(SESSION_SAVE_EVERY_REQUEST=True):
            first = self.client.get('/')
            self.assertNotEqual(self.client.get('/')['X-Render'], first['X-Render'])
        with override_settings(CSRF_USE_SESSIONS=True):
            first = self.client.get('/')
            self.assertNotEqual(self.client.get('/')['X-Render'], first['X-Render'])

    def test_template_csrf_refresh_hits_and_cached_token_passes_real_csrf_check(self):
        global render_page
        template = Engine().from_string('<form method="post">{% csrf_token %}</form>')
        render_page = lambda request: HttpResponse(template.render(RequestContext(request)))
        self.client.handler.enforce_csrf_checks = True
        first = self.client.get('/')
        self.assertEqual(first.cookies['csrftoken'].value, 'a' * 32)
        second = self.client.get('/')
        self.assertEqual(second.content, first.content)
        self.assertEqual(second['X-Render'], first['X-Render'])
        self.assertEqual(calls, 1)
        self.assertNotIn('csrftoken', second.cookies)
        token = re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"', second.content)[1].decode()
        other = self.new_client()
        self.assertNotEqual(other.get('/')['X-Render'], second['X-Render'])
        self.assertEqual(self.client.post('/unknown/', {'csrfmiddlewaretoken': token}).status_code, 200)
        self.client.cookies['csrftoken'] = 'b' * 32
        self.assertEqual(self.client.post('/unknown/', {'csrfmiddlewaretoken': token}).status_code, 403)
        changed = self.client.get('/')
        self.assertNotEqual(changed.content, second.content)
        self.assertEqual(self.client.get('/')['X-Render'], changed['X-Render'])

    def test_template_csrf_rotation_never_stores_under_original_cookie(self):
        global render_page
        template = Engine().from_string('{% csrf_token %}')
        def rotated_template(request):
            rotate_token(request)
            return HttpResponse(template.render(RequestContext(request)))
        render_page = rotated_template
        first = self.client.get('/')
        self.assertNotEqual(first.cookies['csrftoken'].value, 'a' * 32)
        self.client.cookies['csrftoken'] = 'a' * 32
        second = self.client.get('/')
        self.assertNotEqual(second['X-Render'], first['X-Render'])
        self.assertNotEqual(second.content, first.content)

    def test_template_csrf_creation_or_invalid_cookie_is_not_stored(self):
        global render_page
        template = Engine().from_string('{% csrf_token %}')
        render_page = lambda request: HttpResponse(template.render(RequestContext(request)))
        for incoming in (None, 'invalid'):
            with self.subTest(cookie=incoming):
                client = self.new_client()
                if incoming is None:
                    del client.cookies['csrftoken']
                else:
                    client.cookies['csrftoken'] = incoming
                first = client.get('/')
                second = client.get('/')
                self.assertNotEqual(second['X-Render'], first['X-Render'])
                self.assertEqual(client.get('/')['X-Render'], second['X-Render'])


if __name__ == '__main__':
    main()
