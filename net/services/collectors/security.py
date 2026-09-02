"""Compatibility alias for security-device payload processing."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module('net.devices.security.payload')
