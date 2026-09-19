"""Admin registration and secret-safety contracts for Task 5."""

import json

from django.contrib.auth import get_user_model
from django.contrib import admin
from django.test import TestCase
from django.urls import reverse

from net.models.domain import DomainOperationSecret
from net.models import (
    AlertChannel,
    AlertDelivery,
    AlertEvent,
    AlertPolicy,
    AlertState,
    AlertTestSend,
    Computer,
    ComputerAnalysis,
    ComputerAnalysisProfile,
    ComputerLogFile,
    DomainOperation,
    Domain_Account,
    Domain_Computer,
    Domain_Group,
    Domain_Controller_Config,
    Error_Computer,
    Error_Monitor,
    Error_Network_Device,
    Error_Server,
    SecurityDevice,
    Monitor_Inspection,
    Network_Device,
    Network_Device_Inspection,
    People,
    PeopleSyncSource,
    Schedule,
    Server,
    Server_Inspection,
    InspectionProfile,
    TaskRun,
    TaskTargetRun,
)


REQUIRED_ADMIN_MODELS = (
    People,
    Domain_Account,
    Domain_Computer,
    Domain_Group,
    Computer,
    Network_Device,
    Server,
    SecurityDevice,
    Domain_Controller_Config,
    DomainOperation,
    ComputerLogFile,
    ComputerAnalysis,
    Network_Device_Inspection,
    Server_Inspection,
    Monitor_Inspection,
    Error_Computer,
    Error_Network_Device,
    Error_Server,
    Error_Monitor,
    InspectionProfile,
    ComputerAnalysisProfile,
    Schedule,
    TaskRun,
    TaskTargetRun,
    AlertChannel,
    AlertPolicy,
    AlertState,
    AlertEvent,
    AlertDelivery,
    AlertTestSend,
    PeopleSyncSource,
)


class AdminRegistryTests(TestCase):
    def test_required_public_models_are_registered(self):
        """Removing a public model from Django admin must fail visibly."""
        for model in REQUIRED_ADMIN_MODELS:
            with self.subTest(model=model.__name__):
                self.assertIn(model, admin.site._registry)

    def test_domain_operation_secret_is_never_registered(self):
        """The encrypted one-time password payload has no admin surface."""
        self.assertNotIn(DomainOperationSecret, admin.site._registry)

    def test_tasks_and_audits_keep_generated_fields_read_only(self):
        """Changing snapshots, task results, or audit history in admin is a bug."""
        task_admin = admin.site._registry[TaskRun]
        target_admin = admin.site._registry[TaskTargetRun]
        operation_admin = admin.site._registry[DomainOperation]
        event_admin = admin.site._registry[AlertEvent]

        self.assertTrue({
            'profile_snapshot', 'parameters_snapshot', 'selected_items_snapshot',
            'target_scope_snapshot', 'scope_key', 'active_scope_key',
            'total_targets', 'completed_targets', 'successful_targets',
            'failed_targets', 'created_at', 'updated_at',
        } <= set(task_admin.readonly_fields))
        self.assertTrue({
            'target_snapshot', 'execution_scope_key', 'result_type', 'result_id',
            'result_snapshot', 'error_message', 'created_at', 'updated_at',
        } <= set(target_admin.readonly_fields))
        self.assertTrue({
            'requested_by', 'status', 'target_count', 'parameter_summary', 'task',
            'created_at', 'updated_at',
        } <= set(operation_admin.readonly_fields))
        self.assertTrue({
            'task', 'target_run', 'policy', 'profile_type', 'profile_id',
            'target_type', 'target_id', 'findings', 'occurred_at', 'created_at',
            'updated_at',
        } <= set(event_admin.readonly_fields))

    def test_device_credentials_are_not_in_admin_lists_or_searches(self):
        """A later display/search-field expansion must not expose credentials."""
        credential_fields = {
            Network_Device: {
                'username', 'password', 'snmp_community',
                'snmp_auth_password', 'snmp_priv_password',
            },
            Server: {'username', 'password', 'api_token'},
            SecurityDevice: {'api_username', 'api_password', 'api_token'},
        }
        for model, forbidden in credential_fields.items():
            with self.subTest(model=model.__name__):
                model_admin = admin.site._registry[model]
                visible = set(model_admin.list_display) | set(model_admin.search_fields)
                self.assertFalse(forbidden & visible)

    def test_domain_bind_password_is_masked_and_preserved_by_admin_form(self):
        """The fixed marker must retain the secret without exposing its true value."""
        config = Domain_Controller_Config.objects.create(
            name='Primary domain', host='ldap.example.test', port=636,
            use_ssl=True, base_dn='DC=example,DC=test', bind_username='svc-domain',
            bind_password='stored-secret',
        )
        config_admin = admin.site._registry[Domain_Controller_Config]
        form_class = config_admin.get_form(request=None, obj=config)
        form = form_class(instance=config)

        self.assertEqual(form.initial['bind_password'], '••••••••')
        self.assertFalse(form.fields['bind_password'].required)
        self.assertTrue(form.fields['bind_password'].widget.render_value)

        bound = form_class(data={
            'name': 'Renamed domain', 'host': 'ldap.example.test', 'port': 636,
            'use_ssl': 'on', 'base_dn': 'DC=example,DC=test',
            'bind_username': 'svc-domain', 'bind_password': '••••••••',
            'user_filter': '(&(objectCategory=person)(objectClass=user))',
            'computer_filter': '(objectCategory=computer)',
            'group_filter': '(objectCategory=group)',
            'inactive_days': 60,
        }, instance=config)
        self.assertTrue(bound.is_valid(), bound.errors)
        saved = bound.save()
        self.assertEqual(saved.bind_password, 'stored-secret')


class AdminSecretFormTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='admin-secret-test', email='admin@example.test', password='unused',
        )
        self.client.force_login(self.user)

    def change_url(self, instance):
        meta = instance._meta
        return reverse(f'admin:{meta.app_label}_{meta.model_name}_change', args=(instance.pk,))

    def test_device_change_pages_hide_secrets_and_preserve_or_replace_them(self):
        cases = (
            (
                Network_Device.objects.create(
                    device_name='admin-network', ip='192.0.2.41', username='network-user',
                    password='network-password-private',
                    connection_type='hybrid', snmp_version='v3',
                    snmp_community='network-community-private',
                    snmp_security_level='authPriv', snmp_username='snmp-user',
                    snmp_auth_protocol='sha256',
                    snmp_auth_password='network-auth-private',
                    snmp_priv_protocol='aes128',
                    snmp_priv_password='network-priv-private',
                ),
                {
                    'password': 'network-password-private',
                    'snmp_community': 'network-community-private',
                    'snmp_auth_password': 'network-auth-private',
                    'snmp_priv_password': 'network-priv-private',
                },
                {
                    'device_name': 'admin-network', 'ip': '192.0.2.41', 'device_type': '',
                    'model': '', 'vendor': '', 'connection_type': 'hybrid', 'port': '22',
                    'username': 'network-user', 'password': '', 'cpu_model': '',
                    'snmp_version': 'v3', 'snmp_port': '161',
                    'snmp_community': '', 'snmp_security_level': 'authPriv',
                    'snmp_username': 'snmp-user', 'snmp_auth_protocol': 'sha256',
                    'snmp_auth_password': '', 'snmp_priv_protocol': 'aes128',
                    'snmp_priv_password': '', 'snmp_context_name': '',
                    'snmp_retries': '1',
                    'memory_total_gb': '', 'disk_total_gb': '', 'port_count': '',
                    'vlan_count': '',
                },
            ),
            (
                Server.objects.create(
                    name='admin-server', ip='192.0.2.42', username='server-user',
                    password='server-password-private', api_token='server-token-private',
                ),
                {'password': 'server-password-private', 'api_token': 'server-token-private'},
                {
                    'name': 'admin-server', 'ip': '192.0.2.42', 'server_type': 'linux',
                    'os': '', 'port': '22', 'username': 'server-user', 'password': '',
                    'api_url': '', 'api_token': '', 'verify_ssl': 'on', 'cpu_model': '',
                    'memory_total_gb': '', 'disk_total_gb': '',
                },
            ),
            (
                SecurityDevice.objects.create(
                    device_name='admin-monitor', ip='192.0.2.43', api_username='monitor-user',
                    api_password='monitor-password-private', api_token='monitor-token-private',
                ),
                {'api_password': 'monitor-password-private', 'api_token': 'monitor-token-private'},
                {
                    'device_name': 'admin-monitor', 'ip': '192.0.2.43', 'device_type': '',
                    'model': '', 'vendor': '', 'api_url': '', 'api_username': 'monitor-user',
                    'api_password': '', 'api_token': '', 'verify_ssl': 'on', 'cpu_model': '',
                    'memory_total_gb': '', 'disk_total_gb': '',
                },
            ),
        )
        for instance, secrets, blank_data in cases:
            with self.subTest(model=type(instance).__name__):
                response = self.client.get(self.change_url(instance))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, '••••••••')
                self.assertContains(response, 'data-secret-mask="true"')
                for value in secrets.values():
                    self.assertNotContains(response, value)

                masked_data = {
                    **blank_data,
                    **{field: '••••••••' for field in secrets},
                }
                response = self.client.post(self.change_url(instance), masked_data)
                self.assertEqual(response.status_code, 302, response.content.decode())
                instance.refresh_from_db()
                for field, value in secrets.items():
                    self.assertEqual(getattr(instance, field), value)

                replacements = {field: f'replaced-{field}' for field in secrets}
                response = self.client.post(self.change_url(instance), {**blank_data, **replacements})
                self.assertEqual(response.status_code, 302, response.content.decode())
                instance.refresh_from_db()
                for field, value in replacements.items():
                    self.assertEqual(getattr(instance, field), value)

    def test_alert_channel_change_page_hides_settings_secrets_and_merges_updates(self):
        secret = 'alert-password-private'
        channel = AlertChannel.objects.create(
            name='admin-email', channel_type=AlertChannel.ChannelType.EMAIL,
            settings={
                'smtp_host': 'smtp.example.test', 'smtp_port': 587, 'use_tls': True,
                'use_ssl': False, 'username': 'mail-user', 'password': secret,
                'from_email': 'alerts@example.test',
                'recipients': ['ops@example.test', 'removed@example.test'],
            },
        )
        response = self.client.get(self.change_url(channel))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, secret)
        self.assertContains(response, 'smtp.example.test')

        safe_settings = {
            'smtp_host': 'smtp.example.test', 'smtp_port': 587, 'use_tls': True,
            'use_ssl': False, 'username': 'mail-user', 'from_email': 'alerts@example.test',
            'recipients': ['ops@example.test'],
        }
        response = self.client.post(self.change_url(channel), {
            'name': 'admin-email', 'channel_type': AlertChannel.ChannelType.EMAIL,
            'is_enabled': 'on', 'settings': json.dumps(safe_settings),
        })
        self.assertEqual(response.status_code, 302, response.content.decode())
        channel.refresh_from_db()
        self.assertEqual(channel.settings['password'], secret)
        self.assertEqual(channel.settings['smtp_host'], 'smtp.example.test')
        self.assertEqual(channel.settings['recipients'], ['ops@example.test'])

        replacement = {**safe_settings, 'smtp_host': 'smtp.replaced.test', 'password': 'alert-password-replaced'}
        response = self.client.post(self.change_url(channel), {
            'name': 'admin-email', 'channel_type': AlertChannel.ChannelType.EMAIL,
            'is_enabled': 'on', 'settings': json.dumps(replacement),
        })
        self.assertEqual(response.status_code, 302, response.content.decode())
        channel.refresh_from_db()
        self.assertEqual(channel.settings['password'], 'alert-password-replaced')
        self.assertEqual(channel.settings['smtp_host'], 'smtp.replaced.test')
