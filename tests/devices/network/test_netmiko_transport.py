"""Network SSH migration contracts; all device I/O is replaced."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from net.infrastructure import ssh_collectors as ssh


class NetmikoTransportTests(SimpleTestCase):
    def test_network_uses_netmiko_driver_and_closes_session(self):
        session = Mock()
        session.find_prompt.return_value = 'edge#'
        session.send_command.return_value = 'CPU usage: 20%\nedge#'
        with patch('netmiko.ConnectHandler', return_value=session) as connect, \
             patch.object(ssh, '_connect', side_effect=AssertionError('Linux transport used')):
            for vendor, driver in [('cisco', 'cisco_ios'), ('h3c', 'hp_comware'),
                                   ('huawei', 'huawei'), ('ruijie', 'ruijie_os')]:
                with self.subTest(vendor=vendor):
                    session.reset_mock()
                    device = SimpleNamespace(vendor=vendor, ip='192.0.2.1', port=2222,
                                             username='reader', password='private-password')
                    result = ssh.collect_network_ssh(device, timeout=3, selected_items=['cpu'])
                    self.assertEqual(result.status, 'success', result.message)
                    self.assertEqual(result.data['cpu']['usage_percent'], 20)
                    self.assertEqual(connect.call_args.kwargs['device_type'], driver)
                    self.assertEqual(connect.call_args.kwargs['port'], 2222)
                    self.assertEqual(connect.call_args.kwargs['read_timeout_override'], 3)
                    self.assertFalse(connect.call_args.kwargs['use_keys'])
                    self.assertFalse(connect.call_args.kwargs['allow_agent'])
                    session.disconnect.assert_called_once()

    def test_prepare_failure_closes_connection_and_hides_password(self):
        session = Mock()
        session.session_preparation.side_effect = TimeoutError('private-password')
        device = SimpleNamespace(vendor='cisco', ip='192.0.2.1', port=22,
                                 username='reader', password='private-password')
        with patch('netmiko.ConnectHandler', return_value=session), \
             patch.object(ssh, '_connect', side_effect=AssertionError('Linux transport used')):
            result = ssh.collect_network_ssh(device, timeout=1, selected_items=['cpu'])
        self.assertEqual(result.status, 'failed')
        self.assertNotIn('private-password', result.message)
        session.disconnect.assert_called_once()

    def test_command_timeout_closes_session(self):
        session = Mock()
        session.find_prompt.return_value = 'edge#'
        session.send_command.side_effect = TimeoutError('private-password')
        device = SimpleNamespace(vendor='cisco', ip='192.0.2.1', port=22,
                                 username='reader', password='private-password')
        with patch('netmiko.ConnectHandler', return_value=session), \
             patch.object(ssh, '_connect', side_effect=AssertionError('Linux transport used')):
            result = ssh.collect_network_ssh(device, timeout=1, selected_items=['cpu'])
        self.assertEqual(result.status, 'failed')
        self.assertNotIn('private-password', result.message)
        session.disconnect.assert_called_once()

    def test_selected_template_command_overrides_native_cpu(self):
        session = Mock()
        session.find_prompt.return_value = 'edge#'
        session.send_command.side_effect = ['edge#', 'CPU: 37%']
        device = SimpleNamespace(vendor='huawei', ip='192.0.2.1', port=22,
                                 username='reader', password='private-password',
                                 collection_settings={'commands': {'cpu': ['display custom cpu']},
                                                      'parsers': {'cpu': {'engine': 'regex', 'template': r'CPU: (?P<usage>\d+)%'}}})
        with patch('netmiko.ConnectHandler', return_value=session):
            result = ssh.collect_network_ssh(device, timeout=3, selected_items=['cpu'])
        self.assertEqual(result.status, 'success', result.message)
        self.assertEqual(result.data['cpu']['usage_percent'], 37.0)
        self.assertEqual([call.args[0] for call in session.send_command.call_args_list], ['screen-length 0 temporary', 'display custom cpu'])
