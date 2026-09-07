from django.test import TestCase

from index.alerts.forms import FeishuAlertChannelForm
from index.domain.forms import DomainControllerConfigForm
from index.people.forms import PeopleProviderForm
from net.models import AlertChannel, Domain_Controller_Config, PeopleSyncSource


MASK = '••••••••'


class SavedBusinessCredentialMaskTests(TestCase):
    def test_domain_config_renders_a_mask_and_preserves_it_on_submit(self):
        config = Domain_Controller_Config.objects.create(
            name='Primary', host='ldap.example.test', port=636, use_ssl=True,
            base_dn='DC=example,DC=test', bind_username='svc',
            bind_password='domain-private',
        )
        display = DomainControllerConfigForm(instance=config)
        self.assertIn(MASK, display.as_p())
        self.assertNotIn('domain-private', display.as_p())

        form = DomainControllerConfigForm(data={
            'name': 'Primary', 'host': 'ldap.example.test', 'port': 636,
            'use_ssl': 'on', 'base_dn': 'DC=example,DC=test',
            'bind_username': 'svc', 'bind_password': MASK,
            'user_filter': '(&(objectCategory=person)(objectClass=user))',
            'computer_filter': '(objectCategory=computer)',
            'group_filter': '(objectCategory=group)',
        }, instance=config)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.bind_password, 'domain-private')

    def test_alert_channel_renders_masks_and_preserves_them_on_submit(self):
        channel = AlertChannel.objects.create(
            name='Feishu', channel_type='feishu', settings={
                'webhook_url': 'https://example.test/hook?token=private',
                'secret': 'signing-private',
            },
        )
        display = FeishuAlertChannelForm(instance=channel)
        self.assertIn(MASK, display.as_p())
        self.assertNotIn('private', display.as_p())

        form = FeishuAlertChannelForm(data={
            'name': 'Feishu', 'is_enabled': 'on',
            'webhook_url': MASK, 'secret': MASK,
        }, instance=channel)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().settings, channel.settings)

    def test_people_provider_renders_masks_and_preserves_them_on_submit(self):
        source = PeopleSyncSource.objects.create(
            source_type='feishu', name='飞书', source_key='people-provider-feishu',
            credentials={'app_id': 'app-private', 'app_secret': 'secret-private'},
        )
        display = PeopleProviderForm(source=source, provider='feishu')
        self.assertIn(MASK, display.as_p())
        self.assertNotIn('app-private', display.as_p())
        self.assertNotIn('secret-private', display.as_p())

        form = PeopleProviderForm(data={
            'root_department_ids': '', 'is_enabled': 'on',
            'app_id': MASK, 'app_secret': MASK,
        }, source=source, provider='feishu')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().credentials, source.credentials)