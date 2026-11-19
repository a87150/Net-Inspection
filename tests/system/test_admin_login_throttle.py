"""admin 登录必须和 /login/ 一样计入失败次数。"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(
    NET_LOGIN_MAX_FAILURES=3, NET_LOGIN_IP_MAX_FAILURES=20,
    NET_LOGIN_WINDOW_SECONDS=900, NET_LOGIN_LOCK_SECONDS=900,
)
class AdminLoginThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = get_user_model().objects.create_superuser(
            username='admin-throttle', email='a@example.com', password='right-password',
        )
        self.url = reverse('admin:login')

    def attempt(self, password, **kwargs):
        # 用固定的测试来源地址：默认 127.0.0.1 会和整套测试里别的登录失败共用一个
        # 计数桶，整个套件跑下来能达到来源上限，把这条用例锁在门外。
        kwargs.setdefault('REMOTE_ADDR', '203.0.113.77')
        return self.client.post(
            self.url, {'username': self.user.username, 'password': password}, **kwargs,
        )

    def test_repeated_failures_lock_the_admin_login(self):
        for _ in range(3):
            self.attempt('wrong-password')

        # 锁定期内即使密码正确也不放行，否则爆破者只要猜中一次就绕过计数。
        response = self.attempt('right-password')

        self.assertEqual(response.status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_successful_login_clears_the_counter(self):
        self.attempt('wrong-password')
        self.attempt('right-password')
        self.assertIn('_auth_user_id', self.client.session)

        self.client.logout()
        for _ in range(3):
            self.attempt('wrong-password')
        response = self.attempt('right-password')
        self.assertIn('_auth_user_id', self.client.session)
        self.assertEqual(response.status_code, 302)

    def test_lockout_message_is_shown(self):
        for _ in range(3):
            self.attempt('wrong-password')
        response = self.attempt('right-password', follow=True)

        self.assertContains(response, '登录失败次数过多')
