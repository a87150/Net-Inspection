from django.test import TestCase
from net.models import SecurityDevice, InspectionProfile, TaskRun
from net.devices.collection_profiles import resolve_collection_settings, supported_collection_items
from net.inspections.queue import enqueue_task
from index.inspections.forms import InspectionProfileConfigForm


class DeviceItemScopeTests(TestCase):
    def setUp(self):
        self.ping=SecurityDevice.objects.create(ip='192.0.2.10',device_type='camera')
        self.nvr=SecurityDevice.objects.create(ip='192.0.2.11',device_type='nvr',api_url='https://192.0.2.11/status')
        self.access=SecurityDevice.objects.create(ip='192.0.2.12',device_type='access',api_url='https://192.0.2.12/status')

    def test_scope_distinguishes_ping_access_and_recorder(self):
        for asset, expected in [(self.ping,{'status_data'}),(self.access,{'device_info','status_data','config_info'})]:
            effective=resolve_collection_settings('monitors',asset)
            self.assertEqual(set(supported_collection_items('monitors',asset,effective)),expected)
        self.assertIn('channel_status',resolve_collection_settings('monitors',self.nvr)['selected_items'])

    def test_profile_rejects_items_unavailable_on_selected_device(self):
        form=InspectionProfileConfigForm({'name':'bad','timeout_seconds':'60','concurrent_workers':'1',
            'target_rule_mode':'selected','target_rule_ids':[str(self.ping.pk)],'selected_items':['channel_status']},device_type='monitor')
        self.assertFalse(form.is_valid())
        self.assertIn('selected_items',form.errors)

    def test_queue_omits_inapplicable_devices_and_records_reason(self):
        profile=InspectionProfile.objects.create(name='channels',device_type='monitor',selected_items=['channel_status'])
        task=enqueue_task(profile,[str(self.ping.pk),str(self.nvr.pk)],TaskRun.Source.MANUAL)
        self.assertEqual(task.total_targets,1)
        self.assertEqual(task.target_runs.get().target_id,str(self.nvr.pk))
        self.assertEqual(task.parameters_snapshot['inapplicable_target_ids'],[str(self.ping.pk)])
