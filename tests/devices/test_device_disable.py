from django.test import TestCase
from django.urls import reverse

from index.devices.views import SWITCHABLE_KINDS
from net.models import Network_Device, Server, WeakCurrentDevice
from tests.auth import login_admin


class DeviceDisableTests(TestCase):
    """停用 hides a device from the default list and from inspection."""

    def setUp(self):
        login_admin(self.client)
        self.enabled = Network_Device.objects.create(device_name='核心', ip='192.0.2.1')
        self.disabled = Network_Device.objects.create(
            device_name='退役交换机', ip='192.0.2.2', is_enabled=False,
        )

    def row(self, kind, asset):
        """The detail link a device row carries; name filter options do not."""
        return reverse('asset_detail', args=[kind, asset.pk])

    def test_default_list_hides_disabled_devices(self):
        response = self.client.get(reverse('asset_list', args=['networks']))
        self.assertContains(response, self.row('networks', self.enabled))
        self.assertNotContains(response, self.row('networks', self.disabled))

    def test_disabled_devices_are_still_reachable_through_the_filter(self):
        response = self.client.get(reverse('asset_list', args=['networks']),
                                    {'filter_is_enabled': 'false'})
        self.assertContains(response, self.row('networks', self.disabled))
        self.assertNotContains(response, self.row('networks', self.enabled))

    def test_toggle_flips_the_flag_and_keeps_the_row(self):
        response = self.client.post(
            reverse('asset_toggle_enabled', args=['networks']),
            {'pk': str(self.disabled.pk)}, follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.disabled.refresh_from_db()
        self.assertTrue(self.disabled.is_enabled)

    def test_toggle_requires_post(self):
        response = self.client.get(reverse('asset_toggle_enabled', args=['networks']))
        self.assertEqual(response.status_code, 405)

    def test_toggle_rejects_asset_kinds_without_the_flag(self):
        response = self.client.post(reverse('asset_toggle_enabled', args=['people']),
                                    {'pk': '00000000-0000-0000-0000-000000000000'})
        self.assertEqual(response.status_code, 404)

    def test_every_switchable_kind_carries_the_flag(self):
        """Each kind hides its own disabled row and offers the toggle."""
        self.assertEqual(SWITCHABLE_KINDS, {'networks', 'servers', 'weakcurrent'})
        samples = {
            'networks': (Network_Device, 'device_name'),
            'servers': (Server, 'name'),
            'weakcurrent': (WeakCurrentDevice, 'device_name'),
        }
        for index, kind in enumerate(sorted(SWITCHABLE_KINDS)):
            model, name_field = samples[kind]
            on = model.objects.create(
                **{name_field: '在用', 'ip': '192.0.2.%d' % (10 + index)}
            )
            off = model.objects.create(
                **{name_field: '退役', 'ip': '192.0.2.%d' % (20 + index)},
                is_enabled=False,
            )
            listed = self.client.get(reverse('asset_list', args=[kind]))
            self.assertContains(listed, self.row(kind, on), msg_prefix=kind)
            self.assertNotContains(listed, self.row(kind, off), msg_prefix=kind)
            self.assertContains(listed, '停用', msg_prefix=kind)
            show_off = self.client.get(
                reverse('asset_list', args=[kind]), {'filter_is_enabled': 'false'},
            )
            self.assertContains(show_off, self.row(kind, off), msg_prefix=kind)

