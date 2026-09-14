from tests.auth import login_admin, login_reader
from django.test import TestCase
from django.urls import reverse
from net.models import InspectionProfile, Network_Device, Server, SecurityDevice, TaskRun
from net.inspections.queue import enqueue_task


class AssetCreateTests(TestCase):
    def setUp(self):
        login_admin(self.client, username='device-editor')

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
                for field in ('cpu_model', 'memory_total_gb', 'disk_total_gb', 'model', 'os', 'port_count', 'vlan_count'):
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
    def test_admin_can_edit_one_device_without_replacing_an_empty_password(self):
        server = Server.objects.create(ip='192.0.2.70', name='旧名称', username='operator', password='saved-secret')
        response = self.client.post(reverse('asset_edit', args=['servers', server.pk]), {'ip': server.ip, 'name': '新名称', 'username': 'new-operator', 'password': ''})
        self.assertRedirects(response, reverse('asset_edit', args=['servers', server.pk]))
        server.refresh_from_db()
        self.assertEqual(server.name, '新名称')
        self.assertEqual(server.username, 'new-operator')
        self.assertEqual(server.password, 'saved-secret')

    def test_invalid_edit_reopens_the_same_device_modal_without_writing(self):
        server = Server.objects.create(ip='192.0.2.71', name='原服务器')
        response = self.client.post(reverse('asset_edit', args=['servers', server.pk]), {'ip': 'invalid', 'name': '未保存名称'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['open_edit_device_modal'])
        self.assertEqual(response.context['edit_device'].pk, server.pk)
        self.assertIn('ip', response.context['device_form'].errors)
        server.refresh_from_db()
        self.assertEqual(server.name, '原服务器')

    def test_edit_blocks_connection_changes_while_this_device_has_an_active_task(self):
        server = Server.objects.create(ip='192.0.2.73', name='原服务器', username='operator', password='saved-secret')
        profile = InspectionProfile.objects.create(name='活动服务器', device_type='server', selected_items=['cpu'])
        enqueue_task(profile, [server.pk], TaskRun.Source.MANUAL)
        response = self.client.post(reverse('asset_edit', args=['servers', server.pk]), {
            'ip': server.ip, 'name': '原服务器', 'username': 'new-operator', 'password': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['open_edit_device_modal'])
        self.assertContains(response, '活动巡检任务')
        server.refresh_from_db()
        self.assertEqual(server.username, 'operator')
    def test_edit_rejects_another_asset_kind_and_reader_writes(self):
        server = Server.objects.create(ip='192.0.2.72', name='原服务器')
        self.assertEqual(self.client.post(reverse('asset_edit', args=['networks', server.pk]), {}).status_code, 404)
        login_reader(self.client)
        response = self.client.post(reverse('asset_edit', args=['servers', server.pk]), {'ip': server.ip, 'name': '读者不能修改'})
        self.assertEqual(response.status_code, 403)
        server.refresh_from_db()
        self.assertEqual(server.name, '原服务器')
