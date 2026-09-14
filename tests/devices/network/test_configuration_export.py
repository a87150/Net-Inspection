"""Offline collection -> persisted item -> download contracts (no device I/O)."""
import asyncio
import csv
import importlib
import importlib.util
import json
from datetime import timedelta
from io import BytesIO, StringIO
from unittest.mock import Mock, patch
from urllib.parse import unquote
from zipfile import ZipFile

from cryptography.fernet import Fernet
import requests
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from index.inspections.forms import inspection_item_choices
from net.models import (AlertChannel, Domain_Controller_Config, InspectionProfile,
                        SecurityDevice, Network_Device, PeopleSyncSource, Server)
from net.infrastructure.http_collectors import collect_security_api
from net.devices.security.payload import collect_native_configuration
from net.infrastructure.ssh_collectors import collect_network_ssh
from net.inspections.queue import enqueue_task
from net.inspections.executor import execute_target
from net.inspections.executor import _begin_target, persist_execution_failure
from net.inspections.queue import claim_next_task


NETWORK_TEXT = 'version 15.2\nhostname edge\ninterface Gi1\n description uplink\nend\n'
DAHUA_TEXT = ('table.Network.DefaultInterface=eth0\r\n'
              'table.Network.Hostname=camera\r\n'
              'table.Network.eth0.IPAddress=192.0.2.20\r\n')


def snapshot(content=NETWORK_TEXT, *, vendor='cisco', fmt='text', scope='running-config'):
    return dict(status='success', vendor=vendor, format=fmt, content=content,
                scope=scope, complete=True, full_backup=False)


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        self.now += .01
        return self.now

    def sleep(self, duration):
        self.now += duration


class Shell:
    """Transport double: commands must match; actual capture reader stays real."""
    def __init__(self, responses, prompt='edge#'):
        self.responses = responses
        self.pending = [prompt.encode()]
        self.commands = []

    def send(self, command):
        self.commands.append(command.strip())
        response = self.responses[command.strip()]
        if isinstance(response, Exception):
            raise response
        self.pending = [part.encode() if isinstance(part, str) else part
                        for part in (response if isinstance(response, list) else [response])]
        return len(command)

    def recv_ready(self):
        return bool(self.pending)

    def recv(self, size):
        return self.pending.pop(0)


class NativeResponse:
    """Mock HTTP boundary only; the native reader/validation remain real."""
    def __init__(self, body=DAHUA_TEXT, media='text/plain', status=200):
        self.status = status
        self.headers = {'content-type': media}
        self.body = body.encode() if isinstance(body, str) else body
        self.content = self
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def iter_chunked(self, size):
        yield self.body


response = NativeResponse


class VirtualLoop(asyncio.SelectorEventLoop):
    """Advance only scheduled time, never sleep or make a network connection."""
    def __init__(self):
        self.now = 0.0
        super().__init__()
        self._selector.select = self.select

    def time(self):
        return self.now

    def select(self, timeout=None):
        if timeout is None:
            raise AssertionError('unbounded test wait')
        self.now += timeout
        if self.now > 181:
            raise AssertionError('unbounded test deadline')
        return []

    def run_in_executor(self, *args, **kwargs):
        raise AssertionError('native capture must not leave blocking executor work')


