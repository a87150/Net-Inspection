from importlib import import_module

from django.test import SimpleTestCase


class DeviceBoundaryTests(SimpleTestCase):
    BOUNDARIES = (
        ("net.devices.pc.analysis", "net.services.computer_analysis", "analyze_log"),
        ("net.devices.pc.logs", "net.services.computer_logs", "scan_log_directory"),
        ("net.devices.pc.snapshot", "net.services.computer_snapshot", "extract_computer_snapshot"),
        ("net.devices.pc.executor", "net.tasks.executors.computer_analysis", "execute_computer_target"),
        ("net.infrastructure.collection", "net.services.collectors.base", "CollectionResult"),
        ("net.infrastructure.native_http", "net.services.collectors.native_http", "read_dahua_network"),
        ("net.devices.server.windows_http", "net.services.collectors.http", "collect_windows_http"),
        ("net.devices.server.linux_ssh", "net.services.collectors.ssh", "collect_linux_ssh"),
        ("net.devices.network.ssh", "net.services.collectors.ssh", "collect_network_ssh"),
        ("net.devices.security.api", "net.services.collectors.http", "collect_security_api"),
        ("net.devices.inventory", "net.services.inventory_refresh", "refresh_asset_inventory"),
        ("net.data_exchange.inventory_csv", "net.services.inventory_io", "import_csv"),
        ("net.data_exchange.table_csv", "net.exports.csv_export", "export_filtered_csv"),
        ("net.data_exchange.configuration", "net.exports.configuration", "latest_configuration"),
        ("net.devices.network.configuration", "net.exports.adapters.network", "adapt"),
        ("net.devices.security.configuration", "net.exports.adapters.security", "adapt"),
    )

    def test_canonical_and_legacy_paths_export_the_same_objects(self):
        for canonical_name, legacy_name, attribute in self.BOUNDARIES:
            with self.subTest(module=canonical_name, attribute=attribute):
                try:
                    canonical_module = import_module(canonical_name)
                except ModuleNotFoundError:
                    self.fail(f"canonical device module is missing: {canonical_name}")
                legacy_module = import_module(legacy_name)
                self.assertIs(
                    getattr(canonical_module, attribute),
                    getattr(legacy_module, attribute),
                )
