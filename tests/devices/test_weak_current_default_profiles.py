from django.test import TestCase

from net.devices.weakcurrent.default_profiles import (
    SNMP_OIDS,
    TYPES,
    base_settings,
    create_default_weak_current_templates,
    subtype_settings,
)
from net.models import DeviceCollectionTemplate


class WeakCurrentDefaultTemplateTests(TestCase):
    """弱电默认模板 mirrors the network ones: idempotent, scoped, editable."""

    def test_creation_is_idempotent_and_keeps_edits(self):
        created = create_default_weak_current_templates()
        self.assertEqual(len(created), len(TYPES) + 1)
        DeviceCollectionTemplate.objects.filter(kind='weakcurrent').update(
            name='人工改过的名字',
        )
        self.assertEqual(create_default_weak_current_templates(), [])
        edited = DeviceCollectionTemplate.objects.filter(
            kind='weakcurrent', subtype='nvr',
        ).get()
        self.assertEqual(edited.name, '人工改过的名字')

    def test_base_carries_standard_snmp_scalars_only(self):
        settings = base_settings()
        self.assertEqual(settings['snmp_oids'], SNMP_OIDS)
        # Wrong HOST-RESOURCES OIDs read worse than a missing metric.
        for metric in ('cpu', 'memory_total', 'memory_used', 'temperature'):
            self.assertNotIn(metric, settings['snmp_oids'])
        # 'auto' already walks SNMP -> API -> Ping; naming a method disables it.
        self.assertNotIn('item_methods', settings)

    def test_only_subtypes_with_channels_or_storage_enable_them(self):
        nvr = subtype_settings('nvr')['item_enabled']
        self.assertTrue(nvr['channel_status'])
        self.assertTrue(nvr['storage_status'])
        self.assertTrue(subtype_settings('access')['item_enabled']['channel_status'])
        for subtype in ('camera', 'intercom', 'broadcast', 'printer',
                        'environment', 'other'):
            enabled = subtype_settings(subtype)['item_enabled']
            self.assertFalse(enabled['channel_status'], subtype)
            self.assertFalse(enabled['storage_status'], subtype)
            self.assertTrue(enabled['device_info'], subtype)
            self.assertTrue(enabled['status_data'], subtype)

    def test_every_subtype_in_the_subtype_filter_has_a_template(self):
        from net.devices.collection_profiles import SUBTYPES
        create_default_weak_current_templates()
        for _code, label in SUBTYPES['weakcurrent']:
            if not _code:
                continue
            self.assertIn(_code, TYPES, label)