class ConfigurationTests(TestCase):
    def setUp(self):
        from tests.auth import login_admin
        login_admin(self.client)
        # Any accidental outbound request fails even in download and UI tests.
        self.http = self.enterContext(patch('requests.get', side_effect=AssertionError('device HTTP forbidden')))
        self.native_http = self.enterContext(patch('aiohttp.ClientSession.get', side_effect=AssertionError('device HTTP forbidden')))
        self.connect = self.enterContext(patch('net.infrastructure.ssh_collectors._connect_network', side_effect=AssertionError('device SSH forbidden')))
        self.enterContext(patch('net.infrastructure.ssh_collectors.time', Clock()))
        self.device = Network_Device.objects.create(
            ip='192.0.2.10', device_name='edge', vendor='Cisco',
            username='ssh-user-private', password='ssh-pass-private')
        self.camera = SecurityDevice.objects.create(
            ip='192.0.2.20', device_name='camera', vendor='大华',
            api_url='https://192.0.2.20/status?token=URL-token-private',
            api_username='api-user-private', api_password='api-pass-private',
            api_token='api-token-private')
        self.record_number = 0
        self.enterContext(override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=Fernet.generate_key()))

    def backup(self, asset=None, content=NETWORK_TEXT, **metadata):
        from net.devices.configuration_backups import store_configuration_backup
        backup = store_configuration_backup(asset or self.device, snapshot(content))
        for key, value in metadata.items():
            setattr(backup, key, value)
        if metadata:
            backup.save(update_fields=list(metadata))
        return backup

    def saved(self, asset, **fields):
        record = asset.inspections.create(**fields)
        self.record_number += 1
        # Windows clocks can give consecutive inserts identical timestamps;
        # test chronology explicitly, not incidental UUID ordering on a tie.
        asset.inspections.filter(pk=record.pk).update(
            created_at=timezone.now() + timedelta(seconds=self.record_number))
        return record

    def exports(self):
        self.assertIsNotNone(importlib.util.find_spec('net.data_exchange.configuration'),
                             'configuration export service is missing')
        return importlib.import_module('net.data_exchange.configuration')

    def shell(self, output=None, *, prompt='edge#', command='show running-config', paging='terminal length 0'):
        channel = Shell({paging: paging + '\r\n' + prompt,
                         command: output if output is not None else command + '\r\n' + NETWORK_TEXT + prompt}, prompt)
        self.connect.side_effect = None
        channel.pending = []  # Netmiko session preparation consumed the initial prompt.
        session = Mock(remote_conn=channel)
        session.find_prompt.return_value = prompt
        def send_command(command, **kwargs):
            channel.send(command + '\n')
            return b''.join(channel.pending).decode('utf-8')
        session.send_command.side_effect = send_command
        self.connect.return_value = session
        return channel

    def execute(self, asset, kind, items):
        profile = InspectionProfile.objects.create(name='config', device_type=kind, selected_items=items)
        enqueue_task(profile, [asset.pk], 'manual')
        task = claim_next_task('config-worker', 60)
        execute_target(task.target_runs.get(), worker_id='config-worker')
        return asset.inspections.latest('created_at')

    def single(self, asset=None, kind='networks'):
        return self.client.get(f'/assets/{kind}/{(asset or self.device).pk}/configuration/')

    def test_selection_exposes_configuration_only_for_supported_asset_types(self):
        for kind in ('network_device', 'monitor'):
            self.assertIn('config_info', dict(inspection_item_choices(kind)))
        self.assertNotIn('config_info', dict(inspection_item_choices('server')))

    def test_ssh_capture_persists_item_and_downloads_without_reconnection(self):
        native = NETWORK_TEXT.replace('\n', '\r\n')
        channel = self.shell('show running-config\r\n' + native + 'edge#')
        record = self.execute(self.device, 'network_device', ['config_info'])
        self.assertEqual(record.details.get('config_info', {}).get('status'), 'success')
        self.assertNotIn('content', record.raw_output['config_info'])
        self.assertEqual(channel.commands, ['terminal length 0', 'show running-config'])
        self.connect.side_effect = AssertionError('download must be offline')
        result = self.single()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(native.encode(), result.content)

    def test_canonical_vendor_credential_collision_survives_capture_persist_download(self):
        self.device.username = 'cisco'
        self.device.device_name = 'cisco-edge'
        self.device.save()
        native = 'hostname edge\n description cisco\nend\n'
        self.shell('show running-config\n' + native + 'edge#')
        record = self.execute(self.device, 'network_device', ['config_info'])
        from net.inspections.result_storage import expanded_result_snapshot
        for source in (record.details, record.raw_output, expanded_result_snapshot(record.task_target)['details']):
            item = source['config_info']
            self.assertIn('backup_id', item)
            self.assertEqual(item['status'], 'success')
            self.assertNotIn('content', item)
            self.assertNotIn('format', item)
        result = self.single()
        self.assertEqual(result.status_code, 200)
        self.assertIn(b'hostname edge', result.content)
        self.assertNotIn('cisco', unquote(str(result.headers)))
        self.assertEqual(native.encode(), result.content)
        with ZipFile(BytesIO(self.exports().build_configuration_zip([self.device]))) as bundle:
            self.assertNotIn('cisco', ''.join(bundle.namelist()))
            self.assertNotIn(b'cisco', bundle.read('manifest.csv'))

    def test_envelope_key_and_status_credential_collisions_preserve_item_contract(self):
        Server.objects.create(ip='192.0.2.91', username='config_info', password='success', api_token='format')
        self.device.password = 'text'
        self.device.save()
        self.shell('show running-config\nhostname edge\n description config_info success format text\nend\nedge#')
        record = self.execute(self.device, 'network_device', ['config_info'])
        for source in (record.details, record.raw_output):
            self.assertIn('config_info', source)
            self.assertEqual(source['config_info']['status'], 'success')
            self.assertNotIn('format', source['config_info'])
            self.assertNotIn('content', source['config_info'])
        self.assertEqual(self.single().status_code, 200)
        with ZipFile(BytesIO(self.exports().build_configuration_zip([self.device]))) as bundle:
            rows = list(csv.DictReader(StringIO(bundle.read('manifest.csv').decode('utf-8-sig'))))
            self.assertEqual(rows[0]['status'], 'success')

    def test_h3c_chinese_alias_captures_current_configuration(self):
        self.device.vendor = '华三 H3C'
        native = '#\n version 7.1\n sysname edge\n#\nreturn\n'
        channel = self.shell('display current-configuration\n' + native + '<edge>',
                             prompt='<edge>', command='display current-configuration', paging='screen-length disable')
        result = collect_network_ssh(self.device, 2, ['config_info'])
        self.assertEqual(result.data.get('config_info', {}).get('status'), 'success')
        self.assertEqual(result.data['config_info']['content'], native)
        self.assertEqual(channel.commands[-1], 'display current-configuration')

    def test_unselected_configuration_does_not_send_configuration_commands(self):
        channel = self.shell()
        channel.responses['show processes cpu'] = 'CPU usage: 20%\nedge#'
        result = collect_network_ssh(self.device, 2, ['cpu'])
        self.assertNotIn('config_info', result.data)
        self.assertEqual(channel.commands, ['terminal length 0', 'show processes cpu'])
        collect_network_ssh(self.device, 2, [])
        self.assertNotIn('show running-config', channel.commands)

    def test_incomplete_error_echo_pagination_and_timeout_are_failed_items(self):
        for output in ('show running-config\nedge#', 'edge#',
                       'show running-config\n% Invalid input detected\nedge#',
                       'show running-config\nhostname edge\n--More--\nend\nedge#',
                       'show running-config\nhostname edge\nend\nother#',
                       'show running-config\nhostname edge\n',
                       'show running-config\nhostname edge\n[truncated]\nend\nedge#',
                       TimeoutError('ssh-pass-private')):
            with self.subTest(output=type(output).__name__):
                self.shell(output)
                result = collect_network_ssh(self.device, 1, ['config_info'])
                self.assertEqual(result.data.get('config_info', {}).get('status'), 'failed')
                self.assertNotIn('ssh-pass-private', json.dumps(result.data))

    def test_native_dahua_collection_survives_unrelated_status_failure(self):
        self.http.side_effect = requests.ConnectionError('api-pass-private')
        self.native_http.side_effect = [response()]
        record = self.execute(self.camera, 'monitor', ['status_data', 'config_info'])
        self.assertEqual(record.status, 'partial')
        self.assertTrue(record.is_reachable)
        item = record.details['config_info']
        self.assertEqual(item['status'], 'unsupported')
        self.assertNotIn('content', record.raw_output['config_info'])
        call = self.native_http.call_args
        self.assertEqual(call.args[0], 'https://192.0.2.20/cgi-bin/configManager.cgi')
        self.assertEqual(call.kwargs['params'], {'action': 'getConfig', 'name': 'Network'})
        self.assertFalse(call.kwargs['allow_redirects'])
        self.http.side_effect = AssertionError('download must be offline')
        self.native_http.side_effect = AssertionError('download must be offline')
        result = self.single(self.camera, 'monitors')
        self.assertEqual(result.status_code, 404)
        self.assertIn('完整备份'.encode(), result.content)
        archive = self.client.get('/assets/monitors/configurations.zip')
        with ZipFile(BytesIO(archive.content)) as bundle:
            self.assertEqual(bundle.namelist(), ['manifest.csv'])

    def test_config_only_dahua_uses_only_named_read_endpoint(self):
        self.native_http.side_effect = [response()]
        result = collect_security_api(self.camera, 2, ['config_info'])
        self.assertEqual(result.data.get('config_info', {}).get('status'), 'success')
        self.assertEqual(self.native_http.call_count, 1)
        self.http.assert_not_called()

    def test_unknown_security_vendor_and_binary_are_unsupported(self):
        self.camera.vendor = 'unknown'
        result = collect_security_api(self.camera, 2, ['config_info'])
        self.assertEqual(result.data.get('config_info', {}).get('status'), 'unsupported')
        self.http.assert_not_called()
        self.native_http.assert_not_called()
        self.camera.vendor = 'Dahua'
        self.native_http.side_effect = [response(b'\x00\x01secret', 'application/octet-stream')]
        result = collect_security_api(self.camera, 2, ['config_info'])
        self.assertEqual(result.data['config_info']['status'], 'unsupported')

    def test_dahua_does_not_relabel_status_or_error_or_partial_text_as_config(self):
        for reply in (response('ERROR'), response('{"device_info":{"model":"IPC"}}', 'application/json'),
                      response('table.Network.Hostname=camera\ninvalid line'), response('', status=403)):
            self.native_http.side_effect = [reply]
            result = collect_security_api(self.camera, 2, ['config_info'])
            self.assertEqual(result.data.get('config_info', {}).get('status'), 'failed')

    def test_latest_backup_ignores_legacy_inspections(self):
        self.backup(content='hostname latest\r\npassword private\r\nend\r\n')
        self.saved(self.device, status='success', details={'config_info': snapshot('hostname old\nend\n')})
        self.saved(self.device, status='partial', details={'config_info': snapshot()})
        self.saved(self.device, status='failed', details={'config_info': {'status': 'failed', 'message': 'failure'}})
        self.saved(self.device, status='success', details={'cpu': {'usage': 2}})
        result = self.exports().latest_configuration(self.device)
        self.assertEqual(result.status, 'success')
        self.assertEqual(b'hostname latest\r\npassword private\r\nend\r\n', result.content)

    def test_legacy_status_and_raw_only_evidence_never_supply_a_backup(self):
        service = self.exports()
        self.assertEqual(service.latest_configuration(self.device).status, 'missing')
        self.assertEqual(self.single().status_code, 404)
        for state in ('unsupported', 'failed'):
            self.saved(self.device, details={'config_info': {'status': state, 'message': 'private-error'}})
            self.assertEqual(service.latest_configuration(self.device).status, 'missing')
            result = self.single()
            self.assertEqual(result.status_code, 404)
            self.assertNotIn(b'private-error', result.content)
        self.saved(self.device, raw_output={'config_info': snapshot()})
        self.assertEqual(service.latest_configuration(self.device).status, 'missing')

    def test_security_partial_json_is_not_a_complete_backup(self):
        native = {'table.Network.Hostname': 'camera', 'table.Network.eth0.MTU': 1500}
        self.camera.inspections.create(details={'config_info': snapshot(native, vendor='dahua', fmt='json', scope='Network')})
        result = self.exports().latest_configuration(self.camera)
        self.assertEqual(result.status, 'missing')
        self.assertIn('完整备份', result.message)
        other = SecurityDevice.objects.create(ip='192.0.2.30', vendor='unknown')
        other.inspections.create(details={'config_info': snapshot({'status': 'online'}, vendor='unknown', fmt='json')})
        self.assertEqual(self.exports().latest_configuration(other).status, 'missing')

    def test_selected_configuration_download_returns_one_file_or_one_zip(self):
        self.backup(self.device)
        selected = Network_Device.objects.create(
            ip='192.0.2.11', device_name='selected', vendor='H3C')
        self.backup(selected, content='hostname selected\nend\n')
        unselected = Network_Device.objects.create(
            ip='192.0.2.12', device_name='unselected', vendor='Cisco')
        self.backup(unselected, content='hostname unselected\nend\n')

        single = self.client.get('/assets/networks/configurations.zip', {
            'target_ids': str(selected.pk),
            'filter_vendor': 'Cisco',
        })
        self.assertEqual(single.status_code, 200)
        self.assertEqual(single.content, b'hostname selected\nend\n')
        self.assertNotEqual(single['Content-Type'], 'application/zip')

        multiple = self.client.get('/assets/networks/configurations.zip', {
            'target_ids': f'{self.device.pk},{selected.pk}',
            'filter_vendor': 'Other',
        })
        self.assertEqual(multiple.status_code, 200)
        self.assertEqual(multiple['Content-Type'], 'application/zip')
        with ZipFile(BytesIO(multiple.content)) as bundle:
            rows = list(csv.DictReader(StringIO(bundle.read('manifest.csv').decode('utf-8-sig'))))
            self.assertEqual({row['asset_id'] for row in rows}, {str(self.device.pk), str(selected.pk)})
            self.assertNotIn(str(unselected.pk), {row['asset_id'] for row in rows})

    def test_invalid_selected_ids_never_fall_back_to_exporting_all_devices(self):
        self.backup()
        url = '/assets/networks/configurations.zip'
        for value in ('', 'not-a-uuid', f'{self.device.pk},bad', ','.join([str(self.device.pk)] * 501)):
            with self.subTest(value=value[:40]):
                reply = self.client.get(url, {'target_ids': value})
                self.assertEqual(reply.status_code, 400)
                self.assertIn('no-store', reply['Cache-Control'])
        missing = self.client.get(url, {'target_ids': '00000000-0000-0000-0000-000000000001'})
        self.assertEqual(missing.status_code, 404)
        duplicate = self.client.get(url, {'target_ids': f'{self.device.pk},{self.device.pk}'})
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(duplicate.content, NETWORK_TEXT.encode())

    def test_filtered_zip_uses_all_matching_assets_and_manifest_formula_safety(self):
        self.device.device_name = '=SUM(1,2)'
        self.device.save()
        self.device.inspections.create(details={'config_info': snapshot()})
        duplicate = Network_Device.objects.create(ip='192.0.2.11', device_name='=SUM(1,2)', vendor='Cisco')
        duplicate.inspections.create(details={'config_info': snapshot()})
        self.backup(filename='../../=SUM(1,2).cfg', scope='=ssh-pass-private')
        self.backup(duplicate, filename='../../=SUM(1,2).cfg')
        missing = Network_Device.objects.create(ip='192.0.2.12', vendor='Cisco')
        failed = Network_Device.objects.create(ip='192.0.2.13', vendor='Cisco')
        failed.inspections.create(details={'config_info': {'status': 'failed'}})
        self.backup(failed, ciphertext=b'broken')
        unknown = Network_Device.objects.create(ip='192.0.2.14', vendor='Cisco')
        unknown.inspections.create(details={'config_info': {'status': 'unsupported'}})
        Network_Device.objects.create(ip='192.0.2.99', vendor='Other')
        result = self.client.get('/assets/networks/configurations.zip', {
            'filter_vendor': 'Cisco', 'page': 2, 'page_size': 1,
            'sort': 'password', 'filter_password': 'do-not-filter'})
        self.assertEqual(result.status_code, 200)
        with ZipFile(BytesIO(result.content)) as bundle:
            rows = list(csv.DictReader(StringIO(bundle.read('manifest.csv').decode('utf-8-sig'))))
            self.assertEqual(len(rows), 5)
            self.assertEqual({row['status'] for row in rows}, {'success', 'missing', 'failed'})
            self.assertEqual(len(bundle.namelist()), 3)
            self.assertEqual(len({name.casefold() for name in bundle.namelist()}), 3)
            self.assertNotIn(b'ssh-pass-private', bundle.read('manifest.csv'))
            self.assertTrue(all('/' not in name and '..' not in name for name in bundle.namelist()))
            for row in rows:
                if row['status'] == 'success':
                    self.assertEqual(bundle.read(row['filename']), NETWORK_TEXT.encode())
            self.assertTrue(all(row['name'].startswith("'=") for row in rows if row['status'] == 'success'))
            self.assertEqual({row['asset_id'] for row in rows}, {str(x.pk) for x in (self.device, duplicate, missing, failed, unknown)})

    @override_settings(SECRET_KEY='application-signing-private', EMAIL_HOST_PASSWORD='smtp-setting-private')
    def test_credentials_stay_in_original_downloads_but_not_records_or_manifest(self):
        # The overridden signing key invalidates the session created in setUp.
        from tests.auth import login_admin
        login_admin(self.client)
        Server.objects.create(ip='192.0.2.40', username='server-user-private', password='server-pass-private', api_token='server-token-private')
        Domain_Controller_Config.objects.create(bind_username='ldap-user-private', bind_password='ldap-pass-private')
        PeopleSyncSource.objects.create(name='source', source_key='source', source_type='feishu',
                                        credentials={'app_id': 'app-id-private', 'app_secret': 'app-secret-private'})
        AlertChannel.objects.create(name='webhook', channel_type='feishu', settings={
            'webhook_url': 'https://example.invalid/hook/webhook-private', 'secret': 'signing-private'})
        secrets = ('ssh-user-private', 'ssh-pass-private', 'api-user-private', 'api-pass-private',
                   'api-token-private', 'URL-token-private', 'server-user-private', 'server-pass-private',
                   'server-token-private', 'ldap-user-private', 'ldap-pass-private', 'app-id-private',
                   'app-secret-private', 'https://example.invalid/hook/webhook-private', 'signing-private',
                   'application-signing-private', 'smtp-setting-private', 'native-password-private')
        native = 'hostname edge\n' + ''.join(f' description {value}\n' for value in secrets[:-1])
        native += 'username local secret 9 native-password-private\nend\n'
        self.shell('show running-config\n' + native + 'edge#')
        record = self.execute(self.device, 'network_device', ['config_info'])
        self.assertEqual(record.details.get('config_info', {}).get('status'), 'success')
        saved = json.dumps([record.details, record.raw_output, record.summary, record.task_target.result_snapshot])
        self.device.device_name = '../../ssh-pass-private/app-secret-private'
        self.device.save()
        single = self.single()
        self.assertEqual(single.status_code, 200)
        blob = self.exports().build_configuration_zip([self.device, self.camera])
        with ZipFile(BytesIO(blob)) as bundle:
            self.assertEqual(single.content, native.encode())
            rows = list(csv.DictReader(StringIO(bundle.read('manifest.csv').decode('utf-8-sig'))))
            self.assertEqual(bundle.read(rows[0]['filename']), native.encode())
            surfaces = saved + unquote(str(single.headers))
            surfaces += '\n'.join(bundle.namelist()) + bundle.read('manifest.csv').decode('utf-8-sig')
            for value in secrets:
                self.assertNotIn(value, surfaces)
            for name in bundle.namelist():
                self.assertNotIn('/', name)
                self.assertNotIn('..', name)

    def test_ui_exposes_scoped_downloads_without_changing_other_asset_actions(self):
        for kind, asset in (('networks', self.device), ('monitors', self.camera)):
            result = self.client.get(f'/assets/{kind}/', {'filter_vendor': asset.vendor})
            html = result.content.decode()
            self.assertContains(result, f'href="/assets/{kind}/configurations.zip"')
            self.assertContains(result, 'data-selected-config-export')
            self.assertContains(result, '请先选择设备')
            self.assertNotContains(result, '导出筛选设备配置 ZIP')
            self.assertNotContains(result, '配置下载仅使用最近成功的已保存配置项')
            self.assertNotContains(result, f'/assets/{kind}/{asset.pk}/configuration/')
            self.assertLess(html.index('导出筛选结果'), html.index('data-selected-config-export'))
            detail = self.client.get(f'/assets/{kind}/{asset.pk}/')
            self.assertContains(detail, f'/assets/{kind}/{asset.pk}/configuration/')
        server_page = self.client.get('/assets/servers/')
        self.assertNotContains(server_page, 'configurations.zip')
        self.assertNotContains(server_page, 'data-selected-config-export')
        self.assertEqual(self.client.get('/assets/servers/configurations.zip').status_code, 404)

    def test_device_selection_is_first_column_with_current_page_bulk_actions(self):
        result = self.client.get('/assets/networks/')
        html = result.content.decode()
        device_table = html[html.index('<div data-table-workspace'):]
        header = device_table[device_table.index('<thead'):device_table.index('</thead>')]

        self.assertContains(result, 'data-device-selection-column')
        self.assertContains(result, 'data-device-select-all')
        self.assertContains(result, 'data-device-select-invert')
        self.assertContains(result, 'data-configuration-export-feedback')
        self.assertLess(
            header.index('data-device-selection-column'),
            header.index('data-column-key'),
        )
        self.assertLess(html.index('data-device-select-all'), html.index('data-selected-config-export'))
        for kind in ('servers', 'monitors'):
            with self.subTest(kind=kind):
                page = self.client.get(f'/assets/{kind}/')
                self.assertContains(page, 'data-device-selection-column')
                self.assertContains(page, 'data-device-select-all')

    def test_unknown_network_vendor_never_opens_a_config_connection(self):
        self.device.vendor = 'notcisco'
        result = collect_network_ssh(self.device, 1, ['config_info'])
        self.assertEqual(result.data['config_info']['status'], 'unsupported')
        self.connect.assert_not_called()

    def test_strict_ssh_bounds_and_paging_failure_never_save_success(self):
        for output in ('show running-config\nhostname edge\n' + 'x' * 256 + '\nend\nedge#',
                       b'show running-config\nhostname edge\xff\nend\nedge#'):
            with self.subTest(output=type(output).__name__), patch('net.infrastructure.ssh_collectors.MAX_CONFIG_BYTES', 200):
                self.shell(output)
                result = collect_network_ssh(self.device, 2, ['config_info'])
                self.assertEqual(result.data['config_info']['status'], 'failed')
                self.assertNotIn('content', result.raw['config_info'])
        channel = self.shell()
        channel.responses['terminal length 0'] = '% Invalid input\nedge#'
        result = collect_network_ssh(self.device, 2, ['config_info'])
        self.assertEqual(result.data['config_info']['status'], 'failed')
        self.assertNotIn('show running-config', channel.commands)

    def test_prompt_like_config_line_is_not_a_terminal_prompt_and_split_utf8_is_preserved(self):
        native = 'hostname edge\n description 核心#\nend\n'
        encoded = ('show running-config\n' + native + 'edge#').encode()
        cut = encoded.index('核'.encode()) + 1
        self.shell([encoded[:cut], encoded[cut:]])
        result = collect_network_ssh(self.device, 2, ['config_info'])
        self.assertEqual(result.data['config_info']['status'], 'success')
        self.assertEqual(result.data['config_info']['content'], native)

    def test_http_truncation_size_timeout_and_redirect_are_rejected(self):
        truncated = response()
        truncated.headers['content-length'] = str(len(truncated.body) + 1)
        interrupted = response()
        async def chunks(chunk_size):
            yield b'table.Network.Hostname=camera\n'
            raise TimeoutError('api-pass-private')
        interrupted.iter_chunked = chunks
        for reply in (truncated, interrupted, response(status=302), response('table.Network.Hostname=' + 'x' * 300)):
            with self.subTest(status=reply.status), patch('net.data_exchange.adapters.MAX_CONFIG_BYTES', 200):
                self.native_http.side_effect = [reply]
                result = collect_security_api(self.camera, 2, ['config_info'])
                self.assertEqual(result.data['config_info']['status'], 'failed')
                self.assertNotIn('content', result.raw['config_info'])

    def test_native_total_deadline_rejects_late_eof(self):
        reply = response()
        async def chunks(chunk_size):
            await asyncio.sleep(.5)
            yield DAHUA_TEXT.encode()
            await asyncio.sleep(180)
        reply.iter_chunked = chunks
        self.assert_native_cancelled(reply)

    def test_native_total_deadline_interrupts_slow_progress_before_chunk_completion(self):
        reply = response()
        async def chunks(chunk_size):
            for part in DAHUA_TEXT.encode():
                await asyncio.sleep(.25)
                yield bytes([part])
        reply.iter_chunked = chunks
        self.assert_native_cancelled(reply)

    def assert_native_cancelled(self, reply):
        loop = VirtualLoop()
        self.native_http.side_effect = [reply]
        with patch('asyncio.SelectorEventLoop', return_value=loop):
            result = collect_native_configuration(self.camera, 1)
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('content', result)
        self.assertEqual(loop.now, 1.0)
        self.assertTrue(reply.closed)
        self.assertFalse(asyncio.all_tasks(loop))
        self.assertTrue(loop.is_closed())

    def test_native_completion_rechecks_deadline_even_without_an_await_at_eof(self):
        reply = response()
        async def chunks(chunk_size):
            yield DAHUA_TEXT.encode()
            asyncio.get_running_loop().now = 1.0
        reply.iter_chunked = chunks
        self.assert_native_cancelled(reply)

    def test_network_config_item_survives_later_status_command_failure(self):
        channel = self.shell()
        channel.responses['show processes cpu'] = TimeoutError('ssh-pass-private')
        record = self.execute(self.device, 'network_device', ['config_info', 'cpu'])
        self.assertEqual(record.status, 'partial')
        self.assertEqual(record.details['config_info']['status'], 'success')
        self.assertEqual(self.single().status_code, 200)

    def test_legacy_success_and_malformed_items_never_supply_a_backup(self):
        good = self.saved(self.device, details={'config_info': snapshot()})
        for item in (snapshot(''), snapshot('hostname edge\n--More--\nend\n'),
                     {**snapshot(), 'complete': False}, {**snapshot(), 'format': 'binary'},
                     {'status': 'success', 'format': 'text', 'vendor': ['cisco']}):
            self.saved(self.device, details={'config_info': item})
            self.assertEqual(self.exports().latest_configuration(self.device).status, 'missing')
        good.delete()
        self.saved(self.device, details={'config_info': {'status': 'failed'}}, raw_output={'config_info': snapshot()})
        self.assertEqual(self.exports().latest_configuration(self.device).status, 'missing')

    def test_failed_attempt_without_item_has_failed_evidence_and_no_secret_error(self):
        # Missing credentials / pre-collector failures still represent selected
        # config attempts, rather than silently masquerading as never collected.
        self.device.password = ''
        self.device.save()
        record = self.execute(self.device, 'network_device', ['config_info'])
        self.assertEqual(record.details.get('config_info', {}).get('status'), 'failed')
        self.assertEqual(self.exports().latest_configuration(self.device).status, 'missing')

    def test_registered_json_keys_that_contain_credentials_are_redacted_without_losing_scope(self):
        native = {'table.Network.Hostname': 'camera', 'table.Network.Password': 'native-json-private'}
        from net.infrastructure.sanitization import sanitize_configuration
        result = sanitize_configuration(native)
        self.assertNotIn('native-json-private', json.dumps(result))
        self.assertEqual(result['table.Network.Hostname'], 'camera')

    def test_native_cli_and_url_credentials_are_removed_but_nonsecret_configuration_stays(self):
        native = ('hostname edge\n snmp-server community community-private ro\n'
                  ' enable secret 9 enable-private\n password cipher cli-private\n'
                  ' description https://example.invalid/api?token=token-private&ok=1\n'
                  ' description https://example.invalid/path username=user-private password=pwd-private\n'
                  'end\n')
        from net.infrastructure.sanitization import sanitize_configuration
        content = sanitize_configuration(native).encode()
        for secret in ('community-private', 'enable-private', 'cli-private', 'token-private', 'pwd-private'):
            self.assertNotIn(secret.encode(), content)
        self.assertIn(b'hostname edge', content)
        self.assertNotIn(b'ok=1', content)  # All opaque query values are omitted.

    def test_empty_zip_has_manifest_and_views_reject_post(self):
        result = self.client.get('/assets/networks/configurations.zip', {'q': 'no matching assets'})
        self.assertEqual(result.status_code, 200)
        with ZipFile(BytesIO(result.content)) as bundle:
            self.assertEqual(bundle.namelist(), ['manifest.csv'])
            self.assertEqual(len(bundle.read('manifest.csv').decode('utf-8-sig').splitlines()), 1)
        self.assertEqual(self.client.post(f'/assets/networks/{self.device.pk}/configuration/').status_code, 405)
        self.assertEqual(self.client.post('/assets/networks/configurations.zip').status_code, 405)

    def test_unexpected_executor_config_failure_scrubs_other_application_credentials(self):
        Server.objects.create(ip='192.0.2.60', password='unrelated-app-private')
        profile = InspectionProfile.objects.create(name='failure', device_type='network_device', selected_items=['config_info'])
        enqueue_task(profile, [self.device.pk], 'manual')
        task = claim_next_task('failure-worker', 60)
        target = task.target_runs.get()
        _begin_target(target.pk, 'failure-worker')
        persist_execution_failure(target, worker_id='failure-worker', error='opaque unrelated-app-private')
        record = self.device.inspections.get()
        self.assertNotIn('unrelated-app-private', record.summary)
        self.assertNotIn('unrelated-app-private', json.dumps(record.task_target.result_snapshot))

    def test_original_bytes_and_media_type_are_not_wrapped_or_reencoded(self):
        from types import SimpleNamespace
        for raw, media in ((b'\xff\x00password secret\r\n', 'application/octet-stream'),
                           (b'{ "password": "secret" }\r\n', 'application/json')):
            # Exercise formats that storage may support in future without
            # weakening its current native configuration validation.
            backup = SimpleNamespace(filename='native.cfg', media_type=media, scope='running-config')
            with self.subTest(media=media), patch(
                    'net.devices.configuration_backups.latest_configuration_backup', return_value=backup), patch(
                    'net.devices.configuration_backups.read_configuration_backup', return_value=raw):
                reply = self.single()
                self.assertEqual(reply.status_code, 200)
                self.assertEqual(reply.content, raw)
                self.assertEqual(reply['Content-Type'], media)

    def test_decryption_integrity_and_missing_key_fail_safely_without_fallback(self):
        from net.devices.configuration_backups import store_configuration_backup
        self.saved(self.device, details={'config_info': snapshot()})
        self.backup()
        backup = store_configuration_backup(self.device, snapshot('hostname latest\nend\n'),
                                            captured_at=timezone.now() + timedelta(days=1))
        original_ciphertext, original_sha256 = backup.ciphertext, backup.sha256
        for fields in ({'ciphertext': b'private ciphertext'}, {'sha256': '0' * 64}):
            with self.subTest(fields=fields):
                backup.ciphertext, backup.sha256 = original_ciphertext, original_sha256
                for key, value in fields.items():
                    setattr(backup, key, value)
                backup.save(update_fields=['ciphertext', 'sha256'])
                result = self.exports().latest_configuration(self.device)
                self.assertEqual(result.status, 'failed')
                self.assertEqual(result.content, b'')
                reply = self.single()
                self.assertEqual(reply.status_code, 409)
                self.assertNotIn(b'private', reply.content)
                with ZipFile(BytesIO(self.exports().build_configuration_zip([self.device]))) as bundle:
                    self.assertEqual(bundle.namelist(), ['manifest.csv'])
                    self.assertNotIn(b'private', bundle.read('manifest.csv'))
        with override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=''):
            self.assertEqual(self.exports().latest_configuration(self.device).status, 'failed')

    def test_zip_contains_only_latest_backup_and_reserves_manifest_filename(self):
        from net.devices.configuration_backups import store_configuration_backup
        self.backup(filename='manifest.csv')
        newer = store_configuration_backup(self.device, snapshot('hostname newer\r\nend\r\n'),
                                           captured_at=timezone.now() + timedelta(days=1))
        newer.filename = 'manifest.csv'
        newer.save(update_fields=['filename'])
        with ZipFile(BytesIO(self.exports().build_configuration_zip([self.device]))) as bundle:
            self.assertEqual(set(bundle.namelist()), {'manifest.csv', 'manifest-2.csv'})
            self.assertEqual(bundle.read('manifest-2.csv'), b'hostname newer\r\nend\r\n')

    def test_views_enforce_admin_get_and_no_store_without_middleware(self):
        from django.contrib.auth.models import AnonymousUser
        from index.devices.configuration import configuration_download, configuration_zip
        from tests.auth import login_admin, login_reader
        admin = login_admin(self.client)
        reader = login_reader(self.client)
        self.backup()
        for view, args in ((configuration_download, ('networks', self.device.pk)),
                           (configuration_zip, ('networks',))):
            for user, method, status in ((AnonymousUser(), 'get', 302), (reader, 'get', 403),
                                         (admin, 'post', 405), (admin, 'get', 200)):
                with self.subTest(view=view.__name__, status=status):
                    request = getattr(RequestFactory(), method)('/', {'target_ids': str(self.device.pk)})
                    request.user = user
                    reply = view(request, *args)
                    self.assertEqual(reply.status_code, status)
                    self.assertIn('no-store', reply.get('Cache-Control', ''))
