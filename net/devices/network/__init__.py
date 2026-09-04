"""Network-device inspection and configuration feature package."""

from net.devices.network.snmp import SNMP_ITEMS, collect_network_snmp

__all__ = ["SNMP_ITEMS", "collect_network_snmp"]
