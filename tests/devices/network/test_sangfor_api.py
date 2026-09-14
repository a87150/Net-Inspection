from hashlib import md5
from uuid import UUID
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase


class SangforApiCollectorTests(SimpleTestCase):
    def device(self):
        return SimpleNamespace(
            api_url='https://ac.example.invalid:9999/',
            api_shared_secret='shared-secret', verify_ssl=False,
        )

    @patch('net.devices.network.sangfor.uuid.uuid4', return_value=UUID(hex='a' * 32))
    def test_collects_documented_status_endpoints_with_signed_requests(self, _uuid):
        from net.devices.network.sangfor import collect_sangfor_ac

        payloads = {
            'version': 'AC12.0.9', 'online-user': 3, 'session-num': 9,
            'cpu-usage': 20, 'mem-usage': 30, 'disk-usage': 40,
            'sys-time': '2026-09-14 12:00:00', 'bandwidth-usage': 50, 'throughput': {'send': 1, 'recv': 2, 'unit': 'bytes'},
            'log': {'block': 2, 'record': 4}, 'insidelib': [],
        }
        seen = []
        def request(method, url, **kwargs):
            seen.append((method, url, kwargs))
            endpoint = url.split('/v1/status/', 1)[1].split('?', 1)[0]
            return {'code': 0, 'message': 'Successfully', 'data': payloads[endpoint]}

        result = collect_sangfor_ac(self.device(), selected_items=None, request=request)

        self.assertEqual(result.status, 'success')
        self.assertEqual(result.data['cpu']['usage_percent'], 20)
        self.assertEqual(result.data['memory']['usage_percent'], 30)
        self.assertEqual(result.data['disk_usage']['usage_percent'], 40)
        self.assertEqual(result.data['device_info']['version'], 'AC12.0.9')
        self.assertEqual(result.data['bandwidth_usage']['usage_percent'], 50)
        self.assertEqual(result.data['throughput']['recv'], 2)
        post = [row for row in seen if '/throughput?' in row[1]][0]
        self.assertEqual(post[0], 'POST')
        self.assertIn('_method=GET', post[1])
        signature = md5(('shared-secret' + 'a' * 32).encode()).hexdigest()
        self.assertTrue(all('random=' + 'a' * 32 in url and 'md5=' + signature in url for method, url, _ in seen if method == 'GET'))
        self.assertTrue(all(kwargs['verify'] is False for _, _, kwargs in seen))
        self.assertEqual(post[2]['json']['random'], str(UUID(hex='a' * 32).int))
        self.assertEqual(post[2]['json']['md5'], md5(('shared-secret' + str(UUID(hex='a' * 32).int)).encode()).hexdigest())
        self.assertNotIn('random=', post[1])

    def test_marks_undocumented_network_items_unsupported_without_requesting_them(self):
        from net.devices.network.sangfor import collect_sangfor_ac

        requested = []
        result = collect_sangfor_ac(self.device(), selected_items=['temperature', 'interface_status'], request=lambda *_a, **_k: requested.append(1))

        self.assertEqual(requested, [])
        self.assertEqual(result.status, 'partial')
        self.assertEqual(result.data['temperature']['status'], 'unsupported')
        self.assertEqual(result.data['interface_status']['status'], 'unsupported')


