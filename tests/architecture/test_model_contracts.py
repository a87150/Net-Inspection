from importlib import import_module

from django.apps import apps
from django.test import SimpleTestCase

from net.models import (
    AlertChannel,
    Computer,
    DomainOperation,
    Domain_Account,
    Domain_Computer,
    SecurityDevice,
    Network_Device,
    People,
    Server,
    TaskRun,
)


class ModelContractTests(SimpleTestCase):
    EXPECTED_TABLES = {
        People: "net_people",
        Domain_Account: "net_domain_account",
        Domain_Computer: "net_domain_computer",
        Computer: "net_computer",
        Network_Device: "net_network_device",
        Server: "net_server",
        SecurityDevice: "net_securitydevice",
        DomainOperation: "net_domainoperation",
        TaskRun: "net_taskrun",
        AlertChannel: "net_alertchannel",
    }

    def test_model_labels_and_tables_are_stable(self):
        for model, table in self.EXPECTED_TABLES.items():
            with self.subTest(model=model.__name__):
                self.assertEqual(model._meta.app_label, "net")
                self.assertEqual(model._meta.db_table, table)
                self.assertIs(apps.get_model("net", model.__name__), model)

    def test_canonical_model_modules_export_registered_models(self):
        expected_exports = {
            "net.models.people": {"People": People},
            "net.models.domain": {
                "Domain_Account": Domain_Account,
                "Domain_Computer": Domain_Computer,
                "DomainOperation": DomainOperation,
            },
            "net.models.devices": {
                "Computer": Computer,
                "Network_Device": Network_Device,
                "Server": Server,
                "SecurityDevice": SecurityDevice,
            },
            "net.models.tasks": {"TaskRun": TaskRun},
            "net.models.alerts": {"AlertChannel": AlertChannel},
        }

        for module_name, exports in expected_exports.items():
            with self.subTest(module=module_name):
                try:
                    module = import_module(module_name)
                except ModuleNotFoundError:
                    self.fail(f"canonical model module is missing: {module_name}")
                for name, registered_model in exports.items():
                    self.assertIs(getattr(module, name), registered_model)
