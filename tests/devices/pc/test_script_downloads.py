"""HTTP contracts for authenticated PC collection script downloads."""

import importlib
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from net.models import ComputerAnalysisProfile
from net import settings as project_settings


class PcScriptDownloadTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='script-downloader', password='safe-test-password',
        )
        self.profile = ComputerAnalysisProfile.objects.create(
            name='PC collection',
            scan_directories=['C:/inspection-logs'],
            analysis_items=['activation'],
        )

    def download(self, platform='windows', **request_headers):
        self.client.force_login(self.user)
        return self.client.get(
            reverse('pc_script_download', args=[self.profile.pk, platform]),
            **request_headers,
        )

    def test_download_uses_explicit_public_base_url(self):
        """Ignoring the configured public origin sends installed scripts to the wrong host."""
        with override_settings(NET_PUBLIC_BASE_URL='https://monitor.example'):
            response = self.download()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Disposition'],
            'attachment; filename="GetInfo_Upload.ps1"',
        )
        self.assertEqual(response['Content-Type'], 'text/plain; charset=utf-8')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertTrue(response.content.startswith(b'\xef\xbb\xbf'))
        self.assertIn(
            b'https://monitor.example/api/computer_inspection/', response.content,
        )

    def test_download_derives_origin_from_request_when_public_base_url_is_unset(self):
        """A deployment without an override must generate an upload URL for this request host."""
        with override_settings(NET_PUBLIC_BASE_URL=''):
            response = self.download()

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b'http://testserver/api/computer_inspection/', response.content,
        )

    def test_download_uses_django_trusted_proxy_origin_when_enabled(self):
        """Ignoring Django's trusted proxy settings would install scripts with the internal origin."""
        with override_settings(
            NET_PUBLIC_BASE_URL='',
            SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
            USE_X_FORWARDED_HOST=True,
            ALLOWED_HOSTS=['testserver', 'scripts.example'],
        ):
            response = self.download(
                HTTP_X_FORWARDED_PROTO='https',
                HTTP_X_FORWARDED_HOST='scripts.example',
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b'https://scripts.example/api/computer_inspection/', response.content,
        )

    def test_download_rejects_a_disabled_saved_profile(self):
        """Downloading an inactive profile would produce a script that every upload rejects."""
        self.profile.is_enabled = False
        self.profile.save(update_fields=['is_enabled'])

        response = self.download('macos')

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '请先启用分析配置', status_code=400)

    def test_computer_pages_hide_downloads_when_only_profile_is_disabled(self):
        """Showing an executable download for an inactive profile would advertise a broken flow."""
        self.profile.is_enabled = False
        self.profile.save(update_fields=['is_enabled'])
        self.client.force_login(self.user)

        for url in (reverse('asset_list', args=['computers']), reverse('computer_analysis_list')):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertNotContains(response, '下载 Windows 采集脚本')
                self.assertNotContains(response, '下载 macOS 采集脚本')

    def test_download_returns_404_for_unknown_profile_or_platform(self):
        """Treating lookup or platform failures as server errors would expose a broken download flow."""
        self.client.force_login(self.user)

        missing_profile = self.client.get(
            reverse('pc_script_download', args=['00000000-0000-0000-0000-000000000000', 'windows']),
        )
        invalid_platform = self.client.get(
            reverse('pc_script_download', args=[self.profile.pk, 'linux']),
        )

        self.assertEqual(missing_profile.status_code, 404)
        self.assertEqual(invalid_platform.status_code, 404)

    def test_download_returns_400_for_invalid_profile_configuration(self):
        """Returning a script for an invalid profile would defer a configuration error to every endpoint device."""
        self.profile.scan_directories = []
        self.profile.save(update_fields=['scan_directories'])

        response = self.download()

        self.assertEqual(response.status_code, 400)

    def test_download_returns_400_for_invalid_public_url(self):
        """Accepting a non-HTTP public URL would install scripts with an unusable upload destination."""
        with override_settings(NET_PUBLIC_BASE_URL='ftp://monitor.example'):
            response = self.download()

        self.assertEqual(response.status_code, 400)

    def test_download_requires_login(self):
        """An anonymous visitor must not be able to download a profile-bound collection script."""
        response = self.client.get(
            reverse('pc_script_download', args=[self.profile.pk, 'windows']),
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn('next=', response['Location'])

    def test_computer_pages_offer_platform_downloads_and_configuration_guidance(self):
        """Removing download actions would leave operators no supported way to install collection scripts."""
        for url in (
            reverse('asset_list', args=['computers']),
            reverse('computer_analysis_list'),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, '下载 Windows 采集脚本')
                self.assertContains(response, '下载 macOS 采集脚本')
                self.assertContains(
                    response,
                    reverse('pc_script_download', args=[self.profile.pk, 'windows']),
                )

    def test_computer_pages_link_to_analysis_configuration_when_no_scan_directory_exists(self):
        """A missing directory must be actionable instead of presenting a download that will fail."""
        self.profile.scan_directories = []
        self.profile.save(update_fields=['scan_directories'])

        response = self.client.get(reverse('asset_list', args=['computers']))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '请先配置扫描目录')
        self.assertContains(
            response,
            f'{reverse("computer_analysis_list")}?task_modal=profile',
        )


class ProxyHeaderSettingsTests(SimpleTestCase):
    def configured_proxy_settings(self, value):
        with patch.dict(os.environ, {'NET_TRUST_PROXY_HEADERS': value}):
            reloaded = importlib.reload(project_settings)
            result = (
                reloaded.NET_TRUST_PROXY_HEADERS,
                getattr(reloaded, 'SECURE_PROXY_SSL_HEADER', None),
                getattr(reloaded, 'USE_X_FORWARDED_HOST', False),
            )
        importlib.reload(project_settings)
        return result

    def test_proxy_headers_are_disabled_by_default(self):
        """Trusting forwarded headers without an explicit deployment opt-in would allow spoofed origins."""
        self.assertEqual(
            self.configured_proxy_settings('false'),
            (False, None, False),
        )

    def test_proxy_headers_enable_django_https_and_host_support(self):
        """Dropping either Django setting would keep reverse-proxied script origins internal."""
        self.assertEqual(
            self.configured_proxy_settings('true'),
            (True, ('HTTP_X_FORWARDED_PROTO', 'https'), True),
        )
