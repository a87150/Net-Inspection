"""Offline UI contracts, exercised directly without AccessMiddleware."""
import importlib
import importlib.util
import hashlib
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from urllib.parse import unquote

from cryptography.fernet import Fernet

from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import include, path
from django.db import connection
from django.test.utils import CaptureQueriesContext

from net.models import Network_Device, SecurityDevice


def unused_route(request, **kwargs):
    return HttpResponse()


urlpatterns = [
    path('assets/<str:kind>/<uuid:pk>/backups/', unused_route,
         name='configuration_backup_list'),
    path('assets/<str:kind>/<uuid:pk>/backups/<uuid:backup_id>/download/',
         unused_route, name='configuration_backup_download'),
    path('', include('net.urls')),
]


class BackupAccessTests(SimpleTestCase):
    def test_backup_views_exist(self):
        self.assertIsNotNone(importlib.util.find_spec('index.devices.backups'))

    def view(self, name, user, method='get'):
        views = importlib.import_module('index.devices.backups')
        request = getattr(RequestFactory(), method)('/assets/networks/backups/')
        request.user = user
        args = ['networks', uuid4()]
        if name.endswith('download'):
            args.append(uuid4())
        return getattr(views, name)(request, *args)

    def test_non_admins_cannot_list_or_download_without_middleware(self):
        users = [
            (AnonymousUser(), 302),
            (SimpleNamespace(is_authenticated=True, is_active=True,
                             is_staff=False, is_superuser=False), 403),
            (SimpleNamespace(is_authenticated=True, is_active=False,
                             is_staff=True, is_superuser=True), 302),
        ]
        for name in ('configuration_backup_list', 'configuration_backup_download'):
            for user, status in users:
                with self.subTest(name=name, status=status):
                    response = self.view(name, user)
                    self.assertEqual(response.status_code, status)
                    self.assertIn('no-store', response['Cache-Control'])

    def test_admin_endpoints_are_get_only(self):
        for staff, superuser in ((True, False), (False, True)):
            user = SimpleNamespace(is_authenticated=True, is_active=True,
                                   is_staff=staff, is_superuser=superuser)
            for name in ('configuration_backup_list', 'configuration_backup_download'):
                for method in ('post', 'put', 'delete', 'head'):
                    with self.subTest(name=name, method=method, staff=staff):
                        response = self.view(name, user, method)
                        self.assertEqual(response.status_code, 405)
                        self.assertIn('no-store', response['Cache-Control'])


@override_settings(ROOT_URLCONF=__name__)
class BackupHistoryTests(TestCase):
    def setUp(self):
        self.asset = Network_Device.objects.create(ip='192.0.2.10', device_name='edge')
        self.monitor = SecurityDevice.objects.create(ip='192.0.2.11')
        self.request = RequestFactory().get('/')
        self.request.user = SimpleNamespace(is_authenticated=True, is_active=True,
                                            is_staff=True, is_superuser=False)

    def metadata(self, number=0):
        return SimpleNamespace(
            pk=uuid4(), device_type='network_device', device_id=self.asset.pk,
            backup_date=date(2026, 9, 8),
            captured_at=datetime(2026, 9, 8, 1, 2, 3, tzinfo=timezone.utc),
            filename=f'configuration-{number}.cfg', media_type='text/plain',
            scope='running-config', vendor='cisco', sha256='a' * 64,
            byte_size=2048, task_target=None, task_target_id=None,
        )

    def call_list(self, backups, kind='networks', pk=None):
        from index.devices.backups import configuration_backup_list
        # Storage is built independently; keep real lookup and rendering.
        def metadata_only(asset):
            self.assertEqual(asset.pk, pk or self.asset.pk)
            return backups

        def no_secret_read(backup):
            raise AssertionError('History must never decrypt a backup')

        service = SimpleNamespace(list_configuration_backups=metadata_only,
                                  read_configuration_backup=no_secret_read)
        with patch.dict('sys.modules', {'net.devices.configuration_backups': service}):
            return configuration_backup_list(self.request, kind, pk or self.asset.pk)

    def test_history_lists_only_ten_versions_and_metadata(self):
        versions = [self.metadata(i) for i in range(11)]
        response = self.call_list(versions)
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        for version in versions[:10]:
            self.assertContains(response, version.filename)
        self.assertNotContains(response, 'configuration-10.cfg')
        for value in ('a' * 64, 'running-config', '2048', '凭据', '固件'):
            self.assertContains(response, value)

    def test_empty_security_history_explains_partial_snapshots(self):
        response = self.call_list([], 'monitors', self.monitor.pk)
        self.assertContains(response, '暂无')
        self.assertContains(response, '配置节')

    def test_source_task_link_and_metadata_are_html_escaped(self):
        version = self.metadata()
        task_id = uuid4()
        version.task_target_id = uuid4()
        version.task_target = SimpleNamespace(task_id=task_id)
        version.filename = '<script>alert(1)</script>.cfg'
        response = self.call_list([version])
        self.assertContains(response, f'/tasks/{task_id}/')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, '&lt;script&gt;')

    def test_detail_button_is_only_for_admin_networks_and_monitors(self):
        for admin in (True, False):
            for kind in ('networks', 'monitors', 'servers', 'computers'):
                with self.subTest(admin=admin, kind=kind):
                    html = render_to_string('devices/detail.html', {
                        'can_administer': admin, 'item_key': kind,
                        'asset': self.asset, 'back_url': '/',
                    })
                    self.assertEqual('配置备份历史' in html,
                                     admin and kind in ('networks', 'monitors'))


