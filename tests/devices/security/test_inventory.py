from django.test import TestCase

from net import models


class SecurityDeviceInventoryTests(TestCase):
    def test_security_inventory_uses_device_model_name(self):
        self.assertTrue(hasattr(models, 'SecurityDevice'))
        device = models.SecurityDevice.objects.create(
            device_name='东门闸机',
            ip='192.0.2.130',
            device_type='闸机',
        )

        self.assertEqual(str(device), '东门闸机 (192.0.2.130)')
