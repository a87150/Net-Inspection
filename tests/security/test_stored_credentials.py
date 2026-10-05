from cryptography.fernet import Fernet

from django.db import connection
from django.test import TestCase, override_settings

from net.infrastructure.credentials import looks_encrypted
from net.models import AlertChannel, PeopleSyncSource
from net.models.devices import Network_Device, Server, WeakCurrentDevice
from net.models.domain import Domain_Controller_Config


KEY = Fernet.generate_key().decode()


@override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=KEY)
class StoredCredentialsAreEncryptedTests(TestCase):
    """The point of the change is what a database read shows, so assert on raw columns."""

    def _raw(self, table, column, primary_key):
        sql = 'SELECT ' + column + ' FROM ' + table + ' WHERE id = %s'
        # Primary keys are UUIDs and the column stores them without dashes.
        value = primary_key.hex if hasattr(primary_key, 'hex') else primary_key
        with connection.cursor() as cursor:
            cursor.execute(sql, [value])
            return cursor.fetchone()[0]

    def test_device_passwords_are_ciphertext_on_disk_and_plain_in_python(self):
        device = Network_Device.objects.create(
            device_name='enc-1', ip='10.0.0.1', password='ssh-secret',
            snmp_community='public-secret')

        stored = self._raw('net_network_device', 'password', device.pk)
        self.assertNotEqual(stored, 'ssh-secret')
        self.assertTrue(looks_encrypted(stored))
        community = self._raw('net_network_device', 'snmp_community', device.pk)
        self.assertNotIn('public-secret', community)

        reloaded = Network_Device.objects.get(pk=device.pk)
        self.assertEqual(reloaded.password, 'ssh-secret')
        self.assertEqual(reloaded.snmp_community, 'public-secret')

    def test_server_and_weak_current_credentials_round_trip(self):
        server = Server.objects.create(
            name='srv', ip='10.0.0.2', password='pw-srv', api_token='tok-srv')
        weak = WeakCurrentDevice.objects.create(
            device_name='ac', ip='10.0.0.3', api_password='pw-ac', api_token='tok-ac')

        self.assertNotIn('pw-srv', self._raw('net_server', 'password', server.pk))
        self.assertNotIn('tok-ac', self._raw('net_weakcurrentdevice', 'api_token', weak.pk))
        self.assertEqual(Server.objects.get(pk=server.pk).password, 'pw-srv')
        self.assertEqual(WeakCurrentDevice.objects.get(pk=weak.pk).api_password, 'pw-ac')

    def test_domain_bind_password_is_ciphertext_on_disk(self):
        config = Domain_Controller_Config.objects.create(
            name='dc', host='10.0.0.4', bind_password='bind-secret')

        stored = self._raw('net_domain_controller_config', 'bind_password', config.pk)
        self.assertNotIn('bind-secret', stored)
        reloaded = Domain_Controller_Config.objects.get(pk=config.pk)
        self.assertEqual(reloaded.bind_password, 'bind-secret')

    def test_alert_settings_keep_their_shape_but_hide_the_password(self):
        channel = AlertChannel.objects.create(
            name='mail', channel_type='email',
            settings={'host': 'smtp.example.com', 'port': 587, 'password': 'smtp-secret'})

        stored = self._raw('net_alertchannel', 'settings', channel.pk)
        self.assertNotIn('smtp-secret', stored)
        # Every string leaf is encrypted, not only the secret ones; the numeric
        # port and the keys stay readable, which is what keeps the document a
        # valid shape for the callers and for MariaDB's json_valid constraint.
        self.assertNotIn('smtp.example.com', stored)
        self.assertIn('587', stored)
        self.assertIn('password', stored)

        settings = AlertChannel.objects.get(pk=channel.pk).settings
        self.assertEqual(settings['password'], 'smtp-secret')
        self.assertEqual(settings['port'], 587)
        self.assertEqual(settings['host'], 'smtp.example.com')

    def test_people_sync_credentials_round_trip(self):
        source = PeopleSyncSource.objects.create(
            name='feishu', source_type='feishu', source_key='cli_x',
            credentials={'app_id': 'cli_x', 'app_secret': 'app-secret-value'})

        stored = self._raw('net_peoplesyncsource', 'credentials', source.pk)
        self.assertNotIn('app-secret-value', stored)
        reloaded = PeopleSyncSource.objects.get(pk=source.pk)
        self.assertEqual(reloaded.credentials['app_secret'], 'app-secret-value')

    def test_a_wrong_key_raises_instead_of_returning_ciphertext(self):
        device = Network_Device.objects.create(
            device_name='enc-2', ip='10.0.0.5', password='ssh-secret')

        with override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            with self.assertRaises(Exception):
                Network_Device.objects.get(pk=device.pk)