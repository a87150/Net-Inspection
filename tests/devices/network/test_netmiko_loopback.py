"""Real SSH protocol against a disposable loopback peer, never a real device."""
import socket
import threading
from contextlib import contextmanager
from types import SimpleNamespace
import paramiko
from django.test import SimpleTestCase
from net.infrastructure.ssh_collectors import collect_network_ssh, collect_linux_ssh

CONFIG = 'hostname edge\r\n username fixture secret preserved\r\nend\r\n'


class Peer(paramiko.ServerInterface):
    def __init__(self):
        self.ready = threading.Event()
        self.command = None

    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL

    def get_allowed_auths(self, username):
        return 'password'

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED

    def check_channel_pty_request(self, *args):
        return True

    def check_channel_shell_request(self, channel):
        self.ready.set()
        return True

    def check_channel_exec_request(self, channel, command):
        self.command = command
        self.ready.set()
        return True


@contextmanager
def loopback_peer():
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(10)
    key = paramiko.RSAKey.generate(2048)
    state = {}
    done = threading.Event()

    def serve():
        try:
            sock, _ = listener.accept()
            transport = paramiko.Transport(sock)
            state['transport'] = transport
            transport.add_server_key(key)
            peer = Peer()
            transport.start_server(server=peer)
            channel = transport.accept(10)
            if channel is None or not peer.ready.wait(10):
                return
            if peer.command is not None:
                channel.sendall(b'Linux selected CPU evidence\n')
                channel.send_exit_status(0)
                channel.shutdown_write()
                return
            channel.settimeout(.2)
            channel.sendall(b'\r\nedge#')
            pending = b''
            while not done.is_set():
                try:
                    data = channel.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    break
                pending += data
                while b'\n' in pending:
                    command, pending = pending.split(b'\n', 1)
                    command = command.strip().decode()
                    if command == 'exit':
                        return
                    body = CONFIG if command == 'show running-config' else (
                        'CPU usage: 20%\r\n' if command == 'show processes cpu' else '')
                    channel.sendall((command + '\r\n' + body + 'edge#').encode())
        except (EOFError, OSError):
            pass  # client disconnect / test cleanup
        except Exception as exc:
            state['error'] = exc
        finally:
            if 'transport' in state:
                state['transport'].close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(ip='127.0.0.1', port=listener.getsockname()[1],
                              vendor='cisco', username='fixture', password='fixture')
    finally:
        done.set()
        listener.close()
        if 'transport' in state:
            state['transport'].close()
        thread.join(3)
        if thread.is_alive():
            raise AssertionError('loopback SSH peer did not stop')
        if 'error' in state:
            raise state['error']


class LoopbackSSHTests(SimpleTestCase):
    def test_netmiko_collects_cpu_over_real_ssh(self):
        with loopback_peer() as device:
            result = collect_network_ssh(device, timeout=3, selected_items=['cpu'])
        self.assertEqual(result.status, 'success', result.message)
        self.assertEqual(result.data['cpu']['usage_percent'], 20)

    def test_netmiko_configuration_preserves_original_crlf_and_secrets(self):
        with loopback_peer() as device:
            result = collect_network_ssh(device, timeout=3, selected_items=['config_info'])
        self.assertEqual(result.status, 'success', result.message)
        self.assertEqual(result.data['config_info']['content'], CONFIG)

    def test_linux_paramiko_exec_still_works(self):
        with loopback_peer() as device:
            result = collect_linux_ssh(device, timeout=3, selected_items=['cpu'])
        self.assertEqual(result.status, 'success', result.message)
        self.assertEqual(result.data['cpu']['raw'], 'Linux selected CPU evidence')
