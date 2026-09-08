from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from net.models import Network_Device, Server, SecurityDevice, TaskRun


class AssetCreateTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user('device-editor', password='fixture'))

    def test_each_device_list_can_create_one_device(self):
        for kind, model, fields in (
            ('networks', Network_Device, {'device_name': '新交换机'}),
            ('servers', Server, {'name': '新服务器', 'server_type': 'windows', 'api_url': 'http://192.0.2.5:9180/'}),
            ('monitors', SecurityDevice, {'device_name': '前门闸机', 'device_type': '门禁闸机'}),
        ):
            with self.subTest(kind=kind):
                page = self.client.get(reverse('asset_list', args=[kind]))
                self.assertContains(page, 'data-bs-target="#addDeviceModal"')
                response = self.client.post(f'/assets/{kind}/add/', {'ip': '192.0.2.5', **fields})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(model.objects.count(), 1)
                self.assertEqual(model.objects.get().ip, '192.0.2.5')
        self.assertFalse(TaskRun.objects.exists())

    def test_snmp_validation_and_password_fields(self):
        response = self.client.post('/assets/networks/add/', {
            'ip': '192.0.2.8', 'connection_type': 'snmp', 'snmp_version': 'v3',
            'snmp_security_level': 'authPriv', 'password': 'private-test-password',
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('snmp_username', response.context['device_form'].errors)
        self.assertContains(response, 'type="password"')
        self.assertNotContains(response, 'private-test-password')
        self.assertFalse(Network_Device.objects.exists())

    def test_duplicate_ip_keeps_existing_device_and_reopens_form(self):
        Server.objects.create(ip='192.0.2.5', name='原服务器')
        response = self.client.post('/assets/servers/add/', {'ip': '192.0.2.5', 'name': '新服务器'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['open_add_device_modal'])
        self.assertIn('ip', response.context['device_form'].errors)
        self.assertEqual(response.context['device_form']['name'].value(), '新服务器')
        self.assertEqual(Server.objects.get().name, '原服务器')

    def test_bad_ip_and_port_do_not_create_device(self):
        response = self.client.post('/assets/servers/add/', {'ip': 'invalid', 'port': '70000'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('ip', response.context['device_form'].errors)
        self.assertIn('port', response.context['device_form'].errors)
        self.assertFalse(Server.objects.exists())

    def test_discovered_configuration_is_not_editable_during_creation(self):
        for kind, model in (('servers', Server), ('networks', Network_Device), ('monitors', SecurityDevice)):
            with self.subTest(kind=kind):
                response = self.client.get(reverse('asset_list', args=[kind]))
                form = response.context['device_form']
                for field in ('cpu_model', 'memory_total_gb', 'disk_total_gb', 'os_version', 'model', 'os', 'port_count', 'vlan_count'):
                    self.assertNotIn(field, form.fields)
                response = self.client.post(f'/assets/{kind}/add/', {
                    'ip': '192.0.2.18', 'cpu_model': 'must-not-save',
                    'memory_total_gb': '128', 'model': 'must-not-save',
                })
                self.assertEqual(response.status_code, 302)
                device = model.objects.get()
                self.assertIsNone(device.cpu_model)
                self.assertIsNone(device.memory_total_gb)
                self.assertIsNone(device.model)

    def test_adding_does_not_allow_pc_or_anonymous_writes(self):
        self.assertEqual(self.client.post('/assets/computers/add/', {'ip': '192.0.2.5'}).status_code, 404)
        self.assertEqual(self.client.get('/assets/servers/add/').status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post('/assets/servers/add/', {'ip': '192.0.2.5'}).status_code, 302)
        self.assertFalse(Server.objects.exists())
