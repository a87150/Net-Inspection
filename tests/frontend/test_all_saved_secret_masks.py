import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from index.devices.forms import device_form, preserve_empty_secrets
from net.admin.domain import AlertChannelAdminForm
from net.models import AlertChannel, Network_Device, PeopleSyncSource, SecurityDevice, Server


MASK = '••••••••'


class AllSavedSecretMaskTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='all-secret-mask-admin',
            email='mask-admin@example.test',
            password='unused',
        )
        self.client.force_login(self.user)

    def test_main_device_forms_mask_and_preserve_every_saved_credential(self):
        cases = (
            (
                'networks',
                Network_Device.objects.create(
                    ip='192.0.2.80', device_name='核心交换机',
                    password='ssh-private', snmp_community='community-private',
                    snmp_auth_password='auth-private', snmp_priv_password='priv-private',
                    api_shared_secret='sangfor-api-private',
                ),
                {
                    'password': 'ssh-private',
                    'snmp_community': 'community-private',
                    'snmp_auth_password': 'auth-private',
                    'snmp_priv_password': 'priv-private',
                    'api_shared_secret': 'sangfor-api-private',
                },
                {'ip': '192.0.2.80', 'device_name': '核心交换机'},
            ),
            (
                'servers',
                Server.objects.create(
                    ip='192.0.2.81', name='应用服务器', server_type='linux',
                    password='ssh-private', api_token='api-private',
                ),
                {'password': 'ssh-private', 'api_token': 'api-private'},
                {'ip': '192.0.2.81', 'name': '应用服务器', 'server_type': 'linux'},
            ),
            (
                'monitors',
                SecurityDevice.objects.create(
                    ip='192.0.2.82', device_name='前门门禁',
                    api_password='api-password-private', api_token='api-token-private',
                ),
                {'api_password': 'api-password-private', 'api_token': 'api-token-private'},
                {'ip': '192.0.2.82', 'device_name': '前门门禁'},
            ),
        )
        for kind, instance, secrets, public_data in cases:
            with self.subTest(kind=kind):
                display = device_form(kind, instance=instance)
                html = display.as_p()
                self.assertEqual(html.count(MASK), len(secrets))
                masked_fields = [
                    name for name, field in display.fields.items()
                    if field.widget.attrs.get('data-secret-mask') == 'true'
                ]
                self.assertTrue(set(secrets).issubset(masked_fields))
                for value in secrets.values():
                    self.assertNotIn(value, html)

                submitted = device_form(
                    kind,
                    {**public_data, **{name: MASK for name in secrets}},
                    instance=instance,
                )
                self.assertTrue(submitted.is_valid(), submitted.errors)
                preserve_empty_secrets(submitted)
                submitted.save()
                instance.refresh_from_db()
                for name, value in secrets.items():
                    self.assertEqual(getattr(instance, name), value)

    def test_alert_channel_admin_shows_masks_and_preserves_masked_json_values(self):
        channel = AlertChannel.objects.create(
            name='Admin email', channel_type=AlertChannel.ChannelType.EMAIL,
            settings={
                'smtp_host': 'smtp.example.test', 'smtp_port': 587,
                'use_tls': True, 'use_ssl': False,
                'username': 'mailer', 'password': 'mail-private',
                'from_email': 'alerts@example.test',
                'recipients': ['ops@example.test'],
            },
        )
        display = AlertChannelAdminForm(instance=channel)
        html = display.as_p()
        self.assertIn(MASK, html)
        self.assertNotIn('mail-private', html)

        safe_settings = {
            'smtp_host': 'smtp.example.test', 'smtp_port': 587,
            'use_tls': True, 'use_ssl': False,
            'username': 'mailer', 'password': MASK,
            'from_email': 'alerts@example.test',
            'recipients': ['ops@example.test'],
        }
        submitted = AlertChannelAdminForm(data={
            'name': channel.name,
            'channel_type': channel.channel_type,
            'is_enabled': 'on',
            'settings': json.dumps(safe_settings),
        }, instance=channel)
        self.assertTrue(submitted.is_valid(), submitted.errors)
        submitted.save()
        channel.refresh_from_db()
        self.assertEqual(channel.settings['password'], 'mail-private')

    def test_people_source_admin_displays_one_mask_for_each_saved_credential(self):
        source = PeopleSyncSource.objects.create(
            source_type='feishu', name='飞书通讯录', source_key='admin-mask-feishu',
            credentials={'app_id': 'app-private', 'app_secret': 'secret-private'},
        )

        response = self.client.get(reverse(
            'admin:net_peoplesyncsource_change', args=[source.pk],
        ))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, MASK, count=2)
        self.assertNotContains(response, 'app-private')
        self.assertNotContains(response, 'secret-private')
