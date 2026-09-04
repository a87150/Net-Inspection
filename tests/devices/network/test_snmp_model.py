from importlib import import_module

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.test import TestCase

from net.models import Network_Device


SNMP_FIELD_NAMES = {
    "snmp_version",
    "snmp_port",
    "snmp_community",
    "snmp_security_level",
    "snmp_username",
    "snmp_auth_protocol",
    "snmp_auth_password",
    "snmp_priv_protocol",
    "snmp_priv_password",
    "snmp_context_name",
    "snmp_retries",
}


class NetworkSnmpModelTests(TestCase):
    def test_blank_or_unknown_transport_retains_ssh_compatibility(self):
        self.assertEqual(Network_Device(connection_type="").effective_connection_type, "ssh")
        self.assertEqual(
            Network_Device(connection_type="legacy").effective_connection_type,
            "ssh",
        )

    def test_v2c_requires_community_when_snmp_is_enabled(self):
        device = Network_Device(
            ip="192.0.2.10", connection_type="snmp", snmp_version="v2c"
        )
        with self.assertRaises(ValidationError) as raised:
            device.full_clean()

        self.assertIn('snmp_community', raised.exception.message_dict)

    def test_v3_auth_priv_requires_username_and_both_passwords(self):
        device = Network_Device(
            ip="192.0.2.11",
            connection_type="hybrid",
            snmp_version="v3",
            snmp_security_level="authPriv",
            snmp_username="inspector",
        )
        with self.assertRaises(ValidationError) as raised:
            device.full_clean()

        self.assertEqual(
            set(raised.exception.message_dict),
            {
                'snmp_auth_protocol', 'snmp_auth_password',
                'snmp_priv_protocol', 'snmp_priv_password',
            },
        )

    def test_model_defines_all_snmp_fields(self):
        field_names = {field.name for field in Network_Device._meta.get_fields()}

        self.assertTrue(SNMP_FIELD_NAMES <= field_names)

    def test_migration_adds_all_snmp_fields(self):
        migration = import_module("net.migrations.0022_network_device_snmp")
        added_fields = {
            operation.name
            for operation in migration.Migration.operations
            if operation.__class__.__name__ == "AddField"
        }

        self.assertEqual(added_fields, SNMP_FIELD_NAMES)

    def test_snmp_retries_accepts_design_range_and_rejects_six(self):
        field = Network_Device._meta.get_field('snmp_retries')
        minimums = [
            validator.limit_value
            for validator in field.validators
            if isinstance(validator, MinValueValidator)
        ]
        maximums = [
            validator.limit_value
            for validator in field.validators
            if isinstance(validator, MaxValueValidator)
        ]

        self.assertEqual(field.default, 1)
        self.assertIn(0, minimums)
        self.assertIn(5, maximums)
        for retries in (0, 5):
            with self.subTest(retries=retries):
                Network_Device(ip=f'192.0.2.{20 + retries}', snmp_retries=retries).full_clean()
        with self.assertRaises(ValidationError) as raised:
            Network_Device(ip='192.0.2.30', snmp_retries=6).full_clean()
        self.assertIn('snmp_retries', raised.exception.message_dict)

    def test_0022_snmp_retries_operation_matches_model_default_and_validators(self):
        migration = import_module('net.migrations.0022_network_device_snmp')
        operation = next(
            operation
            for operation in migration.Migration.operations
            if operation.__class__.__name__ == 'AddField'
            and operation.name == 'snmp_retries'
        )
        field = operation.field

        self.assertEqual(operation.model_name, 'network_device')
        self.assertEqual(field.default, 1)
        self.assertEqual(
            [
                validator.limit_value
                for validator in field.validators
                if isinstance(validator, MinValueValidator)
            ],
            [0],
        )
        self.assertEqual(
            [
                validator.limit_value
                for validator in field.validators
                if isinstance(validator, MaxValueValidator)
            ],
            [5],
        )
