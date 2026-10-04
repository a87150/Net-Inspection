from django.test import TestCase
from django.urls import reverse

from net.models import Network_Device, Server, WeakCurrentDevice
from tests.auth import login_admin


class WorkspaceButtonStyleTests(TestCase):
    """按钮规则：执行动作=蓝底白字且靠右，仅配置=白底蓝字，导航=白底灰字。"""

    def setUp(self):
        login_admin(self.client)
        Network_Device.objects.create(device_name='核心', ip='192.0.2.1')
        Server.objects.create(name='应用', ip='192.0.2.2')
        WeakCurrentDevice.objects.create(device_name='前门摄像机', ip='192.0.2.3')

    def html(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.content.decode()

    def _opening_tag(self, html, label):
        """The opening <a>/<button> tag that carries this button label."""
        at = html.index('>' + label)      # '>' closing the opening tag
        return html[html.rfind('<', 0, at):at]

    # 会做事（下载/添加/导入/执行）-> 蓝底白字；仅配置 -> 白底蓝字；导航 -> 白底灰字。
    ACTION_LABELS = ('添加设备', '导入设备', '手动执行巡检')
    # 只有服务器有 Windows 采集脚本可下载（workspace_actions.py: action_script_download）。
    SCRIPT_LABEL = '下载 Windows 脚本'
    # 问题等级设置只有 PC 项目有（action_issue_settings）。
    ISSUE_LABEL = '问题等级设置'
    CONFIG_LABELS = ('配置模板', '告警配置')

    def _action_labels(self, kind):
        return ((self.SCRIPT_LABEL,) if kind == 'servers' else ()) + self.ACTION_LABELS

    def _config_labels(self, kind):
        return self.CONFIG_LABELS + ((self.ISSUE_LABEL,) if kind == 'computers' else ())

    def _assert_rule(self, html, where, kind):
        actions = self._action_labels(kind)
        for label in actions:
            tag = self._opening_tag(html, label)
            self.assertIn('btn-primary', tag, where + ' ' + label + ' 应为蓝底白字')
            self.assertNotIn('btn-outline', tag, where + ' ' + label + ' 不该是白底')
        for label in self._config_labels(kind):
            tag = self._opening_tag(html, label)
            self.assertIn('btn-outline-primary', tag, where + ' ' + label + ' 应为白底蓝字')
        # 导航 -> 白底灰字 btn-outline-secondary，且排在最后。
        # 只有记录页有返回按钮，列表页本来就在列表上。
        if '>返回列表<' not in html:
            return
        back = html.index('>返回列表<')
        self.assertIn('btn-outline-secondary', html[back - 160:back], where + ' 返回列表应为白底灰字')
        for label in actions:
            self.assertLess(html.index('>' + label), back, where + ' ' + label + ' 应在返回列表之前')

    def test_config_pages_follow_the_button_rule(self):
        for kind in ('networks', 'servers', 'weakcurrent'):
            self._assert_rule(self.html(reverse('asset_list', args=[kind])), kind + ' 列表页', kind)

    def test_record_pages_follow_the_button_rule(self):
        for kind in ('networks', 'servers', 'weakcurrent'):
            self._assert_rule(self.html(reverse('record_list', args=[kind])), kind + ' 记录页', kind)

    def test_action_buttons_sit_to_the_right_of_config_buttons(self):
        for kind in ('networks', 'servers', 'weakcurrent'):
            html = self.html(reverse('asset_list', args=[kind]))
            # The bar is a flex row, so "right" means "ordered after every config button".
            last_config = max(html.index('>' + label + '<')
                             for label in self._config_labels(kind))
            for label in self._action_labels(kind):
                with self.subTest(kind=kind, label=label):
                    self.assertLess(last_config, html.index('>' + label + '<'),
                                    label + ' 应排在所有配置按钮之后')
            self.assertLess(html.index('>导入设备<'), html.index('>手动执行巡检<'))

