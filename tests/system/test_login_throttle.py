from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from index.common import login_throttle


@override_settings(NET_LOGIN_MAX_FAILURES=3, NET_LOGIN_WINDOW_SECONDS=900,
                   NET_LOGIN_LOCK_SECONDS=900)
class LoginThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username='throttle-admin', password='correct-horse', is_staff=True, is_superuser=True)
        self.url = '/login/'

    def _post(self, username, password='wrong-password'):
        return self.client.post(self.url, {'username': username, 'password': password})

    def test_repeated_failures_lock_the_account_and_a_valid_password_is_refused(self):
        for _ in range(3):
            self._post(self.user.username)
        response = self._post(self.user.username, 'correct-horse')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/login/')
        self.assertFalse('_auth_user_id' in self.client.session)

    def test_a_successful_login_clears_the_counter(self):
        self._post(self.user.username)
        self.assertEqual(self._post(self.user.username, 'correct-horse').status_code, 302)
        self.assertIn('_auth_user_id', self.client.session)
        # A later burst starts from zero again instead of resuming at the old count.
        for _ in range(2):
            self.assertEqual(self._post(self.user.username).status_code, 200)
        self.assertEqual(self._post(self.user.username, 'correct-horse').status_code, 302)

    def test_limit_zero_disables_throttling_entirely(self):
        with override_settings(NET_LOGIN_MAX_FAILURES=0):
            for _ in range(6):
                self._post(self.user.username)
            self.assertEqual(self._post(self.user.username, 'correct-horse').status_code, 302)

    def test_a_cache_outage_fails_open(self):
        class Broken:
            def get(self, *args, **kwargs):
                raise RuntimeError('cache down')

            def set(self, *args, **kwargs):
                raise RuntimeError('cache down')

            def incr(self, *args, **kwargs):
                raise RuntimeError('cache down')

        with override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}}):
            for _ in range(5):
                self.assertEqual(self._post(self.user.username).status_code, 200)
            self.assertEqual(self._post(self.user.username, 'correct-horse').status_code, 302)

    def test_locking_one_account_leaves_another_one_usable(self):
        for _ in range(3):
            self._post('somebody-else')
        self.assertTrue(login_throttle.is_locked('somebody-else', '10.0.0.9'))
        self.assertFalse(login_throttle.is_locked(self.user.username, '10.0.0.9'))
        self.assertEqual(self._post(self.user.username, 'correct-horse').status_code, 302)

    def test_the_source_limit_is_looser_than_the_account_limit(self):
        # One NAT address serves a whole office, so account lockout must not spread
        # to every colleague behind the same address.
        for _ in range(3):
            self._post('somebody-else')
        self.assertTrue(login_throttle.is_locked('somebody-else', '127.0.0.1'))
        self.assertFalse(login_throttle.is_locked(self.user.username, '127.0.0.1'))
        self.assertEqual(self._post(self.user.username, 'correct-horse').status_code, 302)

    def test_repeated_failures_from_one_source_eventually_lock_it(self):
        with override_settings(NET_LOGIN_IP_MAX_FAILURES=4):
            for index in range(4):
                self._post('someone-%d' % index)
            self.assertTrue(login_throttle.is_locked('a-different-account', '127.0.0.1'))