@override_settings(ROOT_URLCONF=__name__)
class BackupDownloadTests(TestCase):
    def setUp(self):
        self.key = Fernet.generate_key()
        self.key_settings = override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=self.key)
        self.key_settings.enable()
        self.addCleanup(self.key_settings.disable)
        self.asset = Network_Device.objects.create(ip='192.0.2.20')
        self.other = Network_Device.objects.create(ip='192.0.2.21')
        self.monitor = SecurityDevice.objects.create(pk=self.asset.pk, ip='192.0.2.22')
        self.request = RequestFactory().get('/')
        self.request.user = SimpleNamespace(is_authenticated=True, is_active=True,
                                            is_staff=True, is_superuser=False)
        self.raw = b'hostname edge\r\npassword sensitive-raw-value\r\nend\r\n'
        self.backup = self.create_backup()

    def create_backup(self, day=1):
        from net.models import DeviceConfigurationBackup
        return DeviceConfigurationBackup.objects.create(
            device_type='network_device', device_id=self.asset.pk,
            backup_date=date(2026, 9, day), captured_at=datetime(2026, 9, day, tzinfo=timezone.utc),
            filename='config.cfg', media_type='text/plain', scope='running-config',
            vendor='cisco', sha256=hashlib.sha256(self.raw).hexdigest(),
            byte_size=len(self.raw), ciphertext=Fernet(self.key).encrypt(self.raw),
        )

    def download(self, kind='networks', pk=None, backup_id=None):
        from index.devices.backups import configuration_backup_download
        return configuration_backup_download(self.request, kind, pk or self.asset.pk,
                                             backup_id or self.backup.pk)

    def test_selected_version_returns_exact_verified_bytes_as_attachment(self):
        self.raw = b'newer configuration'
        self.create_backup(day=2)
        response = self.download()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'hostname edge\r\npassword sensitive-raw-value\r\nend\r\n')
        self.assertTrue(response['Content-Disposition'].startswith('attachment;'))
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertIn('no-store', response['Cache-Control'])

    def test_wrong_device_type_device_id_and_missing_versions_are_404(self):
        for kind, pk, backup_id in (
            ('networks', self.other.pk, self.backup.pk),
            ('monitors', self.monitor.pk, self.backup.pk),
            ('servers', self.asset.pk, self.backup.pk),
            ('networks', uuid4(), self.backup.pk),
            ('networks', self.asset.pk, uuid4()),
        ):
            with self.subTest(kind=kind, pk=pk):
                response = self.download(kind, pk, backup_id)
                self.assertEqual(response.status_code, 404)
                self.assertIn('no-store', response['Cache-Control'])
                self.assertNotIn(b'sensitive-raw-value', response.content)

    def test_attachment_filename_drops_paths_control_characters_and_quotes(self):
        for filename in ('../../secret.cfg', 'C:\\private\\secret.cfg',
                         'bad\r\nInjected: yes\x00".cfg', '..', '', '中文配置.cfg'):
            with self.subTest(filename=filename):
                self.backup.filename = filename
                self.backup.save(update_fields=['filename'])
                response = self.download()
                self.assertEqual(response.status_code, 200)
                header = unquote(response['Content-Disposition'])
                for unsafe in ('/', '\\', '\r', '\n', '\x00', '../', 'C:'):
                    self.assertNotIn(unsafe, header)
                self.assertTrue(header.startswith('attachment;'))

    def test_corrupt_ciphertext_and_hash_do_not_return_plaintext(self):
        for field, value in (('ciphertext', b'invalid'), ('sha256', '0' * 64)):
            with self.subTest(field=field):
                backup = self.create_backup(day=3 if field == 'ciphertext' else 4)
                setattr(backup, field, value)
                backup.save(update_fields=[field])
                response = self.download(backup_id=backup.pk)
                self.assertEqual(response.status_code, 503)
                self.assertNotIn(b'sensitive-raw-value', response.content)
                self.assertIn('no-store', response['Cache-Control'])

    def test_missing_key_fails_closed(self):
        with override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=''):
            response = self.download()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b'sensitive-raw-value', response.content)

    def test_real_history_does_not_query_ciphertext_or_decrypt(self):
        from index.devices.backups import configuration_backup_list
        with override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=''), CaptureQueriesContext(connection) as queries:
            response = configuration_backup_list(self.request, 'networks', self.asset.pk)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'sensitive-raw-value', response.content)
        self.assertFalse(any('ciphertext' in entry['sql'].lower() for entry in queries))
