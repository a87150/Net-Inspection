from datetime import date, timedelta
import re
from importlib import import_module
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from net.models import Domain_Account, Domain_Computer, Domain_Group, Domain_Controller_Config, TaskRun
from tests.auth import login_admin, login_reader


class DomainStatisticsTests(TestCase):
    def test_admin_can_save_inactivity_days_without_changing_connection(self):
        config = Domain_Controller_Config.objects.create(host='dc.example.test', bind_username='admin', bind_password='saved-secret')
        Domain_Account.objects.create(login_name='old', last_login_date=timezone.localdate() - timedelta(days=31))
        Domain_Computer.objects.create(computer_name='recent', last_login_date=timezone.localdate() - timedelta(days=30))
        login_admin(self.client)
        response = self.client.post(reverse('domain_controller_settings'), {'action': 'inactivity', 'inactive_days': '30'}, follow=True)
        config.refresh_from_db()
        self.assertEqual(getattr(config, 'inactive_days', None), 30)
        self.assertEqual(config.host, 'dc.example.test')
        self.assertEqual(config.bind_password, 'saved-secret')
        self.assertEqual(response.context['account_stale'], 1)
        self.assertEqual(response.context['computer_stale'], 0)
        self.assertContains(response, '超过 <span data-domain-inactive-days>30</span> 天未登录', count=2)
        self.assertContains(response, '未登录天数设置')
        response = self.client.get(reverse('domain_controller_settings'))
        self.assertEqual(response.context['inactive_days'], 30)

    def test_inactivity_settings_reject_invalid_values_and_readers(self):
        config = Domain_Controller_Config.objects.create()
        login_admin(self.client)
        for value in ('0', '-1', 'abc', '', '36501'):
            response = self.client.post(reverse('domain_controller_settings'), {'action': 'inactivity', 'inactive_days': value})
            config.refresh_from_db()
            self.assertEqual(getattr(config, 'inactive_days', None), 60)
            self.assertTrue(response.context['inactivity_form'].errors)
        login_reader(self.client)
        response = self.client.post(reverse('domain_controller_settings'), {'action': 'inactivity', 'inactive_days': 15})
        self.assertEqual(response.status_code, 403)

    def statistics(self, **kwargs):
        # A missing feature is reported as a test failure during the initial red run.
        try:
            module = import_module('net.domain.statistics')
        except ModuleNotFoundError as exc:
            self.fail(f'Statistics service is missing: {exc}')
        return module.get_domain_statistics(**kwargs)

    def test_enabled_stale_dates_exclude_boundary_disabled_and_missing(self):
        today = date(2026, 9, 8)
        for model, name in ((Domain_Account, 'login_name'), (Domain_Computer, 'computer_name')):
            for index, (active, days) in enumerate(((True, 61), (True, 60), (True, 59), (False, 90), (True, None), (False, None), (True, -1))):
                model.objects.create(**{name: f'object-{index}'}, is_active=active,
                                     last_login_date=today - timedelta(days=days) if days is not None else None)
        stats = self.statistics(today=today)
        for prefix in ('account', 'computer'):
            self.assertEqual(stats[f'{prefix}_total'], 7)
            self.assertEqual(stats[f'{prefix}_active'], 5)
            self.assertEqual(stats[f'{prefix}_inactive'], 2)
            self.assertEqual(stats[f'{prefix}_stale'], 1)
            self.assertEqual(stats[f'{prefix}_missing_login'], 1)

    def test_empty_database(self):
        stats = self.statistics()
        for key in ('account_total', 'computer_total', 'group_total', 'account_stale',
                    'computer_stale', 'account_missing_login', 'computer_missing_login'):
            self.assertEqual(stats[key], 0)
        self.assertIsNone(stats['latest_successful_sync'])

    def test_statistics_only_query_retained_aggregates(self):
        Domain_Account.objects.create(login_name='one', ou='OU=Shared')
        Domain_Account.objects.create(login_name='two', ou='  ')
        Domain_Group.objects.create(group_name='group', distinguished_name='CN=group', ou='OU=Groups')
        Domain_Group.objects.create(group_name='mail', distinguished_name='CN=mail', ou='', group_category='distribution')
        for index, os in enumerate(('Windows', 'Windows', None, '', '  ', 'Linux', 'macOS', 'A', 'B', 'C')):
            Domain_Computer.objects.create(computer_name=f'pc-{index}', os=os,
                                           ou='OU=Shared' if index % 2 else None)
        with CaptureQueriesContext(connection) as queries:
            stats = self.statistics()
        self.assertEqual(stats['group_total'], 2)
        self.assertEqual(stats['security_group_total'], 1)
        self.assertEqual(stats['distribution_group_total'], 1)
        self.assertEqual(stats['computer_total'], 10)
        self.assertEqual(len(queries), 4)
        for query in queries:
            self.assertNotIn('snapshot', query['sql'].lower())
            self.assertRegex(query['sql'].upper(), r'COUNT\(|MAX\(')

    def test_latest_sync_uses_successful_domain_completion_only(self):
        now = timezone.now()
        for task_type, status, finished in (
            ('domain_sync', 'success', now - timedelta(days=2)),
            ('domain_sync', 'success', now - timedelta(days=3)),
            ('domain_sync', 'failed', now),
            ('domain_sync', 'partial', now),
            ('domain_operation', 'success', now),
        ):
            failed = int(status in ('failed', 'partial'))
            TaskRun.objects.create(task_type=task_type, status=status, finished_at=finished,
                                   progress=100, total_targets=1, completed_targets=1,
                                   successful_targets=1-failed, failed_targets=failed)
        self.assertEqual(self.statistics()['latest_successful_sync'], now - timedelta(days=2))

    def test_home_renders_statistics_and_keeps_existing_access(self):
        Domain_Account.objects.create(login_name='stale', last_login_date=date(2000, 1, 1))
        login_admin(self.client)
        with patch('net.domain.sync._connect', side_effect=AssertionError('No real AD access')):
            response = self.client.get(reverse('domain_controller_settings'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['account_total'], 1)
        self.assertEqual(response.context.get('account_stale'), 1)
        self.assertContains(response, '本地同步数据')
        self.assertContains(response, 'lastLogonTimestamp')
        self.assertContains(response, '暂无成功同步记录')
        self.assertContains(response, '缺少登录记录')
        login_reader(self.client)
        self.assertEqual(self.client.get(reverse('domain_controller_settings')).status_code, 403)

    def test_stale_counts_are_in_object_cards_and_sync_metadata_is_in_sync_card(self):
        Domain_Account.objects.create(login_name='old-one', last_login_date=date(2000, 1, 1))
        Domain_Account.objects.create(login_name='old-two', last_login_date=date(2000, 1, 1))
        Domain_Computer.objects.create(computer_name='old-pc', last_login_date=date(2000, 1, 1))
        login_admin(self.client)
        response = self.client.get(reverse('domain_controller_settings'))
        html = response.content.decode()
        for route, count in (('domain_account_list', 2), ('domain_computer_list', 1)):
            cards = re.findall(r'<a\b[^>]*href="' + re.escape(reverse(route)) + r'"[^>]*>(.*?)</a>', html, re.S)
            card = next((item for item in cards if 'metric-label' in item), '')
            card_text = re.sub(r'<[^>]+>', '', card)
            self.assertIn('超过 60 天未登录 ' + str(count), card_text)
        sections = re.findall(r'<section\b[^>]*>(.*?)</section>', html, re.S)
        sync_card = next((item for item in sections if '同步域控对象' in item), '')
        self.assertIn('统计来自本地同步数据', sync_card)
        self.assertIn('最近成功同步', sync_card)
        self.assertNotContains(response, '对象所在目录路径')
        self.assertNotContains(response, '计算机系统分布')
        self.assertNotContains(response, '本地同步数据概览')
