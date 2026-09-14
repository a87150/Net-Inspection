from django.test import TestCase
from django.core.exceptions import ValidationError
from net.models import Network_Device, DeviceCollectionTemplate, DeviceCollectionBinding
from net.devices.collection_profiles import resolve_collection_settings

class CollectionProfilesTests(TestCase):
    def setUp(self):
        self.device = Network_Device.objects.create(ip='192.0.2.10', vendor='华为', device_type='交换机', connection_type='ssh')

    def test_vendor_type_and_device_override_precedence(self):
        DeviceCollectionTemplate.objects.create(name='all', kind='networks', vendor='', subtype='', settings={'thresholds': {'cpu': 80, 'memory': 85}})
        parent=DeviceCollectionTemplate.objects.create(name='vendor', kind='networks', vendor='huawei', subtype='', settings={'thresholds': {'cpu': 75, 'memory': 85}})
        DeviceCollectionTemplate.objects.create(name='switch', kind='networks', vendor='huawei', subtype='switch', parent=parent, settings={'thresholds': {'cpu': 70}})
        DeviceCollectionBinding.objects.create(kind='networks', target_id=self.device.pk, overrides={'thresholds': {'cpu': 60}})
        result = resolve_collection_settings('networks', self.device)
        self.assertEqual(result['thresholds'], {'cpu': 60, 'memory': 85})

    def test_different_device_type_does_not_match(self):
        DeviceCollectionTemplate.objects.create(name='router', kind='networks', vendor='huawei', subtype='router', settings={'thresholds': {'cpu': 1}})
        self.assertNotIn('thresholds', resolve_collection_settings('networks', self.device))

    def test_plain_credentials_not_allowed_in_template(self):
        row = DeviceCollectionTemplate(name='bad',kind='monitors', settings={'snmp': {'snmp_community': 'secret'}})
        with self.assertRaises(ValidationError):
            row.full_clean()

    def test_queue_freezes_settings_and_executor_uses_per_target_selection(self):
        from net.models import InspectionProfile, TaskRun
        from net.inspections.queue import enqueue_task
        from net.inspections.executor import _device_task
        template=DeviceCollectionTemplate.objects.create(name='cpu',kind='networks',vendor='huawei',subtype='switch',settings={'selected_items':['cpu'],'thresholds':{'cpu':65},'alert_items':[]})
        profile=InspectionProfile.objects.create(name='profile',device_type='network_device',selected_items=['cpu','memory'])
        task=enqueue_task(profile,[str(self.device.pk)],TaskRun.Source.MANUAL)
        target=task.target_runs.get()
        template.settings={'selected_items':['memory']};template.save()
        local=_device_task(target,task)
        self.assertEqual(local.selected_items_snapshot,['cpu','memory','config_info'])
        self.assertEqual(local.profile_snapshot['issue_thresholds']['cpu'],65)
        self.assertNotIn('device_alert_items',local.profile_snapshot)
        self.assertEqual(task.selected_items_snapshot,['cpu','memory','config_info'])

    def test_template_and_binding_views_enforce_admin_and_keep_form_after_save(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        reader=get_user_model().objects.create_user(username='r')
        admin=get_user_model().objects.create_user(username='a',is_staff=True)
        url=reverse('device_collection_settings',args=['networks',self.device.pk])
        self.assertEqual(self.client.get(url).status_code,302)
        self.client.force_login(reader)
        self.assertEqual(self.client.post(url,{}).status_code,403)
        self.client.force_login(admin)
        self.assertEqual(self.client.get(url).status_code,200)
        response=self.client.post(url,{'version':'','item_mode':'custom','selected_items':['cpu'],'alert_mode':'custom','alert_items':[],'threshold_cpu':'60'})
        self.assertEqual(response.status_code,200)
        self.assertTrue(DeviceCollectionBinding.objects.filter(target_id=self.device.pk).exists())
        self.assertContains(response,'已保存')
        conflict=self.client.post(url,{'version':'','item_mode':'inherit','alert_mode':'inherit'})
        self.assertContains(conflict,'已被修改')

    def test_malformed_thresholds_and_cross_project_rules_are_rejected(self):
        from net.devices.collection_profiles import validate_settings
        for settings in [{'thresholds':[]},{'thresholds':{'cpu':101}},{'selected_items':['storage_status']},{'snmp':{'snmp_community':'private'}}]:
            with self.subTest(settings=settings),self.assertRaises(ValidationError):
                validate_settings('networks',settings)

    def test_security_snmp_secrets_are_encrypted_and_not_in_snapshot(self):
        from cryptography.fernet import Fernet
        from django.test import override_settings
        from net.models import SecurityDevice
        from net.devices.collection_profiles import encrypt_credentials, attach_live_credentials
        with override_settings(DEVICE_BACKUP_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            asset=SecurityDevice.objects.create(ip='192.0.2.20',device_type='门禁')
            binding=DeviceCollectionBinding.objects.create(kind='monitors',target_id=asset.pk,overrides={'protocol':'snmp','snmp':{'snmp_version':'v2c'}},encrypted_credentials=encrypt_credentials({'snmp_community':'private-community'}))
            snapshot=resolve_collection_settings('monitors',asset)
            self.assertNotIn('private-community',str(snapshot))
            self.assertNotIn('private-community',binding.encrypted_credentials)
            self.assertEqual(attach_live_credentials('monitors',asset.pk,snapshot)['snmp']['snmp_community'],'private-community')
            binding.encrypted_credentials=encrypt_credentials({'snmp_community':'changed'});binding.save()
            with self.assertRaises(ValidationError):attach_live_credentials('monitors',asset.pk,snapshot)

    def test_server_template_has_no_vendor_and_matches_only_operating_system(self):
        from tests.auth import login_admin
        from django.urls import reverse
        from types import SimpleNamespace
        login_admin(self.client)
        url=reverse('collection_templates',args=['servers'])
        response=self.client.get(url)
        self.assertNotIn('vendor',response.context['form'].fields)
        self.assertContains(response,'配置模板')
        response=self.client.post(url,{'name':'Linux only','subtype':'linux','vendor':'huawei',
            'is_enabled':'on','item_mode':'inherit','alert_mode':'inherit'})
        self.assertContains(response,'已保存')
        template=DeviceCollectionTemplate.objects.get(kind='servers')
        self.assertEqual(template.vendor,'')
        template.settings={'thresholds':{'cpu':60}};template.save()
        result=resolve_collection_settings('servers',SimpleNamespace(pk=self.device.pk,server_type='linux',manufacturer='Dell'))
        self.assertEqual(result['thresholds']['cpu'],60)
        self.assertEqual(resolve_collection_settings('servers',SimpleNamespace(pk=self.device.pk,server_type='windows',manufacturer='Dell')), {})

    def test_brand_choices_are_category_specific_and_reject_cross_category_post(self):
        from index.devices.forms import device_form
        from tests.auth import login_admin
        from django.urls import reverse
        network=device_form('networks'); security=device_form('monitors')
        self.assertNotIn('vendor',device_form('servers').fields)
        self.assertIn('huawei',dict(network.fields['vendor'].choices))
        self.assertNotIn('hikvision',dict(network.fields['vendor'].choices))
        self.assertIn('zkteco',dict(security.fields['vendor'].choices))
        self.assertNotIn('cisco',dict(security.fields['vendor'].choices))
        form=device_form('networks',{'ip':'192.0.2.99','vendor':'hikvision'})
        self.assertFalse(form.is_valid());self.assertIn('vendor',form.errors)
        login_admin(self.client)
        response=self.client.post(reverse('collection_templates',args=['monitors']),
            {'name':'wrong','vendor':'huawei','subtype':'camera','item_mode':'inherit','alert_mode':'inherit'})
        self.assertIn('vendor',response.context['form'].errors)
        self.assertFalse(DeviceCollectionTemplate.objects.filter(name='wrong').exists())

    def test_existing_device_brand_is_preserved_without_becoming_global_choice(self):
        from index.devices.forms import device_form
        self.device.vendor='legacy-brand';self.device.save()
        form=device_form('networks',instance=self.device)
        self.assertIn('legacy-brand',dict(form.fields['vendor'].choices))
        self.assertNotIn('legacy-brand',dict(device_form('networks').fields['vendor'].choices))

    def test_item_command_editor_saves_and_previews_without_saving(self):
        from tests.auth import login_admin
        from django.urls import reverse
        login_admin(self.client)
        url=reverse('collection_templates',args=['networks'])
        values={'name':'CPU template','vendor':'huawei','subtype':'switch','item_mode':'inherit','alert_mode':'inherit',
            'commands_cpu':'display custom-cpu','engine_cpu':'regex','template_cpu':r'CPU (?P<usage>\d+)%',
            'sample_cpu':'CPU 37%','preview_item':'cpu'}
        response=self.client.post(url,values)
        self.assertContains(response,'37')
        self.assertIn('usage_percent',response.context['form'].preview_result)
        self.assertFalse(DeviceCollectionTemplate.objects.filter(name='CPU template').exists())
        values.pop('preview_item')
        response=self.client.post(url,values)
        self.assertContains(response,'已保存')
        settings=DeviceCollectionTemplate.objects.get(name='CPU template').settings
        self.assertEqual(settings['commands']['cpu'],['display custom-cpu'])
        self.assertNotIn('sample_cpu',settings)
        self.assertEqual(settings['parsers']['cpu']['engine'],'regex')

    def test_inspection_page_has_template_modal_and_device_picker_precedes_items(self):
        from tests.auth import login_admin
        from django.urls import reverse
        login_admin(self.client)
        for kind in ('networks','servers','monitors'):
            response=self.client.get(reverse('record_list',args=[kind]))
            self.assertNotContains(response,'data-modal-load="collectionTemplateModal"')
            listing=self.client.get(reverse('asset_list',args=[kind]))
            self.assertContains(listing,'data-modal-load="collectionTemplateModal"')
            self.assertContains(listing,'id="collectionTemplateModal"')
            html=response.content.decode()
            self.assertLess(html.index('data-config-step="targets"'),html.index('data-config-step="items"'))
            if kind=='servers':self.assertNotIn('aria-label="按厂商筛选设备"',html)

    def test_explicit_parent_only_and_cycle_validation(self):
        parent=DeviceCollectionTemplate.objects.create(name='基础',kind='networks',vendor='huawei',settings={'commands':{'cpu':['display cpu-usage']},'thresholds':{'cpu':80,'memory':85}})
        child=DeviceCollectionTemplate.objects.create(name='交换',kind='networks',vendor='huawei',subtype='switch',settings={'thresholds':{'cpu':70}})
        self.assertNotIn('commands',resolve_collection_settings('networks',self.device))
        child.parent=parent;child.full_clean();child.save()
        result=resolve_collection_settings('networks',self.device)
        self.assertEqual(result['commands']['cpu'],['display cpu-usage'])
        self.assertEqual(result['thresholds'],{'cpu':70,'memory':85})
        self.assertEqual(result['_rule_sources']['commands.cpu'],'基础')
        parent.vendor='h3c'
        with self.assertRaises(ValidationError):parent.full_clean()
        parent.vendor='huawei'
        parent.parent=child
        with self.assertRaises(ValidationError):parent.full_clean()
        child.parent=child
        with self.assertRaises(ValidationError):child.full_clean()
        other=DeviceCollectionTemplate.objects.create(name='其他厂商',kind='networks',vendor='h3c')
        child.parent=other
        with self.assertRaises(ValidationError):child.full_clean()

    def test_parent_form_and_disabled_items_are_frozen_at_queue(self):
        from tests.auth import login_admin
        from django.urls import reverse
        from net.models import InspectionProfile,TaskRun
        from net.inspections.queue import enqueue_task
        from net.inspections.executor import _device_task
        login_admin(self.client)
        parent=DeviceCollectionTemplate.objects.create(name='华为基础',kind='networks',vendor='huawei',settings={'commands':{'cpu':['display cpu-usage']},'item_methods':{'cpu':'snmp'},'thresholds':{'cpu':80}})
        response=self.client.post(reverse('collection_templates',args=['networks']),{'name':'交换机','vendor':'huawei','subtype':'switch','parent':str(parent.pk),'is_enabled':'on','threshold_cpu':'65','enabled_memory':'no'})
        self.assertContains(response,'已保存')
        self.assertContains(response,'华为基础')
        child=DeviceCollectionTemplate.objects.get(subtype='switch')
        self.assertEqual(child.parent_id,parent.pk)
        self.assertNotIn('cpu',child.settings['item_methods'])
        profile=InspectionProfile.objects.create(name='scope',device_type='network_device',selected_items=['cpu','memory'])
        task=enqueue_task(profile,[str(self.device.pk)],TaskRun.Source.MANUAL)
        parent.settings['thresholds']['cpu']=99;parent.save()
        child.settings['item_enabled']['memory']=True;child.save()
        local=_device_task(task.target_runs.get(),task)
        self.assertEqual(local.selected_items_snapshot,['cpu','config_info'])
        self.assertEqual(local.profile_snapshot['issue_thresholds']['cpu'],65)
        self.assertEqual(task.target_runs.get().target_snapshot['collection_settings']['item_methods']['cpu'],'snmp')
        self.assertIn('memory',resolve_collection_settings('networks',self.device)['selected_items'])

    def test_snmp_item_method_and_scalar_conversion_form(self):
        from index.devices.collection_profiles import CollectionSettingsForm
        form=CollectionSettingsForm({'method_cpu':'snmp','snmp_oid_cpu':'1.3.6.1.4.1.2011.6.1.0','snmp_scale_cpu':'0.01','threshold_cpu':'60','level_cpu':'critical'},kind='networks')
        self.assertTrue(form.is_valid(),form.errors)
        settings=form.settings_value()
        self.assertEqual(settings['item_methods'],{'cpu':'snmp'})
        self.assertEqual(settings['snmp_transforms']['cpu']['scale'],0.01)
        self.assertEqual(settings['severity_overrides']['cpu'],'critical')
        self.assertNotIn('selected_items',settings)

    def test_inherited_rule_executes_and_persists_parsed_cpu_alarm(self):
        from unittest.mock import patch
        from tests.auth import login_admin
        from django.urls import reverse
        from net.models import InspectionProfile,TaskRun,Network_Device_Inspection
        from net.inspections.queue import enqueue_task,claim_next_task
        from net.inspections.executor import execute_target
        from net.devices.network.collector import collect_network
        from net.devices.network.snmp import collect_network_snmp
        from net.infrastructure.collection import CollectionResult
        from tests.devices.network.test_snmp import MemorySession
        login_admin(self.client)
        response=self.client.post(reverse('collection_templates',args=['networks']),{'name':'基础','vendor':'huawei','is_enabled':'on','method_cpu':'snmp','snmp_oid_cpu':'1.3.6.1.4.1.999.1.0','snmp_scale_cpu':'0.01','threshold_cpu':'60','level_cpu':'critical'})
        self.assertContains(response,'已保存')
        parent=DeviceCollectionTemplate.objects.get(name='基础', vendor='huawei')
        DeviceCollectionTemplate.objects.create(name='交换机',kind='networks',vendor='huawei',subtype='switch',parent=parent)
        profile=InspectionProfile.objects.create(name='CPU',device_type='network_device',selected_items=['cpu'])
        enqueue_task(profile,[str(self.device.pk)],TaskRun.Source.MANUAL)
        claimed=claim_next_task('inheritance-test',30)
        def collect(asset,timeout=12,selected_items=None):
            def snmp(asset,timeout,selected_items):
                return collect_network_snmp(asset,timeout,selected_items=selected_items,session_factory=lambda *args:MemorySession(scalars={'1.3.6.1.4.1.999.1.0':6400}))
            return collect_network(asset,timeout,selected_items,snmp_collector=snmp,ssh_collector=lambda *args,**kwargs:CollectionResult(True,'partial','test: no config backup'))
        with patch('net.inspections.executor.collect_network',side_effect=collect):
            execute_target(claimed.target_runs.get(),worker_id='inheritance-test')
        record=Network_Device_Inspection.objects.get()
        self.assertEqual(record.details['cpu']['usage_percent'],64)
        cpu=[finding for finding in record.details['issue_findings'] if finding['analysis_item']=='cpu']
        self.assertEqual(len(cpu),1)
        self.assertEqual(cpu[0]['severity'],'critical')

    def test_inherit_mode_discards_local_values_and_custom_keeps_only_edited_rule(self):
        from index.devices.collection_profiles import CollectionSettingsForm
        form=CollectionSettingsForm({'rule_mode_cpu':'inherit','threshold_cpu':'invalid','method_cpu':'ssh','commands_cpu':'display cpu','rule_mode_memory':'custom','threshold_memory':'75'},kind='networks')
        self.assertTrue(form.is_valid(),form.errors)
        value=form.settings_value()
        self.assertEqual(value['thresholds'],{'memory':75})
        self.assertNotIn('cpu',value['item_methods'])
        self.assertNotIn('cpu',value.get('commands',{}))

    def test_parent_preview_without_name_does_not_save_or_copy_parent_values(self):
        from tests.auth import login_admin
        from django.urls import reverse
        login_admin(self.client)
        parent=DeviceCollectionTemplate.objects.create(name='base',kind='networks',vendor='huawei',settings={'thresholds':{'cpu':80}})
        response=self.client.post(reverse('collection_templates',args=['networks']),{'parent':str(parent.pk),'preview_inheritance':'1','rule_mode_cpu':'inherit'})
        self.assertContains(response,'80')
        self.assertNotContains(response,'data-config-saved')
        self.assertEqual(DeviceCollectionTemplate.objects.filter(name='base').count(),1)
        self.assertEqual(response.context['form']['threshold_cpu'].value(),None)

    def test_device_inspection_settings_opens_in_list_modal(self):
        from tests.auth import login_admin
        from django.urls import reverse
        login_admin(self.client)
        response=self.client.get(reverse('asset_list',args=['networks']))
        self.assertContains(response,reverse('device_collection_settings',args=['networks',self.device.pk]))
        self.assertContains(response,'data-modal-load="collectionTemplateModal">巡检设置')
        self.assertNotContains(response,'巡检与报警设置')

    def test_reset_one_project_to_parent_keeps_other_project_and_updates_new_snapshot(self):
        from tests.auth import login_admin
        from django.urls import reverse
        from net.models import InspectionProfile,TaskRun
        from net.inspections.queue import enqueue_task
        login_admin(self.client)
        parent=DeviceCollectionTemplate.objects.create(name='基础',kind='networks',vendor='huawei',settings={'thresholds':{'cpu':80},'item_methods':{'cpu':'snmp'}})
        child=DeviceCollectionTemplate.objects.create(name='交换',kind='networks',vendor='huawei',subtype='switch',parent=parent,settings={'thresholds':{'cpu':60,'memory':75},'item_methods':{'cpu':'ssh'}})
        response=self.client.post(reverse('collection_templates',args=['networks'])+'?edit='+str(child.pk),{'name':child.name,'vendor':'huawei','subtype':'switch','parent':str(parent.pk),'is_enabled':'on','version':child.updated_at.isoformat(),'rule_mode_cpu':'inherit','rule_mode_memory':'custom','threshold_memory':'75'})
        self.assertContains(response,'已保存')
        child.refresh_from_db()
        self.assertEqual(child.settings['thresholds'],{'memory':75})
        effective=resolve_collection_settings('networks',self.device)
        self.assertEqual(effective['thresholds'],{'cpu':80,'memory':75})
        self.assertEqual(effective['item_methods']['cpu'],'snmp')
        profile=InspectionProfile.objects.create(name='检查',device_type='network_device',selected_items=['cpu','memory'])
        task=enqueue_task(profile,[self.device.pk],TaskRun.Source.MANUAL)
        self.assertEqual(task.target_runs.get().target_snapshot['collection_settings']['thresholds'],{'cpu':80,'memory':75})

    def test_network_connection_sections_use_simple_default_and_preserve_existing_mode(self):
        from index.devices.forms import device_form,device_form_sections
        form=device_form('networks')
        self.assertEqual(form['connection_type'].value(),'auto')
        sections=device_form_sections(form)
        self.assertEqual(next(section for section in sections if section.get('snmp_group')=='v2c')['fields'][0].name,'snmp_community')
        self.assertEqual(device_form('networks',instance=self.device)['connection_type'].value(),'ssh')
        self.assertTrue(next(section for section in sections if any(field.name=='snmp_port' for field in section['fields']))['advanced'])

    def test_network_form_limits_connection_fields_and_preserves_inactive_values(self):
        from index.devices.forms import device_form, device_form_sections
        api = device_form('networks', {
            'ip': '192.0.2.77', 'vendor': 'sangfor', 'device_type': 'ac_gateway',
            'connection_type': 'sangfor_api', 'api_url': 'https://ac.example.invalid:443',
            'api_shared_secret': 'secret', 'snmp_port': '70000',
        })
        self.assertTrue(api.is_valid(), api.errors)
        sections = device_form_sections(api)
        self.assertEqual(
            {section.get('network_fields') for section in sections if section.get('network_fields')},
            {'sangfor_api', 'standard'},
        )
        existing = Network_Device.objects.create(
            ip='192.0.2.78', connection_type='ssh', username='reader', password='secret',
            api_url='https://saved.example.invalid', api_shared_secret='saved-api-secret', verify_ssl=False,
        )
        standard = device_form('networks', {
            'ip': existing.ip, 'connection_type': 'ssh', 'username': 'next-reader',
            'api_url': 'not a URL', 'api_shared_secret': 'untrusted', 'verify_ssl': 'on',
        }, instance=existing)
        self.assertTrue(standard.is_valid(), standard.errors)
        self.assertEqual(standard.cleaned_data['api_url'], existing.api_url)
        self.assertEqual(standard.cleaned_data['api_shared_secret'], existing.api_shared_secret)
        self.assertFalse(standard.cleaned_data['verify_ssl'])
