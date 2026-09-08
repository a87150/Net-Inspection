from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse


class AccountNavigationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='navigation-admin',
            is_staff=True,
        )
        self.client.force_login(self.user)

    def test_account_dropdown_shows_username_groups_and_keeps_logout(self):
        self.user.groups.add(Group.objects.create(name='网络运维组'))

        response = self.client.get(reverse('index'))

        self.assertContains(response, 'data-account-menu')
        self.assertContains(response, f'>{self.user.username}<')
        self.assertContains(response, '权限组')
        self.assertContains(response, '网络运维组')
        self.assertContains(response, f'action="{reverse("logout")}"')
        self.assertContains(response, '>退出登录<')

    def test_account_dropdown_explains_when_no_group_is_assigned(self):
        response = self.client.get(reverse('index'))

        self.assertContains(response, '未分配权限组')
