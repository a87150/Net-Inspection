from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from net.models import DeviceCollectionTemplate, DeviceCollectionBinding, Network_Device, InspectionProfile, TaskRun
from net.devices.collection_profiles import resolve_collection_settings
from net.devices.inventory import refresh_asset_inventory


class NetworkVersionTemplateTests(TestCase):
    def setUp(self):
        self.base = DeviceCollectionTemplate.objects.create(name='Fixture base', kind='networks', vendor='huawei', settings={'thresholds': {'cpu': 80, 'memory': 85}})
        self.device_type = DeviceCollectionTemplate.objects.create(name='Fixture switch', kind='networks', vendor='huawei', subtype='switch', parent=self.base, settings={'thresholds': {'cpu': 75}})
        self.device = Network_Device.objects.create(ip='192.0.2.71', vendor='huawei', device_type='switch', connection_type='ssh', os_version='VRP V200R019C10', username='fixture', password='fixture-password')

    def version(self, keyword='V200R019', **extra):
        row = DeviceCollectionTemplate(name='Fixture version '+keyword, kind='networks', vendor='huawei', subtype='switch', parent=self.device_type, version_match=keyword, settings={'thresholds': {'cpu': 65}}, **extra)
        row.full_clean()
        row.save()
        return row

    def test_three_template_layers_and_device_override(self):
        version = self.version()
        result = resolve_collection_settings('networks', self.device)
        self.assertEqual(result['thresholds'], {'cpu': 65, 'memory': 85})
        self.assertEqual(result['_templates'], [str(self.base.pk), str(self.device_type.pk), str(version.pk)])
        DeviceCollectionBinding.objects.create(kind='networks', target_id=self.device.pk, overrides={'thresholds': {'cpu': 50}})
        self.assertEqual(resolve_collection_settings('networks', self.device)['thresholds']['cpu'], 50)

    def test_unknown_or_unmatched_version_keeps_type_template(self):
        self.version()
        for value in ('', 'VRP V200R020'):
            self.device.os_version=value
            result=resolve_collection_settings('networks', self.device)
            self.assertEqual(result['thresholds']['cpu'], 75)

    def test_more_specific_keyword_wins_and_number_boundary_is_respected(self):
        self.version('V200R0')
        self.version('V200R019')
        self.assertEqual(resolve_collection_settings('networks', self.device)['thresholds']['cpu'], 65)
        self.device.os_version='VRP V200R0199'
        self.assertEqual(resolve_collection_settings('networks', self.device)['thresholds']['cpu'], 75)

    def test_equal_specificity_conflict_falls_back_with_explanation(self):
        self.version('release1')
        self.version('release2')
        self.device.os_version='release1 release2'
        result=resolve_collection_settings('networks', self.device)
        self.assertEqual(result['thresholds']['cpu'], 75)
        self.assertEqual(result['_version_match']['status'], 'ambiguous')

    def test_explicit_template_does_not_auto_add_version_layer(self):
        self.version()
        DeviceCollectionBinding.objects.create(kind='networks',target_id=self.device.pk,template=self.device_type)
        self.assertEqual(resolve_collection_settings('networks',self.device)['thresholds']['cpu'],75)

    def test_version_requires_same_type_parent_and_no_fourth_template_layer(self):
        version=self.version()
        for parent, kind, subtype in ((self.base,'networks','switch'),(version,'networks','switch'),(self.device_type,'servers','switch'),(self.device_type,'networks','router')):
            candidate=DeviceCollectionTemplate(name='Invalid',kind=kind,vendor='huawei',subtype=subtype,parent=parent,version_match='Other')
            with self.subTest(kind=kind, subtype=subtype, parent=parent.name), self.assertRaises(ValidationError):candidate.full_clean()
        self.device_type.version_match='changed'
        with self.assertRaises(ValidationError):self.device_type.full_clean()

    def test_duplicate_normalized_scope_is_rejected(self):
        self.version(' V200R019 ')
        with self.assertRaises(ValidationError):self.version('v200r019')

    def test_disabled_version_does_not_replace_type(self):
        self.version(is_enabled=False)
        self.assertEqual(resolve_collection_settings('networks',self.device)['thresholds']['cpu'],75)

    def test_selected_version_and_device_version_are_frozen_when_enqueued(self):
        from net.inspections.queue import enqueue_task
        version=self.version()
        profile=InspectionProfile.objects.create(name='Fixture inspection',device_type='network_device',selected_items=['cpu'])
        task=enqueue_task(profile,[str(self.device.pk)],TaskRun.Source.MANUAL)
        snapshot=task.target_runs.get().target_snapshot
        self.assertEqual(snapshot['os_version'],'VRP V200R019C10')
        self.assertIn(str(version.pk),snapshot['collection_settings']['_templates'])
        self.device.os_version='V300R001';self.device.save(update_fields=['os_version'])
        version.settings={'thresholds':{'cpu':30}};version.save()
        self.assertEqual(task.target_runs.get().target_snapshot['collection_settings']['thresholds']['cpu'],65)

    def test_inventory_uses_api_ssh_and_snmp_and_preserves_on_failure(self):
        for key in ('version','version_output','description'):
            refresh_asset_inventory(self.device, {'device_info':{key:'Fixture OS Version 7.1'}})
            self.device.refresh_from_db()
            self.assertEqual(self.device.os_version,'Fixture OS Version 7.1')
        for info in ({'status':'failed','description':'request failed'},{'version':''},{}):
            refresh_asset_inventory(self.device, {'device_info':info})
            self.device.refresh_from_db()
            self.assertEqual(self.device.os_version,'Fixture OS Version 7.1')

    def test_admin_can_create_version_in_existing_modal(self):
        self.client.force_login(get_user_model().objects.create_user('version-admin',is_staff=True))
        count=DeviceCollectionTemplate.objects.count()
        url=reverse('collection_templates',args=['networks'])
        response=self.client.get(url)
        self.assertContains(response,'版本匹配关键字')
        self.assertEqual(DeviceCollectionTemplate.objects.count(),count)
        response=self.client.post(url,{'name':'Custom version','vendor':'huawei','subtype':'switch','parent':str(self.device_type.pk),'version_match':'V200R019','is_enabled':'on','rule_mode_cpu':'custom','threshold_cpu':'60'})
        self.assertContains(response,'已保存')
        self.assertEqual(DeviceCollectionTemplate.objects.count(),count+1)
        self.assertEqual(resolve_collection_settings('networks',self.device)['thresholds']['cpu'],60)
        self.assertContains(response,'data-rule-filter',html=False)

    def test_nonadmin_cannot_create_template(self):
        self.client.force_login(get_user_model().objects.create_user('version-viewer'))
        response=self.client.post(reverse('collection_templates',args=['networks']),{'name':'Forbidden'})
        self.assertIn(response.status_code,(302,403))
        self.assertFalse(DeviceCollectionTemplate.objects.filter(name='Forbidden').exists())

    def test_worker_updates_version_for_next_task_without_changing_current_snapshot(self):
        from unittest.mock import patch
        from net.inspections.queue import enqueue_task, claim_next_task
        from net.inspections.executor import execute_target
        from net.infrastructure.collection import CollectionResult
        version = self.version()
        self.device.os_version = ''
        self.device.save(update_fields=['os_version'])
        profile = InspectionProfile.objects.create(name='Discover version', device_type='network_device', selected_items=['device_info'])
        task = enqueue_task(profile, [self.device.pk], TaskRun.Source.MANUAL)
        original = task.target_runs.get().target_snapshot
        self.assertNotIn(str(version.pk), original['collection_settings']['_templates'])
        claimed = claim_next_task('version-fixture-worker', 30)
        result = CollectionResult(True, 'success', data={'device_info': {'version': 'VRP V200R019C10'}, 'port_count': 24})
        with patch('net.inspections.executor._collect', return_value=result):
            outcome = execute_target(claimed.target_runs.get(), worker_id='version-fixture-worker')
        self.assertFalse(outcome.stale)
        self.device.refresh_from_db()
        self.assertEqual(self.device.os_version, 'VRP V200R019C10')
        self.assertEqual(self.device.port_count, 24)
        self.assertEqual(task.target_runs.get().target_snapshot, original)
        second_profile = InspectionProfile.objects.create(name='Use learned version', device_type='network_device', selected_items=['cpu'])
        next_task = enqueue_task(second_profile, [self.device.pk], TaskRun.Source.MANUAL)
        self.assertIn(str(version.pk), next_task.target_runs.get().target_snapshot['collection_settings']['_templates'])

    def test_existing_defaults_remain_idempotent_with_version_templates(self):
        from net.devices.network.default_profiles import create_default_network_templates
        version=self.version()
        create_default_network_templates()
        count=DeviceCollectionTemplate.objects.count()
        self.assertEqual(create_default_network_templates(), [])
        self.assertEqual(DeviceCollectionTemplate.objects.count(), count)
        self.assertEqual(DeviceCollectionTemplate.objects.get(pk=version.pk).parent_id, self.device_type.pk)

    def test_new_version_entry_prefills_scope_without_creating_a_template(self):
        self.client.force_login(get_user_model().objects.create_user('version-entry-admin', is_staff=True))
        count=DeviceCollectionTemplate.objects.count()
        response=self.client.get(reverse('collection_templates',args=['networks']), {'parent': str(self.device_type.pk)})
        self.assertEqual(response.context['form']['parent'].value(), self.device_type.pk)
        self.assertEqual(response.context['form']['subtype'].value(), 'switch')
        self.assertEqual(response.context['form']['vendor'].value(), 'huawei')
        self.assertEqual(DeviceCollectionTemplate.objects.count(), count)

    def test_invalid_parent_link_returns_404_instead_of_server_error(self):
        self.client.force_login(get_user_model().objects.create_user('invalid-version-admin', is_staff=True))
        response=self.client.get(reverse('collection_templates',args=['networks']), {'parent': 'invalid-id'})
        self.assertEqual(response.status_code,404)

    def test_version_can_be_manually_selected_before_device_version_is_known(self):
        version=self.version()
        self.device.os_version=''
        DeviceCollectionBinding.objects.create(kind='networks',target_id=self.device.pk,template=version)
        result=resolve_collection_settings('networks',self.device)
        self.assertEqual(result['thresholds']['cpu'],65)
        self.assertEqual(result['_version_match']['status'],'explicit')
