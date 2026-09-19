from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from net.models import PCUploadConfig


class PCUploadConfigurationRouteTests(TestCase):
    def test_upload_configuration_save_route_is_available(self):
        self.assertEqual(
            reverse('pc_upload_config_save'),
            '/computers/upload-config/save/',
        )

    def test_first_save_generates_a_token_when_left_blank(self):
        user = get_user_model().objects.create_user('pc-api-admin', is_staff=True)
        self.client.force_login(user)
        response = self.client.post(reverse('pc_upload_config_save'), {
            'endpoint_url': 'https://logs.example.test/api/pc/logs/',
            'is_enabled': 'on', 'log_retention': 'daily_latest', 'token': '',
        })
        self.assertEqual(response.status_code, 302)
        self.assertGreaterEqual(len(PCUploadConfig.load().get_token()), 32)
