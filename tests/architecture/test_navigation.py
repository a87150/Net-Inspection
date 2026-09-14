from html.parser import HTMLParser

from django.test import TestCase
from django.urls import reverse
from tests.auth import login_admin


class NavigationMenuParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current_menu = None
        self.menu_depth = 0
        self.menus = {}
        self._capture = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'li' and attributes.get('data-nav-menu'):
            self.current_menu = attributes['data-nav-menu']
            self.menu_depth = 1
            self.menus[self.current_menu] = {
                'toggle': None,
                'toggle_attributes': {},
                'links': [],
            }
            return
        if self.current_menu and tag == 'li':
            self.menu_depth += 1
        if not self.current_menu:
            return
        if tag == 'button':
            self._capture = ('toggle', attributes, [])
        elif tag == 'a':
            self._capture = ('link', attributes, [])

    def handle_data(self, data):
        if self._capture:
            self._capture[2].append(data)

    def handle_endtag(self, tag):
        if self._capture and (
            (tag == 'button' and self._capture[0] == 'toggle')
            or (tag == 'a' and self._capture[0] == 'link')
        ):
            kind, attributes, parts = self._capture
            label = ''.join(parts).strip()
            menu = self.menus[self.current_menu]
            if kind == 'toggle':
                menu['toggle'] = label
                menu['toggle_attributes'] = attributes
            else:
                menu['links'].append((label, attributes.get('href')))
            self._capture = None
        if self.current_menu and tag == 'li':
            self.menu_depth -= 1
            if self.menu_depth == 0:
                self.current_menu = None


class NavigationDropdownTests(TestCase):
    def setUp(self):
        login_admin(self.client)

    def test_asset_and_domain_navigation_groups_expose_their_workspaces(self):
        response = self.client.get(reverse('index'))
        parser = NavigationMenuParser()
        parser.feed(response.content.decode(response.charset))

        self.assertEqual(
            {
                key: {
                    'toggle': menu['toggle'],
                    'links': menu['links'],
                }
                for key, menu in parser.menus.items()
            },
            {
                'people': {
                    'toggle': '人员',
                    'links': [
                        ('人员列表', '/assets/people/'),
                        ('人员统计', '/people/statistics/'),
                    ],
                },
                'domain': {
                    'toggle': '域控管理',
                    'links': [
                        ('域控管理', '/settings/domain-controller/'),
                        ('域账号', '/domain/accounts/'),
                        ('域计算机', '/domain/computers/'),
                        ('域分组', '/domain/groups/'),
                    ],
                },
                'computers': {
                    'toggle': 'PC',
                    'links': [
                        ('PC 列表', '/assets/computers/'),
                        ('日志分析记录', '/computers/analyses/'),
                    ],
                },
                'networks': {
                    'toggle': '网络设备',
                    'links': [
                        ('网络设备列表', '/assets/networks/'),
                        ('巡检记录', '/records/networks/'),
                    ],
                },
                'servers': {
                    'toggle': '服务器',
                    'links': [
                        ('服务器列表', '/assets/servers/'),
                        ('巡检记录', '/records/servers/'),
                    ],
                },
                'monitors': {
                    'toggle': '安防设备',
                    'links': [
                        ('安防设备列表', '/assets/monitors/'),
                        ('巡检记录', '/records/monitors/'),
                        ('门禁记录', '/access/records/'),
                    ],
                },
            },
        )

    def test_each_group_toggle_supports_bootstrap_click_and_accessibility_state(self):
        response = self.client.get(reverse('index'))
        parser = NavigationMenuParser()
        parser.feed(response.content.decode(response.charset))

        self.assertEqual(len(parser.menus), 6)
        for key, menu in parser.menus.items():
            with self.subTest(menu=key):
                attributes = menu['toggle_attributes']
                self.assertEqual(attributes.get('type'), 'button')
                self.assertEqual(attributes.get('data-bs-toggle'), 'dropdown')
                self.assertEqual(attributes.get('aria-expanded'), 'false')
                self.assertTrue(attributes.get('aria-controls'))
