from django.test import TestCase
from index.devices.pc.simple_source_form import SimplePCLogSourceForm
from net.models.pc_sources import PCLogSourceCredential
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch
import sys
from net.devices.pc.connectors.factory import build_connector
from net.devices.pc.connectors.base import PCLogConnectionError


class WindowsIdentityTests(TestCase):
    def test_default_shared_path_saves_without_credentials(self):
        form = SimplePCLogSourceForm({'source_type': 'smb', 'shared_path': r'\\files.test\logs\incoming'})
        self.assertTrue(form.is_valid(), form.errors)
        source = form.save()
        self.assertEqual(source.smb_auth_mode, 'system')
        self.assertFalse(PCLogSourceCredential.objects.exists())
        connector = build_connector(source)
        self.assertEqual(type(connector).__name__, 'WindowsPCLogConnector')

    @skipUnless(sys.platform == 'win32', 'Windows filesystem semantics')
    def test_system_reader_downloads_and_probes_without_overwriting_existing_files(self):
        form = SimplePCLogSourceForm({'source_type': 'smb', 'shared_path': r'\\files.test\logs\incoming'})
        self.assertTrue(form.is_valid(), form.errors)
        source = form.save()
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'incoming').mkdir()
            (root / 'incoming' / 'pc.json').write_bytes(b'{}')
            connector = build_connector(source)
            connector.root = connector.share_root = str(root)
            self.assertEqual([row.path for row in connector.list_json()], ['incoming/pc.json'])
            connector.download('incoming/pc.json', root / 'download.json')
            self.assertEqual((root / 'download.json').read_bytes(), b'{}')
            connector.probe()
            self.assertFalse(list(root.rglob('.pc-log-probe-*')))
            with self.assertRaises(PCLogConnectionError):
                connector.move('incoming/pc.json', 'download.json')
            self.assertTrue((root / 'incoming' / 'pc.json').exists())
            connector.close()

    def test_non_windows_system_mode_gives_actionable_error_without_login(self):
        form = SimplePCLogSourceForm({'source_type': 'smb', 'shared_path': r'\\files.test\logs\incoming'})
        self.assertTrue(form.is_valid(), form.errors)
        connector = build_connector(form.save())
        with patch('net.devices.pc.connectors.windows.sys.platform', 'linux'):
            with self.assertRaisesRegex(PCLogConnectionError, '手动指定账号密码'):
                connector.list_json()

    def test_manual_mode_requires_username(self):
        form = SimplePCLogSourceForm({'source_type': 'smb', 'shared_path': r'\\files.test\logs\incoming',
                                      'smb_auth_mode': 'credentials'})
        self.assertFalse(form.is_valid())
        self.assertIn('username', form.errors)
