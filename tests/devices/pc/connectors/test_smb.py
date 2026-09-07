import os
import ntpath
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from django.test import SimpleTestCase
from net.devices.pc.connectors.smb import SMBPCLogConnector
from net.devices.pc.connectors.base import PCLogPermissionError
from .test_contract import source


class LocalSMB:
    """Exercise connector behavior against real files, replacing only SMB transport."""
    def __init__(self, root):
        self.root = Path(root)
        self.caches = []
    def register_session(self, host, **kwargs):
        self.caches.append(kwargs['connection_cache'])
        kwargs['connection_cache']['session'] = host
    def reset_connection_cache(self, **kwargs):
        kwargs['connection_cache'].clear()
    def local(self, path):
        drive, tail = ntpath.splitdrive(path)
        if drive != r'\\files.example\logs':
            raise AssertionError(path)
        return self.root.joinpath(*tail.strip('\\').split('\\'))
    def stat(self, path, **kwargs):
        result = self.local(path).stat()
        return SimpleNamespace(st_size=result.st_size, st_mtime=result.st_mtime,
                               st_mode=result.st_mode, st_reparse_tag=0)
    def scandir(self, path, **kwargs):
        with os.scandir(self.local(path)) as rows:
            return list(rows)
    def open_file(self, path, mode='r', **kwargs):
        return self.local(path).open(mode)
    def makedirs(self, path, exist_ok=False, **kwargs):
        self.local(path).mkdir(parents=True, exist_ok=exist_ok)
    def rename(self, source, destination, **kwargs):
        self.local(source).rename(self.local(destination))
    def remove(self, path, **kwargs):
        self.local(path).unlink()


class SMBTests(SimpleTestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        (self.root / 'incoming').mkdir()
        (self.root / 'incoming' / 'pc.json').write_bytes(b'{}')
        self.client = LocalSMB(self.root)
        self.config = source(port=445, share_name='logs', remote_root_directory='')
        self.connector = SMBPCLogConnector(self.config, password='secret', client=self.client)
        self.addCleanup(self.connector.close)

    def test_download_and_archive_inside_share(self):
        destination = self.root / 'download.part'
        self.connector.download('incoming/pc.json', destination)
        self.assertEqual(destination.read_bytes(), b'{}')
        self.connector.move('incoming/pc.json', 'processed/pc.json')
        self.assertFalse((self.root / 'incoming/pc.json').exists())
        self.assertEqual((self.root / 'processed/pc.json').read_bytes(), b'{}')

    def test_connections_are_isolated_between_workers(self):
        other = SMBPCLogConnector(self.config, password='secret', client=self.client)
        self.addCleanup(other.close)
        self.connector.stat('incoming/pc.json')
        other.stat('incoming/pc.json')
        self.assertIsNot(self.client.caches[0], self.client.caches[1])
        self.connector.close()
        self.assertEqual(self.client.caches[1], {'session': 'files.example'})

    def test_probe_preserves_logs_and_removes_temporary_files(self):
        self.connector.probe()
        self.assertEqual([p.name for p in self.root.rglob('*') if p.is_file()], ['pc.json'])

    def test_cannot_use_another_share_or_parent_path(self):
        for path in ('../other.json', '//other/share/x', 'incoming/../../x'):
            with self.assertRaises(ValueError):
                self.connector.stat(path)

    def test_incoming_junction_is_rejected_before_read_or_listing(self):
        original = self.client.stat
        def junction(path, **kwargs):
            result = original(path, **kwargs)
            if path.rstrip('\\').endswith('\\incoming'):
                result.st_reparse_tag = 0xA0000003
            return result
        self.client.stat = junction
        with self.assertRaises(PCLogPermissionError):
            self.connector.stat('incoming/pc.json')
        with self.assertRaises(PCLogPermissionError):
            self.connector.list_json()
