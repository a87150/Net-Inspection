from html.parser import HTMLParser
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from net.models import ComputerAnalysisProfile


class ConfigMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.form = None
        self.forms = {}
        self.buttons = []
        self.nested = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.nested |= self.form is not None
            self.form = attrs.get('id', 'unnamed')
            self.forms[self.form] = {'action': attrs.get('action'), 'enctype': attrs.get('enctype'), 'names': set()}
        elif tag in ('input', 'select', 'textarea') and self.form and attrs.get('name'):
            self.forms[self.form]['names'].add(attrs['name'])
        elif tag == 'button':
            self.buttons.append(attrs)

    def handle_endtag(self, tag):
        if tag == 'form':
            self.form = None


class PCConfigLayoutTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('layout-admin', is_staff=True)
        self.client.force_login(self.user)
        ComputerAnalysisProfile.objects.create(name='配置样式', analysis_items=['cpu_health'])

    def test_configuration_modal_has_api_and_analysis_forms(self):
        response = self.client.get(reverse('computer_analysis_list'))
        self.assertContains(response, 'id="pc-upload-config-form"')
        self.assertContains(response, reverse('pc_upload_config_save'))
        self.assertContains(response, 'name="endpoint_url"')
        self.assertContains(response, 'name="log_retention"')
        self.assertContains(response, 'name="analysis_retention"')
        self.assertNotContains(response, '共享文件夹')
        self.assertNotContains(response, '测试连接')

    def test_non_admin_cannot_see_configuration_controls(self):
        self.user.is_staff = False
        self.user.save()
        response = self.client.get(reverse('computer_analysis_list'))
        self.assertNotContains(response, 'pc-upload-config-form')
