from html.parser import HTMLParser
import re
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from net.models import ComputerAnalysisProfile
from .test_source_models import valid_smb_source


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
            self.forms[self.form] = {
                'action': attrs.get('action'),
                'enctype': attrs.get('enctype'),
                'names': set(),
            }
        elif tag in ('input', 'select', 'textarea') and self.form and attrs.get('name'):
            self.forms[self.form]['names'].add(attrs['name'])
        elif tag == 'button':
            self.buttons.append(attrs)

    def handle_endtag(self, tag):
        if tag == 'form':
            self.form = None


class PCConfigLayoutTests(TestCase):
    def test_rendered_profile_identity_updates_same_configuration_repeatedly(self):
        profile = ComputerAnalysisProfile.objects.get(name='配置样式')
        original_count = ComputerAnalysisProfile.objects.count()
        for workers in (5, 6):
            page = self.client.get(reverse('computer_analysis_list'), {'task_profile': str(profile.pk)})
            html = page.content.decode().split('id="pc-analysis-config-form"', 1)[1].split('</form>', 1)[0]
            identity = re.search(r'name="profile_id"[^>]*value="([^"]+)"', html).group(1)
            response = self.client.post(reverse('computer_analysis_profile_configure'), {
                'profile_id': identity, 'name': profile.name,
                'analysis_items': ['cpu_health'], 'concurrent_workers': workers,
                'next': reverse('computer_analysis_list'),
            })
            self.assertEqual(response.status_code, 302)
            profile.refresh_from_db()
            self.assertEqual(profile.concurrent_workers, workers)
            self.assertEqual(profile.analysis_items, ['cpu_health'])
            self.assertEqual(ComputerAnalysisProfile.objects.count(), original_count)

    def setUp(self):
        valid_smb_source()
        ComputerAnalysisProfile.objects.create(name='配置样式', analysis_items=['cpu_health', 'group_policy'])
        self.user = get_user_model().objects.create_user('layout-admin', is_staff=True)
        self.client.force_login(self.user)

    def test_footer_saves_complete_separate_forms(self):
        response = self.client.get(reverse('computer_analysis_list'))
        parsed = ConfigMarkup(response.content.decode())
        self.assertFalse(parsed.nested)
        self.assertIn('pc-source-config-form', parsed.forms)
        self.assertIn('pc-analysis-config-form', parsed.forms)
        source = parsed.forms['pc-source-config-form']
        self.assertEqual(source['action'], reverse('pc_log_source_save'))
        self.assertTrue({'host', 'password', 'source_type', 'terminal_windows_path',
                         'terminal_macos_path', 'file_time_mode', 'recent_days',
                         'range_start_date', 'range_end_date', 'recursive', 'ftp_use_tls'} <= source['names'])
        analysis = parsed.forms['pc-analysis-config-form']
        self.assertEqual(analysis['action'], reverse('computer_analysis_profile_configure'))
        self.assertEqual(analysis['enctype'], 'multipart/form-data')
        self.assertTrue({'profile_id', 'analysis_items', 'site_ip_prefixes',
                         'cpu_temperature_max_celsius', 'schedule_kind',
                         'schedule_enabled', 'interval_value', 'daily_time',
                         'software_policy_file'} <= analysis['names'])
        self.assertNotIn('software_policy_path', analysis['names'])
        self.assertContains(response, reverse('pc_software_policy_template_download'))
        self.assertContains(response, '下载演示策略模板')
        self.assertTrue({'pc-source-config-form', 'pc-analysis-config-form'} <= {
            b.get('form') for b in parsed.buttons if b.get('type') == 'submit'})
        self.assertContains(response, 'modal-dialog-scrollable modal-shell')

    def test_non_admin_can_read_analysis_without_configuration_controls(self):
        self.user.is_staff = False
        self.user.save()
        response = self.client.get(reverse('computer_analysis_list'))
        parsed = ConfigMarkup(response.content.decode())
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('pc-analysis-config-form', parsed.forms)
        self.assertNotContains(response, 'form="pc-analysis-config-form"')
        self.assertNotIn('pc-source-config-form', parsed.forms)
        self.assertNotContains(response, 'form="pc-source-config-form"')
