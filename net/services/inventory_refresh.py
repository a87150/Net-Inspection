"""Compatibility alias for managed-device inventory refresh."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.devices.inventory')
