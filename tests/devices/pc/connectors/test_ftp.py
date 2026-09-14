import ftplib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.test import SimpleTestCase
from net.devices.pc.connectors.base import PCLogConnectionError, RemoteFileChanged
from net.devices.pc.connectors.ftp import FTPPCLogConnector
from .test_contract import source


class FakeFTP:
    def __init__(self):
        self.files = {'/logs/incoming/pc.json': b'{}'}
        self.directories = {'/', '/logs', '/logs/incoming'}
        self.changed = False
        self.protected = False
    def connect(self, host, port, timeout): pass
    def login(self, user, passwd): pass
    def set_pasv(self, value): pass
    def prot_p(self): self.protected = True
    def mlsd(self, path, facts=None):
        prefix = path.rstrip('/') + '/'
        for name, data in list(self.files.items()):
            relative = name.removeprefix(prefix)
            if name.startswith(prefix) and '/' not in relative:
                yield relative, {'type': 'file', 'size': str(len(data)), 'modify': '20260907040000'}
    def voidcmd(self, command): return '200 OK'
    def sendcmd(self, command):
        return '213 20260907040001' if self.changed else '213 20260907040000'
    def size(self, path):
        if path not in self.files: raise ftplib.error_perm('550 Missing')
        return len(self.files[path])
    def retrbinary(self, command, callback, blocksize=8192):
        callback(self.files[command[5:]])
    def storbinary(self, command, fp, blocksize=8192):
        self.files[command[5:]] = fp.read()
    def mkd(self, path):
        if path in self.directories: raise ftplib.error_perm('550 Exists')
        self.directories.add(path)
    def cwd(self, path):
        if path not in self.directories: raise ftplib.error_perm('550 Missing')
    def pwd(self): return '/'
    def rename(self, src, dst): self.files[dst] = self.files.pop(src)
    def delete(self, path): self.files.pop(path, None)
    def quit(self): pass
    def close(self): pass


class FTPTests(SimpleTestCase):
    def connector(self, **kwargs):
        self.client = FakeFTP()
        return FTPPCLogConnector(source(**kwargs), password='secret', client=self.client)

    def test_download_and_archive(self):
        connector = self.connector()
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'log.part'
            self.assertEqual(connector.download('incoming/pc.json', path).size, 2)
            self.assertEqual(path.read_bytes(), b'{}')
        connector.move('incoming/pc.json', 'processed/pc.json')
        self.assertEqual(self.client.files, {'/logs/processed/pc.json': b'{}'})

    def test_changed_file_removes_partial_download(self):
        connector = self.connector()
        original = self.client.retrbinary
        def changing(*args, **kwargs):
            original(*args, **kwargs)
            self.client.changed = True
        self.client.retrbinary = changing
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'log.part'
            with self.assertRaises(RemoteFileChanged):
                connector.download('incoming/pc.json', path)
            self.assertFalse(path.exists())

    def test_ftps_encrypts_data_channel(self):
        connector = self.connector(ftp_use_tls=True)
        connector.stat('incoming/pc.json')
        self.assertTrue(self.client.protected)

    def test_errors_never_echo_password(self):
        with patch.object(FakeFTP, 'login', side_effect=OSError('secret')):
            with self.assertRaises(PCLogConnectionError) as error:
                self.connector().stat('incoming/pc.json')
        self.assertNotIn('secret', str(error.exception))

    def test_probe_cleans_temporary_files_and_preserves_logs(self):
        connector = self.connector()
        connector.probe()
        self.assertEqual(self.client.files, {'/logs/incoming/pc.json': b'{}'})

    def test_archive_collision_preserves_both_files(self):
        connector = self.connector()
        self.client.files['/logs/processed/pc.json'] = b'previous evidence'
        with self.assertRaises(PCLogConnectionError):
            connector.move('incoming/pc.json', 'processed/pc.json')
        self.assertEqual(self.client.files['/logs/processed/pc.json'], b'previous evidence')
        self.assertEqual(self.client.files['/logs/incoming/pc.json'], b'{}')

    def test_fractional_timestamp_detects_same_second_rewrite(self):
        connector = self.connector()
        timestamps = iter(['213 20260907040000.100', '213 20260907040000.900'])
        self.client.sendcmd = lambda _: next(timestamps)
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'log.part'
            with self.assertRaises(RemoteFileChanged):
                connector.download('incoming/pc.json', path)
            self.assertFalse(path.exists())
