"""Compatibility alias for network configuration adaptation."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.devices.network.configuration')