class SangforApiConfigurationTests(TestCase):
    def test_api_connection_requires_endpoint_and_secret(self):
        from django.core.exceptions import ValidationError
        from net.models import Network_Device
        device = Network_Device(ip='192.0.2.88', connection_type='sangfor_api')
        with self.assertRaises(ValidationError) as raised:
            device.full_clean()
        self.assertIn('api_url', raised.exception.message_dict)
        self.assertIn('api_shared_secret', raised.exception.message_dict)

    def test_sangfor_template_matches_gateway_and_excludes_wireless_items(self):
        from net.devices.collection_profiles import resolve_collection_settings
        from net.models import DeviceCollectionTemplate, Network_Device
        device = Network_Device.objects.create(
            ip='192.0.2.89', vendor='sangfor', device_type='ac_gateway',
            connection_type='sangfor_api', api_url='https://ac.example.invalid:9999',
            api_shared_secret='secret',
        )
        settings = resolve_collection_settings('networks', device)
        self.assertEqual(settings['thresholds']['disk_usage'], 90)
        self.assertIn('throughput', settings['selected_items'])
        self.assertNotIn('wireless_aps', settings['selected_items'])
        self.assertNotIn('config_info', settings['selected_items'])
        self.assertEqual(DeviceCollectionTemplate.objects.filter(kind='networks', vendor='sangfor').count(), 2)

    def test_gateway_template_ui_saves_api_item_rules_and_queue_runs_selected_endpoint(self):
        """A Sangfor gateway must expose API-only items and retain rules through enqueue."""
        from django.urls import reverse
        from net.models import DeviceCollectionTemplate, InspectionProfile, Network_Device, TaskRun
        from net.inspections.queue import enqueue_task
        from tests.auth import login_admin

        device = Network_Device.objects.create(
            ip='192.0.2.90', vendor='sangfor', device_type='ac_gateway',
            connection_type='sangfor_api', api_url='https://ac.example.invalid:9999',
            api_shared_secret='secret',
        )
        login_admin(self.client)
        url = reverse('collection_templates', args=['networks'])
        template = DeviceCollectionTemplate.objects.get(kind='networks', vendor='sangfor', subtype='ac_gateway')
        response = self.client.get(url + '?edit=' + str(template.pk))
        self.assertContains(response, 'AC Open API')
        self.assertContains(response, '/v1/status/throughput')
        self.assertNotContains(response, 'SSH 命令')
        self.assertNotContains(response, 'SNMP 取值与解析')
        self.assertNotContains(response, '从内置命令新建')
        self.assertNotContains(response, '无线 AP 状态')
        base = DeviceCollectionTemplate.objects.get(kind='networks', vendor='sangfor', subtype='')
        base_response = self.client.get(url + '?edit=' + str(base.pk))
        self.assertContains(base_response, '/v1/status/cpu-usage')
        self.assertNotContains(base_response, 'SSH 命令')
        ordinary_response = self.client.get(url + '?builtin=huawei')
        self.assertContains(ordinary_response, '从内置命令新建')
        self.assertNotContains(ordinary_response, '/v1/status/')
        self.assertNotContains(ordinary_response, '内置库')

        response = self.client.post(url + '?edit=' + str(template.pk), {
            'name': template.name, 'vendor': 'sangfor', 'subtype': 'ac_gateway',
            'parent': str(template.parent_id), 'is_enabled': 'on',
            'version': template.updated_at.isoformat(), 'rule_mode_throughput': 'custom',
            'enabled_throughput': 'yes', 'threshold_bandwidth_usage': '75',
        })
        self.assertContains(response, '已保存')
        template.refresh_from_db()
        self.assertTrue(template.settings['item_enabled']['throughput'])
        self.assertEqual(template.settings['thresholds']['bandwidth_usage'], 75)

        profile = InspectionProfile.objects.create(
            name='Sangfor throughput', device_type='network_device', selected_items=['throughput'],
        )
        task = enqueue_task(profile, [str(device.pk)], TaskRun.Source.MANUAL)
        target_snapshot = task.target_runs.get().target_snapshot
        snapshot = target_snapshot['collection_settings']
        self.assertIn('throughput', snapshot['selected_items'])
        self.assertEqual(snapshot['thresholds']['bandwidth_usage'], 75)
        self.assertEqual(target_snapshot['api_url'], 'https://ac.example.invalid:9999')
        self.assertTrue(target_snapshot['verify_ssl'])
        self.assertNotIn('api_shared_secret', target_snapshot)

        from net.inspections.queue import claim_next_task
        from net.inspections.executor import execute_target
        from net.models import Network_Device_Inspection
        seen = []
        def api_request(method, request_url, **kwargs):
            seen.append((method, request_url, kwargs))
            return {'code': 0, 'data': {'send': 1024, 'recv': 2048, 'unit': 'bytes', 'diagnostic': device.api_shared_secret}}
        claimed = claim_next_task('sangfor-api-template-test', 30)
        with patch('requests.request', side_effect=api_request):
            execute_target(claimed.target_runs.get(), worker_id='sangfor-api-template-test')
        inspection = Network_Device_Inspection.objects.get()
        self.assertEqual(inspection.details['throughput']['recv'], 2048)
        persisted = str([inspection.details, inspection.raw_output, task.target_runs.get().result_snapshot])
        self.assertNotIn(device.api_shared_secret, persisted)
        self.assertEqual(len(seen), 1)
        self.assertIn('/v1/status/throughput?', seen[0][1])
        self.assertEqual(seen[0][0], 'POST')

    def test_profile_form_submits_all_documented_api_items_and_persists_evidence(self):
        """The real profile form must accept every documented Sangfor API item."""
        from index.inspections.forms import InspectionProfileConfigForm
        from net.devices.network.sangfor import DEFAULT_ITEMS
        from net.inspections.executor import execute_target
        from net.inspections.queue import claim_next_task, enqueue_task
        from net.models import InspectionProfile, Network_Device, Network_Device_Inspection, TaskRun

        device = Network_Device.objects.create(
            ip='192.0.2.91', vendor='sangfor', device_type='ac_gateway',
            connection_type='sangfor_api', api_url='https://ac.example.invalid:9999',
            api_shared_secret='profile-secret',
        )
        form = InspectionProfileConfigForm({
            'name': 'All Sangfor API status', 'selected_items': list(DEFAULT_ITEMS),
            'timeout_seconds': '30', 'concurrent_workers': '1', 'target_rule_mode': 'selected',
            'target_rule_ids': [str(device.pk)],
        }, device_type=InspectionProfile.DeviceType.NETWORK_DEVICE)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(set(DEFAULT_ITEMS).issubset(dict(form.fields['selected_items'].choices)))
        profile = InspectionProfile(**form.profile_values())
        profile.full_clean()
        profile.save()
        task = enqueue_task(profile, [str(device.pk)], TaskRun.Source.MANUAL)
        self.assertEqual(set(task.selected_items_snapshot), set(DEFAULT_ITEMS))

        payloads = {
            'version': 'AC12.0.9', 'online-user': 12, 'session-num': 24,
            'insidelib': [], 'log': {'block': 2, 'record': 10}, 'cpu-usage': 95,
            'mem-usage': 30, 'disk-usage': 40, 'sys-time': '2026-09-14 12:00:00',
            'bandwidth-usage': 50, 'throughput': {'send': 1024, 'recv': 2048, 'unit': 'bytes'},
        }
        requested = []
        def api_request(method, request_url, **kwargs):
            endpoint = request_url.split('/v1/status/', 1)[1].split('?', 1)[0]
            requested.append((method, endpoint))
            return {'code': 0, 'data': payloads[endpoint]}

        claimed = claim_next_task('sangfor-profile-form-test', 30)
        with patch('requests.request', side_effect=api_request):
            execute_target(claimed.target_runs.get(), worker_id='sangfor-profile-form-test')
        inspection = Network_Device_Inspection.objects.get()
        self.assertEqual(inspection.status, 'success')
        self.assertEqual(set(inspection.details) - {'issue_findings', 'normal_issue_items'}, set(DEFAULT_ITEMS))
        self.assertEqual({endpoint for _method, endpoint in requested}, set(payloads))
        self.assertEqual(inspection.details['throughput']['recv'], 2048)
        self.assertTrue(any(finding['analysis_item'] == 'cpu' for finding in inspection.details['issue_findings']))
        self.assertEqual(set(inspection.raw_output), {'api:' + endpoint for endpoint in payloads})

    def test_mixed_network_task_keeps_configuration_backup_off_api_target(self):
        from index.inspections.forms import InspectionProfileConfigForm
        from net.inspections.executor import _device_task
        from net.inspections.queue import enqueue_task
        from net.models import InspectionProfile, Network_Device, TaskRun

        api = Network_Device.objects.create(
            ip='192.0.2.92', vendor='sangfor', device_type='ac_gateway',
            connection_type='sangfor_api', api_url='https://ac.example.invalid:9999',
            api_shared_secret='mixed-secret',
        )
        ssh = Network_Device.objects.create(
            ip='192.0.2.93', vendor='huawei', device_type='switch',
            connection_type='ssh', username='reader', password='secret',
        )
        form = InspectionProfileConfigForm({
            'name': 'Mixed network profile', 'selected_items': ['cpu'],
            'timeout_seconds': '30', 'concurrent_workers': '1', 'target_rule_mode': 'selected',
            'target_rule_ids': [str(api.pk), str(ssh.pk)],
        }, device_type=InspectionProfile.DeviceType.NETWORK_DEVICE)
        self.assertTrue(form.is_valid(), form.errors)
        profile = InspectionProfile(**form.profile_values())
        profile.full_clean()
        profile.save()
        task = enqueue_task(profile, [str(api.pk), str(ssh.pk)], TaskRun.Source.MANUAL)
        targets = {target.target_snapshot['connection_type']: target for target in task.target_runs.all()}
        self.assertIn('config_info', task.selected_items_snapshot)
        self.assertNotIn('config_info', _device_task(targets['sangfor_api'], task).selected_items_snapshot)
        self.assertIn('config_info', _device_task(targets['ssh'], task).selected_items_snapshot)


class SangforAdminSecretTests(TestCase):
    def test_admin_masks_and_preserves_api_shared_secret(self):
        from net.admin.assets import NetworkDeviceAdminForm
        from net.models import Network_Device
        from net.secret_masks import MASKED_SECRET
        device = Network_Device.objects.create(
            ip='192.0.2.97', connection_type='sangfor_api',
            api_url='https://fixture.invalid:9999', api_shared_secret='admin-private-key')
        display = NetworkDeviceAdminForm(instance=device)
        self.assertNotIn('admin-private-key', str(display['api_shared_secret']))
        self.assertEqual(display.initial['api_shared_secret'], MASKED_SECRET)
        values = display.initial.copy()
        values['api_shared_secret'] = MASKED_SECRET
        bound = NetworkDeviceAdminForm(values, instance=device)
        self.assertTrue(bound.is_valid(), bound.errors)
        bound.save()
        device.refresh_from_db()
        self.assertEqual(device.api_shared_secret, 'admin-private-key')
