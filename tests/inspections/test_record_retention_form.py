"""The record-retention choice round-trips through the profile form and the UI."""
from django.test import TestCase
from django.urls import reverse

from net.models import InspectionProfile, Network_Device
from tests.auth import login_admin


class RecordRetentionFormTests(TestCase):
    def setUp(self):
        login_admin(self.client)
        Network_Device.objects.create(device_name='edge', ip='192.0.2.10')
        self.profile = InspectionProfile.objects.create(
            name='retention-ui', device_type='network_device',
            selected_items=['device_info'], record_retention='180',
        )

    def save(self, **overrides):
        data = {
            'profile_id': str(self.profile.pk), 'name': 'retention-ui',
            'device_type': 'network_device', 'selected_items': ['device_info'],
            'timeout_seconds': '60', 'concurrent_workers': '2',
            'target_rule_mode': 'all',
        }
        data.update(overrides)
        return self.client.post(reverse('inspection_profile_configure'), data, follow=True)

    def test_selected_retention_is_persisted(self):
        self.save(record_retention='30')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.record_retention, '30')

    def test_offered_choices_are_the_four_expected_windows(self):
        response = self.client.get(reverse('asset_list', args=['networks']))
        body = response.content.decode('utf-8')
        self.assertIn('name="record_retention"', body)
        for value in ('none', '30', '90', '180'):
            self.assertIn('value="%s"' % value, body)

    def test_missing_value_falls_back_to_the_default_window(self):
        self.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.record_retention, '180')
