from django.test import TestCase
from index.devices.forms import device_form, preserve_empty_secrets
from net.models import Server


class ServerConnectionFormTests(TestCase):
    def test_windows_ignores_ssh_values_and_defaults_http_address(self):
        form=device_form('servers',{'ip':'192.0.2.44','server_type':'windows','port':'bad','api_token':'token'})
        self.assertTrue(form.is_valid(),form.errors)
        server=form.save()
        self.assertFalse(server.api_url)
        self.assertEqual(server.port,22)

    def test_linux_ignores_invalid_windows_values_and_preserves_saved_configuration(self):
        server=Server.objects.create(ip='192.0.2.45',api_url='https://agent.example/inspection',api_token='existing')
        form=device_form('servers',{'ip':server.ip,'server_type':'linux','port':'2222','api_url':'invalid','api_token':'changed'},instance=server)
        self.assertTrue(form.is_valid(),form.errors)
        preserve_empty_secrets(form)
        form.save()
        server.refresh_from_db()
        self.assertEqual(server.port,2222)
        self.assertEqual(server.api_token,'existing')
        self.assertEqual(server.api_url,'https://agent.example/inspection')
