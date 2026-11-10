"""SSH/Telnet 传输互斥，端口留空取协议默认值。"""
from django.test import TestCase

from net.devices.network.transport import device_type_for
from net.models import Network_Device


class CliProtocolTests(TestCase):
    def test_port_defaults_follow_protocol(self):
        device = Network_Device(ip='192.0.2.90', cli_protocol='ssh')
        self.assertEqual(device.effective_port, 22)
        device.cli_protocol = 'telnet'
        self.assertEqual(device.effective_port, 23)

    def test_explicit_port_wins(self):
        device = Network_Device(ip='192.0.2.91', cli_protocol='telnet', port=2323)
        self.assertEqual(device.effective_port, 2323)

    def test_snmp_port_defaults_to_161(self):
        self.assertEqual(Network_Device(ip='192.0.2.92').effective_snmp_port, 161)
        self.assertEqual(Network_Device(ip='192.0.2.93', snmp_port=1161).effective_snmp_port, 1161)

    def test_unknown_protocol_falls_back_to_ssh(self):
        device = Network_Device(ip='192.0.2.94', cli_protocol='rlogin')
        self.assertEqual(device.effective_cli_protocol, 'ssh')
        self.assertEqual(device.effective_port, 22)

    def test_telnet_uses_the_telnet_driver(self):
        self.assertEqual(device_type_for('huawei', 'telnet'), 'huawei_telnet')
        self.assertEqual(device_type_for('huawei', 'ssh'), 'huawei')
        self.assertEqual(device_type_for('generic', 'telnet'), 'generic_termserver_telnet')

    def test_form_leaves_port_optional(self):
        from index.devices.forms import device_form
        form = device_form('networks')
        self.assertIn('cli_protocol', form.fields)
        self.assertFalse(form.fields['port'].required)
        self.assertFalse(form.fields['snmp_port'].required)
