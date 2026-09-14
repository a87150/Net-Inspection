from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class AccountNavigationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='navigation-admin',
            is_staff=True,
        )
        self.client.force_login(self.user)

    def test_account_dropdown_shows_username_role_and_keeps_logout(self):
        response = self.client.get(reverse('index'))

        self.assertContains(response, 'data-account-menu')
        self.assertContains(response, f'>{self.user.username}<')
        self.assertContains(response, 'class="app-account-trigger dropdown-toggle')
        self.assertNotContains(response, 'btn-outline-light')
        self.assertContains(response, '>用户组<')
        self.assertContains(response, 'data-account-role="administrator"')
        self.assertContains(response, '>管理员<')
        self.assertContains(response, f'action="{reverse("logout")}"')
        self.assertContains(response, '>退出登录<')

    def test_account_dropdown_labels_each_permission_tier(self):
        users = get_user_model()
        cases = (
            ('navigation-reader', {}, 'regular', '普通用户'),
            ('navigation-staff', {'is_staff': True}, 'administrator', '管理员'),
            ('navigation-super', {'is_superuser': True}, 'superuser', '超级管理员'),
        )

        for username, flags, role, label in cases:
            with self.subTest(role=role):
                user = users.objects.create_user(username=username, **flags)
                self.client.force_login(user)
                response = self.client.get(reverse('index'))

                self.assertContains(response, f'data-account-role="{role}"')
                self.assertContains(response, f'>{label}<')
                self.assertNotContains(response, '未分配权限组')

    def test_account_role_uses_compact_secondary_typography(self):
        response = self.client.get(reverse('index'))
        css = Path(settings.BASE_DIR, 'static/app/css/foundation.css').read_text(encoding='utf-8')

        self.assertContains(response, 'class="dropdown-item-text app-account-role"')
        self.assertIn('.app-account-role', css)
        self.assertIn('font-size: .76rem', css)
