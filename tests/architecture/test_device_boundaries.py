from importlib import import_module

from django.test import SimpleTestCase


class DeviceBoundaryTests(SimpleTestCase):
    ENTRY_POINTS = (
        ("net.devices.pc.analysis", "analyze_log"),
        ("net.devices.pc.logs", "import_log_bytes"),
        ("net.devices.pc.remote_ingestion", "fetch_remote_logs"),
        ("net.devices.pc.snapshot", "extract_computer_snapshot"),
        ("net.devices.pc.executor", "execute_computer_target"),
        ("net.infrastructure.collection", "CollectionResult"),
        ("net.infrastructure.native_http", "read_dahua_network"),
        ("net.devices.server.windows_http", "collect_windows_http"),
        ("net.devices.server.linux_ssh", "collect_linux_ssh"),
        ("net.devices.network.ssh", "collect_network_ssh"),
        ("net.devices.network.snmp", "collect_network_snmp"),
        ("net.devices.security.api", "collect_security_api"),
        ("net.devices.inventory", "refresh_asset_inventory"),
        ("net.data_exchange.inventory_csv", "import_csv"),
        ("net.data_exchange.table_csv", "export_filtered_csv"),
        ("net.data_exchange.configuration", "latest_configuration"),
        ("net.devices.network.configuration", "adapt"),
        ("net.devices.security.configuration", "adapt"),
    )

    def test_device_entry_points_are_available(self):
        for module_name, attribute in self.ENTRY_POINTS:
            with self.subTest(module=module_name, attribute=attribute):
                try:
                    module = import_module(module_name)
                except ModuleNotFoundError:
                    self.fail(f"device module is missing: {module_name}")
                self.assertTrue(callable(getattr(module, attribute)))
