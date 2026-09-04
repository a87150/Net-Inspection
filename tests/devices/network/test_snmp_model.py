from importlib import import_module

from django.core.exceptions import ValidationError
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
        with self.assertRaises(ValidationError):
            device.full_clean()

    def test_v3_auth_priv_requires_username_and_both_passwords(self):
        device = Network_Device(
            ip="192.0.2.11",
            connection_type="hybrid",
            snmp_version="v3",
            snmp_security_level="authPriv",
            snmp_username="inspector",
            snmp_auth_protocol="sha256",
            snmp_priv_protocol="aes128",
        )
        with self.assertRaises(ValidationError):
            device.full_clean()

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
